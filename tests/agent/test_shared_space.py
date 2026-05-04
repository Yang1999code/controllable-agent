"""tests/agent/test_shared_space.py — SharedSpace 测试。"""

import pytest

from agent.memory.store import MemoryStore
from agent.memory.shared_space import SharedSpace


@pytest.fixture
def shared_space(tmp_workspace):
    store = MemoryStore(str(tmp_workspace / ".agent-memory" / "shared"))
    return SharedSpace(store)


class TestSharedSpacePlan:

    async def test_read_plan_empty(self, shared_space):
        content = await shared_space.read_plan()
        assert content == ""

    async def test_write_and_read_plan(self, shared_space):
        await shared_space.write_plan("# My Plan\nStep 1: Do stuff")
        content = await shared_space.read_plan()
        assert "My Plan" in content
        assert "Step 1" in content

    async def test_overwrite_plan(self, shared_space):
        await shared_space.write_plan("v1")
        await shared_space.write_plan("v2")
        content = await shared_space.read_plan()
        assert content == "v2"


class TestSharedSpaceStatus:

    async def test_read_status_empty(self, shared_space):
        content = await shared_space.read_status("coder_001")
        assert content == ""

    async def test_write_and_read_status(self, shared_space):
        await shared_space.write_status("coder_001", "Working on module A")
        content = await shared_space.read_status("coder_001")
        assert "module A" in content

    async def test_different_agents_independent(self, shared_space):
        await shared_space.write_status("coder_001", "A")
        await shared_space.write_status("coder_002", "B")
        assert await shared_space.read_status("coder_001") == "A"
        assert await shared_space.read_status("coder_002") == "B"

    async def test_list_statuses(self, shared_space):
        await shared_space.write_status("a", "ok")
        await shared_space.write_status("b", "ok")
        statuses = await shared_space.list_statuses()
        assert set(statuses) == {"a", "b"}


class TestSharedSpaceDecisions:

    async def test_read_decisions_empty(self, shared_space):
        content = await shared_space.read_decisions()
        assert content == ""

    async def test_append_decision(self, shared_space):
        await shared_space.append_decision("coordinator_001", "Start coding")
        content = await shared_space.read_decisions()
        assert "coordinator_001" in content
        assert "Start coding" in content

    async def test_append_multiple_decisions(self, shared_space):
        await shared_space.append_decision("coordinator_001", "Decision 1")
        await shared_space.append_decision("coder_001", "Decision 2")
        content = await shared_space.read_decisions()
        assert "Decision 1" in content
        assert "Decision 2" in content

    async def test_write_decisions_overwrite(self, shared_space):
        await shared_space.append_decision("a", "old")
        await shared_space.write_decisions("fresh start")
        assert await shared_space.read_decisions() == "fresh start"


class TestSharedSpaceIssues:

    async def test_read_issues_empty(self, shared_space):
        content = await shared_space.read_issues()
        assert content == ""

    async def test_append_issue_with_tag(self, shared_space):
        await shared_space.append_issue(
            "reviewer_001", "Module A failed tests", tag="[INTEGRATION]",
        )
        content = await shared_space.read_issues()
        assert "[INTEGRATION]" in content
        assert "Module A failed tests" in content

    async def test_append_issue_without_tag(self, shared_space):
        await shared_space.append_issue("reviewer_001", "Bug found")
        content = await shared_space.read_issues()
        assert "Bug found" in content


class TestSharedSpaceInterrupts:

    async def test_create_interrupt(self, shared_space):
        intr_id = await shared_space.create_interrupt(
            "改用 JWT", priority="medium",
        )
        assert intr_id.startswith("intr_")
        content = await shared_space.read_interrupt(intr_id)
        assert "改用 JWT" in content
        assert "priority: medium" in content

    async def test_list_pending_interrupts(self, shared_space):
        await shared_space.create_interrupt("msg1", priority="low")
        await shared_space.create_interrupt("msg2", priority="high")
        pending = await shared_space.list_pending_interrupts()
        assert len(pending) == 2

    async def test_mark_interrupt_done(self, shared_space):
        intr_id = await shared_space.create_interrupt("test msg")
        done = await shared_space.mark_interrupt_done(intr_id)
        assert done is True
        pending = await shared_space.list_pending_interrupts()
        assert intr_id not in pending

    async def test_mark_nonexistent_interrupt(self, shared_space):
        done = await shared_space.mark_interrupt_done("intr_999")
        assert done is False

    async def test_interrupt_auto_increment(self, shared_space):
        id1 = await shared_space.create_interrupt("a")
        id2 = await shared_space.create_interrupt("b")
        num1 = int(id1.replace("intr_", ""))
        num2 = int(id2.replace("intr_", ""))
        assert num2 == num1 + 1


class TestSharedSpaceSkills:

    async def test_write_and_read_skill(self, shared_space):
        await shared_space.write_skill("python_testing", "# Python Testing Guide")
        content = await shared_space.read_skill("python_testing")
        assert "Python Testing Guide" in content

    async def test_list_skills(self, shared_space):
        await shared_space.write_skill("skill_a", "A")
        await shared_space.write_skill("skill_b", "B")
        skills = await shared_space.list_skills()
        assert "skill_a.md" in skills
        assert "skill_b.md" in skills

    async def test_delete_skill(self, shared_space):
        await shared_space.write_skill("temp_skill", "temp")
        deleted = await shared_space.delete_skill("temp_skill")
        assert deleted is True
        assert await shared_space.read_skill("temp_skill") is None

    async def test_read_nonexistent_skill(self, shared_space):
        assert await shared_space.read_skill("nonexistent") is None


class TestSharedSpaceInitialize:

    async def test_initialize_creates_structure(self, shared_space):
        await shared_space.initialize()
        plan = await shared_space.read_plan()
        assert "当前计划" in plan
        decisions = await shared_space.read_decisions()
        assert "决策记录" in decisions
