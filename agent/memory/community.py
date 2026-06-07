"""agent/memory/community.py — 社区检测（Phase 3）。

在实体关系图上运行 Louvain 聚类算法，自动发现主题群，
替代硬编码的四域分类 (conversation/profile/agent_view/task)。

核心算法：Louvain modularity maximization
- 本地无外部依赖实现（基于 igraph 概念的纯 Python 版本）
- 可选使用 python-igraph 或 networkx + community 加速

使用方式：
```python
comm = CommunityDetector(graph_backend)
communities = await comm.detect()
# → [Community(name="前端技术栈", entities=[...], summary="..."), ... ]
```

参考：Graphiti build_communities / Blondel et al. 2008
"""

import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Community:
    """一个检测到的社区（实体群）。"""

    id: str
    name: str = ""
    entities: list[str] = field(default_factory=list)  # 实体名称列表
    entity_ids: list[str] = field(default_factory=list)
    summary: str = ""  # LLM 生成的社区摘要
    size: int = 0
    modularity: float = 0.0


class CommunityDetector:
    """Louvain 社区检测器。

    在实体关系图上运行模块度最大化聚类，
    自动发现相关实体群。

    V1 实现：本地 Python Louvain（零新依赖）
    V2 预留：python-igraph 加速、Leiden 算法
    """

    def __init__(self):
        self._communities: dict[str, Community] = {}

    async def detect(
        self,
        entity_names: list[str],
        edges: list[tuple[str, str, float]],  # (source, target, weight)
        resolution: float = 1.0,
        max_iterations: int = 100,
    ) -> list[Community]:
        """运行 Louvain 社区检测。

        参数：
        - entity_names: 实体名称列表
        - edges: 边列表 (source, target, weight)
        - resolution: 分辨率参数（越高产生越多小社区）
        - max_iterations: 最大迭代次数

        返回：
        - Community 列表，按大小降序排列
        """
        if not entity_names:
            return []

        # 构建邻接表和图
        name_to_idx = {name: i for i, name in enumerate(entity_names)}
        n = len(entity_names)
        adj: list[dict[int, float]] = [defaultdict(float) for _ in range(n)]
        total_weight = 0.0

        for src, tgt, weight in edges:
            if src in name_to_idx and tgt in name_to_idx:
                si, ti = name_to_idx[src], name_to_idx[tgt]
                adj[si][ti] += weight
                adj[ti][si] += weight
                total_weight += weight

        if total_weight == 0:
            # 无边图——每个实体独立成社区
            return [
                Community(
                    id=f"c_{i:03d}",
                    name=entity_names[i],
                    entities=[entity_names[i]],
                    entity_ids=[entity_names[i]],
                    size=1,
                    modularity=0.0,
                )
                for i in range(n)
            ]

        # Louvain 初始化：每个节点自成一社区
        partition = list(range(n))
        community_nodes: dict[int, set[int]] = {i: {i} for i in range(n)}

        # 节点度数
        node_degrees = [sum(adj[i].values()) for i in range(n)]

        # 迭代优化
        for iteration in range(max_iterations):
            improved = False

            for node_i in range(n):
                current_comm = partition[node_i]

                # 计算邻居社区权重
                neighbor_comms: dict[int, float] = defaultdict(float)
                for neighbor_j, w in adj[node_i].items():
                    neighbor_comms[partition[neighbor_j]] += w

                # 移除当前节点
                community_nodes[current_comm].discard(node_i)

                # 计算最佳移动
                best_comm = current_comm
                best_delta_q = 0.0

                ki = node_degrees[node_i]

                for comm_id in set(list(neighbor_comms.keys()) + [current_comm]):
                    if comm_id == current_comm:
                        continue

                    # Louvain 模块度增量公式
                    sigma_in_comm = sum(
                        node_degrees[j] for j in community_nodes.get(comm_id, set())
                    )

                    delta_q = (
                        neighbor_comms[comm_id] / total_weight
                        - resolution * sigma_in_comm * ki / (2 * total_weight * total_weight)
                    )

                    if delta_q > best_delta_q:
                        best_delta_q = delta_q
                        best_comm = comm_id

                # 移动节点
                if best_comm != current_comm:
                    partition[node_i] = best_comm
                    if best_comm not in community_nodes:
                        community_nodes[best_comm] = set()
                    community_nodes[best_comm].add(node_i)
                    improved = True
                else:
                    # 留在原社区
                    community_nodes[current_comm].add(node_i)

            if not improved:
                break

        # 构建社区对象
        communities: list[Community] = []
        for comm_id in sorted(community_nodes.keys()):
            members = sorted(community_nodes[comm_id])
            if not members:
                continue
            community = Community(
                id=f"c_{comm_id:03d}",
                entities=[entity_names[i] for i in members],
                entity_ids=[entity_names[i] for i in members],
                size=len(members),
            )
            # 社区名 = 度数最高的实体名
            if members:
                max_degree = 0
                for mi in members:
                    if node_degrees[mi] > max_degree:
                        max_degree = node_degrees[mi]
                        community.name = entity_names[mi]
            communities.append(community)

        communities.sort(key=lambda c: c.size, reverse=True)
        self._communities = {c.id: c for c in communities}

        logger.info(
            "Louvain: %d entities → %d communities (iterations=%d)",
            n, len(communities), iteration + 1,
        )
        return communities

    async def summarize_communities(
        self,
        communities: list[Community],
        llm_summarizer=None,
    ) -> list[Community]:
        """为每个社区生成自然语言摘要。

        如果提供 llm_summarizer(community) -> str，则调用它；
        否则使用启发式摘要（实体名列表）。
        """
        for community in communities:
            if llm_summarizer:
                try:
                    community.summary = await llm_summarizer(community)
                except Exception as e:
                    logger.debug("community summary failed for %s: %s", community.id, e)
                    community.summary = self._heuristic_summary(community)
            else:
                community.summary = self._heuristic_summary(community)
        return communities

    async def get_community(self, community_id: str) -> Community | None:
        """获取已检测的社区。"""
        return self._communities.get(community_id)

    async def find_community_for_entity(self, entity_name: str) -> Community | None:
        """查找实体所属的社区。"""
        for community in self._communities.values():
            if entity_name in community.entities:
                return community
        return None

    @staticmethod
    def _heuristic_summary(community: Community) -> str:
        """启发式社区摘要。"""
        if not community.entities:
            return "空社区"
        if len(community.entities) <= 3:
            return f"包含: {', '.join(community.entities)}"
        top3 = community.entities[:3]
        return f"以 {community.name} 为核心，共 {community.size} 个实体: {', '.join(top3)}..."


