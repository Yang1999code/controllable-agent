"""tests/app/test_tools_delegate_task.py — DelegateTaskTool 测试。"""

import pytest
from ai.types import Context
from app.tools.delegate_task import DelegateTaskTool


class TestDelegateTaskTool:
    @pytest.mark.asyncio
    async def test_no_runtime_returns_error(self):
        tool = DelegateTaskTool()
        result = await tool.execute(
            {"task": "Write a function"},
            Context(),
        )
        assert result.success is False
        assert "AgentRuntime not available" in result.error

    def test_tool_definition(self):
        tool = DelegateTaskTool()
        assert tool.definition.name == "delegate_task"
        assert tool.is_concurrency_safe is False
