"""tests/agent/test_memory_store.py — MemoryStore 测试。"""

import pytest


class TestMemoryStore:
    @pytest.mark.asyncio
    async def test_write_and_read(self, memory_store):
        await memory_store.write("test/file.md", "hello world")
        content = await memory_store.read("test/file.md")
        assert content == "hello world"

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, memory_store):
        content = await memory_store.read("nonexistent.md")
        assert content is None

    @pytest.mark.asyncio
    async def test_delete(self, memory_store):
        await memory_store.write("test/del.md", "data")
        assert await memory_store.delete("test/del.md") is True
        assert await memory_store.delete("test/del.md") is False

    @pytest.mark.asyncio
    async def test_exists(self, memory_store):
        await memory_store.write("test/exists.md", "data")
        assert await memory_store.exists("test/exists.md") is True
        assert await memory_store.exists("test/nope.md") is False

    @pytest.mark.asyncio
    async def test_glob(self, memory_store):
        await memory_store.write("a/1.md", "one")
        await memory_store.write("a/2.md", "two")
        await memory_store.write("b/3.md", "three")

        files = await memory_store.glob("**/*.md")
        assert len(files) == 3

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, memory_store):
        with pytest.raises(ValueError, match="traversal"):
            await memory_store.write("../outside.md", "bad")

    @pytest.mark.asyncio
    async def test_list_dir(self, memory_store):
        await memory_store.write("proj/file1.md", "a")
        await memory_store.write("proj/file2.md", "b")
        names = await memory_store.list_dir("proj")
        assert "file1.md" in names
        assert "file2.md" in names
