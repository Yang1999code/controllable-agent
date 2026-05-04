"""tests/agent/test_new_features.py — 新增 5 项优化测试。"""

import pytest

from agent.compaction import compact, CompactionResult, _truncate_large_tool_results, LARGE_TOOL_RESULT_THRESHOLD
from agent.crystallizer import SkillCrystallizer
from agent.skill import Skill, SkillConfig, SkillRegistry


# ── 优化 4: 上下文压缩 — 大工具输出截断 ──


class TestTruncateLargeToolResults:

    def test_short_results_unchanged(self):
        from ai.types import Message
        msgs = [
            Message(role="tool", content="short result", id="1", tool_call_id="tc1"),
        ]
        result = _truncate_large_tool_results(msgs)
        assert result[0].content == "short result"

    def test_large_result_truncated(self):
        from ai.types import Message
        long_content = "x" * (LARGE_TOOL_RESULT_THRESHOLD + 1000)
        msgs = [
            Message(role="tool", content=long_content, id="1", tool_call_id="tc1"),
        ]
        result = _truncate_large_tool_results(msgs)
        assert result[0].content != long_content
        assert len(result[0].content) < len(long_content)
        assert "[...truncated" in result[0].content

    def test_mixed_messages_preserved(self):
        from ai.types import Message
        msgs = [
            Message(role="user", content="hello", id="1"),
            Message(role="tool", content="x" * 6000, id="2", tool_call_id="tc1"),
            Message(role="assistant", content="response", id="3"),
        ]
        result = _truncate_large_tool_results(msgs)
        assert result[0].content == "hello"
        assert len(result[1].content) < 6000
        assert result[2].content == "response"

    def test_exactly_at_threshold_not_truncated(self):
        from ai.types import Message
        content = "x" * LARGE_TOOL_RESULT_THRESHOLD
        msgs = [Message(role="tool", content=content, id="1", tool_call_id="tc1")]
        result = _truncate_large_tool_results(msgs)
        assert result[0].content == content


# ── 优化 2: 技能质量评分与淘汰 ──


class TestSkillScoring:

    def test_score_increases_on_success(self):
        registry = SkillRegistry()
        registry.register(Skill(
            name="test_skill", trigger_condition="test",
            steps=[{"tool": "bash"}], quality_score=50.0,
        ))
        cryst = SkillCrystallizer(registry, skills_dir="/tmp/test_skills")
        new_score = cryst.score_skill("test_skill", success=True)
        assert new_score > 50.0

    def test_score_decreases_on_failure(self):
        registry = SkillRegistry()
        registry.register(Skill(
            name="bad_skill", trigger_condition="bad",
            steps=[{"tool": "bash"}], quality_score=50.0,
        ))
        cryst = SkillCrystallizer(registry, skills_dir="/tmp/test_skills")
        new_score = cryst.score_skill("bad_skill", success=False)
        assert new_score < 50.0

    def test_score_clamped_to_100(self):
        registry = SkillRegistry()
        registry.register(Skill(
            name="great_skill", trigger_condition="great",
            steps=[{"tool": "bash"}], quality_score=99.0,
        ))
        cryst = SkillCrystallizer(registry, skills_dir="/tmp/test_skills")
        new_score = cryst.score_skill("great_skill", success=True, user_feedback=100)
        assert new_score <= 100.0

    def test_score_clamped_to_0(self):
        registry = SkillRegistry()
        registry.register(Skill(
            name="terrible", trigger_condition="x",
            steps=[{"tool": "bash"}], quality_score=5.0,
        ))
        cryst = SkillCrystallizer(registry, skills_dir="/tmp/test_skills")
        new_score = cryst.score_skill("terrible", success=False, user_feedback=0)
        assert new_score >= 0.0

    def test_nonexistent_skill_returns_0(self):
        registry = SkillRegistry()
        cryst = SkillCrystallizer(registry, skills_dir="/tmp/test_skills")
        assert cryst.score_skill("nope", success=True) == 0.0

    def test_use_count_increments(self):
        registry = SkillRegistry()
        registry.register(Skill(
            name="counter", trigger_condition="x",
            steps=[{"tool": "bash"}], quality_score=50.0,
        ))
        cryst = SkillCrystallizer(registry, skills_dir="/tmp/test_skills")
        cryst.score_skill("counter", success=True)
        cryst.score_skill("counter", success=True)
        skill = registry.get("counter")
        assert skill.use_count == 2


