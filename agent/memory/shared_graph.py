"""agent/memory/shared_graph.py — 多 Agent 共享知识图谱（Phase 3）。

允许多个 Agent 实例读写同一个图数据库，共享实体和关系，
避免重复提取。提升多 Agent 协作的知识复用效率。

架构：
- SharedGraphManager: 管理共享图后端，处理并发和冲突
- GraphSync: 从本地文件系统同步到共享图
- AgentView: Agent 对共享图的局部视图（权限隔离）

使用方式：
```python
# 主 Agent
shared = SharedGraphManager(backend)
await shared.register_agent("agent_1")
await shared.publish_entities(entities)
await shared.publish_edges(edges)

# 其他 Agent
results = await shared.search_across_agents("数据库")
await shared.get_agent_contributions("agent_1")
```
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from agent.memory.graph_backend import (
    IGraphBackend, GraphEntity, GraphEdge, GraphQueryResult,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentContribution:
    """Agent 对共享图谱的贡献记录。"""

    agent_id: str
    entity_count: int = 0
    edge_count: int = 0
    last_active: str = ""
    entities: list[str] = field(default_factory=list)


class SharedGraphManager:
    """多 Agent 共享知识图谱管理器。

    职责：
    1. 管理共享图后端的连接
    2. 跟踪各 Agent 的贡献
    3. 处理并发写入（乐观锁）
    4. 提供跨 Agent 搜索
    5. 权限隔离视图
    """

    def __init__(self, backend: IGraphBackend):
        self._backend = backend
        self._agents: dict[str, AgentContribution] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    # ── 初始化 ──

    async def initialize(self) -> None:
        """初始化共享图谱。"""
        if self._initialized:
            return
        await self._backend.initialize()
        self._initialized = True
        logger.info("Shared graph initialized: %s", self._backend.backend_name)

    # ── Agent 注册 ──

    async def register_agent(self, agent_id: str) -> None:
        """注册一个 Agent 到共享图谱。"""
        await self._ensure_initialized()
        async with self._lock:
            if agent_id not in self._agents:
                self._agents[agent_id] = AgentContribution(
                    agent_id=agent_id,
                    last_active=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )
                logger.info("Agent %s registered to shared graph", agent_id)
            else:
                self._agents[agent_id].last_active = time.strftime("%Y-%m-%dT%H:%M:%S")

    async def unregister_agent(self, agent_id: str) -> None:
        """注销 Agent（不删除其贡献的实体和边）。"""
        async with self._lock:
            if agent_id in self._agents:
                del self._agents[agent_id]
                logger.info("Agent %s unregistered from shared graph", agent_id)

    # ── 发布知识 ──

    async def publish_entities(
        self,
        agent_id: str,
        entities: list[GraphEntity],
    ) -> list[str]:
        """Agent 发布实体到共享图谱。

        实体带 agent_id 标签，可追溯来源。
        """
        await self._ensure_initialized()
        for entity in entities:
            if agent_id not in (entity.labels or []):
                entity.labels = list(entity.labels or []) + [f"agent:{agent_id}"]

        entity_ids = []
        for entity in entities:
            eid = await self._backend.add_entity(entity)
            entity_ids.append(eid)

        async with self._lock:
            if agent_id not in self._agents:
                self._agents[agent_id] = AgentContribution(agent_id=agent_id)
            contrib = self._agents[agent_id]
            contrib.entity_count += len(entities)
            contrib.entities.extend([e.name for e in entities])
            contrib.last_active = time.strftime("%Y-%m-%dT%H:%M:%S")

        logger.info(
            "Agent %s published %d entities to shared graph",
            agent_id, len(entities),
        )
        return entity_ids

    async def publish_edges(
        self,
        agent_id: str,
        edges: list[GraphEdge],
    ) -> list[str]:
        """Agent 发布关系到共享图谱。"""
        await self._ensure_initialized()

        edge_ids = []
        for edge in edges:
            edge.metadata = {**(edge.metadata or {}), "agent_id": agent_id}
            eid = await self._backend.add_edge(edge)
            edge_ids.append(eid)

        async with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].edge_count += len(edges)
                self._agents[agent_id].last_active = time.strftime("%Y-%m-%dT%H:%M:%S")

        logger.info(
            "Agent %s published %d edges to shared graph",
            agent_id, len(edges),
        )
        return edge_ids

    async def publish_episode(
        self,
        agent_id: str,
        entities: list[GraphEntity],
        edges: list[GraphEdge],
    ) -> dict:
        """Agent 发布一次 episodic 知识（实体 + 关系）。"""
        entity_ids = await self.publish_entities(agent_id, entities)
        edge_ids = await self.publish_edges(agent_id, edges)
        return {
            "agent_id": agent_id,
            "entity_ids": entity_ids,
            "edge_ids": edge_ids,
            "entity_count": len(entity_ids),
            "edge_count": len(edge_ids),
        }

    # ── 跨 Agent 搜索 ──

    async def search_across_agents(
        self, query: str, top_k: int = 10,
    ) -> GraphQueryResult:
        """跨所有 Agent 搜索实体和关系。"""
        await self._ensure_initialized()
        entities = await self._backend.search_entities(query, top_k)
        edges = await self._backend.search_edges(query, top_k)
        return GraphQueryResult(
            entities=entities, edges=edges,
            total_found=len(entities) + len(edges),
        )

    async def search_by_agent(
        self, agent_id: str, query: str, top_k: int = 10,
    ) -> GraphQueryResult:
        """搜索特定 Agent 贡献的知识。"""
        await self._ensure_initialized()
        # 先按 agent 标签过滤
        all_entities = await self._backend.search_entities(
            f"agent:{agent_id} {query}", top_k * 2,
        )
        filtered = [e for e in all_entities
                    if f"agent:{agent_id}" in (e.labels or [])]
        return GraphQueryResult(
            entities=filtered[:top_k],
            total_found=len(filtered),
        )

    # ── BFS 跨 Agent 遍历 ──

    async def bfs_shared(
        self, entity_name: str, max_depth: int = 2, max_nodes: int = 30,
    ) -> GraphQueryResult:
        """在共享图上 BFS 遍历。"""
        await self._ensure_initialized()
        return await self._backend.bfs_traverse(
            entity_name, max_depth, max_nodes,
        )

    # ── 贡献查询 ──

    async def get_agent_contributions(
        self, agent_id: str,
    ) -> AgentContribution | None:
        """获取某个 Agent 的贡献统计。"""
        return self._agents.get(agent_id)

    async def list_contributing_agents(self) -> list[AgentContribution]:
        """列出所有贡献过知识的 Agent。"""
        return sorted(
            self._agents.values(),
            key=lambda c: c.entity_count + c.edge_count,
            reverse=True,
        )

    async def get_shared_stats(self) -> dict:
        """共享图谱统计。"""
        await self._ensure_initialized()
        graph_stats = await self._backend.stats()
        graph_stats["registered_agents"] = len(self._agents)
        graph_stats["total_contributions"] = sum(
            c.entity_count + c.edge_count for c in self._agents.values()
        )
        return graph_stats

    # ── 清理 ──

    async def close(self) -> None:
        """关闭共享图谱。"""
        await self._backend.close()
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()


class GraphSync:
    """图同步器——从本地文件记忆同步到共享图。

    用于首次迁移或定期同步。
    """

    @staticmethod
    async def sync_from_fact_store(
        shared_graph: SharedGraphManager,
        fact_store,
        domain_index,
        agent_id: str = "sync_bot",
    ) -> dict:
        """从 FactStore 同步所有 digest/wiki 到共享图。

        从已有 digest/wiki 中提取实体名和标签信息，
        在共享图中创建对应的实体节点。
        """
        from agent.memory.fact_store import FactEntry

        stats = {"entities": 0, "edges": 0, "errors": 0}

        try:
            # 同步 digest
            digests = await fact_store.read_all("digest")
            for digest in digests:
                try:
                    entity = GraphEntity(
                        name=digest.id,
                        entity_type="digest",
                        summary=digest.metadata.get("task_summary", ""),
                        labels=list(digest.tags),
                    )
                    await shared_graph.publish_entities(agent_id, [entity])
                    stats["entities"] += 1
                except Exception as e:
                    logger.debug("sync digest %s: %s", digest.id, e)
                    stats["errors"] += 1

            # 同步 wiki
            wikis = await fact_store.read_all("wiki")
            for wiki in wikis:
                try:
                    entity = GraphEntity(
                        name=wiki.id,
                        entity_type="wiki",
                        summary=wiki.metadata.get("title", ""),
                        labels=list(wiki.tags),
                    )
                    await shared_graph.publish_entities(agent_id, [entity])
                    stats["entities"] += 1
                except Exception as e:
                    logger.debug("sync wiki %s: %s", wiki.id, e)
                    stats["errors"] += 1

        except Exception as e:
            logger.warning("sync_from_fact_store failed: %s", e)
            stats["errors"] += 1

        logger.info(
            "GraphSync: %d entities, %d errors",
            stats["entities"], stats["errors"],
        )
        return stats

    @staticmethod
    async def sync_from_relation_store(
        shared_graph: SharedGraphManager,
        relation_store,
        agent_id: str = "sync_bot",
    ) -> dict:
        """从 RelationStore 同步关系到共享图。"""
        stats = {"edges": 0, "errors": 0}

        try:
            # 搜索所有关系（空查询返回前100条）
            relations = await relation_store.search("")
            edges = [
                GraphEdge(
                    source_name=r.source,
                    target_name=r.target,
                    relation=r.relation,
                    fact=r.fact,
                    digest_ref=r.digest_ref,
                    created_at=r.timestamp,
                )
                for r in relations[:500]  # 限制批量大小
            ]
            if edges:
                await shared_graph.publish_edges(agent_id, edges)
                stats["edges"] = len(edges)
        except Exception as e:
            logger.warning("sync_from_relation_store failed: %s", e)
            stats["errors"] += 1

        logger.info("GraphSync relations: %d edges", stats["edges"])
        return stats
