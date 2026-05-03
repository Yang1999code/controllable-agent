"""agent/memory/extractor.py — LLM 记忆提取引擎。

任务完成后调用轻量模型提取 digest，积累到阈值时触发 wiki 合并。
固定 system prompt 设计，KV Cache 友好。
"""

import json
import logging
import time
from dataclasses import dataclass, field

from ai.types import Message
from ai.provider import IModelProvider
from agent.memory.fact_store import FactStore, FactEntry
from agent.memory.domain_index import DomainIndex
from agent.memory.task_detector import TaskDetector, TaskDetection

logger = logging.getLogger(__name__)


# ── 固定 system prompt（KV Cache 友好）────────────────────

_DIGEST_SYSTEM_PROMPT = """你是一个记忆提取助手。从对话历史中提取关键事实，生成结构化摘要。

输出格式（严格 JSON）：
{
  "task_summary": "一句话概括任务",
  "domains": ["conversation"],
  "tags": ["关键词1", "关键词2"],
  "facts": ["事实1", "事实2"],
  "body": "## 任务摘要\\n\\n详细的 Markdown 摘要内容"
}

规则：
- domains 只能是: conversation, profile, agent_view, task
- tags 使用中文或英文均可，3-8 个
- facts 是原子化的事实列表（每条一个独立事实）
- body 是 Markdown 格式的完整摘要
- 只提取有价值的事实，忽略寒暄和闲聊
- 如果对话中没有有价值的信息，返回空 facts 数组"""

_WIKI_MERGE_SYSTEM_PROMPT = """你是一个知识合并助手。将多个任务摘要合并为一个完整的知识页。

输出格式（严格 JSON）：
{
  "title": "知识页标题",
  "tags": ["合并后的关键词"],
  "domains": ["合并后的域"],
  "body": "## 标题\\n\\n完整的 Markdown 知识内容"
}

规则：
- 合并所有相关信息，去除重复
- 保留时间线（按时间顺序组织）
- 冲突信息以最新的为准，标注变更
- 生成的 body 应该是完整、自包含的知识文档
- tags 合并去重，5-10 个"""

_WIKI_MERGE_THRESHOLD = 5


@dataclass(frozen=True)
class ExtractionResult:
    """提取结果。"""

    success: bool
    digest_id: str = ""
    wiki_id: str = ""
    reason: str = ""


