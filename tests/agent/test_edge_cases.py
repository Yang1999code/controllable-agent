"""tests/agent/test_edge_cases.py — 极端情况测试。

并发竞态、空输入、资源耗尽、异常恢复等。
"""

import asyncio
import pytest

from agent.runtime import AgentRuntime, AgentTypeConfig, SubAgentResult
from agent.memory.agent_store_factory import AgentStoreFactory
from agent.memory.shared_space import SharedSpace
from agent.memory.store import MemoryStore
from agent.hook import HookChain
from tests.conftest import MockProvider, MockTool


class TestConcurrency:

    async def test_shared_space_concurrent_writes(self, tmp_workspace):
        """多个 Agent 同时写不同的 status 文件不应冲突。"""
        store = MemoryStore(str(tmp_workspace / ".agent-memory" / "shared"))
        shared_space = SharedSpace(store)

        async def write_status(agent_id: str):
            for i in range(5):
                await shared_space.write_status(agent_id, f"iteration {i}")

        await asyncio.gather(
            write_status("coder_001"),
            write_status("coder_002"),
            write_status("reviewer_001"),
        )

        # 每个文件应该只有最终值
        s1 = await shared_space.read_status("coder_001")
        s2 = await shared_space.read_status("coder_002")
        s3 = await shared_space.read_status("reviewer_001")
        assert "iteration 4" in s1
        assert "iteration 4" in s2
        assert "iteration 4" in s3

    async def test_shared_space_concurrent_decisions(self, tmp_workspace):
        """多 Agent 并发追加 decisions 不应丢数据。"""
        store = MemoryStore(str(tmp_workspace / ".agent-memory" / "shared"))
        shared_space = SharedSpace(store)

        await asyncio.gather(
            shared_space.append_decision("agent_a", "Decision A"),
            shared_space.append_decision("agent_b", "Decision B"),
            shared_space.append_decision("agent_c", "Decision C"),
        )

        decisions = await shared_space.read_decisions()
        assert "agent_a" in decisions
        assert "agent_b" in decisions
        assert "agent_c" in decisions

    async def test_factory_concurrent_create(self, tmp_workspace):
        """并发创建不同 Agent 的存储空间。"""
        factory = AgentStoreFactory(base_path=str(tmp_workspace / ".agent-memory"))

        async def create(agent_id: str):
            return factory.create_agent_stores(agent_id)

        results = await asyncio.gather(
            create("a"), create("b"), create("c"), create("d"),
        )
        ids = [r.agent_id for r in results]
        assert set(ids) == {"a", "b", "c", "d"}


class TestEdgeCases:

    async def test_empty_user_request(self, tmp_workspace):
        """空用户请求不应崩溃。"""
        factory = AgentStoreFactory(base_path=str(tmp_workspace / ".agent-memory"))
        shared_store = factory.get_shared_store()
        shared_space = SharedSpace(shared_store)

        tools = {"read": MockTool(tool_name="read")}
        runtime = AgentRuntime(
            tools=tools,
            provider=MockProvider(),
            hooks=HookChain(),
            store_factory=factory,
            shared_space=shared_space,
        )
        runtime.register_agent_type(AgentTypeConfig(
            name="planner", description="planner",
            system_prompt="You plan.", tools_whitelist=[],
            tools_blacklist=["delegate_task"],
        ))

        # 空任务
        result = await runtime.spawn(agent_type="planner", task="")
        assert result.status in ("completed", "failed")

    async def test_unknown_agent_type(self, tmp_workspace):
        """未知 agent_type 应返回 rejected。"""
        factory = AgentStoreFactory(base_path=str(tmp_workspace / ".agent-memory"))
        tools = {"read": MockTool(tool_name="read")}
        runtime = AgentRuntime(
            tools=tools,
            provider=MockProvider(),
            hooks=HookChain(),
        )
        result = await runtime.spawn(agent_type="nonexistent_agent", task="do stuff")
        assert result.status == "failed"
        assert "Unknown" in result.error

    async def test_max_depth_exceeded(self, tmp_workspace):
        """超过最大嵌套深度应被拒绝。"""
        factory = AgentStoreFactory(base_path=str(tmp_workspace / ".agent-memory"))
        tools = {"read": MockTool(tool_name="read")}
        runtime = AgentRuntime(
            tools=tools,
            provider=MockProvider(),
            hooks=HookChain(),
            max_depth=2,
        )
        runtime.register_agent_type(AgentTypeConfig(
            name="coder", description="coder",
            system_prompt="You code.", tools_whitelist=[],
            tools_blacklist=["delegate_task"],
        ))

        result = await runtime.spawn(
            agent_type="coder", task="test",
            current_depth=2,  # 已到上限
        )
        assert result.status == "rejected"
        assert "Max depth" in result.error

    async def test_shared_space_empty_reads(self, tmp_workspace):
        """读取不存在的文件应返回空字符串而非报错。"""
        store = MemoryStore(str(tmp_workspace / ".agent-memory" / "shared"))
        shared_space = SharedSpace(store)

        assert await shared_space.read_plan() == ""
        assert await shared_space.read_status("ghost") == ""
        assert await shared_space.read_decisions() == ""
        assert await shared_space.read_issues() == ""
        assert await shared_space.read_interrupt("intr_999") is None
        assert await shared_space.read_skill("nonexistent") is None

    async def test_interrupt_priority_values(self, tmp_workspace):
        """三种优先级都应能正确创建。"""
        store = MemoryStore(str(tmp_workspace / ".agent-memory" / "shared"))
        shared_space = SharedSpace(store)

        for priority in ("low", "medium", "high"):
            intr_id = await shared_space.create_interrupt(f"msg {priority}", priority=priority)
            content = await shared_space.read_interrupt(intr_id)
            assert f"priority: {priority}" in content

    async def test_factory_remove_then_recreate(self, tmp_workspace):
        """移除后再创建应返回新实例。"""
        factory = AgentStoreFactory(base_path=str(tmp_workspace / ".agent-memory"))
        s1 = factory.create_agent_stores("temp")
        factory.remove_agent("temp")
        s2 = factory.create_agent_stores("temp")
        assert s1 is not s2

    async def test_shared_space_long_content(self, tmp_workspace):
        """大量内容写入和读取。"""
        store = MemoryStore(str(tmp_workspace / ".agent-memory" / "shared"))
        shared_space = SharedSpace(store)

        long_plan = "# Plan\n" + "\n".join(f"## Step {i}: Do task {i}" for i in range(100))
        await shared_space.write_plan(long_plan)
        content = await shared_space.read_plan()
        assert "Step 99" in content
        assert len(content) > 1000

    async def test_many_agents_stores(self, tmp_workspace):
        """创建大量 Agent 存储空间。"""
        factory = AgentStoreFactory(base_path=str(tmp_workspace / ".agent-memory"))
        for i in range(50):
            factory.create_agent_stores(f"agent_{i:03d}")
        assert len(factory.list_agents()) == 50

    async def test_shared_space_skill_with_extension(self, tmp_workspace):
        """技能文件带扩展名时不应重复加 .md。"""
        store = MemoryStore(str(tmp_workspace / ".agent-memory" / "shared"))
        shared_space = SharedSpace(store)

        await shared_space.write_skill("config.yaml", "key: value")
        content = await shared_space.read_skill("config.yaml")
        assert content == "key: value"
