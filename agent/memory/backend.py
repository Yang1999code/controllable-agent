"""agent/memory/backend.py — IMemoryBackend 实现。

文件系统记忆后端，L0-L4 分层存储，关键词检索。

参考：Hermes MemoryProvider / GenericAgent memory_management_sop.md
"""

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from agent.memory.store import MemoryStore
from agent.memory.index import MemoryIndex


# ── 数据类 ─────────────────────────────────────────────

@dataclass
class MemoryEntry:
    """一条记忆条目。"""

    content: str
    layer: str  # "L0" | "L1" | "L2" | "L3" | "L4"
    source: str
    timestamp: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    entry_id: str = ""


@dataclass
class SearchResult:
    entries: list[MemoryEntry]
    total_found: int


# ── ABC ───────────────────────────────────────────────

class IMemoryBackend(ABC):
    """记忆存储后端抽象。

    参考 Hermes MemoryProvider 接口设计。
    """

    @abstractmethod
    async def store(self, entry: MemoryEntry) -> str:
        """存储一条记忆，返回 entry_id。"""
        ...

    @abstractmethod
    async def search(self, query: str, top_k: int = 5) -> SearchResult:
        """关键词检索记忆。V1 用简单关键词匹配。"""
        ...

    @abstractmethod
    async def get(self, entry_id: str) -> MemoryEntry | None:
        """按 ID 获取记忆。"""
        ...

    @abstractmethod
    async def delete(self, entry_id: str) -> bool:
        """删除记忆。"""
        ...

    @abstractmethod
    async def list_by_layer(self, layer: str, limit: int = 50) -> list[MemoryEntry]:
        """按层级列出记忆。"""
        ...

    @abstractmethod
    async def on_pre_compress(self, current_tokens: int, max_tokens: int) -> str:
        """上下文压缩前的回调——返回压缩后的精简摘要。"""
        ...


# ── 文件系统实现 ─────────────────────────────────────

class FileSystemMemoryBackend(IMemoryBackend):
    """文件系统记忆后端（IMemoryBackend 实现）。

    核心公理（来自 GenericAgent）：
    1. No Execution, No Memory
    2. 神圣不可删改
    3. 禁止存储易变状态
    4. 最小充分指针

    存储路径：.agent-memory/{project}/
    """

    def __init__(self, store: MemoryStore, project: str = "default"):
        self._store = store
        self.index = MemoryIndex(store)
        self.project = project

    def _layer_path(self, layer: str, entry_id: str = "") -> str:
        """构建层级存储路径。"""
        layer_map = {
            "L0": f"{self.project}/index.md",
            "L1": f"{self.project}/l1_navigation/",
            "L2": f"{self.project}/l2_facts/",
            "L3": f"{self.project}/l3_experience/",
            "L4": f"{self.project}/l4_sessions/",
        }
        base = layer_map.get(layer, f"{self.project}/")
        return base + (entry_id if entry_id else "")

    async def store(self, entry: MemoryEntry) -> str:
        """存储一条记忆。"""
        if not entry.entry_id:
            entry.entry_id = uuid.uuid4().hex[:12]
        path = self._layer_path(entry.layer, f"{entry.entry_id}.md")
        content = f"""# {entry.layer} Memory Entry
Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry.timestamp))}
Source: {entry.source}
Tags: {', '.join(entry.tags)}

{entry.content}
"""
        await self._store.write(path, content)
        return entry.entry_id

    async def search(self, query: str, top_k: int = 5) -> SearchResult:
        """关键词检索。先 L1 → L2 → L3 → L4。"""
        files = await self.index.search_keywords(query)
        entries: list[MemoryEntry] = []
        for file_path in files[:top_k]:
            content = await self._store.read(file_path)
            if content:
                entries.append(MemoryEntry(
                    content=content,
                    layer=self._guess_layer(file_path),
                    source=file_path,
                    entry_id=file_path,
                ))
        return SearchResult(entries=entries, total_found=len(files))

    async def get(self, entry_id: str) -> MemoryEntry | None:
        """按 ID 获取。遍历所有层级。"""
        for layer in ["L1", "L2", "L3", "L4"]:
            path = self._layer_path(layer, f"{entry_id}.md")
            content = await self._store.read(path)
            if content:
                return MemoryEntry(
                    content=content,
                    layer=layer,
                    source=path,
                    entry_id=entry_id,
                )
        return None

    async def delete(self, entry_id: str) -> bool:
        """删除记忆。"""
        for layer in ["L1", "L2", "L3", "L4"]:
            path = self._layer_path(layer, f"{entry_id}.md")
            if await self._store.exists(path):
                return await self._store.delete(path)
        return False

    async def list_by_layer(self, layer: str, limit: int = 50) -> list[MemoryEntry]:
        """按层级列出记忆。"""
        base = self._layer_path(layer)
        entries: list[MemoryEntry] = []
        count = 0
        for f in await self._store.glob(f"{base}*.md"):
            if count >= limit:
                break
            content = await self._store.read(str(f.relative_to(self._store.base_path)))
            if content:
                entries.append(MemoryEntry(
                    content=content,
                    layer=layer,
                    source=str(f),
                ))
                count += 1
        return entries

    async def on_pre_compress(self, current_tokens: int, max_tokens: int) -> str:
        """上下文压缩前的回调。

        V1 简化：提取 L1+L2 摘要。
        V2 预留：LLM 驱动压缩。
        """
        parts = []
        l0 = await self.index.get_l0_index()
        if l0:
            parts.append(l0)
        return "\n".join(parts)

    @staticmethod
    def _guess_layer(file_path: str) -> str:
        """从文件路径猜测层级。"""
        if "l1_navigation" in file_path:
            return "L1"
        if "l2_facts" in file_path:
            return "L2"
        if "l3_experience" in file_path:
            return "L3"
        if "l4_sessions" in file_path:
            return "L4"
        return "L0"
