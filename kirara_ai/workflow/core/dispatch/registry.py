import asyncio
import json
import os
import tempfile
import threading
from typing import Any, Callable, Dict, List, Optional, TextIO

from ruamel.yaml import YAML

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry

from .models.dispatch_rules import CombinedDispatchRule, RuleGroup, SimpleDispatchRule
from .reachability import sort_rules_in_dispatch_order
from .rules.base import DispatchRule
from .rules.message_rules import BotMentionMatchRule, KeywordMatchRule, PrefixMatchRule, RegexMatchRule
from .rules.sender_rules import ChatSenderMatchRule, ChatSenderMismatchRule, ChatTypeMatchRule
from .rules.system_rules import FallbackMatchRule, IMInstanceMatchRule, RandomChanceMatchRule


class DispatchRuleRegistry:
    """调度规则注册表，管理调度规则的加载和注册"""

    def __init__(self, container: DependencyContainer):
        self.container = container
        self.workflow_registry = container.resolve(WorkflowRegistry)
        self.rules: Dict[str, CombinedDispatchRule] = {}
        self.preset_rule_ids: set[str] = set()
        self.deleted_preset_rule_ids: set[str] = set()
        # A valid persisted rule file is a user-owned complete configuration.
        # This also preserves deletions made by older versions without tombstones.
        self.has_persisted_rules = False
        self.logger = get_logger("DispatchRuleRegistry")
        self.rules_dir = "data/dispatch_rules"
        # 规则表会被 Web 请求处理器与消息调度并发读写。可重入锁保护字典本身
        # 的一致性，避免"读到一半的规则表"或迭代过程中被改动导致的异常。
        self._lock = threading.RLock()

    def register(self, rule: CombinedDispatchRule):
        """注册一个调度规则"""
        if not rule.rule_id:
            raise ValueError("Rule must have an ID")
        with self._lock:
            self.deleted_preset_rule_ids.discard(rule.rule_id)
            self.rules[rule.rule_id] = rule
        self.logger.info(f"Registered dispatch rule: {rule}")

    def register_preset_rule(self, rule: CombinedDispatchRule):
        """注册预设规则，当用户已有同 id 的规则时跳过

        语义与 WorkflowRegistry.register_preset_workflow 一致：用户在 WebUI 里
        改过或从 YAML 载入的规则优先，内置默认值只用于填补空缺。
        """
        if not rule.rule_id:
            raise ValueError("Rule must have an ID")
        with self._lock:
            self.preset_rule_ids.add(rule.rule_id)
            if rule.rule_id in self.deleted_preset_rule_ids:
                self.logger.debug(f"Preset dispatch rule {rule.rule_id} was deleted by the user, skipping")
                return
            if rule.rule_id in self.rules:
                self.logger.debug(f"Preset dispatch rule {rule.rule_id} already exists, skipping")
                return
            self.rules[rule.rule_id] = rule
        self.logger.info(f"Registered preset dispatch rule: {rule.rule_id} -> {rule.workflow_id}")

    def get_rule(self, rule_id: str) -> Optional[CombinedDispatchRule]:
        """获取指定ID的规则"""
        with self._lock:
            return self.rules.get(rule_id)

    def get_all_rules(self) -> List[CombinedDispatchRule]:
        """获取所有已注册的规则"""
        with self._lock:
            return list(self.rules.values())

    def get_active_rules(self) -> List[CombinedDispatchRule]:
        """获取所有已启用的规则，按优先级降序排序

        同优先级时按 rule_id 升序，保证在任意机器、任意规则文件读取顺序下
        匹配次序都一致（`os.listdir` 的顺序并不跨平台稳定）。排序规则本身由
        `reachability.sort_rules_in_dispatch_order` 唯一定义。
        """
        with self._lock:
            active_rules = [rule for rule in self.rules.values() if rule.enabled]
        return sort_rules_in_dispatch_order(active_rules)

    def create_rule(self, rule: CombinedDispatchRule) -> CombinedDispatchRule:
        """创建并注册一个新规则"""
        # 获取工作流构建器
        workflow_builder = self.workflow_registry.get(rule.workflow_id)
        if not workflow_builder:
            raise ValueError(f"Workflow {rule.workflow_id} not found")

        # 注册规则
        self.register(rule)
        return rule

    def update_rule(
        self, rule_id: str, rule: CombinedDispatchRule
    ) -> CombinedDispatchRule:
        """更新现有规则"""
        with self._lock:
            if rule_id not in self.rules:
                raise ValueError(f"Rule {rule_id} not found")

            # 更新规则
            self.register(rule)
        return rule

    def delete_rule(self, rule_id: str):
        """删除规则"""
        with self._lock:
            if rule_id not in self.rules:
                raise ValueError(f"Rule {rule_id} not found")
            if rule_id in self.preset_rule_ids:
                self.deleted_preset_rule_ids.add(rule_id)
            del self.rules[rule_id]

    def _preset_tombstones_path(self, rules_dir: str) -> str:
        """获取内置规则删除标记的持久化位置。"""
        return os.path.join(rules_dir, ".preset_tombstones.json")

    def _atomic_write(self, file_path: str, write_content: Callable[[TextIO], None]):
        """在同一目录中原子替换一个规则持久化文件。

        规则修改发生在消息处理的运行期。直接打开正式文件写入时，进程异常或
        磁盘写入失败会留下截断的 YAML/JSON，下一次启动就无法可靠恢复规则。
        临时文件和目标文件位于同一目录，`os.replace` 在常见文件系统上是原子
        操作；失败时只清理临时文件，保留上一份有效配置。
        """
        directory = os.path.dirname(file_path) or "."
        prefix = f".{os.path.basename(file_path)}."
        fd, temp_file_path = tempfile.mkstemp(
            dir=directory,
            prefix=prefix,
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                write_content(file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_file_path, file_path)
        except Exception:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            raise

    def _load_preset_tombstones(self, rules_dir: str):
        """加载用户删除的内置规则，避免启动时自动恢复。"""
        file_path = self._preset_tombstones_path(rules_dir)
        if not os.path.exists(file_path):
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                rule_ids = json.load(f)
            if isinstance(rule_ids, list) and all(isinstance(rule_id, str) for rule_id in rule_ids):
                self.deleted_preset_rule_ids = set(rule_ids)
            else:
                self.logger.warning(f"Invalid preset tombstones file {file_path}, expected a list of rule IDs")
        except Exception as e:
            self.logger.error(f"Failed to load preset tombstones from {file_path}: {str(e)}")

    def _save_preset_tombstones(self, rules_dir: str):
        """保存用户删除的内置规则。"""
        file_path = self._preset_tombstones_path(rules_dir)
        with self._lock:
            rule_ids = sorted(self.deleted_preset_rule_ids)

        def write_tombstones(file: TextIO):
            json.dump(rule_ids, file, ensure_ascii=False, indent=2)

        self._atomic_write(file_path, write_tombstones)

    def enable_rule(self, rule_id: str):
        """启用规则"""
        rule = self.get_rule(rule_id)
        if not rule:
            raise ValueError(f"Rule {rule_id} not found")
        rule.enabled = True

    def disable_rule(self, rule_id: str):
        """禁用规则"""
        rule = self.get_rule(rule_id)
        if not rule:
            raise ValueError(f"Rule {rule_id} not found")
        rule.enabled = False

    def _convert_old_rule(self, rule_data: Dict[str, Any]) -> CombinedDispatchRule:
        """将旧版本规则数据转换为新版本格式"""
        rule_type = rule_data["type"]
        rule_class = DispatchRule.get_rule_type(rule_type)

        # 提取规则配置
        config_fields = rule_class.config_class.model_fields.keys()
        rule_config = {k: rule_data[k] for k in config_fields if k in rule_data}

        # 创建简单规则
        simple_rule = SimpleDispatchRule(type=rule_type, config=rule_config)

        # 创建规则组
        rule_group = RuleGroup(operator="or", rules=[simple_rule])

        # 创建组合规则
        return CombinedDispatchRule(
            rule_id=rule_data["rule_id"],
            name=rule_data["name"],
            description=rule_data.get("description", ""),
            workflow_id=rule_data["workflow_id"],
            rule_groups=[rule_group],
            priority=rule_data.get("priority", 5),
            enabled=rule_data.get("enabled", True),
            metadata=rule_data.get("metadata", {}),
        )

    def load_rules(self, rules_dir: Optional[str] = None):
        """从指定目录加载所有调度规则"""
        rules_dir = rules_dir or self.rules_dir
        if not os.path.exists(rules_dir):
            os.makedirs(rules_dir)

        self.has_persisted_rules = False
        self._load_preset_tombstones(rules_dir)

        yaml = YAML(typ="safe")

        for file_name in os.listdir(rules_dir):
            if not file_name.endswith(".yaml"):
                continue

            file_path = os.path.join(rules_dir, file_name)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    rules_data = yaml.load(f)

                if not isinstance(rules_data, list):
                    self.logger.warning(
                        f"Invalid rules file {file_name}, expected list of rules"
                    )
                    continue

                # An empty list is also an explicit user configuration.
                self.has_persisted_rules = True

                for rule_data in rules_data:
                    try:
                        # 检查是否是新版本的组合规则
                        if "rule_groups" in rule_data:
                            rule = CombinedDispatchRule(**rule_data)
                        else:
                            # 旧版本规则，转换为新格式
                            rule = self._convert_old_rule(rule_data)

                        self.register(rule)
                        self.logger.debug(f"Loaded rule: {rule}")
                    except Exception as e:
                        self.logger.error(
                            f"Failed to load rule in file {file_path}: {str(e)}"
                        )

            except Exception as e:
                self.logger.error(f"Failed to load rules from {file_path}: {str(e)}")

    def save_rules(self, rules_dir: Optional[str] = None):
        """保存所有规则到文件"""
        rules_dir = rules_dir or self.rules_dir
        if not os.path.exists(rules_dir):
            os.makedirs(rules_dir)

        yaml = YAML()
        yaml.default_flow_style = False

        # 保存规则
        # 注意使用 model_dump 而非 pydantic v1 的 dict()，后者在本项目锁定的
        # pydantic ≥2 下已废弃并会输出 DeprecationWarning
        with self._lock:
            rules_data = [rule.model_dump() for rule in self.rules.values()]

        # 保存到文件
        file_path = os.path.join(rules_dir, "rules.yaml")

        def write_rules(file: TextIO):
            yaml.dump(rules_data, file)

        self._atomic_write(file_path, write_rules)

        self._save_preset_tombstones(rules_dir)

    async def save_rules_async(self, rules_dir: Optional[str] = None):
        """在线程池里保存规则，避免阻塞事件循环

        `save_rules` 里有 `os.fsync` 与原子替换，都是同步磁盘 I/O。在 Quart 的
        请求处理协程里直接调用会卡住整个事件循环——此刻所有 IM 适配器的消息
        收发也一起停顿。Web 层改调本方法即可保持行为不变而不阻塞。
        """
        await asyncio.to_thread(self.save_rules, rules_dir)


# 注册所有规则类型
DispatchRule.register_rule_type(RegexMatchRule)
DispatchRule.register_rule_type(PrefixMatchRule)
DispatchRule.register_rule_type(KeywordMatchRule)
DispatchRule.register_rule_type(BotMentionMatchRule)
DispatchRule.register_rule_type(RandomChanceMatchRule)
DispatchRule.register_rule_type(ChatSenderMatchRule)
DispatchRule.register_rule_type(ChatSenderMismatchRule)
DispatchRule.register_rule_type(ChatTypeMatchRule)
DispatchRule.register_rule_type(IMInstanceMatchRule)
DispatchRule.register_rule_type(FallbackMatchRule)
