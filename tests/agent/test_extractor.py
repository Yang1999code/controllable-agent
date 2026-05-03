"""tests/agent/test_extractor.py — MemoryExtractor + Deduplicator 测试。"""

import json
import pytest

from ai.types import Message
from ai.provider import LLMEvent
from agent.memory.fact_store import FactStore, FactEntry
from agent.memory.domain_index import DomainIndex
from agent.memory.task_detector import TaskDetector
from agent.memory.extractor import MemoryExtractor, ExtractionResult
from agent.memory.dedup import Deduplicator, DeduplicationResult, DuplicationVerdict
from tests.conftest import MockProvider


class JsonMockProvider(MockProvider):
    """返回固定 JSON 响应的 Mock Provider。"""

    def __init__(self, json_response: dict | None = None):
        super().__init__()
        self.json_response = json_response or {}

    async def chat(self, messages, tools, system_prompt="", max_tokens=4096):
        text = json.dumps(self.json_response, ensure_ascii=False)
        return [
            LLMEvent(type="text_delta", content=text),
            LLMEvent(type="done", usage={"input_tokens": 100, "output_tokens": 50}),
        ]

    async def stream(self, messages, tools, system_prompt="", max_tokens=4096, temperature=0.0):
        events = await self.chat(messages, tools, system_prompt, max_tokens)
        for e in events:
            yield e


class FailingProvider(MockProvider):
    """模拟 LLM 调用失败的 Provider。"""

    async def chat(self, messages, tools, system_prompt="", max_tokens=4096):
        return [
            LLMEvent(type="error", error="rate_limit_exceeded"),
            LLMEvent(type="done", usage={}),
        ]


@pytest.fixture
def fact_store(memory_store):
    return FactStore(memory_store)


@pytest.fixture
def domain_index(memory_store, fact_store):
    return DomainIndex(memory_store, fact_store)


@pytest.fixture
def json_provider():
    digest_json = {
        "task_summary": "修复了 Python 打包问题",
        "domains": ["conversation"],
        "tags": ["python", "打包"],
        "facts": ["用户使用 poetry 打包", "添加了 pyproject.toml 配置"],
        "body": "## Python 打包修复\n\n用户使用 poetry 进行打包管理。",
    }
    return JsonMockProvider(digest_json)


@pytest.fixture
def extractor(json_provider, fact_store, domain_index):
    return MemoryExtractor(
        provider=json_provider,
        fact_store=fact_store,
        domain_index=domain_index,
    )


# ── MemoryExtractor 测试 ──────────────────────────────────


class TestCheckAndExtract:
    @pytest.mark.asyncio
    async def test_task_complete_triggers_extraction(self, extractor, domain_index):
        await domain_index.initialize()
        messages = [
            Message(role="user", content="帮我修一下打包问题"),
            Message(role="assistant", content="好的，我来帮你修复"),
            Message(role="user", content="谢谢"),
        ]
        result = await extractor.check_and_extract(messages, "ses_1", 3, False)
        assert result is not None
        assert result.success is True
        assert result.digest_id.startswith("d_")

    @pytest.mark.asyncio
    async def test_task_not_complete_returns_none(self, extractor, domain_index):
        await domain_index.initialize()
        messages = [
            Message(role="user", content="继续修改"),
            Message(role="assistant", content="好的"),
        ]
        result = await extractor.check_and_extract(messages, "ses_1", 2, True)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_messages_returns_none(self, extractor):
        result = await extractor.check_and_extract([], "ses_1", 0, False)
        assert result is None


