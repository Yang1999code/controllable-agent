"""agent/memory/dedup.py — LLM 去重/冲突判断。

判断新 digest 与已有 wiki 内容是否重复或冲突，
避免记忆膨胀和信息矛盾。
"""

import json
import logging
from dataclasses import dataclass
from enum import Enum

from ai.types import Message
from ai.provider import IModelProvider
from agent.memory.fact_store import FactStore, FactEntry

logger = logging.getLogger(__name__)


class DuplicationVerdict(str, Enum):
    """去重判断结果。"""

    NEW = "new"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    SUPPLEMENT = "supplement"


@dataclass(frozen=True)
class DeduplicationResult:
    """去重结果。"""

    verdict: DuplicationVerdict
    confidence: float
    reason: str
    merged_body: str = ""


_DEDUP_SYSTEM_PROMPT = """你是一个记忆去重助手。判断新信息与已有知识的关系。

输出格式（严格 JSON）：
{
  "verdict": "new|duplicate|conflict|supplement",
  "confidence": 0.9,
  "reason": "判断理由",
  "merged_body": ""
}

规则：
- "new": 新信息与已有知识无关，直接写入
- "duplicate": 新信息已完全包含在已有知识中，跳过
- "conflict": 新信息与已有知识矛盾，merged_body 中给出更新后的内容
- "supplement": 新信息是对已有知识的补充，merged_body 中给出合并后的内容
- confidence 范围 0.0-1.0
- 只在 verdict 为 conflict 或 supplement 时填写 merged_body"""


class Deduplicator:
    """LLM 去重判断器。

    对比新 digest 与已有 wiki，判断是否需要写入。
    """

    def __init__(self, provider: IModelProvider, fact_store: FactStore):
        self.provider = provider
        self.fact_store = fact_store

    async def check_digest(
        self,
        new_digest: FactEntry,
        existing_wiki: FactEntry,
    ) -> DeduplicationResult:
        """判断新 digest 与已有 wiki 的关系。

        参数:
            new_digest: 新提取的 digest
            existing_wiki: 已有的 wiki 知识页
        """
        comparison = (
            f"## 已有知识\n\n{existing_wiki.body}\n\n"
            f"## 新信息\n\n{new_digest.body}"
        )

        llm_messages = [
            Message(role="user", content=comparison),
        ]

        try:
            events = await self.provider.chat(
                messages=llm_messages,
                tools=[],
                system_prompt=_DEDUP_SYSTEM_PROMPT,
                max_tokens=2000,
            )
        except Exception as e:
            logger.error("LLM dedup check failed: %s", e)
            return DeduplicationResult(
                verdict=DuplicationVerdict.NEW,
                confidence=0.3,
                reason=f"llm_error: {e}",
            )

        response_text = ""
        for event in events:
            if event.type == "text_delta":
                response_text += event.content
            elif event.type == "error":
                return DeduplicationResult(
                    verdict=DuplicationVerdict.NEW,
                    confidence=0.3,
                    reason=f"llm_error: {event.error}",
                )

        return self._parse_response(response_text)

    async def find_conflicting_wikis(
        self,
        new_digest: FactEntry,
    ) -> list[tuple[FactEntry, DeduplicationResult]]:
        """查找与新 digest 可能有冲突的 wiki 页面。

        参数:
            new_digest: 新提取的 digest
        返回:
            匹配的 (wiki, dedup_result) 列表
        """
        if not new_digest.tags:
            return []

        results = []
        all_wikis = await self.fact_store.read_all("wiki")

        for wiki in all_wikis:
            tag_overlap = set(new_digest.tags) & set(wiki.tags)
            if not tag_overlap:
                continue

            dedup_result = await self.check_digest(new_digest, wiki)
            if dedup_result.verdict != DuplicationVerdict.NEW:
                results.append((wiki, dedup_result))

        return results

    @staticmethod
    def _parse_response(text: str) -> DeduplicationResult:
        """解析 LLM 去重判断响应。"""
        text = text.strip()

        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1:]
            text = text.rstrip("`").strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                try:
                    parsed = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    return DeduplicationResult(
                        verdict=DuplicationVerdict.NEW,
                        confidence=0.3,
                        reason="json_parse_failed",
                    )
            else:
                return DeduplicationResult(
                    verdict=DuplicationVerdict.NEW,
                    confidence=0.3,
                    reason="json_parse_failed",
                )

        verdict_str = parsed.get("verdict", "new")
        try:
            verdict = DuplicationVerdict(verdict_str)
        except ValueError:
            verdict = DuplicationVerdict.NEW

        return DeduplicationResult(
            verdict=verdict,
            confidence=min(1.0, max(0.0, parsed.get("confidence", 0.5))),
            reason=parsed.get("reason", ""),
            merged_body=parsed.get("merged_body", ""),
        )
