"""创建者专属路由：仅 scope 不够，必须校验身份。

需求 10 要求「只有该项目的创建者才能通过插件修改服务器内容或执行文件操作」。
仅用 scope 判定不足以实现它：`AuthService` 默认签发的 token 带 `["*"]`
（`services.py`），因此任何登录用户都能通过 `resources.manage` 检查。

Agent 执行链路上有 `principal_can_control_agent` 把住这一层，但 HTTP 侧的
依赖安装、任务重试、任务取消此前只有 scope——两条路径的权限边界不一致，
等于给了一条绕过创建者限制的入口：非创建者可以让服务器执行安装命令。

这些用例分别钉住：非创建者被拒、创建者放行、以及三种拒绝的**状态码语义**
（缺 token 401、scope 不足 403、身份不符 403）。
"""

from __future__ import annotations

import pytest
from quart import Quart, jsonify

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.web.auth.middleware import require_auth, require_creator
from kirara_ai.web.auth.services import AuthService, MockAuthService


def make_app(auth_service: AuthService) -> Quart:
    app = Quart(__name__)
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(AuthService, auth_service)

    @app.before_request
    async def _bind_container():
        from quart import g

        g.container = container

    @app.route("/creator-only", methods=["POST"])
    @require_creator("resources.manage")
    async def creator_only():
        return jsonify({"ok": True})

    @app.route("/scoped-only", methods=["POST"])
    @require_auth("resources.manage")
    async def scoped_only():
        return jsonify({"ok": True})

    return app


@pytest.mark.asyncio
async def test_the_creator_is_allowed_through():
    app = make_app(MockAuthService(creator=True))
    client = app.test_client()

    response = await client.post(
        "/creator-only", headers={"Authorization": "Bearer mock_token"}
    )

    assert response.status_code == 200
    assert (await response.get_json())["ok"] is True


@pytest.mark.asyncio
async def test_a_non_creator_with_full_scopes_is_refused():
    """这是这条需求的核心用例。

    默认 token 带 `["*"]`，scope 检查一定通过；如果身份检查缺失，
    非创建者就能让服务器执行安装命令。
    """
    app = make_app(MockAuthService(creator=False))
    client = app.test_client()

    response = await client.post(
        "/creator-only", headers={"Authorization": "Bearer mock_token"}
    )

    assert response.status_code == 403
    assert "creator" in (await response.get_json())["error"].lower()