class TestExtractDigest:
    @pytest.mark.asyncio
    async def test_successful_extraction(self, extractor, fact_store, domain_index):
        await domain_index.initialize()

        messages = [
            Message(role="user", content="帮我修一下打包问题"),
            Message(role="assistant", content="好的，修改了 pyproject.toml"),
        ]
        result = await extractor.extract_digest(messages, "ses_1")
        assert result.success is True
        assert result.digest_id != ""

        entry = await fact_store.read("digest", result.digest_id)
        assert entry is not None
        assert "python" in entry.tags

    @pytest.mark.asyncio
    async def test_llm_failure(self, fact_store, domain_index):
        await domain_index.initialize()
        provider = FailingProvider()
        ext = MemoryExtractor(provider=provider, fact_store=fact_store, domain_index=domain_index)

        messages = [Message(role="user", content="test")]
        result = await ext.extract_digest(messages, "ses_1")
        assert result.success is False
        assert "llm_error" in result.reason

    @pytest.mark.asyncio
    async def test_empty_conversation(self, extractor):
        result = await extractor.extract_digest([], "ses_1")
        assert result.success is False
        assert result.reason == "empty_conversation"

    @pytest.mark.asyncio
    async def test_no_valuable_facts(self, fact_store, domain_index):
        await domain_index.initialize()
        provider = JsonMockProvider({"task_summary": "", "facts": [], "body": ""})
        ext = MemoryExtractor(provider=provider, fact_store=fact_store, domain_index=domain_index)

        messages = [Message(role="user", content="你好")]
        result = await ext.extract_digest(messages, "ses_1")
        assert result.success is False
        assert result.reason == "no_valuable_facts"


class TestFormatConversation:
    def test_basic_format(self):
        messages = [
            Message(role="user", content="你好"),
            Message(role="assistant", content="你好，有什么可以帮你？"),
        ]
        text = MemoryExtractor._format_conversation(messages)
        assert "用户: 你好" in text
        assert "助手: 你好" in text

    def test_tool_calls_format(self):
        messages = [
            Message(role="assistant", content="", tool_calls=[
                {"function": {"name": "read_file", "arguments": "{}"}},
            ]),
        ]
        text = MemoryExtractor._format_conversation(messages)
        assert "read_file" in text

    def test_empty_messages(self):
        text = MemoryExtractor._format_conversation([])
        assert text == ""


