"""agent/memory/temporal.py — 时间旅行查询（Phase 3）。

实现双时序模型（Bi-Temporal），支持:
- valid_at: 事实何时生效
- invalid_at: 事实何时失效（不删除！保留历史）
- at_time 查询: 在特定时间点有效的实体和关系
- time_range 查询: 在时间段内成立的事实
- 变更追踪: 实体/关系的历史版本

参考：Graphiti EntityEdge 双时序设计
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent.memory.graph_backend import GraphEntity, GraphEdge


@dataclass
class TemporalSnapshot:
    """某一时刻的图快照。"""

    timestamp: str
    entities: list[GraphEntity] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class TemporalQueryEngine:
    """时间旅行查询引擎。

    在已有时序数据（valid_at / invalid_at）上提供：
    1. 时间点查询：某一时刻有哪些实体/关系有效
    2. 时间范围查询：某时间段内发生的变化
    3. 变更历史：实体/关系的版本演变
    """

    async def query_at_time(
        self,
        entities: list[GraphEntity],
        edges: list[GraphEdge],
        at_time: str,
    ) -> TemporalSnapshot:
        """查询特定时间点的有效实体和关系。

        参数：
        - entities: 所有实体
        - edges: 所有边（含时序信息）
        - at_time: ISO 8601 时间字符串，如 "2025-12-01T00:00:00"

        返回：
        - TemporalSnapshot: 该时刻有效的实体和边
        """
        at_dt = self._parse_time(at_time)
        if not at_dt:
            return TemporalSnapshot(timestamp=at_time)

        valid_entities = []
        valid_edges = []

        for entity in entities:
            created = self._parse_time(entity.created_at)
            if created and created <= at_dt:
                valid_entities.append(entity)

        for edge in edges:
            # 边需要在目标时间前创建、在目标时间前未失效
            created = self._parse_time(edge.created_at)
            valid_at = self._parse_time(edge.valid_at) or created
            invalid_at = self._parse_time(edge.invalid_at)

            if valid_at and valid_at <= at_dt:
                if invalid_at is None or invalid_at > at_dt:
                    valid_edges.append(edge)

        return TemporalSnapshot(
            timestamp=at_time,
            entities=valid_entities,
            edges=valid_edges,
            metadata={"total_entities": len(entities), "total_edges": len(edges)},
        )

    async def query_time_range(
        self,
        entities: list[GraphEntity],
        edges: list[GraphEdge],
        start_time: str,
        end_time: str,
    ) -> list[GraphEdge]:
        """查询时间段内创建或变更的关系。

        返回在 [start_time, end_time] 内创建或更新的边。
        """
        start_dt = self._parse_time(start_time)
        end_dt = self._parse_time(end_time)
        if not start_dt or not end_dt:
            return []

        results = []
        for edge in edges:
            created = self._parse_time(edge.created_at)
            if created and start_dt <= created <= end_dt:
                results.append(edge)
            # 也检查是否有变更（valid_at / invalid_at 在范围内）
            valid_at = self._parse_time(edge.valid_at)
            invalid_at = self._parse_time(edge.invalid_at)
            if valid_at and start_dt <= valid_at <= end_dt:
                results.append(edge)
            if invalid_at and start_dt <= invalid_at <= end_dt:
                results.append(edge)

        return results

    async def get_entity_history(
        self,
        entity_name: str,
        all_edges: list[GraphEdge],
    ) -> list[dict]:
        """获取实体的变更历史时间线。

        返回按时间排序的变更事件列表。
        """
        events: list[dict] = []

        for edge in all_edges:
            if edge.source_name == entity_name or edge.target_name == entity_name:
                created = edge.created_at
                if created:
                    events.append({
                        "type": "edge_created",
                        "timestamp": created,
                        "relation": edge.relation,
                        "fact": edge.fact,
                        "with_entity": (
                            edge.target_name if edge.source_name == entity_name
                            else edge.source_name
                        ),
                    })
                if edge.invalid_at:
                    events.append({
                        "type": "edge_invalidated",
                        "timestamp": edge.invalid_at,
                        "relation": edge.relation,
                        "fact": edge.fact,
                    })

        events.sort(key=lambda e: e["timestamp"])
        return events

    async def invalidate_edge(
        self,
        edge: GraphEdge,
        invalid_at: str | None = None,
    ) -> GraphEdge:
        """标记边为失效（软删除，保留历史）。

        返回更新后的边。
        """
        edge.invalid_at = invalid_at or time.strftime("%Y-%m-%dT%H:%M:%S")
        return edge

    @staticmethod
    def _parse_time(ts: str) -> datetime | None:
        """解析 ISO 8601 时间字符串。"""
        if not ts:
            return None
        try:
            # 处理多种格式
            ts = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            try:
                return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
            except (ValueError, TypeError):
                try:
                    return datetime.strptime(ts, "%Y-%m-%d")
                except (ValueError, TypeError):
                    return None


class TemporalIndex:
    """时序索引——高效时间范围查询。

    为边建立 valid_at 和 invalid_at 的排序索引，
    支持 O(log n) 时间点查询。
    """

    def __init__(self):
        self._edges_by_valid: list[tuple[str, GraphEdge]] = []
        self._edges_by_invalid: list[tuple[str, GraphEdge]] = []
        self._built = False

    async def build(self, edges: list[GraphEdge]) -> None:
        """从边列表构建时序索引。"""
        valid_entries = []
        invalid_entries = []
        for edge in edges:
            if edge.valid_at:
                valid_entries.append((edge.valid_at, edge))
            elif edge.created_at:
                valid_entries.append((edge.created_at, edge))
            if edge.invalid_at:
                invalid_entries.append((edge.invalid_at, edge))

        valid_entries.sort(key=lambda x: x[0])
        invalid_entries.sort(key=lambda x: x[0])

        self._edges_by_valid = valid_entries
        self._edges_by_invalid = invalid_entries
        self._built = True

    async def query_valid_at(self, at_time: str) -> list[GraphEdge]:
        """查询在 at_time 有效的边。"""
        if not self._built:
            return []

        at_dt = TemporalQueryEngine._parse_time(at_time)
        if not at_dt:
            return []

        result = []
        for ts, edge in self._edges_by_valid:
            ts_dt = TemporalQueryEngine._parse_time(ts)
            if ts_dt and ts_dt <= at_dt:
                # 检查是否在 at_time 前失效
                if edge.invalid_at:
                    inv_dt = TemporalQueryEngine._parse_time(edge.invalid_at)
                    if inv_dt and inv_dt <= at_dt:
                        continue
                result.append(edge)
            elif ts_dt:
                break  # 排序保证后续都大于 at_time

        return result

    async def query_created_between(
        self, start: str, end: str,
    ) -> list[GraphEdge]:
        """查询在 [start, end] 时间范围内创建的边。"""
        if not self._built:
            return []

        start_dt = TemporalQueryEngine._parse_time(start)
        end_dt = TemporalQueryEngine._parse_time(end)
        if not start_dt or not end_dt:
            return []

        result = []
        for ts, edge in self._edges_by_valid:
            ts_dt = TemporalQueryEngine._parse_time(ts)
            if ts_dt and start_dt <= ts_dt <= end_dt:
                result.append(edge)
            elif ts_dt and ts_dt > end_dt:
                break
        return result
