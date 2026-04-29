"""tests/app/test_tools_grep.py — GrepTool 测试。"""

import pytest
from ai.types import Context
from app.tools.grep_tool import GrepTool


class TestGrepTool:
    @pytest.mark.asyncio
    async def test_find_pattern(self, tmp_workspace):
        f = tmp_workspace / "test.py"
        f.write_text("def foo():\n    pass\n\ndef bar():\n    pass\n")

        tool = GrepTool()
        result = await tool.execute(
            {"pattern": "def foo", "path": str(f)},
            Context(),
        )
        assert result.success is True
        assert "def foo" in result.content

    @pytest.mark.asyncio
    async def test_no_match(self, tmp_workspace):
        f = tmp_workspace / "empty.py"
        f.write_text("hello")

        tool = GrepTool()
        result = await tool.execute(
            {"pattern": "xyzzy_nonexistent", "path": str(f)},
            Context(),
        )
        assert result.success is True
        assert "未找到" in result.content

    @pytest.mark.asyncio
    async def test_invalid_regex(self):
        tool = GrepTool()
        result = await tool.execute(
            {"pattern": "[invalid"},
            Context(),
        )
        assert result.success is False
        assert "正则表达式错误" in result.error

    @pytest.mark.asyncio
    async def test_case_insensitive(self, tmp_workspace):
        f = tmp_workspace / "case.txt"
        f.write_text("Hello World\n")

        tool = GrepTool()
        result = await tool.execute(
            {"pattern": "hello", "path": str(f), "-i": True},
            Context(),
        )
        assert result.success is True
        assert "Hello" in result.content

    @pytest.mark.asyncio
    async def test_nonexistent_path(self):
        tool = GrepTool()
        result = await tool.execute(
            {"pattern": "x", "path": "/nonexistent/file.txt"},
            Context(),
        )
        assert result.success is False

    def test_tool_definition(self):
        tool = GrepTool()
        assert tool.definition.name == "grep"
        assert tool.is_concurrency_safe is True
