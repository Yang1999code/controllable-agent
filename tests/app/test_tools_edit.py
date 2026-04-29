"""tests/app/test_tools_edit.py — FileEditTool 测试。"""

import pytest
from ai.types import Context
from app.tools.edit import FileEditTool


class TestFileEditTool:
    @pytest.mark.asyncio
    async def test_edit_unique_match(self, tmp_workspace):
        file_path = tmp_workspace / "edit_test.txt"
        file_path.write_text("hello world\nfoo bar\n")

        tool = FileEditTool()
        result = await tool.execute(
            {"file_path": str(file_path), "old_string": "hello world",
             "new_string": "hello universe"},
            Context(),
        )
        assert result.success is True
        assert "hello universe" in file_path.read_text()

    @pytest.mark.asyncio
    async def test_edit_replace_all(self, tmp_workspace):
        file_path = tmp_workspace / "replace_all.txt"
        file_path.write_text("foo foo foo")

        tool = FileEditTool()
        result = await tool.execute(
            {"file_path": str(file_path), "old_string": "foo",
             "new_string": "bar", "replace_all": True},
            Context(),
        )
        assert result.success is True
        assert file_path.read_text() == "bar bar bar"

    @pytest.mark.asyncio
    async def test_edit_multiple_matches_fails(self, tmp_workspace):
        file_path = tmp_workspace / "multi.txt"
        file_path.write_text("foo and foo again")

        tool = FileEditTool()
        result = await tool.execute(
            {"file_path": str(file_path), "old_string": "foo",
             "new_string": "bar"},
            Context(),
        )
        assert result.success is False
        assert "匹配了" in result.error

    @pytest.mark.asyncio
    async def test_edit_no_match_fails(self, tmp_workspace):
        file_path = tmp_workspace / "nomatch.txt"
        file_path.write_text("hello world")

        tool = FileEditTool()
        result = await tool.execute(
            {"file_path": str(file_path), "old_string": "nonexistent",
             "new_string": "bar"},
            Context(),
        )
        assert result.success is False
        assert "未找到" in result.error

    @pytest.mark.asyncio
    async def test_edit_file_not_found(self, tmp_workspace):
        tool = FileEditTool()
        result = await tool.execute(
            {"file_path": str(tmp_workspace / "nonexistent.txt"),
             "old_string": "x", "new_string": "y"},
            Context(),
        )
        assert result.success is False
        assert "不存在" in result.error

    def test_tool_definition(self):
        tool = FileEditTool()
        assert tool.definition.name == "edit"
        assert tool.is_concurrency_safe is False
