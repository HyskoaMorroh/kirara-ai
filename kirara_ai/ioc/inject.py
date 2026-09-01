from functools import wraps
from inspect import signature
from typing import Any, Callable, Optional, Type

from kirara_ai.ioc.container import DependencyContainer


class _Missing:
    """Sentinel for "this attribute has an annotation but no value".

    不能用 ``None`` 当哨兵：``value: Optional[str] = None`` 是一个作者明确写下的
    默认值，与「只有注解」是两件事。用 ``None`` 表示两者会让前者的默认值在
    读取时被当成「未设置」，而那个区别正是 getter 回落顺序的依据。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试可读性
        return "<missing>"


_MISSING = _Missing()

class _InjectedProperty(property):
    """A property produced by :class:`Inject`, carrying its own provenance.

    必须是子类而不是给内建 ``property`` 挂属性：后者有 ``__slots__``，
    ``setattr`` 直接抛 ``AttributeError``。

    为什么需要标记：**同一个类会被装饰多次**（不同容器、插件热重载、测试各来一遍）。
    第二次装饰时 ``getattr(cls, name)`` 拿到的是上一次的产物，不是作者写的那个。
    把它误当成「作者的自定义 getter」会把第一个容器的解析结果永久锁在类上——
    后续容器拿不到自己注册的实例，而调用方看到的是一个类型正确、来源错误的对象，
    没有任何报错。这类缺陷只在「同一个类被装饰两次」时出现，因此单元测试里
    很容易漏掉，而插件热重载与多容器测试恰好每次都命中。
    """

    # 刻意**不**声明 `__slots__`：`property` 把 `__doc__` 实现为只读描述符，
    # 而带 slots 的子类在 `super().__init__` 里写 `__doc__` 时会抛
    # "attribute '__doc__' is read-only"。多一个 `__dict__` 的代价是每个被注入的
    # 属性多几十字节，而它换来的是 `functools.wraps` 与 Sphinx 都能正常工作。

    def __init__(self, fget=None, fset=None, fdel=None, *, original=None):
        super().__init__(fget, fset, fdel)
        self.original_attribute = original


def get_all_attributes(cls):
    if not hasattr(cls, "__annotations__"):
        return {}
    attributes = dict(cls.__annotations__.items())
    # 获取父类的属性和方法
    for base in cls.__bases__:
        attributes.update(get_all_attributes(base))

    return attributes


class Inject:
    def __init__(self, container: Optional[DependencyContainer] = None):
        self.container = container

    def create(self, target: type):
        # 注入类
        injected_class = self.__call__(target)
        # 注入构造函数
        return self.inject_function(injected_class)

    def __call__(self, target: Any):
        # 如果修饰的是一个类
        if isinstance(target, type):
            return self.inject_class(target)
        # 如果修饰的是一个函数
        elif callable(target):
            return self.inject_function(target)
        else:
            raise TypeError(
                "Inject can only be used on classes, functions."
            )

    def inject_class(self, cls: Type):
        # 遍历类的属性，尝试注入依赖。
        #
        # 类属性有三种形态，必须分开处理：
        #
        # 1. ``property`` —— 用它自己的 fget/fset/fdel；
        # 2. **带默认值的普通属性**（``flag: bool = True``、``settings: dict = {}``）
        #    —— 默认值要留住，读不到实例值时回落到它；
        # 3. 只有注解、没有值（``service: LLMManager``）—— 由容器解析。
        #
        # 此前第 2 种被当成第 1 种：`if prop:` 判的是「真值」而不是「是不是
        # property」，于是 `True.fget` 直接 AttributeError，整个类无法被装饰。
        # 现场表现是保存供应商配置报 `'bool' object has no attribute 'fget'`、
        # MCP 面板「连接失败 / 已连接 0」。带假值的属性恰好走进 else 分支，
        # 所以这个缺陷只在一部分类上出现——这正是它长期没被发现的原因。
        for name, injecting_type in get_all_attributes(cls).items():
            attr = getattr(cls, name, _MISSING)
            setattr(
                cls, name, self.inject_property(name, cls, injecting_type, attr)
            )
        return cls

    def inject_function(self, func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 获取函数的参数签名
            sig = signature(func)
            # 检查是否有 DependencyContainer 对象作为参数传递进来
            container_param = self.find_container(args, kwargs)
            # 如果有 DependencyContainer 对象，则将其作为 self.container
            if container_param:
                self.container = container_param

            # 遍历参数，注入依赖
            bound_args = sig.bind_partial(*args, **kwargs)
            bound_args.apply_defaults()
            for name, param in sig.parameters.items():
                if (
                    param.annotation != param.empty
                    and name not in kwargs
                    and self.container
                ):
                    bound_args.arguments[name] = self.container.resolve(
                        param.annotation
                    )

            # 调用实际的函数
            return func(*bound_args.args, **bound_args.kwargs)

        return wrapper

    def inject_property(self, name, cls, injecting_type, prop: Any = None):
        # 获取 property 的 fget, fset, fdel
        backing_name = f"_{name}_value"

        # **同一个类可以被装饰多次**（不同容器、不同测试、插件热重载各来一遍）。
        # 第二次装饰时 `getattr(cls, name)` 拿到的是**上一次装饰产出的 property**，
        # 不是作者写的那个。把它当成「作者的自定义 getter」会把第一个容器的解析
        # 结果永久锁在类上：随后所有容器都拿不到自己注册的实例，而调用方看到的是
        # 一个类型正确、来源错误的对象——没有任何报错。
        #
        # 因此这里剥掉自己的产物，只认真正由作者写下的 property。
        if isinstance(prop, _InjectedProperty):
            prop = prop.original_attribute

        # 类上原本写的默认值。`_MISSING` 表示「只有注解、没有值」——
        # 它与「值就是 None」必须分开：后者是作者明确写下的默认值。
        class_default = (
            _MISSING if isinstance(prop, property) or prop is _MISSING else prop
        )

        # 定义默认的 getter 方法 (使用实例属性存储值)
        #
        # 回落顺序：实例值 → 类上的默认值 → None。中间那一层此前不存在，
        # 于是 `flag: bool = False` 这类属性被读成 None——默认值被静默丢掉，
        # 而调用方看到的是一个「没有设置过」的假象。
        def default_fget(_self):
            if hasattr(_self, backing_name):
                return getattr(_self, backing_name)
            if class_default is not _MISSING:
                return class_default
            return None

        # 定义默认的 setter 方法 (使用实例属性存储值)
        def default_fset(_self, value):
            setattr(_self, backing_name, value)

        # 定义默认的 deleter 方法 (使用实例属性存储值)
        def default_fdel(_self):
            if hasattr(_self, backing_name):
                delattr(_self, backing_name)

        # 只有**真的是 property** 才用它自己的三件套。
        #
        # 判据必须是 `isinstance`，不能是 `if prop:`：后者把「值恰好为假」与
        # 「类型不是 property」合并成同一个分支，而这两种情况都该走默认路径。
        # 用真值判断的直接后果是 `True.fget` / `{}.fget` 抛 AttributeError，
        # 让整个类无法被 `@Inject` 装饰。
        if isinstance(prop, property):
            fget = prop.fget or default_fget
            fset = prop.fset or default_fset
            fdel = prop.fdel or default_fdel
        else:
            fget = default_fget
            fset = default_fset
            fdel = default_fdel

        # 为 property 的 fget 注入依赖
        @wraps(fget)
        def new_fget(_self):
            # 实例上已经有显式赋过的值时，它优先于容器解析。
            #
            # 顺序反过来会让「加载器实例化适配器后写入真实依赖」这个动作无效：
            # getter 永远返回容器里的那一个，而那次赋值没有任何迹象表明被忽略了。
            if hasattr(_self, backing_name):
                return getattr(_self, backing_name)
            # 自定义 getter 的行为不能被容器解析盖掉：作者写了 getter 就是要它算。
            if isinstance(prop, property) and prop.fget is not None:
                return prop.fget(_self)
            if self.container and isinstance(injecting_type, type) and self.container.has(injecting_type):
                # 如果返回值是一个类型，尝试从 container 中解析
                return self.container.resolve(injecting_type)
            else:
                return default_fget(_self)

        # 返回新的 property。
        #
        # 带上标记与原始值：类被再次装饰时据此还原，避免把自己的产物当成作者的
        # 自定义 getter（那会锁死第一个容器的解析结果）。
        return _InjectedProperty(new_fget, fset, fdel, original=prop)

    def find_container(self, args, kwargs):
        # 检查是否有 DependencyContainer 对象作为参数传递进来
        for arg in args:
            if isinstance(arg, DependencyContainer):
                return arg
        for key, value in kwargs.items():
            if isinstance(value, DependencyContainer):
                return value
        return None
