"""tests/app/test_cross_agent_read.py — CrossAgentReadTool 测试。"""

import pytest

from ai.types import Context, ToolResult
from app.tools.cross_agent_read import CrossAgentReadTool
from agent.memory.agent_store_factory import AgentStoreFactory


@pytest.fixture
def factory(tmp_workspace):
    return AgentStoreFactory(base_path=str(tmp_workspace / ".agent-memory"))


@pytest.fixture
def tool():
    return CrossAgentReadTool()


@pytest.fixture
def context_with_factory(factory):
    return Context(
        system_prompt="test",
        metadata={"_store_factory": factory},
    )


class TestCrossAgentReadTool:

    async def test_read_agent_view(self, tool, factory, context_with_factory):
        stores = factory.create_agent_stores("coder_001")
        await stores.store.write("agent_view/_index.md", "# Coder Status: active")
        result = await tool.execute(
            {"agent_id": "coder_001", "path": "agent_view/_index.md"},
            context_with_factory,
        )
        assert result.success is True
        assert "active" in result.content

    async def test_read_digest(self, tool, factory, context_with_factory):
        stores = factory.create_agent_stores("reviewer_001")
        await stores.store.write("digest/d_001.md", "---\nid: d_001\n---\n\nTest digest")
        result = await tool.execute(
            {"agent_id": "reviewer_001", "path": "digest/d_001.md"},
            context_with_factory,
        )
        assert result.success is True
        assert "Test digest" in result.content

    async def test_path_traversal_blocked(self, tool, context_with_factory):
        result = await tool.execute(
            {"agent_id": "coder_001", "path": "status/secret.md"},
            context_with_factory,
        )
        assert result.success is False
        assert "Access denied" in result.error

    async def test_nonexistent_agent(self, tool, factory, context_with_factory):
        result = await tool.execute(
            {"agent_id": "ghost_999", "path": "agent_view/_index.md"},
            context_with_factory,
        )
        assert result.success is False
        assert "not found" in result.error

    async def test_nonexistent_file(self, tool, factory, context_with_factory):
        factory.create_agent_stores("coder_001")
        result = await tool.execute(
            {"agent_id": "coder_001", "path": "digest/d_999.md"},
            context_with_factory,
        )
        assert result.success is False
        assert "not found" in result.error

    async def test_no_factory_in_context(self, tool):
        ctx = Context(system_prompt="test")
        result = await tool.execute(
            {"agent_id": "coder_001", "path": "agent_view/_index.md"},
            ctx,
        )
        assert result.success is False
        assert "not available" in result.error

    async def test_read_wiki(self, tool, factory, context_with_factory):
        stores = factory.create_agent_stores("memorizer_001")
        await stores.store.write("wiki/python.md", "---\nid: python\n---\n\nPython stack")
        result = await tool.execute(
            {"agent_id": "memorizer_001", "path": "wiki/python.md"},
            context_with_factory,
        )
        assert result.success is True

    async def test_read_domains(self, tool, factory, context_with_factory):
        stores = factory.create_agent_stores("coder_001")
        await stores.store.write("domains/task/_index.md", "---\ntype: domain_index\n---\n")
        result = await tool.execute(
            {"agent_id": "coder_001", "path": "domains/task/_index.md"},
            context_with_factory,
        )
        assert result.success is True

    async def test_definition_has_correct_name(self, tool):
        assert tool.definition.name == "cross_agent_read"

    async def test_is_concurrency_safe(self, tool):
        assert tool.is_concurrency_safe is True

    async def test_agent_id_injection_blocked(self, tool, factory, context_with_factory):
        result = await tool.execute(
            {"agent_id": "../etc", "path": "agent_view/_index.md"},
            context_with_factory,
        )
        assert result.success is False
        assert "Invalid agent_id" in result.error

    async def test_agent_id_special_chars_blocked(self, tool, factory, context_with_factory):
        result = await tool.execute(
            {"agent_id": "coder; rm -rf /", "path": "agent_view/_index.md"},
            context_with_factory,
        )
        assert result.success is False

    async def test_path_traversal_dotdot_blocked(self, tool, factory, context_with_factory):
        factory.create_agent_stores("coder_001")
        result = await tool.execute(
            {"agent_id": "coder_001", "path": "agent_view/../../etc/passwd"},
            context_with_factory,
        )
        assert result.success is False
        assert ".." in result.error
