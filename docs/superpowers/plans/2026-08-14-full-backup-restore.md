# 完整备份与恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增加可验证、可回滚的完整数据备份和恢复 API。

**Architecture:** 新建独立的 `BackupService`，只接收数据根目录并负责归档、校验、暂存和组件级回滚。系统 API 路由只处理认证和 HTTP 文件传输，不直接操作文件；现有配置、工作流和规则模块保持不变。

**Tech Stack:** Python 3.11、Quart、Pydantic、ruamel.yaml、标准库 `zipfile`、`hashlib`、`tempfile`、pytest。

## Global Constraints

- 只处理 `config.yaml`、`dispatch_rules`、`workflows`、`memory`、`media`、`db`、`plugins`、`fonts`、`web`、`auto_detect_state.json`。
- 绝不将 `venv`、日志、缓存、`.pyc`、`__pycache__`、Git 数据或已有 `backups` 重新打包或导入。
- 导入包可以包含敏感配置；任何日志和 API 响应均不得输出其内容。
- 每次实际导入都必须先创建回滚包；失败必须恢复磁盘上的原组件。
- 成功导入必须返回 `restart_required: true`，不在运行中热加载。

---

### Task 1: 归档服务和格式验证

**Files:**
- Create: `kirara_ai/backup/__init__.py`
- Create: `kirara_ai/backup/service.py`
- Create: `tests/backup/test_service.py`

**Interfaces:**
- Produces: `BackupService(data_path: Path)`。
- Produces: `create_backup() -> Path`、`inspect_backup(archive_path: Path) -> BackupManifest`、`restore_backup(archive_path: Path) -> RestoreResult`。
- Produces: `BackupValidationError`，用于安全校验和格式错误。

- [x] **Step 1: 写入导出清单测试**

```python
def test_create_backup_contains_allowed_data_and_manifest(tmp_path):
    service = BackupService(tmp_path / "data")
    archive_path = service.create_backup()
    manifest = service.inspect_backup(archive_path)
    assert "workflows" in manifest.components
    assert "venv" not in manifest.components
```

- [x] **Step 2: 运行单测确认当前失败**

Run: `python -m pytest tests/backup/test_service.py::test_create_backup_contains_allowed_data_and_manifest -v`

Expected: FAIL because `kirara_ai.backup` does not exist.

- [x] **Step 3: 实现最小归档与清单**

```python
service = BackupService(data_path)
archive_path = service.create_backup()
manifest = service.inspect_backup(archive_path)
```

归档仅写入允许组件，清单写入 SHA-256 和格式版本；读取端验证成员名、成员数量、文件大小、压缩比和校验值。

- [x] **Step 4: 运行导出和恶意 ZIP 测试**

Run: `python -m pytest tests/backup/test_service.py -v`

Expected: PASS; 包含路径穿越、未知路径和损坏 SHA-256 的拒绝用例。

### Task 2: 原子恢复与自动回滚

**Files:**
- Modify: `kirara_ai/backup/service.py`
- Modify: `tests/backup/test_service.py`

**Interfaces:**
- Consumes: `BackupService.inspect_backup(archive_path)`。
- Produces: `RestoreResult(rollback_path: Path, restored_components: list[str])`。

- [x] **Step 1: 写入恢复往返与失败恢复测试**

```python
def test_restore_replaces_components_and_creates_rollback(tmp_path):
    result = service.restore_backup(archive_path)
    assert result.rollback_path.exists()
    assert (data_path / "workflows" / "chat" / "normal.yaml").exists()
```

使用模拟替换错误验证失败后原始文件内容仍存在。

- [x] **Step 2: 运行测试确认当前失败**

Run: `python -m pytest tests/backup/test_service.py::test_restore_replaces_components_and_creates_rollback -v`

Expected: FAIL because `restore_backup` does not exist.

- [x] **Step 3: 实现组件级事务恢复**

先创建 `data/backups/` 回滚包，在数据目录同一父目录暂存已验证内容；替换每个清单声明的顶层组件。出现异常时撤销所有已替换组件，成功后清理事务目录。

- [x] **Step 4: 运行恢复测试**

Run: `python -m pytest tests/backup/test_service.py -v`

Expected: PASS; 所有失败路径保持原数据。

### Task 3: 认证 API 和 HTTP 文件传输

**Files:**
- Modify: `kirara_ai/web/api/system/routes.py`
- Create: `tests/web/api/system/test_backups.py`

**Interfaces:**
- Consumes: `BackupService` 和现有 `require_auth`。
- Produces: `GET /api/system/backups/export`、`POST /api/system/backups/inspect`、`POST /api/system/backups/import`、`GET /api/system/backups/rollbacks`、`GET /api/system/backups/rollbacks/<backup_name>`。

- [x] **Step 1: 写入 API 认证与导出测试**

```python
async def test_export_requires_auth(client):
    response = await client.get("/api/system/backups/export")
    assert response.status_code in {401, 403}
```

补充已认证导出、导入校验失败、成功导入返回 `restart_required` 和回滚列表测试。

- [x] **Step 2: 运行测试确认当前失败**

Run: `python -m pytest tests/web/api/system/test_backups.py -v`

Expected: FAIL because the routes do not exist.

- [x] **Step 3: 实现受保护路由**

使用 Quart `request.files` 接收唯一的 `backup` 文件字段，落盘至临时文件后交由 `BackupService`。下载响应只暴露安全生成的文件名，不回显清单中的任何配置内容。

- [x] **Step 4: 运行 API 测试**

Run: `python -m pytest tests/web/api/system/test_backups.py -v`

Expected: PASS。

### Task 4: 文档与回归验证

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-14-full-backup-restore-design.md`

**Interfaces:**
- Documents: 完整备份包含敏感值、恢复必须重启、前端需要独立 WebUI 仓库接入 API。

- [x] **Step 1: 记录 API 与安全限制**

在 README 添加备份用途、归档范围、导入重启要求和“不得提交归档到 GitHub/Docker Hub”的说明。

- [x] **Step 2: 运行针对性与全量测试**

Run: `python -m pytest tests/backup tests/web/api/system/test_backups.py -v && python -m pytest -q`

Expected: 新增测试与现有测试通过；若环境缺少测试依赖，记录精确失败原因而不修改无关代码。

- [x] **Step 3: 检查变更质量**

Run: `git diff --check && git status --short`

Expected: 无空白错误，变更仅限备份功能、测试和文档。
