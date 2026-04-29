"""tests/ai/test_types.py — 核心类型测试。"""

import pytest
from ai.types import (
    Message, ToolDefinition, ToolParameter, ToolResult, ITool,
    Context, AgentEventType, AgentEvent,
)


class TestMessage:
    def test_create_basic(self):
        m = Message(role="user", content="hello")
        assert m.role == "user"
        assert m.content == "hello"
        assert m.tool_call_id is None

    def test_create_tool_message(self):
        m = Message(role="tool", content="result", tool_call_id="tc_1", tool_name="read")
        assert m.role == "tool"
        assert m.tool_call_id == "tc_1"
        assert m.tool_name == "read"

    def test_default_metadata(self):
        m = Message(role="system", content="sys")
        assert m.metadata == {}


class TestToolDefinition:
    def test_create_empty(self):
        td = ToolDefinition(name="test", description="desc")
        assert td.name == "test"
        assert td.parameters == []

    def test_create_with_params(self):
        td = ToolDefinition(
            name="test",
            description="desc",
            parameters=[
                ToolParameter(name="x", type="integer", required=True),
                ToolParameter(name="y", type="string", required=False, enum=["a", "b"]),
            ],
        )
        assert len(td.parameters) == 2
        assert td.parameters[0].required is True
        assert td.parameters[1].enum == ["a", "b"]


class TestToolResult:
    def test_success(self):
        r = ToolResult(tool_name="read", success=True, content="file content")
        assert r.success is True
        assert r.error is None

    def test_failure(self):
        r = ToolResult(tool_name="bash", success=False, content="", error="command not found")
        assert r.success is False
        assert r.error == "command not found"

    def test_truncated(self):
        r = ToolResult(tool_name="read", success=True, content="...",
                       truncated=True, file_path="/tmp/result.txt")
        assert r.truncated is True
        assert r.file_path == "/tmp/result.txt"


class TestIToolProtocol:
    def test_protocol_check(self):
        """Protocol 鸭子类型检查。"""
        class MyTool:
            definition = ToolDefinition(name="my_tool", description="test")
            is_concurrency_safe = True

            async def execute(self, args, context):
                return ToolResult(tool_name="my_tool", success=True, content="ok")

        tool = MyTool()
        assert isinstance(tool, ITool)


class TestContext:
    def test_default_context(self):
        ctx = Context()
        assert ctx.system_prompt == ""
        assert ctx.messages == []
        assert ctx.tools == {}

    def test_with_metadata(self):
        ctx = Context(metadata={"session_id": "123", "project_path": "/tmp"})
        assert ctx.metadata["session_id"] == "123"


class TestAgentEvent:
    def test_create_event(self):
        e = AgentEvent(type=AgentEventType.TURN_START, data={"turn": 1})
        assert e.type == AgentEventType.TURN_START
        assert e.data["turn"] == 1

    def test_all_event_types(self):
        """确保所有事件类型可创建。"""
        for etype in AgentEventType:
            e = AgentEvent(type=etype)
            assert e.type == etype
