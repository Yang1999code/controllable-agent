"""tests/agent/test_memory_index.py — MemoryIndex 测试。"""

import pytest
from agent.memory.index import MemoryIndex


class TestTokenize:
    def test_english_tokenize(self):
        tokens = MemoryIndex._tokenize("hello world test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_chinese_tokenize(self):
        tokens = MemoryIndex._tokenize("部署 Django 到 AWS")
        assert len(tokens) > 0

    def test_mixed_tokenize(self):
        tokens = MemoryIndex._tokenize("Python 重构 auth 模块")
        assert "python" in tokens
        assert len(tokens) >= 3

    def test_empty_text(self):
        tokens = MemoryIndex._tokenize("")
        assert tokens == []

    def test_punctuation_filtered(self):
        tokens = MemoryIndex._tokenize("hello!!! world...")
        assert "hello" in tokens
        assert "world" in tokens


class TestSearchKeywords:
    @pytest.mark.asyncio
    async def test_search_finds_content(self, memory_store):
        index = MemoryIndex(memory_store)
        await memory_store.write("proj/facts.md", "Python is used for backend development")
        await memory_store.write("proj/config.md", "Database is PostgreSQL")

        results = await index.search_keywords("Python")
        assert len(results) >= 1
        assert any("facts" in r for r in results)

    @pytest.mark.asyncio
    async def test_search_no_match(self, memory_store):
        index = MemoryIndex(memory_store)
        await memory_store.write("proj/data.md", "some content")

        results = await index.search_keywords("xyzzyNotFound")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_empty_query(self, memory_store):
        index = MemoryIndex(memory_store)
        results = await index.search_keywords("")
        assert results == []
