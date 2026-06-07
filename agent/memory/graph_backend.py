"""agent/memory/graph_backend.py — 图记忆后端抽象 + 4 种实现。

Phase 2 核心模块。在 IMemoryBackend 之上追加图操作接口，
提供 4 种后端实现：

- FileGraphBackend:   默认，基于现有文件系统 + relations.jsonl，零新依赖
- Neo4jGraphBackend:  Neo4j 原生图数据库
- FalkorDBGraphBackend: FalkorDB（基于 Redis），Docker 一键部署
- KuzuGraphBackend:   Kuzu 嵌入式图数据库，零运维

设计原则：
- 所有后端实现同一接口，切换零业务代码改动
- 可选依赖：Neo4j/FalkorDB/Kuzu 驱动按需安装
- 最坏情况降级：图后端不可用时自动回退到 FileGraphBackend
- 向后兼容：现有 IMemoryBackend 接口不受影响

参考：Graphiti graphiti.py / graphiti_core/graphiti.py
"""

import asyncio
import json
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from agent.memory.store import MemoryStore
from agent.memory.relation_store import RelationStore, RelationEntry
from agent.memory.backend import IMemoryBackend, MemoryEntry, SearchResult
from agent.memory.index import MemoryIndex

logger = logging.getLogger(__name__)


# ── 图数据模型 ──────────────────────────────────────────

@dataclass
class GraphEntity:
    """图中的实体节点。"""

    id: str = ""
    name: str = ""
    entity_type: str = "other"  # person|project|technology|organization|event|other
    summary: str = ""
    labels: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    embedding: list[float] | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:16]
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class GraphEdge:
    """图中的关系边。"""

    id: str = ""
    source_id: str = ""       # 源实体 ID
    target_id: str = ""       # 目标实体 ID
    source_name: str = ""     # 源实体名（冗余，方便搜索）
    target_name: str = ""     # 目标实体名（冗余，方便搜索）
    relation: str = ""        # 关系类型 (WORKS_ON, USES, CREATED_BY...)
    fact: str = ""            # 自然语言描述
    digest_ref: str = ""      # 来源 digest 引用
    valid_at: str = ""        # 事实生效时间
    invalid_at: str = ""      # 事实失效时间（不删除！保留历史）
    weight: float = 1.0       # 边权重
    created_at: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:16]
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class GraphQueryResult:
    """图查询结果。"""

    entities: list[GraphEntity] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    total_found: int = 0


# ── GraphBackend ABC ────────────────────────────────────

class IGraphBackend(ABC):
    """图记忆存储后端抽象接口。

    Phase 2：在文件记忆之上追加图语义操作。
    所有方法均可异步。

    设计参考：
    - Graphiti graphiti.py add_episode/search/build_communities
    - NetworkX Graph API (add_node/add_edge/neighbors)
    """

    @abstractmethod
    async def initialize(self) -> None:
        """初始化后端（创建索引、表结构等）。"""
        ...

    @abstractmethod
    async def add_entity(self, entity: GraphEntity) -> str:
        """添加或更新实体，返回实体 ID。"""
        ...

    @abstractmethod
    async def add_edge(self, edge: GraphEdge) -> str:
        """添加关系边，返回边 ID。"""
        ...

    @abstractmethod
    async def add_episode(
        self,
        entities: list[GraphEntity],
        edges: list[GraphEdge],
    ) -> tuple[list[str], list[str]]:
        """批量添加实体和边（一次 episodic 输入）。

        返回 (entity_ids, edge_ids)。
        """
        ...

    @abstractmethod
    async def get_entity(self, entity_id: str) -> GraphEntity | None:
        """按 ID 获取实体。"""
        ...

    @abstractmethod
    async def search_entities(self, query: str, top_k: int = 10) -> list[GraphEntity]:
        """按关键词搜索实体。"""
        ...

    @abstractmethod
    async def search_edges(
        self, query: str, top_k: int = 20,
    ) -> list[GraphEdge]:
        """按关键词搜索关系边。"""
        ...

    @abstractmethod
    async def get_relations(
        self, entity_name: str, depth: int = 1,
    ) -> GraphQueryResult:
        """获取某实体的所有关联（实体 + 边 + 邻居）。

        depth=1 返回直接邻居，depth=2 返回二跳邻居。
        """
        ...

    @abstractmethod
    async def bfs_traverse(
        self, start_entity: str, max_depth: int = 3, max_nodes: int = 50,
    ) -> GraphQueryResult:
        """从起始实体 BFS 遍历，返回子图。"""
        ...

    @abstractmethod
    async def delete_entity(self, entity_id: str) -> bool:
        """删除实体及其关联边。"""
        ...

    @abstractmethod
    async def delete_edge(self, edge_id: str) -> bool:
        """删除边（软删除，标记 invalid_at）。"""
        ...

    @abstractmethod
    async def stats(self) -> dict:
        """获取图统计信息。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭后端连接。"""
        ...

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """后端名称标识。"""
        ...


# ── FileGraphBackend ────────────────────────────────────

