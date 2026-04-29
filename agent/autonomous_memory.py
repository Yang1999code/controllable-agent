"""agent/autonomous_memory.py — IAutonomousMemory（★ 需求4 核心增量）。

工作检查点 + 结晶评估 + 结晶执行 + Nudge + 长期更新 + 子Agent结果沉淀。

参考：GenericAgent 技能结晶 / Hermes Nudge 机制 / GenericAgent memory_management_sop.md
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import yaml

from agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)


@dataclass
class SkillCard:
    """技能卡（ISkillConfig 实现）。

    持久化位置：.agent-memory/{project}/skills/{name}.yaml
    """

    name: str
    description: str = ""
    trigger_condition: str = ""
    steps: list[dict] = field(default_factory=list)
    quality_score: float = 0.0
    created_at: float = 0.0
    last_used_at: float = 0.0
    use_count: int = 0


# ── ABC ───────────────────────────────────────────────

class IAutonomousMemory(ABC):
    """自主记忆抽象接口。

    管理 Agent 的会话内工作检查点、跨会话长期更新、技能结晶和 Nudge 提醒。
    """

    @abstractmethod
    def update_working_checkpoint(self, key_info: str, related_sop: str = "",
                                  project: str = "default") -> None: ...
    @abstractmethod
    def get_working_checkpoint(self, project: str = "default") -> dict: ...
    @abstractmethod
    def should_crystallize(self, task_result: dict) -> bool: ...
    @abstractmethod
    async def crystallize(self, task_result: dict) -> SkillCard | None: ...
    @abstractmethod
    def get_nudge_content(self, turn_count: int, nudge_type: str = "memory") -> str | None: ...
    @abstractmethod
    def record_delegation_result(self, task: str, result) -> None: ...
    @abstractmethod
    async def start_long_term_update(self, session_summary: dict,
                                     project: str = "default") -> None: ...
    @abstractmethod
    def get_crystallized_skill(self, name: str, project: str = "default") -> SkillCard | None: ...


# ── 实现 ──────────────────────────────────────────────

class AutonomousMemory(IAutonomousMemory):
    """IAutonomousMemory 实现。

    参考 GenericAgent 的结晶理念 + Hermes 的 Nudge 机制。

    核心公理（来自 GenericAgent）：
    - No Execution, No Memory
    - 神圣不可删改
    - 禁止存储易变状态
    - 最小充分指针
    """

    def __init__(self, store: MemoryStore, agent_id: str = "default"):
        self.store = store
        self.agent_id = agent_id
        self._checkpoint_path = "{project}/agents/" + agent_id + "/working_checkpoint.md"
        self._skills_dir = "{project}/skills"
        self._last_crystallized: set[str] = set()
        self._quality_scorer = None

    def set_quality_scorer(self, scorer) -> None:
        """注入质量评分函数（ISelfModification.quality_score）。"""
        self._quality_scorer = scorer

    # ── 工作检查点 ──

    async def update_working_checkpoint(self, key_info: str,
                                        related_sop: str = "",
                                        project: str = "default") -> None:
        content = f"""# Working Checkpoint
Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}

## Current Task
{key_info}