@pytest.mark.asyncio
async def test_the_same_non_creator_still_passes_a_scope_only_route():
    """对照组：证明差异来自身份检查，而不是 token 本身失效。"""
    app = make_app(MockAuthService(creator=False))
    client = app.test_client()

    response = await client.post(
        "/scoped-only", headers={"Authorization": "Bearer mock_token"}
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_missing_token_is_still_401_not_403():
    """缺凭据与身份不符是两回事：前者 401，后者 403。混淆会误导排查方向。"""
    app = make_app(MockAuthService(creator=True))
    client = app.test_client()

    response = await client.post("/creator-only")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_an_invalid_token_is_401():
    app = make_app(MockAuthService(creator=True))
    client = app.test_client()

    response = await client.post(
        "/creator-only", headers={"Authorization": "Bearer wrong"}
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_insufficient_scope_is_403_even_for_the_creator():
    """创建者身份不能豁免 scope：两道检查各自独立。"""
    app = make_app(MockAuthService(creator=True, scopes=["resources.read"]))
    client = app.test_client()

    response = await client.post(
        "/creator-only", headers={"Authorization": "Bearer mock_token"}
    )

    assert response.status_code == 403


def test_every_vps_mutating_dependency_route_is_creator_guarded():
    """源码级断言：三条会在服务器上执行命令的路由必须用 `require_creator`。

    以源码为断言对象，是为了让「将来新增一条安装类路由却只加 scope」这件事
    立刻失败——那正是本次修复前的状态。
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "kirara_ai"
        / "web"
        / "api"
        / "resource"
        / "routes.py"
    ).read_text(encoding="utf-8")

    for route in (
        '"/dependencies/<dependency_id>/install"',
        '"/dependency-tasks/<task_id>/retry"',
        '"/dependency-tasks/<task_id>/cancel"',
    ):
        index = source.find(route)
        assert index != -1, f"路由 {route} 不存在，请同步更新这条契约"
        # 取该路由声明之后的一小段，检查紧随的装饰器。
        window = source[index : index + 400]
        assert "@require_creator(" in window, (
            f"{route} 仅有 scope 校验；它会在服务器上执行命令，必须限创建者"
        )


def test_every_resource_route_that_writes_to_disk_is_creator_guarded():
    """写入 VPS 文件系统的资源路由同样必须限创建者。

    此前只有依赖安装/重试/取消三条用了 `require_creator`，而安装 ZIP、
    导入、目录安装、远程安装、升级版本、启用、从备份恢复、删除备份
    这一整批**都会在服务器磁盘上落文件或删文件**，却只有 scope 检查。
    默认 token 带 `["*"]`，等于任何登录用户都能改 VPS 内容——
    与需求 10「只有创建者才能修改服务器内容或执行文件操作」直接冲突，
    也与依赖安装那三条自相矛盾。

    `enable` 之所以算写操作：启用 `mcp` 资源会触发
    `MCPServerManager.refresh_managed_servers()`，在服务器上真正拉起进程。
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "kirara_ai"
        / "web"
        / "api"
        / "resource"
        / "routes.py"
    ).read_text(encoding="utf-8")

    disk_mutating_routes = (
        ('""', '"POST"'),  # 上传安装
        ('"/imports"', '"POST"'),
        ('"/catalog/install"', '"POST"'),
        ('"/remote-install"', '"POST"'),
        ('"/<resource_id>/versions"', '"POST"'),
        # 从纯文本创建 / 改正文：与上传 ZIP 同一件事，只是打包在服务器侧做。
        ('"/documents"', '"POST"'),
        ('"/<resource_id>/documents"', '"PUT"'),
        ('"/<resource_id>/update"', '"POST"'),
        ('"/<resource_id>/enable"', '"POST"'),
        ('"/<resource_id>/restore"', '"POST"'),
        ('"/backups/<backup_id>/restore"', '"POST"'),
        ('"/backups/<backup_id>"', '"DELETE"'),
        # 摘掉仓库来源登记：写 `registry.json`，改变「哪些外部来源可被安装」。
        # 与启停同一边界，且因为不可逆还额外要求显式确认。
        ('"/repositories/<owner>/<name>/<branch>"', '"DELETE"'),
    )

    for route, method in disk_mutating_routes:
        declaration = f"@resource_bp.route({route}, methods=[{method}])"
        index = source.find(declaration)
        assert index != -1, f"路由声明 {declaration} 不存在，请同步更新这条契约"
        window = source[index : index + 300]
        assert "@require_creator(" in window, (
            f"{route} {method} 会写入或删除服务器磁盘内容，必须限创建者"
        )


def test_resource_read_and_disable_routes_stay_scope_only():
    """只读与「停用」不收紧：停用只是降级到不生效，不写入新内容。

    需求原文是「其他使用者收到涉及修改部署 VPS 的命令一律忽视，但仍会进行
    正常的 AI 回复」——查看资源、查看审计、以及在出问题时停掉一个扩展
    属于正常运维，不是往服务器里塞东西。
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "kirara_ai"
        / "web"
        / "api"
        / "resource"
        / "routes.py"
    ).read_text(encoding="utf-8")

    for declaration in (
        '@resource_bp.route("", methods=["GET"])',
        '@resource_bp.route("/audit", methods=["GET"])',
        '@resource_bp.route("/backups", methods=["GET"])',
        '@resource_bp.route("/updates", methods=["GET"])',
        '@resource_bp.route("/<resource_id>/disable", methods=["POST"])',
    ):
        index = source.find(declaration)
        assert index != -1, f"路由声明 {declaration} 不存在"
        window = source[index : index + 300]
        assert "@require_creator(" not in window, (
            f"{declaration} 不写入服务器内容，不应限创建者"
        )


def test_read_only_dependency_routes_stay_scope_only():
    """只读端点不该被收紧成创建者专属：那会让普通使用者看不到依赖状态。

    需求原文是「其他使用者收到涉及修改部署 VPS 的命令一律忽视，但仍会进行
    正常的 AI 回复」——读取状态属于正常使用，不是修改部署。
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "kirara_ai"
        / "web"
        / "api"
        / "resource"
        / "routes.py"
    ).read_text(encoding="utf-8")

    for route in ('"/dependencies"', '"/dependency-tasks"'):
        index = source.find(f"@resource_bp.route({route}")
        assert index != -1, f"路由 {route} 不存在"
        window = source[index : index + 300]
        assert "@require_creator(" not in window, f"{route} 是只读端点，不应限创建者"


