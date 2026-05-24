"""agent/memory/relation_store.py — 关系索引存储。

在现有 digest→wiki 文件记忆系统上，追加一个轻量级关系索引。
每行一条关系 JSON，不做图数据库，不做复杂查询引擎——
只负责：写入关系、按关键词查关系、通过关系找关联文档。

设计原则：
- 与 digest/wiki 存储完全解耦，互不干扰
- 最坏情况文件损坏 → 退化为纯关键词搜索，系统照常工作
- 零新依赖
"""

import json
import asyncio
import logging
from dataclasses import dataclass

from agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RelationEntry:
    """一条关系记录（对应图中的一条边）。"""

    source: str        # 源实体名
    target: str        # 目标实体名
    relation: str      # 关系类型，如 WORKS_ON, USES, CREATED_BY
    fact: str          # 自然语言描述
    digest_ref: str    # 来源 digest 文件路径
    timestamp: str     # ISO 时间戳


class RelationStore:
    """关系索引文件的读写。

    文件格式：relations.jsonl，每行一条 JSON。
    与 MemoryStore 使用同一存储目录，和 digest/wiki 同级。
    """

    def __init__(self, store: MemoryStore):
        self._store = store
        self._path = "relations.jsonl"
        self._lock = asyncio.Lock()

    async def append(self, entry: RelationEntry) -> None:
        """追加一条关系到索引。"""
        async with self._lock:
            line = json.dumps(
                {
                    "source": entry.source,
                    "target": entry.target,
                    "relation": entry.relation,
                    "fact": entry.fact,
                    "digest_ref": entry.digest_ref,
                    "timestamp": entry.timestamp,
                },
                ensure_ascii=False,
            )
            raw = await self._store.read(self._path) or ""
            await self._store.write(self._path, raw + line + "\n")

    async def append_batch(self, entries: list[RelationEntry]) -> None:
        """批量追加关系。"""
        if not entries:
            return
        async with self._lock:
            lines = []
            for e in entries:
                lines.append(
                    json.dumps(
                        {
                            "source": e.source,
                            "target": e.target,
                            "relation": e.relation,
                            "fact": e.fact,
                            "digest_ref": e.digest_ref,
                            "timestamp": e.timestamp,
                        },
                        ensure_ascii=False,
                    )
                )
            raw = await self._store.read(self._path) or ""
            await self._store.write(self._path, raw + "\n".join(lines) + "\n")

    async def search(self, query: str) -> list[RelationEntry]:
        """在关系索引中搜索匹配项。

        匹配 source、target、relation、fact 任一字段。
        """
        raw = await self._store.read(self._path)
        if not raw:
            return []

        results: list[RelationEntry] = []
        query_lower = query.lower()
        query_tokens = query_lower.split()

        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("skip malformed relation line")
                continue

            searchable = " ".join(
                [
                    data.get("source", ""),
                    data.get("target", ""),
                    data.get("relation", ""),
                    data.get("fact", ""),
                ]
            ).lower()

            # 全 query 匹配或任一 token 匹配
            if query_lower in searchable or any(
                token in searchable for token in query_tokens
            ):
                results.append(
                    RelationEntry(
                        source=data.get("source", ""),
                        target=data.get("target", ""),
                        relation=data.get("relation", ""),
                        fact=data.get("fact", ""),
                        digest_ref=data.get("digest_ref", ""),
                        timestamp=data.get("timestamp", ""),
                    )
                )

        return results

    async def find_related_docs(self, query: str) -> list[str]:
        """搜索关系索引，返回关联的文档引用路径（去重）。"""
        entries = await self.search(query)
        seen: set[str] = set()
        refs: list[str] = []
        for e in entries:
            if e.digest_ref and e.digest_ref not in seen:
                seen.add(e.digest_ref)
                refs.append(e.digest_ref)
        return refs

    async def get_relations_for_entity(self, entity_name: str) -> list[RelationEntry]:
        """获取某个实体的所有关联关系。"""
        return await self.search(entity_name)

    async def stats(self) -> dict:
        """获取关系索引统计信息。"""
        raw = await self._store.read(self._path)
        if not raw:
            return {"total_relations": 0, "file_exists": False}

        lines = [l for l in raw.strip().split("\n") if l.strip()]
        sources: set[str] = set()
        targets: set[str] = set()
        relation_types: set[str] = set()

        for line in lines:
            try:
                d = json.loads(line)
                if d.get("source"):
                    sources.add(d["source"])
                if d.get("target"):
                    targets.add(d["target"])
                if d.get("relation"):
                    relation_types.add(d["relation"])
            except json.JSONDecodeError:
                pass

        return {
            "total_relations": len(lines),
            "file_exists": True,
            "unique_entities": len(sources | targets),
            "relation_types": sorted(relation_types),
        }