class MemoryExtractor:
    """LLM 记忆提取引擎。

    工作流：
    1. TaskDetector 判断任务完成
    2. 调用 LLM 提取 digest
    3. 积累阈值时触发 wiki 合并
    4. 更新索引
    """

    def __init__(
        self,
        provider: IModelProvider,
        fact_store: FactStore,
        domain_index: DomainIndex,
        task_detector: TaskDetector | None = None,
        wiki_merge_threshold: int = _WIKI_MERGE_THRESHOLD,
    ):
        self.provider = provider
        self.fact_store = fact_store
        self.domain_index = domain_index
        self.task_detector = task_detector or TaskDetector()
        self.wiki_merge_threshold = wiki_merge_threshold

    async def check_and_extract(
        self,
        messages: list[Message],
        session_id: str,
        turn_count: int,
        had_tool_calls: bool,
    ) -> ExtractionResult | None:
        """检查任务是否完成，如果完成则提取 digest。

        返回 ExtractionResult 或 None（任务未完成）。
        """
        if not messages:
            return None

        user_input = ""
        for msg in reversed(messages):
            if msg.role == "user":
                user_input = msg.content
                break

        detection = self.task_detector.detect(user_input, turn_count, had_tool_calls)
        if not detection.is_complete:
            return None

        if detection.confidence < 0.6:
            logger.info("task detected complete but low confidence (%.2f), skipping", detection.confidence)
            return None

        logger.info("task complete detected (reason=%s, confidence=%.2f), extracting digest",
                     detection.reason, detection.confidence)

        return await self.extract_digest(messages, session_id)

    async def extract_digest(
        self,
        messages: list[Message],
        session_id: str,
    ) -> ExtractionResult:
        """从对话历史提取 digest。

        调用 LLM 解析对话，生成结构化摘要写入 fact_store。
        """
        conversation = self._format_conversation(messages)
        if not conversation.strip():
            return ExtractionResult(success=False, reason="empty_conversation")

        llm_messages = [
            Message(role="user", content=f"请从以下对话中提取记忆：\n\n{conversation}"),
        ]

        try:
            events = await self.provider.chat(
                messages=llm_messages,
                tools=[],
                system_prompt=_DIGEST_SYSTEM_PROMPT,
                max_tokens=2000,
            )
        except Exception as e:
            logger.error("LLM digest extraction failed: %s", e)
            return ExtractionResult(success=False, reason=f"llm_error: {e}")

        response_text = ""
        for event in events:
            if event.type == "text_delta":
                response_text += event.content
            elif event.type == "error":
                return ExtractionResult(success=False, reason=f"llm_error: {event.error}")

        parsed = self._parse_json_response(response_text)
        if parsed is None:
            return ExtractionResult(success=False, reason="json_parse_failed")

        facts = parsed.get("facts", [])
        if not facts:
            return ExtractionResult(success=False, reason="no_valuable_facts")

        digest_id = await self.fact_store.next_digest_id()
        entry = await self.fact_store.create_digest(
            digest_id=digest_id,
            source_session=session_id,
            task_summary=parsed.get("task_summary", ""),
            domains=parsed.get("domains", ["conversation"]),
            tags=parsed.get("tags", []),
            facts=facts,
            body=parsed.get("body", ""),
        )

        await self.domain_index.update_index_for(entry)

        # 检查是否需要 wiki 合并
        wiki_result = await self._check_and_merge_wiki(entry)

        return ExtractionResult(
            success=True,
            digest_id=digest_id,
            wiki_id=wiki_result or "",
        )

    async def _check_and_merge_wiki(self, new_digest: FactEntry) -> str | None:
        """检查同主题 digest 是否达到合并阈值。

        返回新创建的 wiki ID 或 None。
        """
        if not new_digest.tags:
            return None

        primary_tag = new_digest.tags[0]

        all_digests = await self.fact_store.read_all("digest")
        same_topic = [
            d for d in all_digests
            if primary_tag in d.tags
        ]

        if len(same_topic) < self.wiki_merge_threshold:
            return None

        logger.info("wiki merge triggered for topic '%s' (%d digests)",
                     primary_tag, len(same_topic))

        wiki_id = await self._merge_digests_to_wiki(same_topic, primary_tag)
        return wiki_id

    async def _merge_digests_to_wiki(
        self,
        digests: list[FactEntry],
        topic: str,
    ) -> str | None:
        """合并多个 digest 为一个 wiki 页面。"""
        digest_contents = []
        for d in digests:
            digest_contents.append(
                f"### {d.id} ({d.metadata.get('task_summary', '')})\n{d.body}"
            )

        merge_input = "\n\n---\n\n".join(digest_contents)

        llm_messages = [
            Message(role="user", content=f"请将以下 {len(digests)} 个任务摘要合并为一个知识页：\n\n{merge_input}"),
        ]

        try:
            events = await self.provider.chat(
                messages=llm_messages,
                tools=[],
                system_prompt=_WIKI_MERGE_SYSTEM_PROMPT,
                max_tokens=4000,
            )
        except Exception as e:
            logger.error("LLM wiki merge failed: %s", e)
            return None

        response_text = ""
        for event in events:
            if event.type == "text_delta":
                response_text += event.content
            elif event.type == "error":
                logger.error("LLM wiki merge error: %s", event.error)
                return None

        parsed = self._parse_json_response(response_text)
        if parsed is None:
            return None

        wiki_id = topic.replace(" ", "_").lower()
        digest_ids = [d.id for d in digests]

        wiki_entry = await self.fact_store.create_wiki(
            id=wiki_id,
            title=parsed.get("title", topic),
            source_digests=digest_ids,
            domains=parsed.get("domains", ["profile"]),
            tags=parsed.get("tags", [topic]),
            body=parsed.get("body", ""),
        )

        await self.domain_index.update_index_for(wiki_entry)

        for d in digests:
            await self.domain_index.add_to_domain(
                domain=d.metadata.get("primary_domain", "conversation"),
                topic=d.metadata.get("task_summary", ""),
                level="digest",
                ref_path=f"../digest/{d.id}.md",
            )

        await self.domain_index.add_to_domain(
            domain=parsed.get("domains", ["profile"])[0] if parsed.get("domains") else "profile",
            topic=parsed.get("title", topic),
            level="wiki",
            ref_path=f"../wiki/{wiki_id}.md",
            summary=parsed.get("title", topic),
        )

        return wiki_id

    @staticmethod
    def _format_conversation(messages: list[Message]) -> str:
        """将消息列表格式化为可读文本。"""
        lines = []
        for msg in messages:
            role = {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}.get(msg.role, msg.role)
            content = msg.content or ""
            if msg.tool_calls:
                tool_names = [tc.get("function", {}).get("name", "?") for tc in msg.tool_calls]
                content = f"[调用工具: {', '.join(tool_names)}]"
            if content:
                lines.append(f"{role}: {content[:500]}")
        return "\n".join(lines)

    @staticmethod
    def _parse_json_response(text: str) -> dict | None:
        """从 LLM 响应中解析 JSON。

        支持三种格式：
        1. 纯 JSON
        2. ```json ... ``` 代码块包裹
        3. ``` ... ``` 代码块包裹
        """
        text = text.strip()

        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1:]
            text = text.rstrip("`").strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    return None
            return None