def _route_window(module_parts: tuple[str, ...], declaration: str) -> str:
    from pathlib import Path

    path = Path(__file__).resolve().parents[3]
    for part in module_parts:
        path = path / part
    source = path.read_text(encoding="utf-8")
    index = source.find(declaration)
    assert index != -1, f"路由声明 {declaration} 不存在，请同步更新这条契约"
    return source[index : index + 400]


def test_every_command_executing_dependency_route_is_creator_guarded():
    """探测也在服务器上**执行**登记的 argv，与安装同一边界。

    `probe` 会跑 `agent-browser doctor` / `rtk --version` 这类命令
    （`system_dependencies.py` 的 `_run_command`）。只是「不安装」不等于
    「不执行」——需求 10 管的是「在 VPS 中执行文件操作等指令」，执行就算。
    """
    for declaration in (
        '@resource_bp.route("/dependencies/<dependency_id>/probe", methods=["POST"])',
        '@resource_bp.route("/dependencies/<dependency_id>/install", methods=["POST"])',
        '@resource_bp.route("/dependency-tasks/<task_id>/retry", methods=["POST"])',
        '@resource_bp.route("/dependency-tasks/<task_id>/cancel", methods=["POST"])',
    ):
        window = _route_window(("kirara_ai", "web", "api", "resource", "routes.py"), declaration)
        assert "@require_creator(" in window, f"{declaration} 会在服务器上执行命令，必须限创建者"


def test_repository_registry_writes_are_creator_guarded():
    """新增 / 启停外部仓库会写 registry.json 并改变「哪些来源可被安装」。"""
    for declaration in (
        '@resource_bp.route("/repositories", methods=["POST"])',
        '@resource_bp.route("/repositories/<owner>/<name>/<branch>/enabled", methods=["POST"])',
    ):
        window = _route_window(("kirara_ai", "web", "api", "resource", "routes.py"), declaration)
        assert "@require_creator(" in window, f"{declaration} 写入服务器注册表，必须限创建者"


def test_mcp_server_mutations_are_creator_guarded():
    """MCP 的增删改与启动必须与资源侧同一边界。

    这是之前最明显的自相矛盾：启用一个 **mcp 资源**要创建者身份，
    而直接 `POST /mcp/servers` + `start` 达到同样效果却只要 scope；
    `start` 更是真的在服务器上拉起一个 stdio 子进程。
    默认 token 带 `["*"]`，等于任何登录用户都能在 VPS 上起进程。
    """
    for declaration in (
        '@mcp_bp.route("/servers", methods=["POST"])',
        '@mcp_bp.route("/servers/<server_id>", methods=["PUT"])',
        '@mcp_bp.route("/servers/<server_id>", methods=["DELETE"])',
        '@mcp_bp.route("/servers/<server_id>/start", methods=["POST"])',
    ):
        window = _route_window(("kirara_ai", "web", "api", "mcp", "routes.py"), declaration)
        assert "@require_creator(" in window, f"{declaration} 改变服务器状态，必须限创建者"


def test_mcp_read_and_stop_routes_stay_scope_only():
    """只读与「停止」不收紧：停止只让扩展不再生效，不引入新的服务器副作用。

    与资源侧 `disable` 保持同一判断——出问题时任何运维都该能把它停掉。
    """
    for declaration in (
        '@mcp_bp.route("/servers", methods=["GET"])',
        '@mcp_bp.route("/servers/<server_id>/stop", methods=["POST"])',
        '@mcp_bp.route("/audit", methods=["GET"])',
    ):
        window = _route_window(("kirara_ai", "web", "api", "mcp", "routes.py"), declaration)
        assert "@require_creator(" not in window, f"{declaration} 不该限创建者"
