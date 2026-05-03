"""agent/memory/fact_store.py — digest/wiki MD 文件 CRUD + frontmatter 解析。

依赖 MemoryStore 做实际文件 I/O，本模块负责 frontmatter 解析与构建。
新记忆系统的核心存储引擎，与现有 L0-L4 层并行运行。
"""

import logging
import re
from dataclasses import dataclass, field

import frontmatter

from agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)

VALID_LEVELS = frozenset({"digest", "wiki"})


@dataclass(frozen=True)
class FactEntry:
    """digest/wiki 文件解析后的数据对象。不可变。

    注意：tags/domains/metadata 内部容器在构造后转为 tuple/frozenset，
    确保深层不可变。调用者应通过 FactStore 操作，不要直接修改。
    """

    id: str
    level: str
    metadata: dict = field(default_factory=dict)
    body: str = ""
    tags: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    timestamp: str = ""

    def __post_init__(self):
        object.__setattr__(self, "tags", tuple(self.tags))
        object.__setattr__(self, "domains", tuple(self.domains))


class FactStore:
    """digest/wiki 文件的 CRUD 操作。

    通过 MemoryStore 做文件读写，负责 frontmatter 的解析（读）和构建（写）。
    """

    def __init__(self, store: MemoryStore):
        self._store = store

    # ── 读操作 ──

    async def read(self, level: str, file_id: str) -> FactEntry | None:
        """读取并解析单个 digest/wiki 文件。"""
        self._validate_level(level)
        path = self._build_path(level, file_id)
        raw = await self._store.read(path)
        if raw is None:
            return None
        return self._parse_frontmatter(raw, file_id)

    async def list_ids(self, level: str) -> list[str]:
        """列出指定层级的所有文件 ID（不含 .md 后缀）。"""
        self._validate_level(level)
        prefix = f"{level}/"
        files = await self._store.glob(f"{level}/*.md")
        ids: list[str] = []
        for f in files:
            name = f.name
            if name.endswith(".md"):
                ids.append(name[:-3])
        return sorted(ids)

    async def read_all(self, level: str) -> list[FactEntry]:
        """读取指定层级所有文件。"""
        ids = await self.list_ids(level)
        entries: list[FactEntry] = []
        for fid in ids:
            entry = await self.read(level, fid)
            if entry is not None:
                entries.append(entry)
        return entries

    # ── 写操作 ──

    async def write(self, entry: FactEntry) -> None:
        """写入 digest/wiki 文件（创建或覆盖）。"""
        self._validate_level(entry.level)
        content = self._build_frontmatter(entry)
        path = self._build_path(entry.level, entry.id)
        await self._store.write(path, content)

    async def create_digest(
        self,
        digest_id: str,
        source_session: str,
        task_summary: str,
        domains: list[str],
        tags: list[str],
        facts: list[str],
        body: str,
        confidence: float = 0.9,
    ) -> FactEntry:
        """便捷方法：创建 digest 文件。"""
        entry = FactEntry(
            id=digest_id,
            level="digest",
            metadata={
                "source_session": source_session,
                "task_summary": task_summary,
                "facts": facts,
                "confidence": confidence,
            },
            body=body,
            tags=tags,
            domains=domains,
        )
        await self.write(entry)
        return entry

    async def create_wiki(
        self,
        wiki_id: str,
        title: str,
        source_digests: list[str],
        domains: list[str],
        tags: list[str],
        body: str,
    ) -> FactEntry:
        """便捷方法：创建 wiki 文件。"""
        entry = FactEntry(
            id=wiki_id,
            level="wiki",
            metadata={
                "title": title,
                "source_digests": source_digests,
            },
            body=body,
            tags=tags,
            domains=domains,
        )
        await self.write(entry)
        return entry

    # ── 删除 ──

    async def delete(self, level: str, file_id: str) -> bool:
        """删除指定文件。"""
        self._validate_level(level)
        path = self._build_path(level, file_id)
        return await self._store.delete(path)

    # ── 工具方法 ──

    async def exists(self, level: str, file_id: str) -> bool:
        """检查文件是否存在。"""
        path = self._build_path(level, file_id)
        return await self._store.exists(path)

    async def next_digest_id(self) -> str:
        """生成下一个 digest ID（d_NNN 格式，N 递增）。"""
        existing = await self.list_ids("digest")
        max_num = 0
        for fid in existing:
            m = re.match(r"^d_(\d+)$", fid)
            if m:
                max_num = max(max_num, int(m.group(1)))
        return f"d_{max_num + 1:03d}"

    def _build_path(self, level: str, file_id: str) -> str:
        """构建文件相对路径。"""
        return f"{level}/{file_id}.md"

    @staticmethod
    def _parse_frontmatter(raw: str, file_id: str) -> FactEntry | None:
        """解析 frontmatter 字符串为 FactEntry。解析失败返回 None。"""
        try:
            post = frontmatter.loads(raw)
        except Exception:
            logger.warning("Failed to parse frontmatter for %s", file_id)
            return None

        meta = dict(post.metadata)
        return FactEntry(
            id=meta.get("id", file_id),
            level=meta.get("level", ""),
            metadata=meta,
            body=post.content,
            tags=meta.get("tags", []),
            domains=meta.get("domains", []),
            timestamp=meta.get("timestamp", ""),
        )

    @staticmethod
    def _build_frontmatter(entry: FactEntry) -> str:
        """从 FactEntry 构建完整的 MD 字符串。"""
        meta = dict(entry.metadata)
        meta["id"] = entry.id
        meta["level"] = entry.level
        if entry.tags:
            meta["tags"] = list(entry.tags)
        if entry.domains:
            meta["domains"] = list(entry.domains)
        if entry.timestamp:
            meta["timestamp"] = entry.timestamp

        post = frontmatter.Post(entry.body, **meta)
        return frontmatter.dumps(post)

    @staticmethod
    def _validate_level(level: str) -> None:
        """校验 level 参数。"""
        if level not in VALID_LEVELS:
            raise ValueError(f"Invalid level '{level}', must be one of {VALID_LEVELS}")
