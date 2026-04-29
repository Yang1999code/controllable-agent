"""tests/agent/test_tool_registry.py — ToolRegistry 测试。"""

import pytest
from agent.tool_registry import ToolRegistry
from tests.conftest import MockTool


class TestToolRegistry:
    def test_register_tool(self, context):
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)
        assert "mock_tool" in registry.tools

    def test_register_empty_name_raises(self):
        registry = ToolRegistry()
        tool = MockTool()
        tool.definition.name = ""
        with pytest.raises(ValueError, match="cannot be empty"):
            registry.register(tool)

    def test_unregister_tool(self, context):
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)
        registry.unregister("mock_tool")
        assert "mock_tool" not in registry.tools

    def test_get_definitions(self):
        registry = ToolRegistry()
        registry.register(MockTool())
        defs = registry.get_definitions()
        assert len(defs) == 1
        assert defs[0].name == "mock_tool"

    @pytest.mark.asyncio
    async def test_execute_many_safe_parallel(self, context):
        registry = ToolRegistry()
        t1 = MockTool(return_value="result1")
        t1.definition.name = "tool1"
        t2 = MockTool(return_value="result2")
        t2.definition.name = "tool2"
        registry.register(t1)
        registry.register(t2)

        results = await registry.execute_many([
            {"tool_name": "tool1", "args": {"text": "a"}},
            {"tool_name": "tool2", "args": {"text": "b"}},
        ], context)

        assert len(results) == 2
        assert all(r.success for r in results)
        assert t1.call_count == 1
        assert t2.call_count == 1

    @pytest.mark.asyncio
    async def test_execute_many_unsafe_serial(self, context):
        registry = ToolRegistry()
        tool = MockTool()
        tool.is_concurrency_safe = False
        registry.register(tool)

        results = await registry.execute_many([
            {"tool_name": "mock_tool", "args": {"text": "a"}},
            {"tool_name": "mock_tool", "args": {"text": "b"}},
        ], context)

        assert len(results) == 2
        assert tool.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_handles_error(self, context):
        registry = ToolRegistry()
        tool = MockTool(should_fail=True)
        registry.register(tool)

        results = await registry.execute_many([
            {"tool_name": "mock_tool", "args": {"text": "x"}},
        ], context)

        assert len(results) == 1
        assert results[0].success is False

    def test_validate_args_required(self):
        from ai.types import ToolDefinition, ToolParameter

        class ToolWithRequired:
            definition = ToolDefinition(
                name="test_tool", description="Test",
                parameters=[ToolParameter(name="input", type="string",
                           description="Input", required=True)],
            )
            is_concurrency_safe = True

        errors = ToolRegistry.validate_args(ToolWithRequired(), {})
        assert len(errors) > 0
        assert any("input" in e for e in errors)

    def test_validate_args_enum(self):
        from ai.types import ToolDefinition, ToolParameter

        class ToolWithEnum:
            definition = ToolDefinition(
                name="test_tool", description="Test",
                parameters=[
                    ToolParameter(name="input", type="string", description="I", required=True),
                    ToolParameter(name="mode", type="string", description="Mode",
                                  required=False, enum=["fast", "slow"]),
                ],
            )
            is_concurrency_safe = True

        errors = ToolRegistry.validate_args(ToolWithEnum(), {"input": "test", "mode": "invalid"})
        assert len(errors) > 0
