"""带默认值的类属性不得让 `@Inject` 崩掉（需求 8 的实操前提）。

现场报障：保存 OpenAI 供应商配置报
``'bool' object has no attribute 'fget'``，MCP 面板显示「连接失败 / 已连接 0 /
工具数 0」。两处是同一个异常——``Inject.inject_class`` 遍历类属性时写的是::

    attr = getattr(cls, name) if hasattr(cls, name) else None
    ...
    if prop:                 # 判「真值」，不是判「是不是 property」
        fget = prop.fget     # attr 是 True 时，True.fget 不存在

于是**任何带类型注解且默认值为真值的类属性**都会在装饰阶段抛 AttributeError：
``flag: bool = True``、非空字符串、非零整数、非空 dict/list 全部命中。
而带假值的（``False``、``0``、``""``、``None``）恰好走进 else 分支，所以这个缺陷
在很长时间里只在一部分类上出现——这正是它难被发现的原因。

判据：``if x:`` 与 ``isinstance(x, property)`` 不是同一个判断。前者把
「值恰好为假」和「类型不是 property」合并成同一个分支，而这里两者都该走默认路径。
"""

import asyncio
from typing import Any, Optional

import pytest

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject


class _Service:
    """A resolvable dependency used to prove real injection still happens."""

    def __init__(self, tag: str = "resolved") -> None:
        self.tag = tag


def _container() -> DependencyContainer:
    container = DependencyContainer()
    container.register(_Service, _Service())
    return container


@pytest.mark.parametrize(
    ("annotation", "default"),
    [
        (bool, True),
        (int, 1),
        (str, "text"),
        (float, 1.5),
        (tuple, ("a",)),
        (frozenset, frozenset({"a"})),
    ],
)
def test_a_truthy_annotated_class_attribute_does_not_break_injection(
    annotation: type, default: Any
) -> None:
    """真值默认值是最常见的写法，它绝不该让整个类无法被装饰。"""

    namespace = {"__annotations__": {"field": annotation}, "field": default}
    cls = type("Truthy", (), namespace)

    injected = Inject(_container())(cls)

    instance = injected()
    assert instance.field == default


@pytest.mark.parametrize(
    ("annotation", "default"),
    [
        (bool, False),
        (int, 0),
        (str, ""),
        (type(None), None),
    ],
)
def test_a_falsy_annotated_class_attribute_keeps_working(
    annotation: type, default: Any
) -> None:
    """假值此前恰好能过（走 else 分支），修复不得改变它。"""

    namespace = {"__annotations__": {"field": annotation}, "field": default}
    cls = type("Falsy", (), namespace)

    injected = Inject(_container())(cls)

    assert injected().field == default


def test_a_mutable_default_is_not_shared_between_instances() -> None:
    """可变默认值必须每个实例各一份。

    注入把类属性换成 property + 实例级 backing store，因此两个实例改各自的
    dict 不能互相影响——共享会让一个适配器的配置改动出现在另一个上。
    """

    namespace = {"__annotations__": {"settings": dict}, "settings": {}}
    cls = type("WithDict", (), namespace)
    injected = Inject(_container())(cls)

    first, second = injected(), injected()
    first.settings = {"a": 1}

    assert second.settings != {"a": 1}


def test_a_real_property_still_uses_its_own_getter() -> None:
    """真正的 property 必须继续走它自己的 getter。

    这条是上面那个 `isinstance` 判断的另一半：不能为了修真值默认值而把
    property 的自定义行为也一并丢掉。
    """

    class WithProperty:
        computed: str

        @property
        def computed(self) -> str:  # type: ignore[no-redef]
            return "from-getter"

    injected = Inject(DependencyContainer())(WithProperty)

    assert injected().computed == "from-getter"


def test_an_annotated_attribute_is_still_resolved_from_the_container() -> None:
    """注入本身不能被削弱：可解析的类型仍从容器取。

    注解写成真实类型对象而不是字符串：`inject_property` 的容器解析判据是
    `isinstance(injecting_type, type)`，而 `from __future__ import annotations`
    会把注解变成字符串，那时这条判据永远不成立。产品代码里没有任何
    `@Inject` 类使用 future annotations，因此这里按产品的写法构造。
    """

    Consumer = type("Consumer", (), {"__annotations__": {"service": _Service}})

    injected = Inject(_container())(Consumer)

    assert isinstance(injected().service, _Service)
    assert injected().service.tag == "resolved"