class TestParseJsonResponse:
    def test_pure_json(self):
        result = MemoryExtractor._parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_code_block(self):
        result = MemoryExtractor._parse_json_response('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_json_in_plain_code_block(self):
        result = MemoryExtractor._parse_json_response('```\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_json_with_surrounding_text(self):
        result = MemoryExtractor._parse_json_response('Here is the result:\n{"key": "value"}\nDone.')
        assert result == {"key": "value"}

    def test_invalid_json(self):
        result = MemoryExtractor._parse_json_response("not json at all")
        assert result is None

    def test_empty_string(self):
        result = MemoryExtractor._parse_json_response("")
        assert result is None

    def test_chinese_json(self):
        result = MemoryExtractor._parse_json_response('{"task_summary": "修复打包", "facts": ["测试"]}')
        assert result["task_summary"] == "修复打包"


# ── Deduplicator 测试 ─────────────────────────────────────


class TestDeduplicator:
    @pytest.mark.asyncio
    async def test_new_verdict(self, fact_store):
        provider = JsonMockProvider({
            "verdict": "new",
            "confidence": 0.95,
            "reason": "完全不同的主题",
        })
        dedup = Deduplicator(provider=provider, fact_store=fact_store)

        digest = FactEntry(id="d_001", level="digest", tags=["python"], body="Python content")
        wiki = FactEntry(id="w_rust", level="wiki", tags=["rust"], body="Rust content")

        result = await dedup.check_digest(digest, wiki)
        assert result.verdict == DuplicationVerdict.NEW
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_duplicate_verdict(self, fact_store):
        provider = JsonMockProvider({
            "verdict": "duplicate",
            "confidence": 0.9,
            "reason": "信息已包含在 wiki 中",
        })
        dedup = Deduplicator(provider=provider, fact_store=fact_store)

        digest = FactEntry(id="d_002", level="digest", tags=["python"], body="Python 3.12")
        wiki = FactEntry(id="w_python", level="wiki", tags=["python"], body="Python 3.12...")

        result = await dedup.check_digest(digest, wiki)
        assert result.verdict == DuplicationVerdict.DUPLICATE

    @pytest.mark.asyncio
    async def test_conflict_verdict(self, fact_store):
        provider = JsonMockProvider({
            "verdict": "conflict",
            "confidence": 0.85,
            "reason": "version mismatch",
            "merged_body": "## Python\n\nLatest: 3.13",
        })
        dedup = Deduplicator(provider=provider, fact_store=fact_store)

        digest = FactEntry(id="d_003", level="digest", tags=["python"], body="Python 3.13")
        wiki = FactEntry(id="w_python", level="wiki", tags=["python"], body="Python 3.12")

        result = await dedup.check_digest(digest, wiki)
        assert result.verdict == DuplicationVerdict.CONFLICT
        assert "3.13" in result.merged_body

    @pytest.mark.asyncio
    async def test_llm_error_returns_new(self, fact_store):
        provider = FailingProvider()
        dedup = Deduplicator(provider=provider, fact_store=fact_store)

        digest = FactEntry(id="d_004", level="digest", tags=["python"], body="test")
        wiki = FactEntry(id="w_test", level="wiki", tags=["python"], body="test")

        result = await dedup.check_digest(digest, wiki)
        assert result.verdict == DuplicationVerdict.NEW
        assert "llm_error" in result.reason

    @pytest.mark.asyncio
    async def test_find_conflicting_wikis(self, fact_store):
        provider = JsonMockProvider({
            "verdict": "supplement",
            "confidence": 0.8,
            "reason": "补充信息",
            "merged_body": "Updated wiki body",
        })
        dedup = Deduplicator(provider=provider, fact_store=fact_store)

        await fact_store.write(FactEntry(
            id="w_python", level="wiki",
            tags=("python", "编程"), body="Python 基础",
        ))
        await fact_store.write(FactEntry(
            id="w_rust", level="wiki",
            tags=("rust", "编程"), body="Rust 基础",
        ))

        new_digest = FactEntry(id="d_005", level="digest", tags=("python",), body="新信息")

        results = await dedup.find_conflicting_wikis(new_digest)
        assert len(results) >= 1
        assert results[0][0].id == "w_python"

    @pytest.mark.asyncio
    async def test_find_no_conflicts(self, fact_store):
        provider = JsonMockProvider({
            "verdict": "new",
            "confidence": 0.9,
            "reason": "不相关",
        })
        dedup = Deduplicator(provider=provider, fact_store=fact_store)

        await fact_store.write(FactEntry(
            id="w_rust", level="wiki",
            tags=("rust",), body="Rust 基础",
        ))

        new_digest = FactEntry(id="d_006", level="digest", tags=("java",), body="Java 信息")
        results = await dedup.find_conflicting_wikis(new_digest)
        assert len(results) == 0


class TestDeduplicatorParseResponse:
    def test_valid_response(self):
        result = Deduplicator._parse_response(
            '{"verdict": "new", "confidence": 0.9, "reason": "test"}'
        )
        assert result.verdict == DuplicationVerdict.NEW
        assert result.confidence == 0.9

    def test_invalid_verdict_defaults_new(self):
        result = Deduplicator._parse_response(
            '{"verdict": "unknown", "confidence": 0.5, "reason": "test"}'
        )
        assert result.verdict == DuplicationVerdict.NEW

    def test_confidence_clamped(self):
        result = Deduplicator._parse_response(
            '{"verdict": "new", "confidence": 1.5, "reason": "test"}'
        )
        assert result.confidence == 1.0

    def test_json_parse_failed(self):
        result = Deduplicator._parse_response("not json")
        assert result.verdict == DuplicationVerdict.NEW
        assert result.confidence == 0.3
