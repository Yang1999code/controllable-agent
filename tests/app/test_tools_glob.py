"""tests/app/test_tools_glob.py — GlobTool 测试。"""

import pytest
from ai.types import Context
from app.tools.glob_tool import GlobTool


class TestGlobTool:
    @pytest.mark.asyncio
    async def test_find_py_files(self, tmp_workspace):
        (tmp_workspace / "a.py").write_text("")
        (tmp_workspace / "b.py").write_text("")
        (tmp_workspace / "c.txt").write_text("")

        tool = GlobTool()
        result = await tool.execute(
            {"pattern": "*.py", "path": str(tmp_workspace)},
            Context(),
        )
        assert result.success is True
        assert "a.py" in result.content
        assert "b.py" in result.content
        assert "c.txt" not in result.content

    @pytest.mark.asyncio
    async def test_no_match(self, tmp_workspace):
        tool = GlobTool()
        result = await tool.execute(
            {"pattern": "*.rs", "path": str(tmp_workspace)},
            Context(),
        )
        assert result.success is True
        assert "未找到" in result.content

    @pytest.mark.asyncio
    async def test_nonexistent_directory(self):
        tool = GlobTool()
        result = await tool.execute(
            {"pattern": "*", "path": "/nonexistent/path"},
            Context(),
        )
        assert result.success is False
        assert "不存在" in result.error

    def test_tool_definition(self):
        tool = GlobTool()
        assert tool.definition.name == "glob"
        assert tool.is_concurrency_safe is True