def test_a_setter_still_wins_over_container_resolution() -> None:
    """显式赋值必须盖过容器解析。

    适配器实例化之后由加载器写入真实依赖；若 getter 永远返回容器里的那一个，
    那次赋值等于无效，而调用方看不出任何迹象。
    """

    Consumer = type("Consumer", (), {"__annotations__": {"service": _Service}})

    injected = Inject(_container())(Consumer)
    instance = injected()
    explicit = _Service(tag="explicit")
    instance.service = explicit

    assert instance.service is explicit


def test_a_class_attribute_holding_a_lock_survives_decoration() -> None:
    """`asyncio.Lock` 这类对象既非 property 也非假值——现场就是这一类。

    MCP 面板「连接失败 / 已连接 0」与保存供应商报错是同一个异常，
    触发点正是这种「带注解的非 property 真值属性」。
    """

    lock = asyncio.Lock()
    namespace = {"__annotations__": {"guard": asyncio.Lock}, "guard": lock}
    cls = type("WithLock", (), namespace)

    injected = Inject(_container())(cls)

    assert injected().guard is lock


def test_inheritance_does_not_reintroduce_the_failure() -> None:
    """父类的真值属性同样会被 `get_all_attributes` 收进来。"""

    class Base:
        enabled: bool = True

    class Child(Base):
        name: str = "child"

    injected = Inject(_container())(Child)

    instance = injected()
    assert instance.enabled is True
    assert instance.name == "child"


def test_deleting_an_injected_attribute_falls_back_to_the_default() -> None:
    """删除实例值之后读回默认值，而不是抛异常。"""

    namespace = {"__annotations__": {"field": str}, "field": "default"}
    cls = type("Deletable", (), namespace)
    injected = Inject(_container())(cls)

    instance = injected()
    instance.field = "changed"
    assert instance.field == "changed"
    del instance.field
    assert instance.field == "default"


def test_decorating_the_same_class_twice_does_not_lock_the_first_container() -> None:
    """同一个类被装饰两次时，第二个容器必须拿到自己注册的实例。

    这是修「真值属性」时**引入又当场修掉**的一个回归，值得单独钉住：
    第二次装饰时 ``getattr(cls, name)`` 拿到的是第一次装饰产出的 property，
    而不是作者写的那个。把它当成「作者的自定义 getter」会让第一个容器的解析结果
    永久锁在类上——后续所有容器都拿不到自己注册的实例，且**没有任何报错**，
    调用方看到的是一个类型正确、来源错误的对象。

    现场形态：插件热重载、以及同一进程内多个容器（测试套件正是这样）。
    单独跑一个用例永远发现不了它，因为它要求同一个类被装饰两次。
    """

    Consumer = type("Consumer", (), {"__annotations__": {"service": _Service}})

    first_container = DependencyContainer()
    first_service = _Service(tag="first")
    first_container.register(_Service, first_service)
    Inject(first_container)(Consumer)

    second_container = DependencyContainer()
    second_service = _Service(tag="second")
    second_container.register(_Service, second_service)
    Inject(second_container)(Consumer)

    assert Consumer().service is second_service
    assert Consumer().service.tag == "second"


def test_a_default_survives_repeated_decoration() -> None:
    """重复装饰不得把类上的默认值吃掉。

    剥离上一次产物时若还原成 ``None`` 而不是原始值，默认值会在第二次装饰后消失——
    而第一次装饰后它还在，因此这个缺陷只在装饰两次时显形。
    """

    namespace = {"__annotations__": {"flag": bool}, "flag": True}
    cls = type("Repeated", (), namespace)

    Inject(_container())(cls)
    Inject(_container())(cls)

    assert cls().flag is True


def test_a_custom_getter_survives_repeated_decoration() -> None:
    """作者写的 getter 在多次装饰后仍然被调用。"""

    class WithProperty:
        computed: str

        @property
        def computed(self) -> str:  # type: ignore[no-redef]
            return "from-getter"

    Inject(DependencyContainer())(WithProperty)
    Inject(DependencyContainer())(WithProperty)

    assert WithProperty().computed == "from-getter"


def test_an_explicitly_assigned_value_survives_repeated_decoration() -> None:
    """显式赋值仍然优先于容器解析——装饰两次之后也一样。

    加载器实例化适配器后写入真实依赖，那次赋值必须生效；否则 getter 永远返回
    容器里的那一个，而调用方看不出任何迹象表明赋值被忽略了。
    """

    Consumer = type("Consumer", (), {"__annotations__": {"service": _Service}})
    Inject(_container())(Consumer)
    Inject(_container())(Consumer)

    instance = Consumer()
    explicit = _Service(tag="explicit")
    instance.service = explicit

    assert instance.service is explicit