## Related SOP
{related_sop or 'None'}
"""
        path = self._checkpoint_path.format(project=project)
        await self.store.write(path, content)

    async def get_working_checkpoint(self, project: str = "default") -> dict:
        path = self._checkpoint_path.format(project=project)
        content = await self.store.read(path)
        if content:
            return {"key_info": content, "last_updated": time.time()}
        return {"key_info": "", "last_updated": 0}

    # ── 结晶触发 ──

    def should_crystallize(self, task_result: dict) -> bool:
        """评估任务是否值得结晶（启发式规则，不调 LLM）。"""
        if not task_result.get("success"):
            return False
        if task_result.get("tool_calls_count", 0) < 3:
            return False
        if task_result.get("duration_ms", 0) < 5000:
            return False
        desc = task_result.get("task_description", "")
        if desc in self._last_crystallized:
            return False
        return True

    async def crystallize(self, task_result: dict) -> SkillCard | None:
        """结晶为技能。"""
        tool_sequence = task_result.get("tool_sequence", [])
        task_desc = task_result.get("task_description", "Unknown task")

        skill_name = "_".join(task_desc.split()[:4]).lower().replace(" ", "_")

        skill = SkillCard(
            name=skill_name,
            description=task_desc,
            trigger_condition=f"当需要{task_desc}时",
            steps=[
                {"tool": ts.get("tool_name", ""),
                 "args": ts.get("args", {}),
                 "description": ts.get("description", "")}
                for ts in tool_sequence
            ],
            created_at=time.time(),
        )

        # 质量评分
        if self._quality_scorer:
            score_result = self._quality_scorer(skill)
            skill.quality_score = score_result.get("quality_score", 0)

        if skill.quality_score >= 60:
            skill_path = f"{self._skills_dir}/{skill_name}.yaml"
            skill_data = {
                "name": skill.name,
                "description": skill.description,
                "trigger_condition": skill.trigger_condition,
                "steps": skill.steps,
                "quality_score": skill.quality_score,
                "created_at": skill.created_at,
            }
            await self.store.write(
                skill_path.format(project="default"),
                yaml.dump(skill_data, allow_unicode=True),
            )
            self._last_crystallized.add(task_desc)
            return skill
        else:
            logger.info(f"Skill '{skill_name}' rejected: quality {skill.quality_score} < 60")
            return None

    # ── Nudge ──

    def get_nudge_content(self, turn_count: int,
                          nudge_type: str = "memory") -> str | None:
        """Nudge 提示内容。每 10 轮触发一次。"""
        if nudge_type == "memory" and turn_count > 0 and turn_count % 10 == 0:
            return "你应该考虑使用记忆工具检查或存储相关记忆。"
        if nudge_type == "skill" and turn_count > 0 and turn_count % 10 == 5:
            return "你应该考虑检查是否有可复用的已结晶技能。"
        return None

    # ── 长期更新 ──

    async def start_long_term_update(self, session_summary: dict,
                                     project: str = "default") -> None:
        """会话结束后更新长期记忆。"""
        # 提取环境事实 → L2
        if env_facts := session_summary.get("environment_facts"):
            facts_path = f"{project}/agents/{self.agent_id}/facts.md"
            existing = await self.store.read(facts_path) or ""
            await self.store.write(facts_path, existing + "\n" + str(env_facts))

        # 提取任务经验 → L3
        if task_lessons := session_summary.get("task_lessons"):
            lessons_path = f"{project}/agents/{self.agent_id}/sessions/lessons.md"
            existing = await self.store.read(lessons_path) or ""
            await self.store.write(lessons_path, existing + "\n" + str(task_lessons))

    # ── 子Agent结果沉淀 ──

    def record_delegation_result(self, task: str, result) -> None:
        """记录子Agent结果（同步版本，供同步场景调用）。"""
        import asyncio
        content = f"""## Delegation Result
Task: {task}
Agent: {result.agent_type}
Status: {result.status}
Output: {result.output[:1000]}
Usage: {result.usage}
Tool trace: {result.tool_trace}
"""
        path = f"default/agents/{self.agent_id}/delegations/{result.task_id}.md"
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.store.write(path, content))
            else:
                asyncio.run(self.store.write(path, content))
        except RuntimeError:
            asyncio.run(self.store.write(path, content))

    # ── 技能查询 ──

    def get_crystallized_skill(self, name: str,
                                project: str = "default") -> SkillCard | None:
        """按名称查询已结晶技能。"""
        import asyncio
        skill_path = f"{self._skills_dir.format(project=project)}/{name}.yaml"
        try:
            content = asyncio.run(self.store.read(skill_path))
        except RuntimeError:
            return None
        if content:
            data = yaml.safe_load(content)
            if data:
                return SkillCard(**data)
        return None
