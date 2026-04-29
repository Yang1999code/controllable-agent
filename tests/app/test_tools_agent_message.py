"""tests/app/test_tools_agent_message.py — AgentMessageTool 测试。"""

import pytest
from ai.types import Context
from app.tools.agent_message import AgentMessageTool


class TestAgentMessageTool:
    @pytest.mark.asyncio
    async def test_no_runtime_returns_error(self):
        tool = AgentMessageTool()
        result = await tool.execute(
            {"to_agent": "child_1", "content": "hello"},
            Context(),
        )
        assert result.success is False
        assert "AgentRuntime not available" in result.error

    @pytest.mark.asyncio
    async def test_send_message_with_runtime(self):
        from agent.runtime import AgentRuntime
        from agent.hook import HookChain
        from tests.conftest import MockProvider, MockTool

        tools = {
            "read": MockTool(return_value="ok"),
            "write": MockTool(return_value="ok"),
        }
        for name, tool in tools.items():
            tool.definition.name = name

        runtime = AgentRuntime(
            tools=tools,
            provider=MockProvider(),
            hooks=HookChain(),
        )

        tool = AgentMessageTool()
        ctx = Context(metadata={"_runtime": runtime, "agent_id": "test_agent"})
        result = await tool.execute(
            {"to_agent": "main", "content": "发现关键文件", "message_type": "info"},
            ctx,
        )
        assert result.success is True
        assert "已发送" in result.content

        msg = runtime.check_inbox("main")
        assert msg is not None
        assert "发现关键文件" in msg

    @pytest.mark.asyncio
    async def test_message_type_request(self):
        from agent.runtime import AgentRuntime
        from agent.hook import HookChain
        from tests.conftest import MockProvider, MockTool

        tools = {"mock": MockTool(return_value="ok")}
        tools["mock"].definition.name = "mock"
        runtime = AgentRuntime(
            tools=tools, provider=MockProvider(), hooks=HookChain(),
        )

        tool = AgentMessageTool()
        ctx = Context(metadata={"_runtime": runtime, "agent_id": "child"})
        result = await tool.execute(
            {"to_agent": "main", "content": "需要更多上下文",
             "message_type": "request"},
            ctx,
        )
        assert result.success is True
        msg = runtime.check_inbox("main")
        assert "[request]" in msg

    def test_tool_definition(self):
        tool = AgentMessageTool()
        assert tool.definition.name == "agent_message"
        assert tool.is_concurrency_safe is True
