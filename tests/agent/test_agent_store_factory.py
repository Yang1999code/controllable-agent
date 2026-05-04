"""tests/agent/test_agent_store_factory.py — AgentStoreFactory 测试。"""

import pytest

from agent.memory.agent_store_factory import AgentStoreFactory, AgentStores
from agent.memory.store import MemoryStore
from agent.memory.fact_store import FactStore
from agent.memory.domain_index import DomainIndex


@pytest.fixture
def factory(tmp_workspace):
    return AgentStoreFactory(base_path=str(tmp_workspace / ".agent-memory"))


class TestAgentStoreFactory:

    async def test_create_agent_stores_returns_correct_types(self, factory):
        stores = factory.create_agent_stores("coder_001")
        assert isinstance(stores, AgentStores)
        assert isinstance(stores.store, MemoryStore)
        assert isinstance(stores.fact_store, FactStore)
        assert isinstance(stores.domain_index, DomainIndex)

    async def test_create_agent_stores_caches(self, factory):
        s1 = factory.create_agent_stores("coder_001")
        s2 = factory.create_agent_stores("coder_001")
        assert s1 is s2

    async def test_different_agents_get_different_stores(self, factory):
        s1 = factory.create_agent_stores("coder_001")
        s2 = factory.create_agent_stores("coder_002")
        assert s1 is not s2
        assert s1.store is not s2.store

    async def test_get_agent_stores_existing(self, factory):
        created = factory.create_agent_stores("planner_001")
        retrieved = factory.get_agent_stores("planner_001")
        assert created is retrieved

    async def test_get_agent_stores_nonexistent(self, factory):
        result = factory.get_agent_stores("ghost_999")
        assert result is None

    async def test_get_shared_store_singleton(self, factory):
        s1 = factory.get_shared_store()
        s2 = factory.get_shared_store()
        assert s1 is s2

    async def test_shared_store_different_from_agent_store(self, factory):
        shared = factory.get_shared_store()
        agent = factory.create_agent_stores("coder_001")
        assert shared is not agent.store

    async def test_list_agents(self, factory):
        factory.create_agent_stores("a")
        factory.create_agent_stores("b")
        factory.create_agent_stores("c")
        agents = factory.list_agents()
        assert set(agents) == {"a", "b", "c"}

    async def test_list_agents_empty(self, factory):
        assert factory.list_agents() == []

    async def test_remove_agent_existing(self, factory):
        factory.create_agent_stores("temp_001")
        assert factory.remove_agent("temp_001") is True
        assert factory.get_agent_stores("temp_001") is None

    async def test_remove_agent_nonexistent(self, factory):
        assert factory.remove_agent("ghost") is False

    async def test_agent_id_preserved(self, factory):
        stores = factory.create_agent_stores("reviewer_001")
        assert stores.agent_id == "reviewer_001"

    async def test_stores_are_frozen(self, factory):
        stores = factory.create_agent_stores("coder_001")
        with pytest.raises(AttributeError):
            stores.agent_id = "hacked"

    async def test_isolation_write_does_not_leak(self, factory):
        s1 = factory.create_agent_stores("agent_a")
        s2 = factory.create_agent_stores("agent_b")
        await s1.store.write("digest/test.md", "secret of A")
        content_b = await s2.store.read("digest/test.md")
        assert content_b is None