class TestSkillPruning:

    def test_prune_low_quality(self, tmp_path):
        registry = SkillRegistry()
        # 低分 + 使用 3 次以上 → 应该被淘汰
        registry.register(Skill(
            name="bad", trigger_condition="x",
            steps=[{"tool": "bash"}], quality_score=10.0, use_count=5,
        ))
        # 高分 → 不应该被淘汰
        registry.register(Skill(
            name="good", trigger_condition="y",
            steps=[{"tool": "bash"}], quality_score=80.0, use_count=5,
        ))
        cryst = SkillCrystallizer(registry, skills_dir=tmp_path)
        pruned = cryst.prune_low_quality()
        assert "bad" in pruned
        assert "good" not in pruned
        assert registry.get("bad") is None
        assert registry.get("good") is not None

    def test_dont_prune_few_uses(self, tmp_path):
        """使用不足 3 次的低分技能不应该被淘汰。"""
        registry = SkillRegistry()
        registry.register(Skill(
            name="new_but_bad", trigger_condition="x",
            steps=[{"tool": "bash"}], quality_score=10.0, use_count=1,
        ))
        cryst = SkillCrystallizer(registry, skills_dir=tmp_path)
        pruned = cryst.prune_low_quality()
        assert "new_but_bad" not in pruned
        assert registry.get("new_but_bad") is not None


# ── 优化 3: 动态超时 ──


class TestDynamicTimeout:

    def test_simple_task_timeout(self):
        from agent.runtime import AgentRuntime
        plan = "创建一个 main.py"
        result = AgentRuntime._estimate_task_complexity(plan)
        assert result["timeout_coder"] == 300
        assert result["timeout_reviewer"] == 300

    def test_complex_task_timeout(self):
        from agent.runtime import AgentRuntime
        plan = "\n".join([f"- file_{i}.py" for i in range(10)])
        result = AgentRuntime._estimate_task_complexity(plan)
        assert result["timeout_coder"] == 900
        assert result["timeout_reviewer"] == 600

    def test_medium_task_timeout(self):
        from agent.runtime import AgentRuntime
        plan = "\n".join([f"- file_{i}.py" for i in range(5)])
        result = AgentRuntime._estimate_task_complexity(plan)
        assert result["timeout_coder"] == 600
        assert result["timeout_reviewer"] == 420


# ── 优化 1: 子 Agent 串行化验证 ──


class TestSerialOrchestration:
    """验证 orchestrate 流程中 coder 在 reviewer 之前完成。

    这通过检查 runtime.py 源码中 Phase B-2/B-3 的顺序来验证。
    """

    def test_coder_before_reviewer_in_source(self):
        import inspect
        from agent.runtime import AgentRuntime
        source = inspect.getsource(AgentRuntime.orchestrate)
        coder_pos = source.find("Phase B-2: Coder")
        reviewer_pos = source.find("Phase B-3: Reviewer")
        assert coder_pos > 0, "Phase B-2 Coder not found"
        assert reviewer_pos > 0, "Phase B-3 Reviewer not found"
        assert coder_pos < reviewer_pos, "Coder should come before Reviewer"

    def test_no_parallel_coder_reviewer(self):
        """确认 coder 和 reviewer 不在同一个 spawn_parallel 调用中。"""
        import inspect
        from agent.runtime import AgentRuntime
        source = inspect.getsource(AgentRuntime.orchestrate)
        # 查找 spawn_parallel 调用
        parallel_calls = []
        idx = 0
        while True:
            pos = source.find("spawn_parallel", idx)
            if pos == -1:
                break
            # 找到这个调用附近的代码块
            block_start = source.rfind("phase_b", 0, pos)
            if block_start == -1:
                block_start = pos - 200
            block = source[block_start:pos + 200]
            parallel_calls.append(block)
            idx = pos + 1

        # 每个 spawn_parallel 调用中不应该同时有 coder 和 reviewer
        for block in parallel_calls:
            has_coder = '"coder"' in block or "'coder'" in block
            has_reviewer = '"reviewer"' in block or "'reviewer'" in block
            assert not (has_coder and has_reviewer), \
                "Coder and Reviewer should NOT be in the same parallel batch"
