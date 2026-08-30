import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML


PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 示例 compose 里的两个 QQ 实例。1.txt 需求 7 给出的拓扑是三服务
#: （llonebot + llonebot2 + kirara-agent），且 `.env.example` 已经声明了
#: `LLONEBOT2_*`——只定义一个服务会让那两个变量指向一个不存在的服务，
#: 比不提供第二实例更糟：读起来像「配好了」，实际连不上。
QQ_SERVICES = ("llonebot", "llonebot2")


def _load(name: str) -> dict:
    return YAML(typ="safe").load((PROJECT_ROOT / name).read_text(encoding="utf-8"))


def _environment_text(service: dict) -> str:
    environment = service["environment"]
    return "\n".join(environment if isinstance(environment, list) else environment)


@pytest.mark.parametrize(
    "compose_name",
    ["docker-compose.yml", "docker-compose.yml.example"],
)
def test_compose_pins_data_path_to_the_persistent_volume(compose_name: str):
    """两份 compose 都必须把 DATA_PATH 钉在挂载卷上。

    只断言 ``docker-compose.yml`` 时，``.example`` 可以（并且确实曾经）漏掉
    ``DATA_PATH``：容器工作目录与挂载点不同时，数据会落在卷外，重建即丢失。
    示例文件是使用者实际复制的那一份，契约必须同样覆盖它。
    """
    service = _load(compose_name)["services"]["kirara-agent"]

    assert service["environment"]["DATA_PATH"] == "/app/data"
    assert "./data:/app/data" in service["volumes"]


def test_compose_example_documents_the_three_service_qq_topology():
    """示例 compose 必须包含两个 LLOneBot 服务与共享网络。

    QQ 接入至少需要两个容器：LLOneBot 跑 QQ 客户端并作为 OneBot 实现，Kirara 提供
    反向 WebSocket 端点。需求给出的拓扑是两个 QQ 实例并存，而 `.env.example` 也
    已声明 `LLONEBOT2_*`；示例里只给一个服务会让那组变量无处可用。
    """
    compose = _load("docker-compose.yml.example")
    services = compose["services"]

    for name in QQ_SERVICES:
        assert name in services, f"示例 compose 缺少 {name} 服务"

    kirara_networks = set(services["kirara-agent"]["networks"])
    for name in QQ_SERVICES:
        shared = set(services[name]["networks"]) & kirara_networks
        assert shared, f"{name} 与 kirara-agent 不在同一网络，容器名无法解析"
        assert shared.issubset(compose["networks"].keys())


def test_each_qq_instance_owns_its_own_login_state_volumes():
    """每个 QQ 实例必须有独立的数据卷。

    两个实例共用 `./QQ` 会互相覆盖登录态与设备标识，现象是「登录一个就把另一个
    挤下线」，而且排查时看起来像 QQ 侧的随机掉线。
    """
    services = _load("docker-compose.yml.example")["services"]

    mounts: dict[str, set[str]] = {}
    for name in QQ_SERVICES:
        volumes = services[name]["volumes"]
        # 每个实例都必须挂载这两个容器内路径。
        targets = {mapping.split(":", 1)[1] for mapping in volumes}
        assert "/root/.config/QQ" in targets, f"{name} 未挂载 QQ 登录态目录"
        assert "/root/llonebot" in targets, f"{name} 未挂载 OneBot 配置目录"
        mounts[name] = {mapping.split(":", 1)[0] for mapping in volumes}

    first, second = (mounts[name] for name in QQ_SERVICES)
    assert not (first & second), (
        f"两个 QQ 实例共用了宿主目录 {first & second}，登录态会互相覆盖"
    )


def test_each_qq_instance_uses_a_distinct_published_port():
    """两个实例的发布端口必须整组错开，否则第二个容器起不来。"""
    services = _load("docker-compose.yml.example")["services"]

    published: dict[str, set[str]] = {}
    for name in QQ_SERVICES:
        ports = set()
        for mapping in services[name]["ports"]:
            # 形如 "8888:3080" 或 "127.0.0.1:13000:13000"
            parts = str(mapping).split(":")
            ports.add(":".join(parts[:-1]))
        published[name] = ports

    first, second = (published[name] for name in QQ_SERVICES)
    assert not (first & second), f"两个 QQ 实例发布端口冲突：{first & second}"


@pytest.mark.parametrize("service_name", QQ_SERVICES)
def test_compose_example_keeps_credentials_in_env_and_pmhq_local(service_name: str):
    """凭据只能来自 .env，且可直接操作 QQ 客户端的端口只绑本机。"""
    services = _load("docker-compose.yml.example")["services"]
    joined = _environment_text(services[service_name])
    suffix = "2" if service_name.endswith("2") else "1"

    # `:?` 形式让缺失即失败：空 AUTH_TOKEN 等于开放无鉴权控制端口。
    assert f"${{LLONEBOT{suffix}_AUTH_TOKEN:?" in joined
    assert f"${{LLONEBOT{suffix}_QQ:?" in joined
    # 不得出现任何明文 Token：等号后面必须紧跟变量展开。
    for line in joined.splitlines():
        if "AUTH_TOKEN=" in line:
            assert "AUTH_TOKEN=${" in line, f"疑似明文 Token：{line}"

    for mapping in services[service_name]["ports"]:
        if str(mapping).endswith(":13000"):
            assert str(mapping).startswith("127.0.0.1:"), (
                "PMHQ 可直接操作 QQ 客户端，暴露到公网等于交出账号"
            )


def test_no_real_account_number_is_committed_in_the_example():
    """示例里不得出现形似真实 QQ 号的字面值。

    需求 18.2 禁止把账号写进源码与配置示例。`QUICK_LOGIN_QQ` 必须走变量展开。
    """
    text = (PROJECT_ROOT / "docker-compose.yml.example").read_text(encoding="utf-8")

    for line in text.splitlines():
        if "QUICK_LOGIN_QQ" in line:
            assert "${" in line, f"QUICK_LOGIN_QQ 应走 .env 变量：{line}"
    # 9-11 位连续数字在这份文件里没有任何正当用途（端口最多 5 位）。
    assert not re.search(r"\b\d{9,11}\b", text), "疑似真实 QQ 号被提交"


def test_env_example_declares_every_variable_the_compose_requires():
    """`.env.example` 必须覆盖示例 compose 强制要求的每个变量。

    这条契约防的是双向漂移：compose 新增 `:?` 变量而示例 env 没跟上（首次
    `up -d` 直接失败），或 env 声明了 compose 根本不用的变量（读起来像配好了，
    实际那个服务不存在）——后者正是 `LLONEBOT2_*` 曾经的状态。
    """
    compose_text = (PROJECT_ROOT / "docker-compose.yml.example").read_text(
        encoding="utf-8"
    )
    env_text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", compose_text))
    declared = {
        line.split("=", 1)[0].strip()
        for line in env_text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }

    missing = required - declared
    assert not missing, f".env.example 缺少 compose 强制要求的变量：{sorted(missing)}"

    # 反向：env 里声明的 LLONEBOT* 变量必须真的被 compose 使用。
    referenced = set(re.findall(r"\$\{([A-Z0-9_]+)", compose_text))
    dangling = {
        name for name in declared if name.startswith("LLONEBOT") and name not in referenced
    }
    assert not dangling, (
        f".env.example 声明了 compose 未使用的变量：{sorted(dangling)}；"
        "要么补上对应服务，要么删掉这些变量"
    )
