"""agent/self_modify.py — ISelfModification（★ 需求4 核心增量）。

quality_score() 三维评分（clarity/completeness/actionability），V1 用启发式规则。

参考：GenericAgent SkillIndex / Superpowers writing-skills
"""

from dataclasses import dataclass, field


@dataclass
class QualityScore:
    """三维质量评分结果。"""

    clarity: float = 0.0
    completeness: float = 0.0
    actionability: float = 0.0
    quality_score: float = 0.0
    passed: bool = False


def calculate_quality_score(skill_config) -> QualityScore:
    """基于规则的启发式评分（V1）。

    参考 GenericAgent 的三维评分体系。

    评分规则：
    - clarity：描述长度（>20字=80, >10字=50, ≤10字=20）+ 触发条件非空（+20）
    - completeness：步骤数（≥3=80, ≥1=50, 0=10）+ 每步有 description（+10/步，最多+30）
    - actionability：每步有 tool_name（+25/步，最多+75）+ 步骤数≥2（+25）
    """
    desc = getattr(skill_config, "description", "") or ""
    trigger = getattr(skill_config, "trigger_condition", "") or ""
    steps = getattr(skill_config, "steps", []) or []

    # clarity
    desc_len = len(desc)
    if desc_len > 20:
        clarity = 80.0
    elif desc_len > 10:
        clarity = 50.0
    else:
        clarity = 20.0
    if trigger:
        clarity = min(100.0, clarity + 20)

    # completeness
    step_count = len(steps)
    if step_count >= 3:
        completeness = 80.0
    elif step_count >= 1:
        completeness = 50.0
    else:
        completeness = 10.0
    described_steps = sum(1 for s in steps if isinstance(s, dict) and s.get("description"))
    completeness = min(100.0, completeness + described_steps * 10)

    # actionability
    actionable_steps = sum(1 for s in steps if isinstance(s, dict) and s.get("tool_name"))
    actionability = min(75.0, actionable_steps * 25.0)
    if step_count >= 2:
        actionability = min(100.0, actionability + 25.0)

    quality_score = round(clarity * 0.3 + completeness * 0.3 + actionability * 0.4, 1)

    return QualityScore(
        clarity=clarity,
        completeness=completeness,
        actionability=actionability,
        quality_score=quality_score,
        passed=quality_score >= 60,
    )


# ── ISelfModification 接口 ─────────────────────────────

class ISelfModification:
    """Agent 自我修改接口。

    需求2 预留 → 需求4 部分升级（新增 quality_score）。
    """

    @staticmethod
    def quality_score(candidate) -> dict:
        """V1 实现：基于规则的启发式评分。"""
        result = calculate_quality_score(candidate)
        return {
            "clarity": result.clarity,
            "completeness": result.completeness,
            "actionability": result.actionability,
            "quality_score": result.quality_score,
            "pass": result.passed,
        }

    # ── 以下留 V4 ──
    async def evaluate_performance(self, session_history: dict) -> dict:
        raise NotImplementedError("V4")

    async def propose_skill(self, pattern: dict) -> dict | None:
        raise NotImplementedError("V4")

    async def propose_prompt_improvement(self, fragment_source: str) -> dict | None:
        raise NotImplementedError("V4")

    async def self_test(self, proposal) -> dict:
        raise NotImplementedError("V4")