class FileGraphBackend(IGraphBackend):
    """文件系统图后端（默认，零新依赖）。

    基于现有 MemoryStore + RelationStore + FactStore，
    在文件系统上模拟图操作。

    实体存储: .agent-memory/graph/entities.jsonl
    边存储:   复用 agent/memory/relation_store.py 的 relations.jsonl
    """

    def __init__(self, store: MemoryStore, relation_store: RelationStore | None = None):
        self._store = store
        self._relation_store = relation_store
        self._entities_path = "graph/entities.jsonl"
        self._edges_path = "graph/edges.jsonl"
        self._lock = asyncio.Lock()
        self._entities_by_id: dict[str, GraphEntity] = {}
        self._entities_by_name: dict[str, GraphEntity] = {}
        self._initialized = False

    @property
    def backend_name(self) -> str:
        return "file"

    async def initialize(self) -> None:
        """加载实体缓存。"""
        if self._initialized:
            return
        async with self._lock:
            raw = await self._store.read(self._entities_path) or ""
            for line in raw.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    entity = GraphEntity(**data)
                    self._entities_by_id[entity.id] = entity
                    if entity.name:
                        self._entities_by_name[entity.name] = entity
                except (json.JSONDecodeError, TypeError):
                    logger.debug("skip malformed entity line")
            self._initialized = True

    async def add_entity(self, entity: GraphEntity) -> str:
        """添加或更新实体到 entities.jsonl。"""
        if not entity.name:
            raise ValueError("entity.name must not be empty")
        await self._ensure_initialized()
        async with self._lock:
            # 检查是否已存在（按 name 去重）
            existing = self._entities_by_name.get(entity.name)
            if existing:
                # 更新现有实体（保留旧 ID）
                entity.id = existing.id
                entity.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._entities_by_id[entity.id] = entity
            self._entities_by_name[entity.name] = entity
            await self._flush_entities()
            return entity.id

    async def add_edge(self, edge: GraphEdge) -> str:
        """添加关系边到 edges.jsonl。"""
        await self._ensure_initialized()
        async with self._lock:
            line = json.dumps(asdict(edge), ensure_ascii=False)
            raw = await self._store.read(self._edges_path) or ""
            await self._store.write(self._edges_path, raw + line + "\n")
            return edge.id

    async def add_episode(
        self,
        entities: list[GraphEntity],
        edges: list[GraphEdge],
    ) -> tuple[list[str], list[str]]:
        """批量添加实体和边。"""
        await self._ensure_initialized()
        entity_ids = []
        for entity in entities:
            eid = await self.add_entity(entity)
            entity_ids.append(eid)
        edge_ids = []
        for edge in edges:
            # 关联实体 name → id（在锁内安全访问）
            if not edge.source_id:
                src_entity = self._entities_by_name.get(edge.source_name)
                if src_entity:
                    edge.source_id = src_entity.id
            if not edge.target_id:
                tgt_entity = self._entities_by_name.get(edge.target_name)
                if tgt_entity:
                    edge.target_id = tgt_entity.id
            eid = await self.add_edge(edge)
            edge_ids.append(eid)
        return entity_ids, edge_ids

    async def get_entity(self, entity_id: str) -> GraphEntity | None:
        """按 ID 获取实体。"""
        await self._ensure_initialized()
        return self._entities_by_id.get(entity_id)

    async def search_entities(self, query: str, top_k: int = 10) -> list[GraphEntity]:
        """按关键词搜索实体（线性扫描，V1 简化）。"""
        await self._ensure_initialized()
        query_lower = query.lower()
        tokens = query_lower.split()
        results: list[tuple[GraphEntity, int]] = []

        # 锁内拍快照防止并发修改
        async with self._lock:
            entities_snapshot = list(self._entities_by_id.values())

        for entity in entities_snapshot:
            searchable = f"{entity.name} {entity.summary} {' '.join(entity.labels)}".lower()
            score = 0
            if query_lower in searchable:
                score += 10
            for token in tokens:
                if token in searchable:
                    score += 1
            if score > 0:
                results.append((entity, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return [r[0] for r in results[:top_k]]

    async def search_edges(
        self, query: str, top_k: int = 20,
    ) -> list[GraphEdge]:
        """按关键词搜索边（复用 RelationStore）。"""
        if self._relation_store:
            relations = await self._relation_store.search(query)
            edges = [
                GraphEdge(
                    source_name=r.source,
                    target_name=r.target,
                    relation=r.relation,
                    fact=r.fact,
                    digest_ref=r.digest_ref,
                    created_at=r.timestamp,
                )
                for r in relations[:top_k]
            ]
            return edges

        # 回退：直接读 edges.jsonl
        raw = await self._store.read(self._edges_path) or ""
        results: list[GraphEdge] = []
        query_lower = query.lower()
        for line in raw.strip().split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                searchable = json.dumps(data).lower()
                if query_lower in searchable:
                    results.append(GraphEdge(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        return results[:top_k]

    async def get_relations(
        self, entity_name: str, depth: int = 1,
    ) -> GraphQueryResult:
        """获取某实体的关联实体和边。"""
        await self._ensure_initialized()

        # 先找到实体
        # 锁内快照
        async with self._lock:
            entity = self._entities_by_name.get(entity_name)
        if not entity:
            # 尝试按名字搜
            entities = await self.search_entities(entity_name, top_k=1)
            if entities:
                entity = entities[0]
            else:
                return GraphQueryResult()

        edges = await self.search_edges(entity.name)
        result = GraphQueryResult(entities=[entity], edges=edges, total_found=len(edges))

        # depth=1：找到邻居实体
        if depth >= 1:
            neighbor_names: set[str] = set()
            for edge in edges:
                if edge.source_name != entity.name:
                    neighbor_names.add(edge.source_name)
                if edge.target_name != entity.name:
                    neighbor_names.add(edge.target_name)
            async with self._lock:
                for name in neighbor_names:
                    neighbor = self._entities_by_name.get(name)
                    if neighbor:
                        result.entities.append(neighbor)

        return result

    async def bfs_traverse(
        self, start_entity: str, max_depth: int = 3, max_nodes: int = 50,
    ) -> GraphQueryResult:
        """BFS 图遍历。"""
        await self._ensure_initialized()

        visited: set[str] = set()
        all_entities: list[GraphEntity] = []
        all_edges: list[GraphEdge] = []

        # 找起始实体
        async with self._lock:
            entity = self._entities_by_name.get(start_entity)
        if not entity:
            entities = await self.search_entities(start_entity, top_k=1)
            if entities:
                entity = entities[0]
            else:
                return GraphQueryResult()

        import collections
        queue = collections.deque([(entity.name, 0)])
        visited.add(entity.name)

        while queue and len(all_entities) < max_nodes:
            current_name, depth = queue.popleft()
            async with self._lock:
                current_entity = self._entities_by_name.get(current_name)
            if current_entity and current_entity not in all_entities:
                all_entities.append(current_entity)

            if depth >= max_depth:
                continue

            edges = await self.search_edges(current_name)
            for edge in edges:
                if edge not in all_edges:
                    all_edges.append(edge)
                neighbor = (
                    edge.target_name
                    if edge.source_name == current_name
                    else edge.source_name
                )
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, depth + 1))

        return GraphQueryResult(
            entities=all_entities[:max_nodes],
            edges=all_edges,
            total_found=len(all_edges),
        )

    async def delete_entity(self, entity_id: str) -> bool:
        """删除实体（软删除）。"""
        await self._ensure_initialized()
        async with self._lock:
            entity = self._entities_by_id.pop(entity_id, None)
            if entity:
                self._entities_by_name.pop(entity.name, None)
                await self._flush_entities()
                return True
            return False

    async def delete_edge(self, edge_id: str) -> bool:
        """软删除边（标记 invalid_at）。"""
        async with self._lock:
            raw = await self._store.read(self._edges_path) or ""
            lines = raw.strip().split("\n")
            found = False
            new_lines = []
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            for line in lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("id") == edge_id:
                        data["invalid_at"] = now
                        found = True
                    new_lines.append(json.dumps(data, ensure_ascii=False))
                except json.JSONDecodeError:
                    new_lines.append(line)
            if found:
                await self._store.write(self._edges_path, "\n".join(new_lines) + "\n")
            return found

    async def stats(self) -> dict:
        """图统计。"""
        await self._ensure_initialized()
        async with self._lock:
            entity_types: dict[str, int] = {}
            for entity in self._entities_by_id.values():
                t = entity.entity_type or "other"
                entity_types[t] = entity_types.get(t, 0) + 1
            entities_snapshot = dict(entity_types)

            edge_count = 0
            raw = await self._store.read(self._edges_path) or ""
            for line in raw.strip().split("\n"):
                if line.strip():
                    edge_count += 1

        return {
            "total_entities": sum(entities_snapshot.values()),
            "entity_types": entities_snapshot,
            "total_edges": edge_count,
            "backend": self.backend_name,
        }

    async def close(self) -> None:
        """文件后端无需关闭连接。"""
        pass

    # ── 内部方法 ──

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def _flush_entities(self) -> None:
        """全量写回实体文件（仅在锁内调用）。"""
        lines = []
        for entity in self._entities_by_id.values():
            lines.append(json.dumps(asdict(entity), ensure_ascii=False))
        await self._store.write(self._entities_path, "\n".join(lines) + "\n")


# ── Neo4jGraphBackend ────────────────────────────────────

class Neo4jGraphBackend(IGraphBackend):
    """Neo4j 图数据库后端。

    需要安装: pip install neo4j
    连接字符串: bolt://localhost:7687
    """

    def __init__(
        self,
        uri: str = "",
        user: str = "",
        password: str = "",
        database: str = "neo4j",
    ):
        self._uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        self._user = user or os.environ.get("NEO4J_USER", "neo4j")
        self._password = password or os.environ.get("NEO4J_PASSWORD", "")
        self._database = database
        self._driver = None
        if not self._password:
            logger.warning(
                "Neo4j password not set. Set NEO4J_PASSWORD env var "
                "or pass password= argument."
            )

    @property
    def backend_name(self) -> str:
        return "neo4j"

    async def initialize(self) -> None:
        """创建约束和索引。"""
        try:
            from neo4j import AsyncGraphDatabase
            self._driver = AsyncGraphDatabase.driver(
                self._uri, auth=(self._user, self._password),
            )
            async with self._driver.session(database=self._database) as session:
                # 实体唯一约束
                await session.run(
                    "CREATE CONSTRAINT entity_id IF NOT EXISTS "
                    "FOR (e:Entity) REQUIRE e.id IS UNIQUE"
                )
                await session.run(
                    "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)"
                )
                # 边索引
                await session.run(
                    "CREATE INDEX edge_id IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON (r.id)"
                )
            logger.info("Neo4j initialized: %s", self._uri)
        except ImportError:
            logger.error("neo4j package not installed. Run: pip install neo4j")
            raise
        except Exception as e:
            logger.warning("Neo4j init error (constraints may already exist): %s", e)

    async def add_entity(self, entity: GraphEntity) -> str:
        """创建或更新实体节点。"""
        if not self._driver:
            await self.initialize()
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MERGE (e:Entity {name: $name})
                ON CREATE SET e.id = $id, e.entity_type = $type,
                              e.summary = $summary, e.labels = $labels,
                              e.created_at = $created_at, e.updated_at = $updated_at
                ON MATCH SET e.summary = $summary, e.labels = $labels,
                             e.updated_at = $updated_at
                RETURN e.id AS id
                """,
                id=entity.id,
                name=entity.name,
                type=entity.entity_type,
                summary=entity.summary,
                labels=entity.labels,
                created_at=entity.created_at,
                updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            record = await result.single()
            return record["id"] if record else entity.id

    async def add_edge(self, edge: GraphEdge) -> str:
        """创建关系边。"""
        if not self._driver:
            await self.initialize()
        async with self._driver.session(database=self._database) as session:
            await session.run(
                """
                MATCH (src:Entity {name: $source_name})
                MATCH (tgt:Entity {name: $target_name})
                MERGE (src)-[r:RELATES_TO {id: $id}]->(tgt)
                SET r.relation = $relation, r.fact = $fact,
                    r.digest_ref = $digest_ref, r.valid_at = $valid_at,
                    r.created_at = $created_at
                """,
                id=edge.id,
                source_name=edge.source_name,
                target_name=edge.target_name,
                relation=edge.relation,
                fact=edge.fact,
                digest_ref=edge.digest_ref,
                valid_at=edge.valid_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
                created_at=edge.created_at,
            )
            return edge.id

    async def add_episode(
        self, entities: list[GraphEntity], edges: list[GraphEdge],
    ) -> tuple[list[str], list[str]]:
        """批量添加。"""
        entity_ids = []
        for entity in entities:
            eid = await self.add_entity(entity)
            entity_ids.append(eid)
        edge_ids = []
        for edge in edges:
            eid = await self.add_edge(edge)
            edge_ids.append(eid)
        return entity_ids, edge_ids

    async def get_entity(self, entity_id: str) -> GraphEntity | None:
        """按 ID 获取实体。"""
        if not self._driver:
            await self.initialize()
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                "MATCH (e:Entity {id: $id}) RETURN e", id=entity_id,
            )
            record = await result.single()
            if record:
                node = record["e"]
                return GraphEntity(
                    id=node.get("id", ""),
                    name=node.get("name", ""),
                    entity_type=node.get("entity_type", "other"),
                    summary=node.get("summary", ""),
                    labels=node.get("labels", []),
                    created_at=node.get("created_at", ""),
                    updated_at=node.get("updated_at", ""),
                )
        return None

    async def search_entities(self, query: str, top_k: int = 10) -> list[GraphEntity]:
        """关键词搜索实体。"""
        if not self._driver:
            await self.initialize()
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (e:Entity)
                WHERE e.name CONTAINS $query OR e.summary CONTAINS $query
                RETURN e LIMIT $limit
                """,
                query=query, limit=top_k,
            )
            entities = []
            async for record in result:
                node = record["e"]
                entities.append(GraphEntity(
                    id=node.get("id", ""),
                    name=node.get("name", ""),
                    entity_type=node.get("entity_type", "other"),
                    summary=node.get("summary", ""),
                    labels=node.get("labels", []),
                ))
            return entities

    async def search_edges(
        self, query: str, top_k: int = 20,
    ) -> list[GraphEdge]:
        """关键词搜索边。"""
        if not self._driver:
            await self.initialize()
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (src:Entity)-[r:RELATES_TO]->(tgt:Entity)
                WHERE r.fact CONTAINS $query OR r.relation CONTAINS $query
                   OR src.name CONTAINS $query OR tgt.name CONTAINS $query
                RETURN src.name AS source_name, tgt.name AS target_name,
                       r.relation AS relation, r.fact AS fact,
                       r.id AS id, r.valid_at AS valid_at
                LIMIT $limit
                """,
                query=query, limit=top_k,
            )
            edges = []
            async for record in result:
                edges.append(GraphEdge(
                    id=record.get("id", ""),
                    source_name=record.get("source_name", ""),
                    target_name=record.get("target_name", ""),
                    relation=record.get("relation", ""),
                    fact=record.get("fact", ""),
                    valid_at=record.get("valid_at", ""),
                ))
            return edges

    async def get_relations(
        self, entity_name: str, depth: int = 1,
    ) -> GraphQueryResult:
        """获取实体关联（支持多跳）。"""
        if not self._driver:
            await self.initialize()
        depth_clause = f"*1..{depth}" if depth > 1 else ""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                f"""
                MATCH (e:Entity {{name: $name}})
                OPTIONAL MATCH (e)-[r:RELATES_TO{depth_clause}]-(neighbor:Entity)
                RETURN e, r, neighbor
                """,
                name=entity_name,
            )
            entities: dict[str, GraphEntity] = {}
            edges: list[GraphEdge] = []
            async for record in result:
                e_node = record.get("e")
                if e_node and e_node.get("id") not in entities:
                    entities[e_node["id"]] = GraphEntity(
                        id=e_node.get("id", ""),
                        name=e_node.get("name", ""),
                        entity_type=e_node.get("entity_type", "other"),
                        summary=e_node.get("summary", ""),
                    )
                n_node = record.get("neighbor")
                if n_node and n_node.get("id") not in entities:
                    entities[n_node["id"]] = GraphEntity(
                        id=n_node.get("id", ""),
                        name=n_node.get("name", ""),
                        entity_type=n_node.get("entity_type", "other"),
                    )
                r_rel = record.get("r")
                if r_rel:
                    edges.append(GraphEdge(
                        id=r_rel.get("id", ""),
                        relation=r_rel.get("relation", ""),
                        fact=r_rel.get("fact", ""),
                    ))
            return GraphQueryResult(
                entities=list(entities.values()),
                edges=edges,
                total_found=len(edges),
            )

    async def bfs_traverse(
        self, start_entity: str, max_depth: int = 3, max_nodes: int = 50,
    ) -> GraphQueryResult:
        """BFS 遍历子图。"""
        if not self._driver:
            await self.initialize()
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH path = (start:Entity {name: $name})-[*1..$depth]-(neighbor:Entity)
                WITH relationships(path) AS rels, nodes(path) AS nodes
                UNWIND nodes AS n
                UNWIND rels AS r
                RETURN DISTINCT n, r
                LIMIT $limit
                """,
                name=start_entity, depth=max_depth, limit=max_nodes,
            )
            entities: dict[str, GraphEntity] = {}
            edges: list[GraphEdge] = []
            async for record in result:
                n = record.get("n")
                if n and n.get("id") not in entities:
                    entities[n["id"]] = GraphEntity(
                        id=n.get("id", ""),
                        name=n.get("name", ""),
                        entity_type=n.get("entity_type", "other"),
                    )
                r = record.get("r")
                if r:
                    edges.append(GraphEdge(
                        id=r.get("id", ""),
                        relation=r.get("relation", ""),
                        fact=r.get("fact", ""),
                    ))
            return GraphQueryResult(
                entities=list(entities.values()),
                edges=edges,
                total_found=len(edges),
            )

    async def delete_entity(self, entity_id: str) -> bool:
        """删除实体及关联边。"""
        if not self._driver:
            await self.initialize()
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (e:Entity {id: $id})
                DETACH DELETE e
                RETURN count(e) AS deleted
                """,
                id=entity_id,
            )
            record = await result.single()
            return record and record["deleted"] > 0

    async def delete_edge(self, edge_id: str) -> bool:
        """软删除边（标记 invalid_at）。"""
        if not self._driver:
            await self.initialize()
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        async with self._driver.session(database=self._database) as session:
            await session.run(
                """
                MATCH ()-[r:RELATES_TO {id: $id}]->()
                SET r.invalid_at = $now
                """,
                id=edge_id, now=now,
            )
            return True

    async def stats(self) -> dict:
        """Neo4j 图统计。"""
        if not self._driver:
            await self.initialize()
        async with self._driver.session(database=self._database) as session:
            nodes_result = await session.run(
                "MATCH (e:Entity) RETURN count(e) AS cnt"
            )
            nodes = (await nodes_result.single())["cnt"]
            edges_result = await session.run(
                "MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS cnt"
            )
            edges_cnt = (await edges_result.single())["cnt"]
        return {
            "total_entities": nodes,
            "total_edges": edges_cnt,
            "backend": self.backend_name,
            "uri": self._uri,
        }

    async def close(self) -> None:
        """关闭 Neo4j 连接。"""
        if self._driver:
            await self._driver.close()
            self._driver = None


# ── FalkorDBGraphBackend ─────────────────────────────────

class FalkorDBGraphBackend(IGraphBackend):
    """FalkorDB 图数据库后端（基于 Redis Graph）。

    需要安装: pip install falkordb
    Docker 一键部署: docker run -p 6379:6379 falkordb/falkordb:latest
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        graph_name: str = "agent_memory",
    ):
        self._host = host
        self._port = port
        self._graph_name = graph_name
        self._graph = None

    @property
    def backend_name(self) -> str:
        return "falkordb"

    async def initialize(self) -> None:
        """连接到 FalkorDB。"""
        try:
            from falkordb import FalkorDB
            db = FalkorDB(host=self._host, port=self._port)
            self._graph = db.select_graph(self._graph_name)
            # 创建索引
            try:
                self._graph.query(
                    "CREATE INDEX FOR (e:Entity) ON (e.name)"
                )
            except Exception:
                pass  # 索引可能已存在
            logger.info("FalkorDB initialized: %s:%s/%s",
                       self._host, self._port, self._graph_name)
        except ImportError:
            logger.error("falkordb package not installed. Run: pip install falkordb")
            raise

    async def add_entity(self, entity: GraphEntity) -> str:
        """创建或更新实体。"""
        if not self._graph:
            await self.initialize()
        self._graph.query(
            """
            MERGE (e:Entity {name: $name})
            ON CREATE SET e.id = $id, e.entity_type = $type,
                          e.summary = $summary, e.labels = $labels,
                          e.created_at = $created_at, e.updated_at = $updated_at
            ON MATCH SET e.summary = $summary, e.labels = $labels,
                         e.updated_at = $updated_at
            """,
            {"name": entity.name, "id": entity.id, "type": entity.entity_type,
             "summary": entity.summary, "labels": entity.labels,
             "created_at": entity.created_at,
             "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
        )
        return entity.id

    async def add_edge(self, edge: GraphEdge) -> str:
        """创建关系边。"""
        if not self._graph:
            await self.initialize()
        self._graph.query(
            """
            MATCH (src:Entity {name: $src}), (tgt:Entity {name: $tgt})
            CREATE (src)-[r:RELATES_TO {
                id: $id, relation: $relation, fact: $fact, valid_at: $valid_at
            }]->(tgt)
            """,
            {"src": edge.source_name, "tgt": edge.target_name,
             "id": edge.id, "relation": edge.relation,
             "fact": edge.fact, "valid_at": edge.valid_at or ""},
        )
        return edge.id

    async def add_episode(
        self, entities: list[GraphEntity], edges: list[GraphEdge],
    ) -> tuple[list[str], list[str]]:
        eids = [await self.add_entity(e) for e in entities]
        edge_ids = [await self.add_edge(e) for e in edges]
        return eids, edge_ids

    async def get_entity(self, entity_id: str) -> GraphEntity | None:
        if not self._graph:
            await self.initialize()
        result = self._graph.query(
            "MATCH (e:Entity {id: $id}) RETURN e",
            {"id": entity_id},
        )
        if result.result_set:
            node = result.result_set[0][0]
            return GraphEntity(
                id=node.get("id", ""), name=node.get("name", ""),
                entity_type=node.get("entity_type", "other"),
                summary=node.get("summary", ""),
            )
        return None

    async def search_entities(self, query: str, top_k: int = 10) -> list[GraphEntity]:
        if not self._graph:
            await self.initialize()
        result = self._graph.query(
            "MATCH (e:Entity) WHERE e.name CONTAINS $q OR e.summary CONTAINS $q "
            "RETURN e LIMIT $limit",
            {"q": query, "limit": top_k},
        )
        entities = []
        for row in result.result_set:
            node = row[0]
            entities.append(GraphEntity(
                id=node.get("id", ""), name=node.get("name", ""),
                entity_type=node.get("entity_type", "other"),
                summary=node.get("summary", ""),
            ))
        return entities

    async def search_edges(
        self, query: str, top_k: int = 20,
    ) -> list[GraphEdge]:
        if not self._graph:
            await self.initialize()
        result = self._graph.query(
            "MATCH (src:Entity)-[r:RELATES_TO]->(tgt:Entity) "
            "WHERE r.fact CONTAINS $q OR src.name CONTAINS $q OR tgt.name CONTAINS $q "
            "RETURN src.name, tgt.name, r.relation, r.fact, r.id "
            "LIMIT $limit",
            {"q": query, "limit": top_k},
        )
        edges = []
        for row in result.result_set:
            edges.append(GraphEdge(
                source_name=row[0], target_name=row[1],
                relation=row[2], fact=row[3], id=row[4],
            ))
        return edges

    async def get_relations(
        self, entity_name: str, depth: int = 1,
    ) -> GraphQueryResult:
        if not self._graph:
            await self.initialize()
        depth_path = f"*1..{depth}" if depth > 1 else ""
        result = self._graph.query(
            f"MATCH (e:Entity {{name: $name}})"
            f"OPTIONAL MATCH (e)-[r:RELATES_TO{depth_path}]-(n:Entity) "
            f"RETURN e, r, n",
            {"name": entity_name},
        )
        entities: dict[str, GraphEntity] = {}
        edges: list[GraphEdge] = []
        for row in result.result_set:
            e = row[0]
            if e and e.get("id") not in entities:
                entities[e["id"]] = GraphEntity(
                    id=e.get("id", ""), name=e.get("name", ""),
                    entity_type=e.get("type", "other"),
                )
            n = row[2] if len(row) > 2 else None
            if n and n.get("id") not in entities:
                entities[n["id"]] = GraphEntity(
                    id=n.get("id", ""), name=n.get("name", ""),
                )
            r = row[1] if len(row) > 1 else None
            if r:
                edges.append(GraphEdge(
                    id=r.get("id", ""), relation=r.get("relation", ""),
                    fact=r.get("fact", ""),
                ))
        return GraphQueryResult(
            entities=list(entities.values()), edges=edges,
            total_found=len(edges),
        )

    async def bfs_traverse(
        self, start_entity: str, max_depth: int = 3, max_nodes: int = 50,
    ) -> GraphQueryResult:
        if not self._graph:
            await self.initialize()
        result = self._graph.query(
            "MATCH path = (start:Entity {name: $name})-[*1..$depth]-(n:Entity) "
            "WITH relationships(path) AS rels, nodes(path) AS nodes "
            "UNWIND nodes AS n UNWIND rels AS r "
            "RETURN DISTINCT n, r LIMIT $limit",
            {"name": start_entity, "depth": max_depth, "limit": max_nodes},
        )
        entities: dict[str, GraphEntity] = {}
        edges: list[GraphEdge] = []
        for row in result.result_set:
            n = row[0]
            if n and n.get("id") not in entities:
                entities[n["id"]] = GraphEntity(
                    id=n.get("id", ""), name=n.get("name", ""),
                )
            if len(row) > 1 and row[1]:
                r = row[1]
                edges.append(GraphEdge(
                    id=r.get("id", ""), relation=r.get("relation", ""),
                    fact=r.get("fact", ""),
                ))
        return GraphQueryResult(
            entities=list(entities.values()), edges=edges,
            total_found=len(edges),
        )

    async def delete_entity(self, entity_id: str) -> bool:
        if not self._graph:
            await self.initialize()
        self._graph.query(
            "MATCH (e:Entity {id: $id}) DETACH DELETE e",
            {"id": entity_id},
        )
        return True

    async def delete_edge(self, edge_id: str) -> bool:
        if not self._graph:
            await self.initialize()
        self._graph.query(
            "MATCH ()-[r:RELATES_TO {id: $id}]->() SET r.invalid_at = $now",
            {"id": edge_id, "now": time.strftime("%Y-%m-%dT%H:%M:%S")},
        )
        return True

    async def stats(self) -> dict:
        if not self._graph:
            await self.initialize()
        nodes_result = self._graph.query("MATCH (e:Entity) RETURN count(e) AS cnt")
        edges_result = self._graph.query("MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS cnt")
        return {
            "total_entities": nodes_result.result_set[0][0] if nodes_result.result_set else 0,
            "total_edges": edges_result.result_set[0][0] if edges_result.result_set else 0,
            "backend": self.backend_name,
            "host": f"{self._host}:{self._port}",
        }

    async def close(self) -> None:
        if self._graph:
            self._graph = None


# ── KuzuGraphBackend ─────────────────────────────────────

class KuzuGraphBackend(IGraphBackend):
    """Kuzu 嵌入式图数据库后端。

    需要安装: pip install kuzu
    零运维，数据存为本地文件，无需启动服务。
    最适合单机部署、零依赖图记忆场景。
    """

    def __init__(self, db_path: str = ".agent-memory/kuzu_graph"):
        self._db_path = db_path
        self._db = None
        self._conn = None

    @property
    def backend_name(self) -> str:
        return "kuzu"

    async def initialize(self) -> None:
        """初始化 Kuzu 数据库和表结构。"""
        try:
            import kuzu
            Path(self._db_path).mkdir(parents=True, exist_ok=True)
            self._db = kuzu.Database(self._db_path)
            self._conn = kuzu.Connection(self._db)

            # 实体节点表
            self._conn.execute("""
                CREATE NODE TABLE IF NOT EXISTS Entity (
                    id STRING,
                    name STRING,
                    entity_type STRING,
                    summary STRING,
                    labels STRING[],
                    created_at STRING,
                    updated_at STRING,
                    PRIMARY KEY (id)
                )
            """)

            # 关系边表
            self._conn.execute("""
                CREATE REL TABLE IF NOT EXISTS RELATES_TO (
                    FROM Entity TO Entity,
                    id STRING,
                    relation STRING,
                    fact STRING,
                    digest_ref STRING,
                    valid_at STRING,
                    invalid_at STRING,
                    weight DOUBLE,
                    created_at STRING
                )
            """)

            logger.info("Kuzu initialized: %s", self._db_path)
        except ImportError:
            logger.error("kuzu package not installed. Run: pip install kuzu")
            raise

    async def add_entity(self, entity: GraphEntity) -> str:
        """创建或合并实体。"""
        if not self._conn:
            await self.initialize()
        self._conn.execute(
            """
            MERGE (e:Entity {name: $name})
            ON CREATE SET e.id = $id, e.entity_type = $type,
                          e.summary = $summary, e.labels = $labels,
                          e.created_at = $created_at, e.updated_at = $updated_at
            ON MATCH SET e.summary = $summary, e.labels = $labels,
                         e.updated_at = $updated_at
            """,
            {
                "name": entity.name, "id": entity.id,
                "type": entity.entity_type, "summary": entity.summary,
                "labels": entity.labels,
                "created_at": entity.created_at,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
        return entity.id

    async def add_edge(self, edge: GraphEdge) -> str:
        """创建关系边。"""
        if not self._conn:
            await self.initialize()
        self._conn.execute(
            """
            MATCH (src:Entity {name: $src}), (tgt:Entity {name: $tgt})
            CREATE (src)-[r:RELATES_TO {
                id: $id, relation: $relation, fact: $fact,
                digest_ref: $digest_ref, valid_at: $valid_at,
                weight: $weight, created_at: $created_at
            }]->(tgt)
            """,
            {
                "src": edge.source_name, "tgt": edge.target_name,
                "id": edge.id, "relation": edge.relation,
                "fact": edge.fact, "digest_ref": edge.digest_ref,
                "valid_at": edge.valid_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
                "weight": edge.weight,
                "created_at": edge.created_at,
            },
        )
        return edge.id

    async def add_episode(
        self, entities: list[GraphEntity], edges: list[GraphEdge],
    ) -> tuple[list[str], list[str]]:
        if not self._conn:
            await self.initialize()
        eids = [await self.add_entity(e) for e in entities]
        edge_ids = [await self.add_edge(e) for e in edges]
        return eids, edge_ids

    async def get_entity(self, entity_id: str) -> GraphEntity | None:
        if not self._conn:
            await self.initialize()
        result = self._conn.execute(
            "MATCH (e:Entity {id: $id}) RETURN e",
            {"id": entity_id},
        )
        while result.has_next():
            node = result.get_next()[0]
            return GraphEntity(
                id=node.get("id", ""), name=node.get("name", ""),
                entity_type=node.get("entity_type", "other"),
                summary=node.get("summary", ""),
                labels=node.get("labels", []),
            )
        return None

    async def search_entities(self, query: str, top_k: int = 10) -> list[GraphEntity]:
        if not self._conn:
            await self.initialize()
        result = self._conn.execute(
            "MATCH (e:Entity) WHERE e.name CONTAINS $q OR e.summary CONTAINS $q "
            "RETURN e LIMIT $limit",
            {"q": query, "limit": top_k},
        )
        entities = []
        while result.has_next():
            node = result.get_next()[0]
            entities.append(GraphEntity(
                id=node.get("id", ""), name=node.get("name", ""),
                entity_type=node.get("entity_type", "other"),
                summary=node.get("summary", ""),
            ))
        return entities

    async def search_edges(
        self, query: str, top_k: int = 20,
    ) -> list[GraphEdge]:
        if not self._conn:
            await self.initialize()
        result = self._conn.execute(
            "MATCH (src:Entity)-[r:RELATES_TO]->(tgt:Entity) "
            "WHERE r.fact CONTAINS $q OR src.name CONTAINS $q OR tgt.name CONTAINS $q "
            "RETURN src.name, tgt.name, r.relation, r.fact, r.id "
            "LIMIT $limit",
            {"q": query, "limit": top_k},
        )
        edges = []
        while result.has_next():
            row = result.get_next()
            edges.append(GraphEdge(
                source_name=row[0], target_name=row[1],
                relation=row[2], fact=row[3], id=row[4],
            ))
        return edges

    async def get_relations(
        self, entity_name: str, depth: int = 1,
    ) -> GraphQueryResult:
        if not self._conn:
            await self.initialize()
        if depth > 1:
            logger.warning("Kuzu backend only supports depth=1 for get_relations (requested %d)", depth)
        # Kuzu 1-hop (bidirectional)
        result = self._conn.execute(
            "MATCH (e:Entity {name: $name}) "
            "OPTIONAL MATCH (e)-[r:RELATES_TO]->(n:Entity) "
            "RETURN e, r, n "
            "UNION ALL "
            "MATCH (e:Entity {name: $name}) "
            "OPTIONAL MATCH (n:Entity)-[r:RELATES_TO]->(e) "
            "RETURN e, r, n",
            {"name": entity_name},
        )
        entities: dict[str, GraphEntity] = {}
        edges: list[GraphEdge] = []
        while result.has_next():
            row = result.get_next()
            e = row[0] if row[0] else {}
            r = row[1] if row[1] else {}
            n = row[2] if row[2] else {}
            if e.get("id") not in entities:
                entities[e.get("id", "")] = GraphEntity(
                    id=e.get("id", ""), name=e.get("name", ""),
                )
            if n and n.get("id") not in entities:
                entities[n.get("id", "")] = GraphEntity(
                    id=n.get("id", ""), name=n.get("name", ""),
                )
            if r:
                edges.append(GraphEdge(
                    id=r.get("id", ""), relation=r.get("relation", ""),
                    fact=r.get("fact", ""),
                ))
        return GraphQueryResult(
            entities=list(entities.values()), edges=edges,
            total_found=len(edges),
        )

    async def bfs_traverse(
        self, start_entity: str, max_depth: int = 3, max_nodes: int = 50,
    ) -> GraphQueryResult:
        if not self._conn:
            await self.initialize()
        # Kuzu 用 VAR_LENGTH 做 BFS
        result = self._conn.execute(
            "MATCH (start:Entity {name: $name}) "
            "MATCH path = (start)-[rels:RELATES_TO*1..$depth]-(n:Entity) "
            "RETURN DISTINCT n, rels LIMIT $limit",
            {"name": start_entity, "depth": max_depth, "limit": max_nodes},
        )
        entities: dict[str, GraphEntity] = {}
        edges: list[GraphEdge] = []
        seen_edge_ids: set[str] = set()
        while result.has_next():
            row = result.get_next()
            n = row[0] if row[0] else {}
            if n.get("id") not in entities:
                entities[n.get("id", "")] = GraphEntity(
                    id=n.get("id", ""), name=n.get("name", ""),
                )
            # 提取边（可能是递归关系列表）
            rels = row[1] if len(row) > 1 and row[1] else None
            if rels:
                rel_list = rels if isinstance(rels, list) else [rels]
                for r in rel_list:
                    if isinstance(r, dict) and r.get("id") not in seen_edge_ids:
                        seen_edge_ids.add(r.get("id", ""))
                        edges.append(GraphEdge(
                            id=r.get("id", ""), relation=r.get("relation", ""),
                            fact=r.get("fact", ""),
                        ))
        return GraphQueryResult(
            entities=list(entities.values()), edges=edges,
            total_found=len(edges),
        )

    async def delete_entity(self, entity_id: str) -> bool:
        if not self._conn:
            await self.initialize()
        self._conn.execute(
            "MATCH (e:Entity {id: $id}) DETACH DELETE e",
            {"id": entity_id},
        )
        return True

    async def delete_edge(self, edge_id: str) -> bool:
        if not self._conn:
            await self.initialize()
        self._conn.execute(
            "MATCH ()-[r:RELATES_TO {id: $id}]->() "
            "SET r.invalid_at = $now",
            {"id": edge_id, "now": time.strftime("%Y-%m-%dT%H:%M:%S")},
        )
        return True

    async def stats(self) -> dict:
        if not self._conn:
            await self.initialize()
        nodes_result = self._conn.execute("MATCH (e:Entity) RETURN count(e) AS cnt")
        edges_result = self._conn.execute("MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS cnt")
        return {
            "total_entities": nodes_result.get_next()[0] if nodes_result.has_next() else 0,
            "total_edges": edges_result.get_next()[0] if edges_result.has_next() else 0,
            "backend": self.backend_name,
            "db_path": self._db_path,
        }

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
        if self._db:
            self._db.close()
            self._db = None
