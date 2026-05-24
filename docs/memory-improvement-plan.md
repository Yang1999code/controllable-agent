# 记忆系统改进计划：从文件记忆到图结构记忆

> 基于对 [Graphiti](https://github.com/getzep/graphiti) 的深度分析，结合当前 Agent 记忆系统现状，
> 提出渐进式优化方案。

---

## 目录

1. [背景：为什么要改进](#1-背景为什么要改进)
2. [Graphiti 深度分析](#2-graphiti-深度分析)
3. [当前系统 vs Graphiti 对比](#3-当前系统-vs-graphiti-对比)
4. [改进方案总览](#4-改进方案总览)
5. [Phase 1：关系感知记忆（推荐立即实施）](#5-phase-1关系感知记忆推荐立即实施)
6. [Phase 2：可选图数据库后端](#6-phase-2可选图数据库后端)
7. [Phase 3：完全图记忆](#7-phase-3完全图记忆)
8. [决策参考](#8-决策参考)

---

## 1. 背景：为什么要改进

### 1.1 当前系统的记忆流程

```
用户对话 → TaskDetector（检测任务完成）
        → MemoryExtractor（调用 LLM 提取 digest）
        → FactStore（存为 .md 文件，带 frontmatter）
        → 累积 5 个 digest → 触发 wiki 合并
        → DomainIndex（更新四域索引 + 全局关键词倒排索引）
```

### 1.2 当前系统的优势（不能丢）

| 优势 | 说明 |
|------|------|
| 零外部依赖 | 纯文件存储，不需要数据库 |
| 部署简单 | clone 即用，不需要额外运维 |
| jieba 中文分词 | 专为中文优化的关键词检索 |
| digest → wiki 两层结构 | 原子事实 → 合并知识，结构清晰 |
| 四域分类 | conversation / profile / agent_view / task，手动但有用 |
| K-V Cache 友好 | 固定 system prompt，减少 LLM 推理成本 |

### 1.3 当前系统的核心弱点

1. **没有关系** — digest/wiki 是独立文档。"Alice" 和 "项目X" 各是一篇文档里的词，系统不知道它们之间有什么关系
2. **搜索碰运气** — jieba 分词匹配关键词，如果 wiki 里写的是"前端框架选型 React"，用户搜"技术栈"就搜不到
3. **没有去重** — 同一实体多次出现，存在不同的 digest 里，无法自动关联
4. **没有时间线** — 不知道某个事实什么时候成立、什么时候过时
5. **主题发现靠手工** — 4 域分类是硬编码的，无法自动发现新主题

### 1.4 改进目标

> 在不破坏现有架构、不加外部依赖的前提下，让记忆系统从"一堆独立文档"变成"文档之间有关系连线"。
> 搜索时沿着关系走，而不是靠关键词撞大运。

---

## 2. Graphiti 深度分析

### 2.1 项目概况

- **项目名**: Graphiti (by Zep Software)
- **仓库**: https://github.com/getzep/graphiti
- **定位**: 为 AI Agent 构建的时序感知知识图谱框架
- **许可证**: Apache 2.0
- **语言**: Python 3.12+

### 2.2 数据模型（5 层层级）

```
Episodes（原始数据输入）
  │  EpisodicNode: content, source, source_description, valid_at
  │
  └─→ Entities（LLM 提取的实体节点）
       │  EntityNode: name, name_embedding, summary, labels, attributes
       │
       └─→ Facts / Relationships（实体间关系边）
            │  EntityEdge: source_node_uuid → target_node_uuid
            │  fact, fact_embedding, valid_at, invalid_at, expired_at
            │
            └─→ Communities（社区聚类节点）
                 │  CommunityNode: name, name_embedding, summary
                 │
                 └─→ Sagas（Episode 分组节点）
                      SagaNode: incremental summarization with watermarks
```

### 2.3 管线流程

```
add_episode()
  ├─ extract_nodes()           — LLM 提取实体，返回 ExtractedEntities
  ├─ resolve_extracted_nodes() — embedding 相似度比对，去重合并
  ├─ extract_edges()           — LLM 提取实体间关系
  ├─ resolve_extracted_edges() — embedding 相似度比对，去重合并
  ├─ extract_attributes()      — LLM 提取实体属性
  └─ save to graph DB          — 写入 Neo4j/FalkorDB

每次 add_episode 触发 5+ 次 LLM 调用，通过 semaphore_gather 并发控制
```

### 2.4 搜索架构（核心亮点）

搜索分为**召回**和**排序**两个阶段，每个阶段可独立配置。

**召回（三路并行）**：

| 方法 | 说明 | 适用场景 |
|------|------|----------|
| BM25 | 全文关键词检索 | 精确匹配 |
| Cosine Similarity | 语义向量相似度 | 语义相近 |
| BFS | 图遍历扩散 | 从已知节点沿边探索 |

**排序器（五种可选）**：

| 排序器 | 算法 | 特点 |
|--------|------|------|
| RRF | Reciprocal Rank Fusion | 多路结果融合，默认选择 |
| MMR | Maximal Marginal Relevance | 去冗余，保证结果多样性 |
| Cross-Encoder | 交叉编码器精排 | 最准确但最慢 |
| Node Distance | 图最短路径距离 | 按与中心节点的距离排序 |
| Episode Mentions | 按被引用次数排序 | 高频事实优先 |

**预设配置（recipes）**：

```python
# 轻量：BM25 + 语义 + RRF 融合
COMBINED_HYBRID_SEARCH_RRF

# 去重优先：BM25 + 语义 + MMR
COMBINED_HYBRID_SEARCH_MMR

# 最高精度：BM25 + 语义 + BFS + Cross-Encoder 精排
COMBINED_HYBRID_SEARCH_CROSS_ENCODER
```

### 2.5 五大核心创新

#### 2.5.1 双时序模型（Bi-Temporal）

```python
class EntityEdge:
    valid_at: datetime    # 事实何时成立
    invalid_at: datetime  # 事实何时失效（不删除！）
    expired_at: datetime  # 实体何时过期
```

- 事实变更时，旧边标记 `invalid_at` 而不是删除
- 保留完整历史，可做时间旅行查询
- 解决了"事实已过时但不知道什么时候变的"问题

#### 2.5.2 Embedding 去重

```python
# extract_nodes.py 的 dedup 逻辑
resolve_extracted_nodes():
    1. 获取当前 episode 涉及的现有实体
    2. 用 embedding 相似度比对所有候选
    3. LLM 判断是否重复 → NodeDuplicate(id, duplicate_candidate_id)
    4. 重复的合并，新的创建
```

- 节点和边都有独立的去重流程
- 避免同一实体因不同表述被重复创建

#### 2.5.3 社区检测

```python
build_communities():
    1. 对实体节点运行 Leiden 聚类算法
    2. 对每个社区调用 LLM 生成摘要
    3. 创建 CommunityNode + HAS_MEMBER 边
```

- 自动发现相关实体群
- 社区摘要提供了宏观视角

#### 2.5.4 增量摘要（Saga 系统）

```python
class SagaNode:
    last_summarized_at: datetime              # 上次摘要的挂钟时间
    last_summarized_episode_valid_at: datetime # 上次摘要的 episode 时间水印

summarize_saga():
    # 只对 watermark 之后的新 episode 做增量摘要
    new_episodes = get_episodes_since(last_summarized_episode_valid_at)
    if len(new_episodes) >= threshold:
        generate_summary(existing_summary, new_episodes)
```

- 不需要全量重算，只处理增量
- 类似你的 digest → wiki 累积阈值触发

#### 2.5.5 边失效检测

```python
get_edge_invalidation_candidates():
    # 新边和旧边做 embedding 相似度比对
    # 内容矛盾 → 标记旧边 invalid_at
```

- 当新的关系与旧关系矛盾时，自动发现并标记
- 不依赖人工判断

### 2.6 多后端支持

| 后端 | 类型 | 特点 |
|------|------|------|
| Neo4j | 原生图数据库 | 默认选择，生态成熟 |
| FalkorDB | 图数据库（基于 Redis） | Docker 一键部署 |
| Kuzu | 嵌入式图数据库 | 零依赖，本地文件存储 |
| Amazon Neptune | 云原生图数据库 | AWS 托管 |

### 2.7 MCP Server 架构

Graphiti 提供独立的 MCP Server（[mcp_server/](research/graphiti/mcp_server/)）：

- 基于 FastMCP 框架
- 支持 3 种传输：stdio / SSE / Streamable HTTP
- 队列服务（QueueService）：每个 group_id 串行处理，避免竞态
- 暴露工具：add_memory、search_nodes、search_facts、delete_episode、clear_graph 等
- 支持自定义实体类型（Pydantic 模型动态生成）

---

## 3. 当前系统 vs Graphiti 对比

### 3.1 架构对比

| 维度 | Graphiti | 当前 Agent |
|------|----------|-----------|
| **存储引擎** | Neo4j / FalkorDB / Kuzu | 文件系统 (.md) |
| **数据模型** | 图（实体→关系→社区→Saga） | 平铺文档（digest → wiki） |
| **实体提取** | ✅ LLM 结构化输出 + 类型分类 | ✅ LLM digest 提取 |
| **关系提取** | ✅ source→target + fact + 类型 | ❌ 无（事实是扁平列表） |
| **去重机制** | ✅ Embedding 语义比对 | ⚠️ wiki 合并覆盖旧版 |
| **搜索方式** | BM25 + 余弦相似度 + BFS + Cross-Encoder | jieba 关键词匹配 |
| **时间追踪** | ✅ valid_at / invalid_at / expired_at | ❌ 仅有创建时间戳 |
| **社区发现** | ✅ Leiden 聚类 + LLM 摘要 | ✅ 手动四域分类 |
| **增量更新** | ✅ 实时增量 | ✅ digest 累积 → wiki 合并 |
| **中文优化** | 依赖 LLM 通用能力 | ✅ jieba 分词 |
| **外部依赖** | 图数据库 + LLM + Embedder | 仅文件系统 + LLM |
| **部署复杂度** | 高（需运行数据库） | 低（零外部依赖） |
| **配置灵活性** | 高（搜索配置 recipes） | 低（简单常量） |

### 3.2 搜索能力对比（关键差距）

```
当前搜索路径：
  用户问: "Alice 负责的项目用的什么技术栈?"
  → jieba 分词: ["alice", "负责", "项目", "技术栈"]
  → 遍历所有 .md 文件，匹配关键词
  → Wiki 里写的是 "前端框架选型为 React"，不含 "技术栈" 三个字
  → ❌ 搜不到或排名很低

Graphiti 搜索路径：
  用户问: "Alice 负责的项目用的什么技术栈?"
  → embedding 编码为向量
  → 并行:
      BM25 搜 "Alice 项目" → 找到 Alice 实体
      Vector 搜语义 → 找到 "前端框架选型"
      BFS 从 Alice 出发 → Alice-[WORKS_ON]→项目X-[USES]→React
  → RRF 融合排序 → ✅ 准确返回结果
```

### 3.3 关系建模对比

```
当前系统：
  digest_001.md:  "Alice 加入了团队"
  digest_002.md:  "项目 X 启动，使用 React"
  digest_003.md:  "Alice 负责前端开发"
  → 三条独立记录，不知道 Alice 和项目 X 的关系

Graphiti：
  (Alice:Person) --[JOINED]--> (团队:Team)
  (Alice:Person) --[WORKS_ON]--> (项目X:Project)
  (项目X:Project) --[USES]--> (React:Technology)
  → 图结构，可沿任意路径遍历查询
```

---

## 4. 改进方案总览

```
Phase 1 (立即, 2-3天)           Phase 2 (中期, 1-2周)            Phase 3 (长期)
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│ 不改存储架构          │    │ 增加可选图DB后端      │    │ 完全图记忆           │
│ 不改现有流程          │    │ 保持文件模式为默认     │    │                     │
│                     │    │                     │    │                     │
│ + Entity-Edge 提取   │───→│ + MemoryBackend 接口 │───→│ 社区检测替代域分类    │
│ + relations.jsonl   │    │ + Graphiti 适配器    │    │ 时间旅行查询         │
│ + 关系索引搜索        │    │ + 渐进式迁移          │    │ 多Agent共享知识图谱   │
│                     │    │                     │    │                     │
│ 零新依赖              │    │ 可选依赖 Graphiti     │    │                     │
└─────────────────────┘    └─────────────────────┘    └─────────────────────┘
```

---

## 5. Phase 1：关系感知记忆（推荐立即实施）

### 5.1 核心思路

> 在现有 digest 提取 prompt 中，让 LLM **顺便**输出实体和关系。
> 关系存为一个独立的轻量索引文件。搜索时先查关系找关联文档，再读文档。

**改了什么**：只改 extractor.py 的 prompt 模板 + 加一个 `.jsonl` 文件读写 + 搜索时多查一步。

**不改什么**：digest→wiki 流程、文件存储、jieba 搜索、四域分类，全部保持不变。

### 5.2 改动 1：扩展 digest 提取 prompt

在 `extractor.py` 的 `_DIGEST_SYSTEM_PROMPT` 中，输出格式增加两个字段：

```python
# agent/memory/extractor.py

_DIGEST_SYSTEM_PROMPT = """你是一个记忆提取助手。从对话历史中提取关键事实，生成结构化摘要。

输出格式（严格 JSON）：
{
  "task_summary": "一句话概括任务",
  "domains": ["conversation"],
  "tags": ["关键词1", "关键词2"],
  "facts": ["事实1", "事实2"],
  "body": "## 任务摘要\\n\\n详细的 Markdown 摘要内容",

  // ── 新增 ──
  "entities": [
    {"name": "实体名", "type": "person|project|technology|organization|event|other"},
  ],
  "relations": [
    {
      "source": "源实体名",
      "target": "目标实体名",
      "relation": "关系类型，如 WORKS_ON, USES, CREATED_BY, BELONGS_TO",
      "fact": "用自然语言描述这个关系"
    }
  ]
}

规则：
- domains 只能是: conversation, profile, agent_view, task
- tags 使用中文或英文均可，3-8 个
- facts 是原子化的事实列表（每条一个独立事实）
- body 是 Markdown 格式的完整摘要
- entities 只提取有明确指代的人、项目、技术、组织等，不要提取泛指概念
- relations 只描述有明确信息支撑的关系，不要臆造
- 如果对话中没有足够明确的实体或关系，返回空数组
- 只提取有价值的事实，忽略寒暄和闲聊
"""
```

### 5.3 改动 2：加关系索引存储

新建或扩展，负责 `relations.jsonl` 的追加写入和查询：

```python
# agent/memory/relation_store.py — 关系索引存储

import json
import asyncio
from pathlib import Path
from dataclasses import dataclass

from agent.memory.store import MemoryStore


@dataclass(frozen=True)
class RelationEntry:
    """一条关系记录。"""
    source: str
    target: str
    relation: str
    fact: str
    digest_ref: str
    timestamp: str


class RelationStore:
    """关系索引文件的读写。"""

    def __init__(self, store: MemoryStore):
        self._store = store
        self._path = "relations.jsonl"
        self._lock = asyncio.Lock()

    async def append(self, entry: RelationEntry) -> None:
        """追加一条关系到索引。"""
        async with self._lock:
            line = json.dumps({
                "source": entry.source,
                "target": entry.target,
                "relation": entry.relation,
                "fact": entry.fact,
                "digest_ref": entry.digest_ref,
                "timestamp": entry.timestamp,
            }, ensure_ascii=False)
            raw = await self._store.read(self._path) or ""
            await self._store.write(self._path, raw + line + "\n")

    async def search(self, query: str) -> list[RelationEntry]:
        """在关系中搜索匹配项。返回匹配到的关系 + 关联的文档引用。"""
        raw = await self._store.read(self._path)
        if not raw:
            return []

        results: list[RelationEntry] = []
        query_lower = query.lower()
        for line in raw.strip().split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            # 匹配 source, target, fact, relation 任一字段
            searchable = " ".join([
                data.get("source", ""),
                data.get("target", ""),
                data.get("relation", ""),
                data.get("fact", ""),
            ]).lower()

            if query_lower in searchable or any(
                token in searchable for token in query_lower.split()
            ):
                results.append(RelationEntry(
                    source=data.get("source", ""),
                    target=data.get("target", ""),
                    relation=data.get("relation", ""),
                    fact=data.get("fact", ""),
                    digest_ref=data.get("digest_ref", ""),
                    timestamp=data.get("timestamp", ""),
                ))

        return results

    async def get_relations_for_entity(self, entity_name: str) -> list[RelationEntry]:
        """获取某个实体的所有关系。"""
        return await self.search(entity_name)

    async def find_related_docs(self, query: str) -> list[str]:
        """搜索关系索引，返回关联的文档引用路径列表。"""
        entries = await self.search(query)
        return list(set(e.digest_ref for e in entries if e.digest_ref))
```

### 5.4 改动 3：搜索时多加一步

在现有 `MemoryIndex.search_keywords()` 之外，增加关系增强搜索：

```python
# 调用方（AgentRuntime 或 Web Server 中）

async def search_memory(query: str, index: MemoryIndex, relation_store: RelationStore) -> list[str]:
    """
    混合搜索：关键词匹配 + 关系索引增强
    """
    # 步骤 1：现有逻辑 — jieba 关键词匹配
    keyword_results = await index.search_keywords(query)

    # 步骤 2：新增 — 关系索引查找关联文档
    related_docs = await relation_store.find_related_docs(query)

    # 步骤 3：合并排序
    # 策略：关系命中的文档排在关键词结果前面（关系匹配更有信息量）
    seen = set()
    merged: list[str] = []

    for doc in related_docs:
        if doc not in seen:
            merged.append(doc)
            seen.add(doc)

    for doc_path in keyword_results:
        doc_str = str(doc_path) if hasattr(doc_path, '__str__') else doc_path
        if doc_str not in seen:
            merged.append(doc_str)
            seen.add(doc_str)

    return merged
```

### 5.5 改动 4：MemoryExtractor 接入 RelationStore

在 `MemoryExtractor.check_and_extract()` 中，digest 保存后追加关系索引更新：

```python
# 伪代码，展示改动点

async def check_and_extract(self, messages, session_id, turn_count):
    # ... 现有逻辑：提取 digest ...

    # ── 新增 ──
    entities = result.get("entities", [])
    relations = result.get("relations", [])

    if relations and self._relation_store:
        for rel in relations:
            await self._relation_store.append(RelationEntry(
                source=rel["source"],
                target=rel["target"],
                relation=rel["relation"],
                fact=rel["fact"],
                digest_ref=f"digest/{digest_id}.md",
                timestamp=datetime.now().isoformat(),
            ))

    # ... 现有逻辑：阈值检查 → wiki 合并 ...
```

### 5.6 影响范围

| 文件 | 改动 | 行数 |
|------|------|------|
| `agent/memory/extractor.py` | 修改 `_DIGEST_SYSTEM_PROMPT`，输出增加 entities/relations | ~10 行 |
| `agent/memory/relation_store.py` | **新建**，关系索引读写 | ~80 行 |
| `web_server.py` 或 `AgentRuntime` | 搜索时调用 RelationStore | ~15 行 |
| `agent/memory/__init__.py` | 导出 RelationStore | ~3 行 |

**总计 ~110 行，零新依赖。**

### 5.7 预期效果

```
改前：
  "Alice 负责的项目用什么技术栈?"
  → jieba 分词 → 匹配到含有 Alice 的 digest → ❌ 技术栈信息在另一篇 wiki 里

改后：
  "Alice 负责的项目用什么技术栈?"
  → 关系索引命中: Alice-[WORKS_ON]→项目X, 项目X-[USES]→React
  → 直接定位到项目X的 wiki → ✅ 读到完整答案
```

---

## 6. Phase 2：可选图数据库后端

### 6.1 出发点

当 Phase 1 运行一段时间后，`relations.jsonl` 可能会很大（成千上万条关系），
文件的线性扫描效率下降。此时可以引入图数据库作为可选后端。

### 6.2 抽象 MemoryBackend 接口

```python
# agent/memory/backend.py

from typing import Protocol, runtime_checkable

@runtime_checkable
class MemoryBackend(Protocol):
    """记忆存储后端抽象接口。"""

    async def add_entity(self, name: str, summary: str, embedding: list[float], ...) -> str: ...
    async def add_edge(self, source: str, target: str, fact: str, valid_at, ...) -> str: ...
    async def search_entities(self, query: str, embedding: list[float]) -> list[...]: ...
    async def search_edges(self, query: str, embedding: list[float]) -> list[...]: ...
    async def get_relations(self, entity_name: str) -> list[...]: ...
```

两个实现：

```python
class FileMemoryBackend:
    """默认实现：基于现有文件系统 + relations.jsonl"""

class GraphitiMemoryBackend:
    """可选实现：包装 Graphiti，使用 Neo4j/FalkorDB/Kuzu"""
    def __init__(self, graphiti_client: Graphiti):
        self._g = graphiti_client

    async def add_entity(self, ...):
        return await self._g.add_episode(...)  # 走 Graphiti 管线
```

### 6.3 启动时选择后端

```bash
# 默认文件模式
python web_server.py

# 图数据库模式（需要 Neo4j 运行中）
python web_server.py --memory-backend neo4j --neo4j-uri bolt://localhost:7687

# 零依赖图模式（Kuzu 嵌入式，本地文件）
python web_server.py --memory-backend kuzu
```

### 6.4 迁移策略

1. 默认保持文件后端，现有用户不受影响
2. 首次使用图后端时，自动从现有 `.agent-memory/` 导入所有 digest/wiki
3. 提供导出命令，可从图后端导出回文件格式

---

## 7. Phase 3：完全图记忆

### 7.1 社区检测替代手动域分类

- 用 Graphiti 的 Leiden 聚类自动发现主题群
- 替代硬编码的 conversation / profile / agent_view / task 四域
- 社区数量动态变化，不再限于 4 个

### 7.2 时间旅行查询

```
用户: "2025年12月时，这个项目的技术选型是什么？"
→ 查询 at_time='2025-12-01' 的有效事实
→ 只返回当时 valid 的边，过滤掉后来被标记 invalid 的
```

### 7.3 多 Agent 共享知识图谱

- 不同 Agent 实例读写同一个图数据库
- 共享实体和关系，避免重复提取
- 适用于多 Agent 协作场景

---

## 8. 决策参考

### 8.1 什么时候升级到 Phase 2？

满足以下任一条件时考虑：

- `relations.jsonl` 超过 10000 行，搜索变慢
- 需要多 Agent 共享记忆
- 需要时间旅行查询能力
- 现有搜索精度已无法满足需求

### 8.2 为什么不直接跳到 Phase 3？

1. **Graphiti 引入运维成本**：需要管理 Neo4j/FalkorDB 实例
2. **当前系统够用**：文件记忆对单用户 Agent 来说足够好
3. **渐进式改进更安全**：每步都可验证、可回滚
4. **先验证核心假设**：Phase 1 验证"关系增强搜索是否有价值"，

### 8.3 Graphiti 的局限（来自代码审查发现）

| 问题 | 影响 |
|------|------|
| 每次 add_episode 触发 5+ 次 LLM 调用 | 成本高、延迟大 |
| dedup 依赖 embedding 质量 | 中文 embedding 可能不够好 |
| jieba 分词无替代 | Graphiti 没有中文专用分词 |
| Neo4j 需要 5.26+ | 版本要求不低，升级麻烦 |
| 社区检测需要全量数据 | 增量社区更新不完善 |
| 小模型可能出 schema 验证错 | 建议用支持 structured output 的模型 |

---

## 附录 A：关键文件索引

### Graphiti 源码（本地 research/ 目录）

| 文件 | 内容 |
|------|------|
| `graphiti_core/graphiti.py` | 主入口，Graphiti 类，add_episode/search/build_communities |
| `graphiti_core/nodes.py` | 5 种节点类型定义 + CRUD |
| `graphiti_core/edges.py` | 5 种边类型定义 + CRUD + 时序字段 |
| `graphiti_core/search/search.py` | 混合搜索主逻辑，4 种搜索 × 5 种排序器 |
| `graphiti_core/search/search_config.py` | 搜索配置模型 |
| `graphiti_core/search/search_config_recipes.py` | 预设搜索配置（RRF/MMR/Cross-Encoder） |
| `graphiti_core/search/search_utils.py` | 搜索底层实现 + RRF/MMR/距离排序 |
| `graphiti_core/prompts/extract_nodes.py` | 实体提取 prompt 模板 |
| `graphiti_core/prompts/extract_edges.py` | 关系提取 prompt 模板 |
| `graphiti_core/prompts/dedupe_nodes.py` | 实体去重 prompt 模板 |
| `graphiti_core/llm_client/client.py` | LLM 客户端基类，缓存 + 重试 |
| `mcp_server/src/graphiti_mcp_server.py` | MCP Server 实现 |

### 当前 Agent 记忆系统

| 文件 | 内容 |
|------|------|
| `agent/memory/store.py` | MemoryStore — 文件系统读写 |
| `agent/memory/extractor.py` | MemoryExtractor — LLM 记忆提取引擎 |
| `agent/memory/index.py` | MemoryIndex — L0-L4 索引 + jieba 搜索 |
| `agent/memory/domain_index.py` | DomainIndex — 四域管理 + 关键词倒排索引 |
| `agent/memory/fact_store.py` | FactStore — digest/wiki 文件 CRUD |
| `agent/memory/task_detector.py` | TaskDetector — 任务完成检测 |
| `agent/memory/dedup.py` | — 去重逻辑 |

---

## 附录 B：Graphiti prompt 设计关键摘录

### 实体提取规则（extract_nodes.py）

```
NEVER extract any of the following:
- Pronouns (you, me, I, he, she, they, we, us, it, them, him, her, this, that, those)
- Abstract concepts or feelings (joy, balance, growth, resilience, happiness, passion)
- Generic common nouns or bare object words (day, life, people, work, stuff, things)
- Generic media/content nouns (photo, pic, picture, image, video, post, story)
- Generic event/activity nouns (event, game, meeting, class, workshop)
- Broad institutional nouns (government, school, company, team, office)
```

### 关系提取（extract_edges.py）

```
- source_entity_name: 源实体名称（必须来自 ENTITIES 列表）
- target_entity_name: 目标实体名称（必须来自 ENTITIES 列表）
- relation_type: 关系类型，SCREAMING_SNAKE_CASE (e.g., WORKS_AT, LIVES_IN)
- fact: 自然语言描述
- valid_at: ISO 8601 时间戳（事实成立时间）
- invalid_at: ISO 8601 时间戳（事实失效时间）
```

### 去重判断（dedupe_nodes.py）

```
NodeDuplicate:
  id: 新实体的 id
  name: 新实体名称（如确认为重复，用最完整表述作为名称）
  duplicate_candidate_id: 匹配到的已有实体 id，-1 表示不重复
```

---

> 文档版本: v1.1 | 创建日期: 2026-05-24 | 更新: Phase 1 已实施

---

## 附录 C：Phase 1 实施记录

### 实施日期

2026-05-24

### 变更清单

| # | 文件 | 操作 | 说明 |
|---|------|------|------|
| 1 | `agent/memory/relation_store.py` | **新建** | 关系索引存储（~140 行） |
| 2 | `agent/memory/extractor.py` | 修改 | 更新 digest prompt 增加 entities/relations 字段；集成 RelationStore |
| 3 | `agent/memory/__init__.py` | 不变 | 包描述文件，无需修改 |
| 4 | `my_agent.py` | 修改 | 新增导出 `RelationStore`, `RelationEntry` |
| 5 | `web_server.py` | 修改 | 创建 RelationStore 并注入 MemoryExtractor；status 接口增加 relations 字段 |
| 6 | `app/cli.py` | 修改 | 创建 RelationStore 并注入 MemoryExtractor |

### 实际代码量

~180 行纯代码（含 relation_store.py 140 行 + 各文件集成 ~40 行），零新依赖。

### 向后兼容性

| 场景 | 行为 |
|------|------|
| RelationStore 为 None | `_save_relations()` 静默跳过，行为与改前完全一致 |
| LLM 未返回 entities/relations | json.get() 返回空数组，不影响 digest 创建 |
| relations.jsonl 损坏 | `RelationStore.search()` 跳过损坏行，不抛异常 |
| relations.jsonl 不存在 | `MemoryStore.read()` 返回 None，退化为空结果 |

### 改动详情

#### 1. RelationStore (`agent/memory/relation_store.py`)

核心数据结构：

```python
@dataclass(frozen=True)
class RelationEntry:
    source: str        # 源实体名
    target: str        # 目标实体名
    relation: str      # 关系类型（WORKS_ON, USES, CREATED_BY...）
    fact: str          # 自然语言描述
    digest_ref: str    # 来源 digest 路径（如 digest/001.md）
    timestamp: str     # ISO 时间戳
```

主要方法：

- `append(entry)` — 追加单条关系
- `append_batch(entries)` — 批量追加（高效的 lock 只获取一次）
- `search(query)` — 按关键词搜索，匹配 source/target/relation/fact
- `find_related_docs(query)` — 搜索并返回关联文档引用（去重）
- `get_relations_for_entity(name)` — 获取某实体的所有关系
- `stats()` — 统计信息（关系数、实体数、关系类型）

#### 2. Prompt 变更 (`agent/memory/extractor.py`)

输出格式新增两个字段（**向下兼容**，旧代码忽略多余字段）：

```json
{
  // ... 原有 6 个字段不变 ...
  "entities": [
    {"name": "实体名", "type": "person|project|technology|organization|event|other"}
  ],
  "relations": [
    {
      "source": "源实体名（必须在 entities 列表中出现过）",
      "target": "目标实体名（必须在 entities 列表中出现过）",
      "relation": "关系类型，如 WORKS_ON, USES, CREATED_BY, BELONGS_TO, MANAGES",
      "fact": "用自然语言描述这个关系"
    }
  ]
}
```

#### 3. 集成点

```
web_server.py / cli.py
  │
  ├─ MemoryStore ──── same as before
  ├─ FactStore ────── same as before
  ├─ DomainIndex ──── same as before
  ├─ RelationStore ── NEW: uses same MemoryStore
  └─ MemoryExtractor
       │
       ├─ extract_digest() → New: _save_relations()
       └─ extract_from_orchestration() → New: _save_relations()
```

### 验证方式

1. **启动 Web 服务**：`python web_server.py`，访问 `/status` 确认 `"relations": true`
2. **启动 CLI**：`python -m app.cli`，确认 `[记忆] 自动提取引擎已启用`
3. **发送对话**：通过 Web UI 或 CLI 发送有实体关系的对话
4. **检查文件**：`cat ~/.agent-memory/relations.jsonl` 确认关系已写入
5. **最坏情况测试**：删除 `relations.jsonl`，系统照常工作（退化为关键词搜索）

### 已知限制

- `search()` 是线性扫描，关系数 >10000 时可能变慢（Phase 2 解决）
- 不做实体合并/去重（简单场景不需要，Phase 3 解决）
- 不追踪关系的时间有效性（valid_at / invalid_at），仅记录创建时间戳
