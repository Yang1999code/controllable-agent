"""agent/memory/domain_index.py — 四域管理 + 关键词倒排索引。

管理 domains/ 下四个域的 _index.md 文件，
以及 index.md 全局关键词倒排索引。
复用 MemoryIndex._tokenize() 的分词逻辑。
"""

import logging
import re
import time
from dataclasses import dataclass, field

from agent.memory.store import MemoryStore
from agent.memory.fact_store import FactStore, FactEntry

logger = logging.getLogger(__name__)

VALID_DOMAINS = frozenset({"conversation", "profile", "agent_view", "task"})


@dataclass
class IndexEntry:
    """倒排索引中的一条记录。"""

    keyword: str
    wiki_refs: list[str] = field(default_factory=list)
    digest_refs: list[str] = field(default_factory=list)


@dataclass
class DomainEntry:
    """域 _index.md 中的一条指针记录。"""

    topic: str
    level: str
    ref_path: str
    summary: str = ""


class DomainIndex:
    """四域目录管理 + 关键词倒排索引。

    通过 MemoryStore 做文件读写，通过 FactStore 解析 digest/wiki 内容。
    """

    def __init__(self, store: MemoryStore, fact_store: FactStore):
        self._store = store
        self._fact_store = fact_store

    # ── 初始化 ──

    async def initialize(self) -> None:
        """确保所有域目录和 _index.md 存在。"""
        for domain in VALID_DOMAINS:
            index_path = f"domains/{domain}/_index.md"
            if not await self._store.exists(index_path):
                content = self._build_domain_index(domain, [])
                await self._store.write(index_path, content)
        if not await self._store.exists("index.md"):
            await self._store.write("index.md", self._build_index_md({}))

    # ── 全局索引操作 ──

    async def rebuild_index(self) -> None:
        """从所有 digest/wiki 文件重建全局倒排索引。"""
        entries = await self._fact_store.read_all("digest") + \
                  await self._fact_store.read_all("wiki")
        index: dict[str, IndexEntry] = {}
        for entry in entries:
            self._add_entry_to_index(index, entry)
        await self._store.write("index.md", self._build_index_md(index))

    async def update_index_for(self, entry: FactEntry) -> None:
        """增量更新：为单个 FactEntry 更新索引。"""
        raw = await self._store.read("index.md")
        index = self._parse_index_md(raw or "")
        self._add_entry_to_index(index, entry)
        await self._store.write("index.md", self._build_index_md(index))

    async def remove_from_index(self, entry: FactEntry) -> None:
        """从索引中移除指定 FactEntry 的引用。"""
        raw = await self._store.read("index.md")
        if not raw:
            return
        index = self._parse_index_md(raw)
        ref_prefix = "wiki" if entry.level == "wiki" else "digest"
        ref = f"{ref_prefix}/{entry.id}.md"
        for ie in index.values():
            if ref in ie.wiki_refs:
                ie.wiki_refs.remove(ref)
            if ref in ie.digest_refs:
                ie.digest_refs.remove(ref)
        index = {kw: ie for kw, ie in index.items()
                 if ie.wiki_refs or ie.digest_refs}
        await self._store.write("index.md", self._build_index_md(index))

    async def search(self, query: str, top_k: int = 10) -> list[FactEntry]:
        """关键词检索。wiki 优先返回，digest 其次。"""
        keywords = self._tokenize(query)
        if not keywords:
            return []

        raw = await self._store.read("index.md")
        if not raw:
            return []
        index = self._parse_index_md(raw)

        wiki_scores: dict[str, int] = {}
        digest_scores: dict[str, int] = {}
        for kw in keywords:
            idx_entry = index.get(kw)
            if not idx_entry:
                continue
            for ref in idx_entry.wiki_refs:
                wiki_scores[ref] = wiki_scores.get(ref, 0) + 1
            for ref in idx_entry.digest_refs:
                digest_scores[ref] = digest_scores.get(ref, 0) + 1

        results: list[FactEntry] = []
        seen_ids: set[str] = set()

        for ref, _ in sorted(wiki_scores.items(), key=lambda x: x[1], reverse=True):
            file_id = ref.replace("wiki/", "").replace(".md", "")
            fact = await self._fact_store.read("wiki", file_id)
            if fact and fact.id not in seen_ids:
                results.append(fact)
                seen_ids.add(fact.id)
            if len(results) >= top_k:
                return results

        for ref, _ in sorted(digest_scores.items(), key=lambda x: x[1], reverse=True):
            file_id = ref.replace("digest/", "").replace(".md", "")
            fact = await self._fact_store.read("digest", file_id)
            if fact and fact.id not in seen_ids:
                results.append(fact)
                seen_ids.add(fact.id)
            if len(results) >= top_k:
                break

        return results

    # ── 域索引操作 ──

    async def get_domain_index(self, domain: str) -> list[DomainEntry]:
        """读取域的 _index.md，返回指针列表。"""
        self._validate_domain(domain)
        index_path = f"domains/{domain}/_index.md"
        raw = await self._store.read(index_path)
        if not raw:
            return []
        return self._parse_domain_index(raw)

    async def add_to_domain(
        self, domain: str, topic: str, level: str, ref_path: str, summary: str = "",
    ) -> None:
        """向域的 _index.md 添加一条指针记录。"""
        self._validate_domain(domain)
        entries = await self.get_domain_index(domain)
        entries.append(DomainEntry(topic=topic, level=level, ref_path=ref_path, summary=summary))
        index_path = f"domains/{domain}/_index.md"
        await self._store.write(index_path, self._build_domain_index(domain, entries))

    async def remove_from_domain(self, domain: str, ref_path: str) -> bool:
        """从域的 _index.md 移除一条指针记录。"""
        self._validate_domain(domain)
        entries = await self.get_domain_index(domain)
        original_len = len(entries)
        entries = [e for e in entries if e.ref_path != ref_path]
        if len(entries) == original_len:
            return False
        index_path = f"domains/{domain}/_index.md"
        await self._store.write(index_path, self._build_domain_index(domain, entries))
        return True

    # ── 域浏览 ──

    async def list_domains(self) -> list[str]:
        """返回有内容的域列表。"""
        result: list[str] = []
        for domain in VALID_DOMAINS:
            entries = await self.get_domain_index(domain)
            if entries:
                result.append(domain)
        return result

    async def browse_domain(self, domain: str) -> list[FactEntry]:
        """浏览指定域下的所有 digest/wiki（通过 _index.md 指针解析）。"""
        domain_entries = await self.get_domain_index(domain)
        results: list[FactEntry] = []
        seen: set[str] = set()
        for de in domain_entries:
            file_id = de.ref_path.split("/")[-1].replace(".md", "")
            level = de.level
            entry = await self._fact_store.read(level, file_id)
            if entry and entry.id not in seen:
                results.append(entry)
                seen.add(entry.id)
        return results

    # ── 内部方法 ──

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """分词：复用 MemoryIndex._tokenize() 的 canonical 实现。"""
        try:
            import jieba
            words = jieba.lcut(text.lower())
            return [w.strip() for w in words
                    if w.strip() and not re.match(r'^[\s\W_]+$', w)]
        except ImportError:
            pass

        clean = text.lower().strip()
        tokens: list[str] = []
        eng_words = re.findall(r'[a-z0-9]+', clean)
        tokens.extend(eng_words)
        chinese_chars = re.findall(r'[一-鿿]+', clean)
        for segment in chinese_chars:
            for i in range(len(segment) - 1):
                tokens.append(segment[i:i + 2])
            if len(segment) == 1:
                tokens.append(segment)
        return tokens

    def _add_entry_to_index(self, index: dict[str, IndexEntry], entry: FactEntry) -> None:
        """将 FactEntry 的 tags 和 body 关键词加入倒排索引。"""
        ref_prefix = "wiki" if entry.level == "wiki" else "digest"
        ref = f"{ref_prefix}/{entry.id}.md"

        all_keywords: set[str] = set()
        for tag in entry.tags:
            all_keywords.update(self._tokenize(tag))
        all_keywords.update(self._tokenize(entry.body))

        for kw in all_keywords:
            if kw not in index:
                index[kw] = IndexEntry(keyword=kw)
            if entry.level == "wiki":
                if ref not in index[kw].wiki_refs:
                    index[kw].wiki_refs.append(ref)
            else:
                if ref not in index[kw].digest_refs:
                    index[kw].digest_refs.append(ref)

    def _parse_index_md(self, content: str) -> dict[str, IndexEntry]:
        """解析 index.md 内容为 keyword -> IndexEntry 映射。"""
        index: dict[str, IndexEntry] = {}
        current_keyword = ""
        for line in content.split("\n"):
            if line.startswith("## ") and not line.startswith("## type"):
                current_keyword = line[3:].strip()
                index[current_keyword] = IndexEntry(keyword=current_keyword)
            elif current_keyword and line.startswith("- wiki:"):
                ref = self._extract_ref(line, "wiki:")
                if ref and current_keyword in index:
                    index[current_keyword].wiki_refs.append(ref)
            elif current_keyword and line.startswith("- digest:"):
                ref = self._extract_ref(line, "digest:")
                if ref and current_keyword in index:
                    index[current_keyword].digest_refs.append(ref)
        return index

    @staticmethod
    def _extract_ref(line: str, prefix: str) -> str:
        """从 '- wiki: [title](path)' 格式中提取 path。"""
        start = line.find(prefix)
        if start == -1:
            return ""
        match = re.search(r'\(([^)]+)\)', line[start:])
        return match.group(1) if match else ""

    def _build_index_md(self, entries: dict[str, IndexEntry]) -> str:
        """从 keyword -> IndexEntry 映射构建 index.md 内容。"""
        lines = [
            "---",
            "type: keyword_index",
            f'updated: "{time.strftime("%Y-%m-%dT%H:%M:%S")}"',
            "---",
            "",
        ]
        for kw in sorted(entries.keys()):
            ie = entries[kw]
            lines.append(f"## {kw}")
            for ref in ie.wiki_refs:
                file_id = ref.replace("wiki/", "").replace(".md", "")
                lines.append(f"- wiki: [{file_id}]({ref})")
            for ref in ie.digest_refs:
                file_id = ref.replace("digest/", "").replace(".md", "")
                lines.append(f"- digest: [{file_id}]({ref})")
            lines.append("")
        return "\n".join(lines)

    def _parse_domain_index(self, content: str) -> list[DomainEntry]:
        """解析域 _index.md 内容为 DomainEntry 列表。"""
        entries: list[DomainEntry] = []
        current_topic = ""
        for line in content.split("\n"):
            if line.startswith("## ") and not line.startswith("## type"):
                current_topic = line[3:].strip()
            elif current_topic and line.startswith("- "):
                rest = line[2:].strip()
                level = "wiki" if rest.startswith("wiki:") else "digest"
                ref = self._extract_ref(rest, ":")
                summary_match = re.search(r'\)\s*--\s*(.+)$', rest)
                summary = summary_match.group(1).strip() if summary_match else ""
                if ref:
                    entries.append(DomainEntry(
                        topic=current_topic, level=level,
                        ref_path=ref, summary=summary,
                    ))
        return entries

    def _build_domain_index(self, domain: str, entries: list[DomainEntry]) -> str:
        """从 DomainEntry 列表构建域 _index.md 内容。"""
        lines = [
            "---",
            "type: domain_index",
            f"domain: {domain}",
            f'updated: "{time.strftime("%Y-%m-%dT%H:%M:%S")}"',
            "---",
            "",
        ]
        topic_entries: dict[str, list[DomainEntry]] = {}
        for de in entries:
            topic_entries.setdefault(de.topic, []).append(de)
        for topic in topic_entries:
            lines.append(f"## {topic}")
            for de in topic_entries[topic]:
                file_id = de.ref_path.split("/")[-1].replace(".md", "")
                suffix = f" -- {de.summary}" if de.summary else ""
                lines.append(f"- {de.level}: [{file_id}]({de.ref_path}){suffix}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _validate_domain(domain: str) -> None:
        """校验 domain 参数。"""
        if domain not in VALID_DOMAINS:
            raise ValueError(f"Invalid domain '{domain}', must be one of {VALID_DOMAINS}")
