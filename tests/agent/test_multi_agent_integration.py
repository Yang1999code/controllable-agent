"""tests/agent/test_multi_agent_integration.py — 多 Agent 系统集成测试。

模拟真实使用场景：角色注册 → orchestrate → 共享区协作 → 跨 Agent 读取。
"""

import asyncio
import pytest

from agent.runtime import AgentRuntime, AgentTypeConfig, SubAgentResult
from agent.memory.agent_store_factory import AgentStoreFactory
from agent.memory.shared_space import SharedSpace
from agent.memory.fact_store import FactStore, FactEntry
from agent.memory.domain_index import DomainIndex
from agent.hook import HookChain
from tests.conftest import MockProvider, MockTool


def _make_system(tmp_workspace) -> tuple:
    """创建完整的多 Agent 测试系统。"""
    factory = AgentStoreFactory(base_path=str(tmp_workspace / ".agent-memory"))
    shared_store = factory.get_shared_store()
    shared_space = SharedSpace(shared_store)

    tools = {}
    for name in ["read", "write", "edit", "bash", "glob", "grep",
                  "delegate_task", "cross_agent_read"]:
        tools[name] = MockTool(return_value="ok", tool_name=name)

    provider = MockProvider(responses=["Task completed."])

    runtime = AgentRuntime(
        tools=tools,
        provider=provider,
        hooks=HookChain(),
        max_concurrent=5,
        store_factory=factory,
        shared_space=shared_space,
    )

    return runtime, factory, shared_space


class TestFullOrchestration:

    async def test_orchestrate_full_flow(self, tmp_workspace):
        """完整编排流程：A→B→C→D 四个阶段。"""
        runtime, factory, shared_space = _make_system(tmp_workspace)
        from agent.role_prompts import register_roles
        register_roles(runtime)
        await shared_space.initialize()

        results = await runtime.orchestrate("实现一个简单的计算器")

        # 验证阶段顺序
        types = [r.agent_type for r in results]
        assert types[0] == "planner"
        assert types[-1] == "memorizer"
        # Phase B 中应有 coordinator
        assert "coordinator" in types

    async def test_orchestrate_stats_updated(self, tmp_workspace):
        """编排完成后统计信息应更新。"""
        runtime, factory, shared_space = _make_system(tmp_workspace)
        from agent.role_prompts import register_roles
        register_roles(runtime)
        await shared_space.initialize()

        await runtime.orchestrate("test")
        stats = runtime.get_delegation_stats()
        assert stats["total_spawned"] >= 1


class TestSharedSpaceCollaboration:

    async def test_planner_writes_plan_others_read(self, tmp_workspace):
        """Planner 写 plan.md，其他 Agent 通过 SharedSpace 读取。"""
        _, _, shared_space = _make_system(tmp_workspace)
        await shared_space.initialize()

        await shared_space.write_plan("# 用户需求\n实现认证模块\n\n# 计划\n步骤1...")
        plan = await shared_space.read_plan()
        assert "认证模块" in plan

    async def test_coder_writes_status_reviewer_reads(self, tmp_workspace):
        """Coder 写 status，Reviewer 通过 SharedSpace 读取。"""
        _, _, shared_space = _make_system(tmp_workspace)

        await shared_space.write_status("coder_001", "模块A完成，开始模块B")
        status = await shared_space.read_status("coder_001")
        assert "模块A完成" in status

    async def test_reviewer_appends_issues(self, tmp_workspace):
        """Reviewer 追加 issues，其他 Agent 读取。"""
        _, _, shared_space = _make_system(tmp_workspace)

        await shared_space.append_issue("reviewer_001", "模块A缺少错误处理")
        await shared_space.append_issue("reviewer_final", "接口不一致", tag="[INTEGRATION]")
        issues = await shared_space.read_issues()
        assert "错误处理" in issues
        assert "[INTEGRATION]" in issues

    async def test_coordinator_reads_interrupts(self, tmp_workspace):
        """用户写 interrupt，Coordinator 读取并处理。"""
        _, _, shared_space = _make_system(tmp_workspace)

        intr_id = await shared_space.create_interrupt("改用 JWT 认证", priority="medium")
        pending = await shared_space.list_pending_interrupts()
        assert intr_id in pending

        content = await shared_space.read_interrupt(intr_id)
        assert "JWT" in content

        done = await shared_space.mark_interrupt_done(intr_id)
        assert done is True
        pending_after = await shared_space.list_pending_interrupts()
        assert intr_id not in pending_after

    async def test_memorizer_skill_proposal_flow(self, tmp_workspace):
        """Memorizer 提炼技能的完整流程：提案→确认→写入。"""
        _, _, shared_space = _make_system(tmp_workspace)

        # Memorizer 写提案
        await shared_space.write_skill_proposal(
            "skill_proposal_001",
            "---\ntype: skill_proposal\nstatus: pending\n---\n\n# 认证模块标准流程\n...",
        )

        # 用户确认后写入 skills
        await shared_space.write_skill("auth_module_steps", "# 认证模块标准流程\n1. 定义模型\n2. 实现路由\n3. 添加中间件")

        skills = await shared_space.list_skills()
        assert "auth_module_steps.md" in skills

        content = await shared_space.read_skill("auth_module_steps")
        assert "认证模块标准流程" in content


