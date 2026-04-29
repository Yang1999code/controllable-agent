"""tests/app/test_tools_write.py — FileWriteTool 测试。"""

import tempfile
from pathlib import Path

import pytest
from ai.types import Context
from app.tools.write import FileWriteTool


class TestFileWriteTool:
    @pytest.mark.asyncio
    async def test_write_new_file(self, tmp_workspace):
        file_path = str(tmp_workspace / "test_output.txt")
        tool = FileWriteTool()
        result = await tool.execute(
            {"file_path": file_path, "content": "Hello, world!"},
            Context(),
        )
        assert result.success is True
        assert Path(file_path).read_text() == "Hello, world!"

    @pytest.mark.asyncio
    async def test_write_overwrite_existing(self, tmp_workspace):
        file_path = tmp_workspace / "existing.txt"
        file_path.write_text("old content")

        tool = FileWriteTool()
        result = await tool.execute(
            {"file_path": str(file_path), "content": "new content"},
            Context(),
        )
        assert result.success is True
        assert file_path.read_text() == "new content"

    @pytest.mark.asyncio
    async def test_write_creates_parent_dir(self, tmp_workspace):
        file_path = str(tmp_workspace / "subdir" / "nested" / "file.txt")
        tool = FileWriteTool()
        result = await tool.execute(
            {"file_path": file_path, "content": "nested content"},
            Context(),
        )
        assert result.success is True
        assert Path(file_path).read_text() == "nested content"

    def test_tool_definition(self):
        tool = FileWriteTool()
        assert tool.definition.name == "write"
        assert tool.is_concurrency_safe is False
