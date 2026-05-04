"""agent/crystallizer.py — 技能结晶器。

从 memorizer Phase D 产出中提取技能提案，验证质量，持久化为 YAML。
启动时自动加载已有技能到 SkillRegistry。

工作流：
1. 从 Phase D memorizer 的输出中提取技能块（Markdown 格式）
2. 验证技能质量（必要字段完整、步骤可执行）
3. 写入 .agent-base/skills/{skill_name}.yaml
4. 更新 SkillRegistry（热加载）
"""

import logging
import re
import time
from pathlib import Path

import yaml

from agent.skill import Skill, SkillConfig, SkillRegistry

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(".agent-base/skills")

# 技能提案的必要字段
_REQUIRED_FIELDS = ("name", "trigger_condition", "steps")


class SkillCrystallizer:
    """技能结晶器：从 memorizer 产出提取 → 验证 → 持久化技能。"""

    def __init__(self, skill_registry: SkillRegistry, skills_dir: Path | str | None = None):
        self.registry = skill_registry
        self.skills_dir = Path(skills_dir) if skills_dir else SKILLS_DIR

    def crystallize(self, memorizer_output: str) -> list[Skill]:
        """从 memorizer 输出中提取并持久化技能。

        查找格式如下的技能块：
        ```skill
        name: 技能名
        description: 描述
        trigger_condition: 触发条件
        steps:
          - tool: bash
            args: {command: "..."}
            description: 步骤描述
        ```
        或 YAML 格式的 skill 块。
        """
        proposals = self._extract_skill_proposals(memorizer_output)
        if not proposals:
            logger.debug("No skill proposals found in memorizer output")
            return []

        crystallized = []
        for proposal in proposals:
            skill = self._validate_and_build(proposal)
            if skill is None:
                continue

            persisted = self._persist(skill)
            if persisted:
                self.registry.register(skill)
                crystallized.append(skill)
                logger.info("Crystallized skill: %s", skill.name)

        return crystallized

    def _extract_skill_proposals(self, text: str) -> list[dict]:
        """从文本中提取技能提案（YAML 代码块）。"""
        proposals = []

        # 模式 1: ```skill ... ``` 或 ```yaml ... ``` 包含 name + steps
        pattern = r"```(?:skill|yaml)\s*\n(.*?)```"
        for match in re.finditer(pattern, text, re.DOTALL):
            raw = match.group(1).strip()
            try:
                data = yaml.safe_load(raw)
                if isinstance(data, dict) and "name" in data:
                    proposals.append(data)
            except yaml.YAMLError:
                continue

        # 模式 2: 直接在文本中以 "## 技能" 或 "### 技能" 标题开头的块
        skill_section = re.search(
            r"(?:##\s*技能|###\s*技能|##\s*Skill|###\s*Skill).*?\n(.*?)(?=\n##|\n###|\Z)",
            text, re.DOTALL,
        )
        if skill_section:
            section_text = skill_section.group(1)
            for match in re.finditer(r"```yaml\s*\n(.*?)```", section_text, re.DOTALL):
                raw = match.group(1).strip()
                try:
                    data = yaml.safe_load(raw)
                    if isinstance(data, dict) and "name" in data:
                        proposals.append(data)
                except yaml.YAMLError:
                    continue

        return proposals

    def _validate_and_build(self, data: dict) -> Skill | None:
        """验证技能提案并构建 Skill 对象。"""
        if not isinstance(data, dict):
            return None

        # 检查必要字段
        name = data.get("name", "")
        if not name or not isinstance(name, str):
            logger.warning("Skill proposal missing 'name', skipping")
            return None

        trigger = data.get("trigger_condition", "")
        if not trigger:
            logger.warning("Skill '%s' missing trigger_condition, skipping", name)
            return None

        steps = data.get("steps", [])
        if not steps or not isinstance(steps, list):
            logger.warning("Skill '%s' has no steps, skipping", name)
            return None

        # 验证步骤格式
        valid_steps = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if "tool" not in step:
                logger.debug("Skill '%s' step %d missing 'tool', including anyway", name, i)
            valid_steps.append(step)

        if not valid_steps:
            logger.warning("Skill '%s' has no valid steps after filtering", name)
            return None

        config_data = data.get("config", {}) or {}
        now = time.time()

        return Skill(
            name=name,
            description=data.get("description", ""),
            trigger_condition=trigger,
            steps=valid_steps,
            config=SkillConfig(**{k: v for k, v in config_data.items()
                                  if k in ("category", "priority", "tags", "auto_load",
                                           "requires_confirmation")}),
            quality_score=data.get("quality_score", 80.0),
            created_at=now,
            last_used_at=now,
            use_count=0,
        )

    def _persist(self, skill: Skill) -> bool:
        """将技能持久化为 YAML 文件。"""
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r'[^\w一-鿿-]', '_', skill.name)
            file_path = self.skills_dir / f"{safe_name}.yaml"

            data = {
                "name": skill.name,
                "description": skill.description,
                "trigger_condition": skill.trigger_condition,
                "steps": skill.steps,
                "config": skill.config.to_dict(),
                "quality_score": skill.quality_score,
                "created_at": skill.created_at,
            }

            existing = None
            if file_path.exists():
                try:
                    existing = yaml.safe_load(file_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
                if isinstance(existing, dict) and existing.get("use_count", 0) > 0:
                    data["use_count"] = existing["use_count"]
                    data["last_used_at"] = existing.get("last_used_at", skill.last_used_at)

            file_path.write_text(
                yaml.dump(data, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
            logger.info("Persisted skill to %s", file_path)
            return True
        except Exception as e:
            logger.error("Failed to persist skill '%s': %s", skill.name, e)
            return False

    def load_existing_skills(self) -> int:
        """启动时加载已有的技能文件到 SkillRegistry。"""
        if not self.skills_dir.exists():
            return 0
        count = 0
        for yaml_file in self.skills_dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or "name" not in data:
                    continue
                steps = data.get("steps", [])
                config_data = data.get("config", {}) or {}
                skill = Skill(
                    name=data["name"],
                    description=data.get("description", ""),
                    trigger_condition=data.get("trigger_condition", ""),
                    steps=steps if isinstance(steps, list) else [],
                    config=SkillConfig(**{k: v for k, v in config_data.items()
                                          if k in ("category", "priority", "tags",
                                                   "auto_load", "requires_confirmation")}),
                    quality_score=data.get("quality_score", 80.0),
                    created_at=data.get("created_at", 0.0),
                    last_used_at=data.get("last_used_at", 0.0),
                    use_count=data.get("use_count", 0),
                )
                self.registry.register(skill)
                count += 1
            except Exception as e:
                logger.warning("Failed to load skill from %s: %s", yaml_file, e)
        if count:
            logger.info("Loaded %d skills from %s", count, self.skills_dir)
        return count
