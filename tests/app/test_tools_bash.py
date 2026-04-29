"""tests/app/test_tools_bash.py — BashTool 测试。"""

import pytest
from ai.types import Context
from app.tools.bash import BashTool


class TestBashTool:
    @pytest.mark.asyncio
    async def test_echo_command(self):
        tool = BashTool()
        result = await tool.execute({"command": "echo hello"}, Context())
        assert result.success is True
        assert "hello" in result.content

    @pytest.mark.asyncio
    async def test_failing_command(self):
        tool = BashTool()
        result = await tool.execute(
            {"command": "exit 1", "timeout": "5"},
            Context(),
        )
        assert result.success is False
        assert "exit_code: 1" in result.content

    @pytest.mark.asyncio
    async def test_command_with_stderr(self):
        tool = BashTool()
        result = await tool.execute(
            {"command": "echo ok && echo err >&2"},
            Context(),
        )
        assert result.success is True
        assert "ok" in result.content

    @pytest.mark.asyncio
    async def test_timeout(self):
        tool = BashTool()
        # Python 自旋等待作为跨平台超时测试
        result = await tool.execute(
            {"command": "python -c \"import time; time.sleep(10)\"", "timeout": "1"},
            Context(),
        )
        assert result.success is False
        assert "超时" in result.error

    def test_tool_definition(self):
        tool = BashTool()
        assert tool.definition.name == "bash"
        assert tool.is_concurrency_safe is False
