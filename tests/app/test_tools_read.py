"""tests/app/test_tools_read.py — FileReadTool 测试。"""

import tempfile
from pathlib import Path

import pytest
from ai.types import Context
from app.tools.read import FileReadTool


class TestFileReadTool:
    @pytest.mark.asyncio
    async def test_read_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\nline3\nline4\nline5\n")
            path = f.name

        try:
            tool = FileReadTool()
            result = await tool.execute({"file_path": path}, Context())
            assert result.success is True
            assert "line1" in result.content
            assert "line5" in result.content
        finally:
            Path(path).unlink()

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self):
        tool = FileReadTool()
        result = await tool.execute({"file_path": "/nonexistent/path.txt"}, Context())
        assert result.success is False
        assert "不存在" in result.error

    @pytest.mark.asyncio
    async def test_read_with_offset_limit(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("\n".join(str(i) for i in range(1, 11)))
            path = f.name

        try:
            tool = FileReadTool()
            result = await tool.execute(
                {"file_path": path, "offset": 3, "limit": 2},
                Context(),
            )
            assert result.success is True
            assert "3" in result.content
            assert "4" in result.content
            assert "5" not in result.content.split("\n")[:5]
        finally:
            Path(path).unlink()

    def test_tool_definition(self):
        tool = FileReadTool()
        assert tool.definition.name == "read"
        assert tool.is_concurrency_safe is True