class LabelPropagationDetector:
    """标签传播社区检测（备选算法）。

    比 Louvain 快，适合大规模图。不保证模块度最优。
    """

    async def detect(
        self,
        entity_names: list[str],
        edges: list[tuple[str, str]],
        max_iterations: int = 50,
    ) -> list[Community]:
        """标签传播聚类。"""
        if not entity_names:
            return []

        n = len(entity_names)
        name_to_idx = {name: i for i, name in enumerate(entity_names)}

        # 构建邻接表
        adj: list[list[int]] = [[] for _ in range(n)]
        for src, tgt, *_ in edges:
            if src in name_to_idx and tgt in name_to_idx:
                si, ti = name_to_idx[src], name_to_idx[tgt]
                adj[si].append(ti)
                adj[ti].append(si)

        # 初始化标签
        labels = list(range(n))

        for iteration in range(max_iterations):
            changed = 0
            # 随机顺序
            order = list(range(n))
            random.shuffle(order)

            for node_i in order:
                # 统计邻居标签频率
                label_counts: dict[int, int] = defaultdict(int)
                for neighbor_j in adj[node_i]:
                    label_counts[labels[neighbor_j]] += 1

                if not label_counts:
                    continue

                # 选择最常见的邻居标签
                best_label = max(label_counts, key=label_counts.get)
                if best_label != labels[node_i]:
                    labels[node_i] = best_label
                    changed += 1

            if changed == 0:
                logger.info("Label propagation converged after %d iterations", iteration + 1)
                break

        # 按标签分组
        label_groups: dict[int, list[int]] = defaultdict(list)
        for i, label in enumerate(labels):
            label_groups[label].append(i)

        communities = []
        for label, members in sorted(label_groups.items()):
            communities.append(Community(
                id=f"lp_{label:03d}",
                name=entity_names[members[0]] if members else "",
                entities=[entity_names[i] for i in members],
                entity_ids=[entity_names[i] for i in members],
                size=len(members),
            ))

        communities.sort(key=lambda c: c.size, reverse=True)
        return communities
