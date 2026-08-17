import asyncio
import os
import re
import shutil
import json
import threading
from pathlib import Path
from typing import Dict, Optional

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.workflow.base import Workflow
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.persistence import (
    FileMutation,
    FileTransaction,
    atomic_write_text,
)


class WorkflowRegistry:
    """工作流注册表，管理工作流的注册和获取"""

    WORKFLOWS_DIR = os.path.realpath("data/workflows")

    def __init__(self, container: DependencyContainer):
        self._workflows: Dict[str, WorkflowBuilder] = {}
        self.preset_workflow_ids: set[str] = set()
        self.deleted_preset_workflow_ids: set[str] = set()
        self.logger = get_logger("WorkflowRegistry")
        self.container = container
        self.workflows_dir = self.WORKFLOWS_DIR
        # 工作流表同时被 Web 请求处理器与消息调度访问。可重入锁保护字典本身，
        # 避免请求线程改表时调度侧读到不完整的状态。
        self._lock = threading.RLock()

    @classmethod
    def get_workflow_path(cls, group_id: str, workflow_id: str) -> str:
        """获取工作流文件路径"""
        group_dir = os.path.join(cls.WORKFLOWS_DIR, group_id)
        final_path = os.path.join(group_dir, f"{workflow_id}.yaml")
        if (
            os.path.commonprefix((os.path.realpath(final_path), cls.WORKFLOWS_DIR))
            != cls.WORKFLOWS_DIR
        ):
            raise ValueError("Invalid workflow path")

        # check is valid path symbols
        if not re.match(r"^[a-zA-Z0-9_-]+$", workflow_id):
            invalid_chars = re.findall(r"[^a-zA-Z0-9_-]", workflow_id)
            raise ValueError(
                f"Invalid symbols in workflow path: {''.join(invalid_chars)}"
            )
        if not re.match(r"^[a-zA-Z0-9_-]+$", group_id):
            invalid_chars = re.findall(r"[^a-zA-Z0-9_-]", group_id)
            raise ValueError(
                f"Invalid symbols in workflow path: {''.join(invalid_chars)}"
            )
        if not os.path.exists(group_dir):
            os.makedirs(group_dir)
        return final_path

    @staticmethod
    def _resolve_workflow_path(
        workflows_dir: str, group_id: str, workflow_id: str
    ) -> str:
        """Resolve a workflow path below an explicit registry data directory."""
        for label, value in (("workflow", workflow_id), ("group", group_id)):
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
                invalid_chars = re.findall(r"[^a-zA-Z0-9_-]", value)
                raise ValueError(
                    f"Invalid symbols in {label} path: {''.join(invalid_chars)}"
                )

        root = Path(workflows_dir).resolve()
        group_dir = root / group_id
        final_path = (group_dir / f"{workflow_id}.yaml").resolve()
        try:
            final_path.relative_to(root)
        except ValueError as error:
            raise ValueError("Invalid workflow path") from error
        group_dir.mkdir(parents=True, exist_ok=True)
        return str(final_path)

    def resolve_workflow_path(self, group_id: str, workflow_id: str) -> str:
        """获取当前注册表数据目录中的工作流文件路径。"""
        return self._resolve_workflow_path(
            self.workflows_dir, group_id, workflow_id
        )

    def snapshot_builders(self) -> tuple[tuple[str, WorkflowBuilder], ...]:
        """Return an immutable registry snapshot for lock-free consumers."""
        with self._lock:
            return tuple(self._workflows.items())

    @staticmethod
    def _workflow_name(group_id: str, workflow_id: str) -> str:
        return f"{group_id}:{workflow_id}"

    def _tombstone_mutation(
        self, workflow_ids: set[str]
    ) -> FileMutation:
        file_path = Path(self._preset_tombstones_path()).resolve()

        def write_tombstones(staged_path: Path) -> None:
            with staged_path.open("w", encoding="utf-8") as file:
                json.dump(sorted(workflow_ids), file, ensure_ascii=False, indent=2)

        return FileMutation.replace(file_path, write_tombstones)

    def persist_builder(
        self,
        group_id: str,
        workflow_id: str,
        workflow_builder: WorkflowBuilder,
        *,
        previous_group_id: Optional[str] = None,
        previous_workflow_id: Optional[str] = None,
        create_only: bool = False,
    ) -> None:
        """Persist and register one builder as a recoverable logical change."""
        full_name = self._workflow_name(group_id, workflow_id)
        has_previous_identity = (
            previous_group_id is not None and previous_workflow_id is not None
        )
        previous_full_name = (
            self._workflow_name(previous_group_id, previous_workflow_id)
            if has_previous_identity
            else full_name
        )

        with self._lock:
            new_path = Path(
                self.resolve_workflow_path(group_id, workflow_id)
            ).resolve()
            previous_path = Path(
                self.resolve_workflow_path(
                    previous_group_id or group_id,
                    previous_workflow_id or workflow_id,
                )
            ).resolve()

            if create_only and (
                full_name in self._workflows or new_path.exists()
            ):
                raise FileExistsError(f"Workflow {full_name} already exists")
            if previous_full_name != full_name and (
                full_name in self._workflows or new_path.exists()
            ):
                raise FileExistsError(f"Workflow {full_name} already exists")
            if has_previous_identity and previous_full_name not in self._workflows:
                raise ValueError(f"Workflow {previous_full_name} not found")

            next_tombstones = set(self.deleted_preset_workflow_ids)
            next_tombstones.discard(full_name)
            if (
                previous_full_name != full_name
                and previous_full_name in self.preset_workflow_ids
            ):
                next_tombstones.add(previous_full_name)

            def write_builder(staged_path: Path) -> None:
                workflow_builder.save_to_yaml(str(staged_path), self.container)

            mutations = [
                FileMutation.replace(new_path, write_builder),
                self._tombstone_mutation(next_tombstones),
            ]
            if previous_path != new_path:
                mutations.append(FileMutation.remove(previous_path))

            def publish_registry() -> None:
                if previous_full_name != full_name:
                    self._workflows.pop(previous_full_name, None)
                workflow_builder.id = full_name
                self._workflows[full_name] = workflow_builder
                self.deleted_preset_workflow_ids = next_tombstones

            FileTransaction(self.workflows_dir, mutations).commit(
                after_publish=publish_registry
            )
        self.logger.info(f"Registered workflow: {full_name}")

    def delete_persisted(self, group_id: str, workflow_id: str) -> None:
        """Delete YAML, preset tombstone, and registry entry together."""
        full_name = self._workflow_name(group_id, workflow_id)
        with self._lock:
            if full_name not in self._workflows:
                raise ValueError(f"Workflow {full_name} not found")
            file_path = Path(
                self.resolve_workflow_path(group_id, workflow_id)
            ).resolve()
            next_tombstones = set(self.deleted_preset_workflow_ids)
            if full_name in self.preset_workflow_ids:
                next_tombstones.add(full_name)

            def publish_registry() -> None:
                self._workflows.pop(full_name, None)
                self.deleted_preset_workflow_ids = next_tombstones

            FileTransaction(
                self.workflows_dir,
                [
                    FileMutation.remove(file_path),
                    self._tombstone_mutation(next_tombstones),
                ],
            ).commit(after_publish=publish_registry)
        self.logger.info(f"Unregistered workflow: {full_name}")

    def unregister(self, group_id: str, workflow_id: str):
        """注销一个工作流"""
        full_name = f"{group_id}:{workflow_id}"
        with self._lock:
            if full_name in self._workflows:
                del self._workflows[full_name]
                self.logger.info(f"Unregistered workflow: {full_name}")

    def delete(self, group_id: str, workflow_id: str):
        """Unregister a workflow and remember explicit preset deletions."""
        full_name = f"{group_id}:{workflow_id}"
        with self._lock:
            if full_name not in self._workflows:
                raise ValueError(f"Workflow {full_name} not found")
            self.mark_preset_deleted(full_name)
            self.unregister(group_id, workflow_id)

    def mark_preset_deleted(self, full_name: str):
        """Persist a preset deletion before removing its backing workflow file."""
        with self._lock:
            if full_name not in self.preset_workflow_ids:
                return
            self.deleted_preset_workflow_ids.add(full_name)
            self._save_preset_tombstones()

    def restore_preset(self, full_name: str):
        """Rollback a pending preset deletion when filesystem removal fails."""
        with self._lock:
            if full_name not in self.deleted_preset_workflow_ids:
                return
            self.deleted_preset_workflow_ids.discard(full_name)
            self._save_preset_tombstones()

    def register(
        self, group_id: str, workflow_id: str, workflow_builder: WorkflowBuilder
    ):
        """注册一个工作流"""
        full_name = f"{group_id}:{workflow_id}"
        with self._lock:
            if full_name in self.deleted_preset_workflow_ids:
                next_tombstones = set(self.deleted_preset_workflow_ids)
                next_tombstones.discard(full_name)
                self._write_preset_tombstones(next_tombstones)
                self.deleted_preset_workflow_ids = next_tombstones
            if full_name in self._workflows:
                self.logger.warning(f"Workflow {full_name} already registered, overwriting")
            workflow_builder.id = full_name
            self._workflows[full_name] = workflow_builder
        self.logger.info(f"Registered workflow: {full_name}")

    def register_preset_workflow(
        self, group_id: str, workflow_id: str, workflow_builder: WorkflowBuilder
    ):
        """预设工作流注册，当用户保存了同 id 的工作流时，则会不注册"""
        full_name = f"{group_id}:{workflow_id}"
        with self._lock:
            self.preset_workflow_ids.add(full_name)
            if full_name in self.deleted_preset_workflow_ids:
                self.logger.debug(f"Preset workflow {full_name} was deleted by the user, skipping")
                return
            if full_name in self._workflows:
                self.logger.debug(
                    f"Preset workflow {full_name} already registered, skipping"
                )
                return
            self._workflows[full_name] = workflow_builder
        self.logger.info(f"Registered preset workflow: {full_name}")

    def get_workflow(self, name: str, container: DependencyContainer) -> Optional[Workflow]:
        with self._lock:
            builder = self._workflows.get(name)
        if builder:
            return builder.build(container)
        return None

    def get(
        self, name: str, container: Optional[DependencyContainer] = None
    ) -> Optional[WorkflowBuilder | Workflow]:
        """获取工作流构建器或实例"""
        with self._lock:
            builder = self._workflows.get(name)
        if builder and container:
            return builder.build(container)
        return builder

    def load_workflows(self, workflows_dir: Optional[str] = None):
        """从指定目录加载所有工作流定义"""
        workflows_dir = workflows_dir or self.workflows_dir
        self.workflows_dir = workflows_dir
        if not os.path.exists(workflows_dir):
            os.makedirs(workflows_dir)

        FileTransaction.recover_directory(workflows_dir)

        self._load_preset_tombstones()

        # 首次启动时把随包分发的预设工作流释放到数据目录，
        # 让 pip 安装的用户也能拿到多模态、分段回复等进阶模板
        self._extract_bundled_presets(workflows_dir)

        # 遍历所有组目录
        for group_id in os.listdir(workflows_dir):
            group_dir = os.path.join(workflows_dir, group_id)
            if not os.path.isdir(group_dir):
                continue

            # 遍历组内的工作流文件
            for file_name in os.listdir(group_dir):
                if not file_name.endswith(".yaml"):
                    continue

                workflow_id = os.path.splitext(file_name)[0]
                file_path = os.path.join(group_dir, file_name)

                try:
                    workflow = WorkflowBuilder.load_from_yaml(file_path, self.container)
                    self.register(group_id, workflow_id, workflow)
                except Exception as e:
                    self.logger.error(
                        f"Failed to load workflow from {file_path}: {str(e)}"
                    )

    async def load_workflows_async(self, workflows_dir: Optional[str] = None):
        """在线程池里加载工作流

        `load_workflows` 会做目录遍历、预设复制（shutil.copyfile）与 tombstone
        的 fsync 落盘，全部是同步磁盘 I/O。启动阶段事件循环还没跑起来，直接
        调用同步版本没有问题；但若在运行期（如重载配置、恢复备份）重新加载，
        就必须走本方法，否则会卡住所有 IM 适配器的消息收发。
        """
        await asyncio.to_thread(self.load_workflows, workflows_dir)

    def _extract_bundled_presets(self, workflows_dir: str):
        """把包内自带的预设工作流复制到数据目录

        只在目标文件不存在时复制，因此用户在 WebUI 中对预设的编辑不会被覆盖。
        用户明确删除过的预设由 tombstone 记录，即使目标 YAML 不存在也不会被
        再次释放；用同 ID 新建工作流则会清除该删除标记。
        """
        try:
            from kirara_ai.workflow.presets import PRESETS_DIR
        except ImportError:
            return

        if not os.path.isdir(PRESETS_DIR):
            return

        for group_id in os.listdir(PRESETS_DIR):
            src_group = os.path.join(PRESETS_DIR, group_id)
            if not os.path.isdir(src_group) or group_id == "__pycache__":
                continue

            dst_group = os.path.join(workflows_dir, group_id)
            os.makedirs(dst_group, exist_ok=True)

            for file_name in os.listdir(src_group):
                if not file_name.endswith(".yaml"):
                    continue
                workflow_id = os.path.splitext(file_name)[0]
                full_name = f"{group_id}:{workflow_id}"
                self.preset_workflow_ids.add(full_name)
                if full_name in self.deleted_preset_workflow_ids:
                    continue
                dst_path = os.path.join(dst_group, file_name)
                if os.path.exists(dst_path):
                    continue
                try:
                    shutil.copyfile(os.path.join(src_group, file_name), dst_path)
                    self.logger.info(
                        f"Extracted bundled workflow preset: {group_id}/{file_name}"
                    )
                except OSError as e:
                    self.logger.warning(
                        f"Failed to extract preset {group_id}/{file_name}: {e}"
                    )

    def _preset_tombstones_path(self) -> str:
        return os.path.join(self.workflows_dir, ".preset_tombstones.json")

    def _load_preset_tombstones(self):
        file_path = self._preset_tombstones_path()
        if not os.path.exists(file_path):
            self.deleted_preset_workflow_ids = set()
            return
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                workflow_ids = json.load(file)
            if isinstance(workflow_ids, list) and all(isinstance(workflow_id, str) for workflow_id in workflow_ids):
                self.deleted_preset_workflow_ids = set(workflow_ids)
            else:
                self.logger.warning("Invalid workflow preset tombstones file, expected a list of workflow IDs")
        except (OSError, json.JSONDecodeError) as error:
            self.logger.warning(f"Failed to load workflow preset tombstones: {error}")

    def _save_preset_tombstones(self):
        with self._lock:
            workflow_ids = set(self.deleted_preset_workflow_ids)
        self._write_preset_tombstones(workflow_ids)

    def _write_preset_tombstones(self, workflow_ids: set[str]):
        os.makedirs(self.workflows_dir, exist_ok=True)
        file_path = self._preset_tombstones_path()

        def write_tombstones(file):
            json.dump(sorted(workflow_ids), file, ensure_ascii=False, indent=2)

        atomic_write_text(file_path, write_tombstones)
