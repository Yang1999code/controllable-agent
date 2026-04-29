"""agent/memory/index.py — L0-L4 索引管理 + 中文分词搜索。

参考 GenericAgent memory_management_sop.md 层级定义：
- L0（全局索引）≤30行，1K tokens
- L1（导航层）场景关键词 → 存储定位
- L2（事实库）环境事实、路径、配置
- L3（任务经验）特定任务 SOP、避坑指南
- L4（原始会话）对话记录归档
"""

import logging
import re
from pathlib import Path

from agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class MemoryIndex:
    """L0-L4 记忆索引。"""

    def __init__(self, store: MemoryStore):
        self.store = store

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中文分词 + 英文分词统一入口。

        优先使用 jieba 分词（中文友好），jieba 不可用时回退到字符二元组。
        英文部分始终按空格和标点拆分。
        此方法为规范副本（canonical），AgentRuntime._tokenize() 须与此保持一致。
        """
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

    async def search_keywords(self, query: str) -> list[str]:
        """中文友好的关键词检索。V1 不做 embedding。

        分词：用 _tokenize() 统一分词（jieba 优先，字符二元组回退）
        匹配度：按命中次数排序，返回匹配文件路径列表
        """
        keywords = self._tokenize(query)
        if not keywords:
            return []

        scores: dict[str, int] = {}
        for f in await self.store.glob("**/*.md"):
            try:
                content = await self.store.read(str(f.relative_to(self.store.base_path)))
            except Exception:
                continue
            if not content:
                continue
            content_lower = content.lower()
            score = 0
            for kw in keywords:
                count = content_lower.count(kw)
                if count > 0:
                    score += count
            if score > 0:
                scores[str(f.relative_to(self.store.base_path))] = score

        return [f for f, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]

    async def update_l0_index(self, projects: list[dict]) -> None:
        """更新 L0 顶层索引。

        projects: [{"name": str, "summary": str}, ...]
        """
        lines = ["# Memory Index\n"]
        for p in projects:
            lines.append(f"- {p['name']}: {p['summary']}")
        await self.store.write("index.md", "\n".join(lines))

    async def get_l0_index(self) -> str:
        """获取 L0 索引内容。"""
        content = await self.store.read("index.md")
        return content or ""

    async def build_index(self) -> None:
        """扫描所有 .md 文件并建立 / 更新 L0 索引。

        扫描 .agent-memory 下所有项目目录，生成索引摘要。
        """
        entries: list[dict] = []
        for d in await self.store.glob("*/"):
            project_name = d.name
            project_md = d / "project.md"
            summary = ""
            if project_md.exists():
                try:
                    text = project_md.read_text(encoding="utf-8")
                    summary = text.split("\n")[0][:80]
                except Exception:
                    pass
            entries.append({"name": project_name, "summary": summary})
        await self.update_l0_index(entries)
