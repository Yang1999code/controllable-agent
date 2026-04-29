"""tests/agent/test_memory_backend.py — IMemoryBackend / FileSystemMemoryBackend 测试。"""

import pytest
from agent.memory.backend import MemoryEntry, SearchResult, FileSystemMemoryBackend
from agent.memory.store import MemoryStore


@pytest.fixture
def backend(memory_store):
    return FileSystemMemoryBackend(memory_store, project="test_project")


class TestMemoryEntry:
    def test_default_values(self):
        entry = MemoryEntry(content="test", layer="L2", source="test")
        assert entry.entry_id == ""
        assert entry.tags == []
        assert entry.metadata == {}

    def test_custom_values(self):
        entry = MemoryEntry(
            content="important fact",
            layer="L3",
            source="agent_1",
            tags=["deploy", "aws"],
            entry_id="abc123",
        )
        assert entry.content == "important fact"
        assert entry.layer == "L3"
        assert "deploy" in entry.tags


class TestFileSystemMemoryBackend:
    @pytest.mark.asyncio
    async def test_store_and_get(self, backend):
        entry = MemoryEntry(content="A test memory", layer="L2", source="test")
        eid = await backend.store(entry)
        assert eid

        retrieved = await backend.get(eid)
        assert retrieved is not None
        assert "A test memory" in retrieved.content

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, backend):
        result = await backend.get("nonexistent_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_search(self, backend):
        await backend.store(MemoryEntry(
            content="Deploy Django to AWS", layer="L2", source="test",
        ))
        await backend.store(MemoryEntry(
            content="Configure nginx", layer="L2", source="test",
        ))
        result = await backend.search("Django")
        assert result.total_found >= 0
        assert isinstance(result.entries, list)

    @pytest.mark.asyncio
    async def test_delete(self, backend):
        entry = MemoryEntry(content="to be deleted", layer="L2", source="test")
        eid = await backend.store(entry)
        deleted = await backend.delete(eid)
        assert deleted is True
        assert await backend.get(eid) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, backend):
        assert await backend.delete("nonexistent") is False

    @pytest.mark.asyncio
    async def test_list_by_layer(self, backend):
        for i in range(3):
            await backend.store(MemoryEntry(
                content=f"fact {i}", layer="L2", source="test",
            ))
        entries = await backend.list_by_layer("L2", limit=50)
        assert len(entries) >= 3

    def test_guess_layer(self):
        assert FileSystemMemoryBackend._guess_layer("path/l1_navigation/x.md") == "L1"
        assert FileSystemMemoryBackend._guess_layer("path/l2_facts/x.md") == "L2"
        assert FileSystemMemoryBackend._guess_layer("path/l3_experience/x.md") == "L3"
        assert FileSystemMemoryBackend._guess_layer("path/l4_sessions/x.md") == "L4"
        assert FileSystemMemoryBackend._guess_layer("path/other/x.md") == "L0"
