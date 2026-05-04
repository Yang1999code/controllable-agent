"""agent/memory/agent_store_factory.py — Agent 隔离存储工厂。

为每个 Agent 创建独立的 MemoryStore + FactStore + DomainIndex 组合，
确保 Agent 间记忆完全隔离。同时管理共享区的 MemoryStore 实例。
"""

import logging
from dataclasses import dataclass, field

from agent.memory.store import MemoryStore
from agent.memory.fact_store import FactStore
from agent.memory.domain_index import DomainIndex

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentStores:
    """单个 Agent 的全套存储组件。不可变。"""

    agent_id: str
    store: MemoryStore
    fact_store: FactStore
    domain_index: DomainIndex


class AgentStoreFactory:
    """Agent 隔离存储工厂。

    职责：
    1. 为每个 Agent 创建独立的 MemoryStore/FactStore/DomainIndex
    2. 管理共享区的 MemoryStore 实例（单例）
    3. 跟踪已创建的 Agent 存储空间
    """

    def __init__(self, base_path: str = ".agent-memory"):
        self._base_path = base_path
        self._agents: dict[str, AgentStores] = {}
        self._shared_store: MemoryStore | None = None

    def create_agent_stores(self, agent_id: str) -> AgentStores:
        """为指定 Agent 创建隔离存储组件。

        每次调用返回新实例，但同一 agent_id 重复调用会返回缓存。
        """
        if agent_id in self._agents:
            return self._agents[agent_id]

        agent_path = f"{self._base_path}/agents/{agent_id}"
        store = MemoryStore(agent_path)
        fact_store = FactStore(store)
        domain_index = DomainIndex(store, fact_store)

        agent_stores = AgentStores(
            agent_id=agent_id,
            store=store,
            fact_store=fact_store,
            domain_index=domain_index,
        )
        self._agents[agent_id] = agent_stores
        logger.info("Created isolated stores for agent: %s", agent_id)
        return agent_stores

    def get_agent_stores(self, agent_id: str) -> AgentStores | None:
        """获取已创建的 Agent 存储组件。"""
        return self._agents.get(agent_id)

    def get_shared_store(self) -> MemoryStore:
        """获取共享区 MemoryStore（单例）。"""
        if self._shared_store is None:
            self._shared_store = MemoryStore(f"{self._base_path}/shared")
        return self._shared_store

    def list_agents(self) -> list[str]:
        """列出所有已创建存储空间的 Agent ID。"""
        return list(self._agents.keys())

    def remove_agent(self, agent_id: str) -> bool:
        """从工厂中移除 Agent 的跟踪记录（不删除文件）。

        返回 True 表示成功移除，False 表示 Agent 不存在。
        """
        if agent_id in self._agents:
            del self._agents[agent_id]
            logger.info("Removed agent stores tracking: %s", agent_id)
            return True
        return False