class TestCrossAgentRead:

    async def test_memorizer_reads_coder_view(self, tmp_workspace):
        """Memorizer 通过 cross_agent_read 读取 Coder 的 agent_view。"""
        _, factory, _ = _make_system(tmp_workspace)

        coder_stores = factory.create_agent_stores("coder_001")
        await coder_stores.store.write("agent_view/_index.md", "# Coder Status\n已完成模块A和B")
        await coder_stores.fact_store.create_digest(
            "d_001", "session_1", "实现了认证路由", ["task"],
            ["auth", "jwt"], ["使用了 JWT 库"], "认证路由代码...",
        )

        # 通过 cross_agent_read 工具读取
        from app.tools.cross_agent_read import CrossAgentReadTool
        from ai.types import Context

        tool = CrossAgentReadTool()
        ctx = Context(system_prompt="", metadata={"_store_factory": factory})

        result = await tool.execute(
            {"agent_id": "coder_001", "path": "agent_view/_index.md"},
            ctx,
        )
        assert result.success is True
        assert "模块A" in result.content


class TestAgentStoreIsolation:

    async def test_agents_cannot_see_each_other_digests(self, tmp_workspace):
        """Agent 间 digest 完全隔离。"""
        _, factory, _ = _make_system(tmp_workspace)

        coder = factory.create_agent_stores("coder_001")
        reviewer = factory.create_agent_stores("reviewer_001")

        await coder.fact_store.create_digest(
            "d_001", "s1", "coder secret", ["task"],
            ["secret"], ["coder found a bug"], "secret details",
        )

        # reviewer 的 fact_store 不应该看到 coder 的 digest
        ids = await reviewer.fact_store.list_ids("digest")
        assert len(ids) == 0

    async def test_shared_space_is_shared(self, tmp_workspace):
        """共享区对所有人可见。"""
        factory, _, shared_space = _make_system(tmp_workspace)

        await shared_space.write_plan("shared plan")
        plan = await shared_space.read_plan()
        assert plan == "shared plan"

        # 任何 Agent 的 status 都在共享区
        await shared_space.write_status("coder_001", "coding")
        await shared_space.write_status("reviewer_001", "reviewing")
        statuses = await shared_space.list_statuses()
        assert "coder_001" in statuses
        assert "reviewer_001" in statuses


class TestDomainIndexPerAgent:

    async def test_each_agent_has_own_domain_index(self, tmp_workspace):
        """每个 Agent 有独立的 DomainIndex。"""
        _, factory, _ = _make_system(tmp_workspace)

        coder = factory.create_agent_stores("coder_001")
        memorizer = factory.create_agent_stores("memorizer_001")

        await coder.domain_index.initialize()
        await memorizer.domain_index.initialize()

        # Coder 添加一个 task 域条目
        await coder.domain_index.add_to_domain(
            "task", "auth", "digest", "digest/d_001.md", "认证实现",
        )

        coder_tasks = await coder.domain_index.get_domain_index("task")
        memorizer_tasks = await memorizer.domain_index.get_domain_index("task")

        assert len(coder_tasks) == 1
        assert len(memorizer_tasks) == 0
