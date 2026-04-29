"""tests/agent/test_autonomous_memory.py — AutonomousMemory 测试（需求4 核心）。"""

import pytest
from agent.autonomous_memory import AutonomousMemory
from agent.self_modify import ISelfModification


@pytest.fixture
def auto_memory(memory_store):
    mem = AutonomousMemory(memory_store, agent_id="test_agent")
    mem.set_quality_scorer(ISelfModification.quality_score)
    return mem


class TestWorkCheckpoint:
    @pytest.mark.asyncio
    async def test_update_and_get(self, auto_memory):
        await auto_memory.update_working_checkpoint("正在重构 auth 模块")
        cp = await auto_memory.get_working_checkpoint()
        assert cp["key_info"] != ""
        assert "重构 auth 模块" in cp["key_info"]

    @pytest.mark.asyncio
    async def test_empty_checkpoint(self, memory_store):
        mem = AutonomousMemory(memory_store, agent_id="empty_agent")
        cp = await mem.get_working_checkpoint()
        assert cp["key_info"] == ""


class TestCrystallize:
    def test_should_crystallize_success(self, auto_memory):
        """成功 + 3+ 工具 + 耗时 >= 5s → True"""
        result = {
            "success": True,
            "tool_calls_count": 5,
            "duration_ms": 10000,
            "task_description": "部署 Django 到 AWS",
            "tool_sequence": [
                {"tool_name": "bash", "args": {}, "description": "install"},
                {"tool_name": "write", "args": {}, "description": "create config"},
                {"tool_name": "bash", "args": {}, "description": "deploy"},
            ],
        }
        assert auto_memory.should_crystallize(result) is True

    def test_should_not_crystallize_failed(self, auto_memory):
        assert auto_memory.should_crystallize({"success": False}) is False

    def test_should_not_crystallize_few_tools(self, auto_memory):
        result = {"success": True, "tool_calls_count": 1, "duration_ms": 10000}
        assert auto_memory.should_crystallize(result) is False

    def test_should_not_crystallize_short_duration(self, auto_memory):
        result = {"success": True, "tool_calls_count": 5, "duration_ms": 100}
        assert auto_memory.should_crystallize(result) is False

    @pytest.mark.asyncio
    async def test_should_not_crystallize_duplicate(self, auto_memory):
        result = {
            "success": True, "tool_calls_count": 5, "duration_ms": 10000,
            "task_description": "deploy app duplicate",
            "tool_sequence": [
                {"tool_name": "bash", "args": {}, "description": "install"},
                {"tool_name": "write", "args": {}, "description": "config"},
                {"tool_name": "bash", "args": {}, "description": "run"},
            ],
        }
        assert auto_memory.should_crystallize(result) is True
        # 先结晶一次，_last_crystallized 记录 task_description
        await auto_memory.crystallize(result)
        # 第二次相同任务去重
        assert auto_memory.should_crystallize(result) is False

    @pytest.mark.asyncio
    async def test_crystallize_good_skill(self, auto_memory):
        result = {
            "success": True,
            "task_description": "Deploy Django to AWS EC2 with full CI/CD pipeline",
            "tool_sequence": [
                {"tool_name": "bash", "args": {}, "description": "install dependencies"},
                {"tool_name": "write", "args": {}, "description": "create config file"},
                {"tool_name": "bash", "args": {}, "description": "run deployment command"},
            ],
        }
        skill = await auto_memory.crystallize(result)
        assert skill is not None
        assert skill.quality_score >= 60

    @pytest.mark.asyncio
    async def test_crystallize_bad_skill_rejected(self, auto_memory):
        result = {
            "success": True,
            "task_description": "x",
            "tool_sequence": [],
        }
        skill = await auto_memory.crystallize(result)
        assert skill is None


class TestNudge:
    def test_memory_nudge_at_10(self, auto_memory):
        content = auto_memory.get_nudge_content(10, "memory")
        assert content is not None
        assert "记忆" in content

    def test_no_nudge_at_5(self, auto_memory):
        content = auto_memory.get_nudge_content(5, "memory")
        assert content is None

    def test_skill_nudge_at_15(self, auto_memory):
        content = auto_memory.get_nudge_content(15, "skill")
        assert content is not None

    def test_no_nudge_at_0(self, auto_memory):
        assert auto_memory.get_nudge_content(0, "memory") is None
