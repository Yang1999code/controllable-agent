"""agent/skill.py — ISkill / ISkillConfig 实现。

技能注册、YAML 加载、关键词查找。提供手动注册技能的接口（静态技能），
Phase 3 由 IAutonomousMemory 补充自动结晶来源。

参考：GenericAgent SkillIndex / Hermes 技能管理平台 / Superpowers writing-skills
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# ── ABC ───────────────────────────────────────────────

class ISkill(ABC):
    """技能抽象接口。

    技能 = 可复用的任务模式（YAML 描述 + 工具调用序列）。
    来源：手动注册（YAML）+ 自动结晶（IAutonomousMemory.crystallize()）。
    """

    @abstractmethod
    def match(self, task_description: str) -> bool:
        """判断该技能是否匹配给定任务描述。"""
        ...

    @abstractmethod
    def get_steps(self) -> list[dict]:
        """获取技能执行步骤列表 [{tool, args, description}, ...]。"""
        ...


class ISkillConfig(ABC):
    """技能配置抽象接口。

    元数据层：分类、优先级、标签、自动加载策略。
    与 ISkill 分离——同一个技能可有不同配置。
    """

    @abstractmethod
    def to_dict(self) -> dict: ...

    @abstractmethod
    def to_yaml(self, path: str) -> None: ...


# ── 数据类 ─────────────────────────────────────────────

@dataclass
class SkillConfig(ISkillConfig):
    """ISkillConfig 实现。"""

    category: str = "general"
    priority: int = 50
    tags: list[str] = field(default_factory=list)
    auto_load: bool = True
    requires_confirmation: bool = False

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "priority": self.priority,
            "tags": self.tags,
            "auto_load": self.auto_load,
            "requires_confirmation": self.requires_confirmation,
        }

    def to_yaml(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, allow_unicode=True)


@dataclass
class Skill(ISkill):
    """ISkill 实现——单条技能卡。

    参考 GenericAgent Skill Card 格式。
    """

    name: str
    description: str = ""
    trigger_condition: str = ""
    steps: list[dict] = field(default_factory=list)
    config: SkillConfig = field(default_factory=SkillConfig)
    quality_score: float = 100.0
    created_at: float = 0.0
    last_used_at: float = 0.0
    use_count: int = 0

    def match(self, task_description: str) -> bool:
        """基于 trigger_condition 做简单关键词匹配。"""
        if not self.trigger_condition:
            return False
        keywords = self.trigger_condition.lower().split()
        task_lower = task_description.lower()
        return any(kw in task_lower for kw in keywords)

    def get_steps(self) -> list[dict]:
        return self.steps


# ── 注册表 ────────────────────────────────────────────

class SkillRegistry:
    """ISkill 注册表。

    管理技能的生命周期：注册 → 查找 → 匹配 → 卸载。
    Phase 1: 手动注册 + YAML 目录加载
    Phase 3: IAutonomousMemory 结晶自动注册
    """

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            logger.info(f"Skill '{skill.name}' already registered, overwriting")
        self._skills[skill.name] = skill

    def unregister(self, name: str) -> None:
        self._skills.pop(name, None)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def find_by_tags(self, tags: list[str]) -> list[Skill]:
        return [s for s in self._skills.values()
                if any(t in s.config.tags for t in tags)]

    def match_task(self, task: str) -> list[Skill]:
        """返回匹配技能列表，按 priority 降序。"""
        matched = [s for s in self._skills.values() if s.match(task)]
        matched.sort(key=lambda s: s.config.priority, reverse=True)
        return matched

    def list_all(self) -> list[Skill]:
        return list(self._skills.values())

    def load_from_dir(self, dir_path: str) -> int:
        """从目录加载 YAML 技能卡。"""
        count = 0
        skill_dir = Path(dir_path)
        if not skill_dir.exists():
            return 0
        for yaml_file in skill_dir.glob("*.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data:
                    continue
                config_data = data.pop("config", {}) or {}
                skill = Skill(
                    name=data.get("name", yaml_file.stem),
                    description=data.get("description", ""),
                    trigger_condition=data.get("trigger_condition", ""),
                    steps=data.get("steps", []),
                    config=SkillConfig(**config_data) if config_data else SkillConfig(),
                    quality_score=data.get("quality_score", 100.0),
                    created_at=data.get("created_at", 0.0),
                )
                self.register(skill)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to load skill from {yaml_file}: {e}")
        return count
