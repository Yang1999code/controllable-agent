"""tests/agent/test_domain_index.py — DomainIndex 测试。"""

import pytest
from agent.memory.fact_store import FactStore, FactEntry
from agent.memory.domain_index import DomainIndex, IndexEntry, DomainEntry


@pytest.fixture
def fact_store(memory_store):
    return FactStore(memory_store)


@pytest.fixture
def domain_index(memory_store, fact_store):
    return DomainIndex(memory_store, fact_store)


class TestInitialize:
    @pytest.mark.asyncio
    async def test_creates_all_domains(self, domain_index, memory_store):
        await domain_index.initialize()
        for domain in ["conversation", "profile", "agent_view", "task"]:
            assert await memory_store.exists(f"domains/{domain}/_index.md")
        assert await memory_store.exists("index.md")

    @pytest.mark.asyncio
    async def test_idempotent(self, domain_index, memory_store):
        await domain_index.initialize()
        await domain_index.initialize()
        for domain in ["conversation", "profile", "agent_view", "task"]:
            assert await memory_store.exists(f"domains/{domain}/_index.md")


class TestRebuildIndex:
    @pytest.mark.asyncio
    async def test_empty_store(self, domain_index, memory_store):
        await domain_index.initialize()
        await domain_index.rebuild_index()
        raw = await memory_store.read("index.md")
        assert "type: keyword_index" in raw

    @pytest.mark.asyncio
    async def test_with_digests(self, domain_index, fact_store, memory_store):
        await fact_store.create_digest(
            "d_001", "ses_1", "修复打包", ["conversation"],
            ["python", "打包"], ["fact1"], "## Python 打包修复",
        )
        await domain_index.initialize()
        await domain_index.rebuild_index()

        raw = await memory_store.read("index.md")
        assert "python" in raw
        assert "打包" in raw

    @pytest.mark.asyncio
    async def test_with_wikis(self, domain_index, fact_store, memory_store):
        await fact_store.create_wiki(
            "python_stack", "Python技术栈", ["d_001"],
            ["profile"], ["python", "技术栈"], "## Python 3.12",
        )
        await domain_index.initialize()
        await domain_index.rebuild_index()

        raw = await memory_store.read("index.md")
        assert "python" in raw


class TestUpdateIndexFor:
    @pytest.mark.asyncio
    async def test_single_digest(self, domain_index, fact_store):
        await domain_index.initialize()
        entry = await fact_store.create_digest(
            "d_001", "ses_1", "测试任务", ["conversation"],
            ["测试"], ["fact1"], "## 测试要点",
        )
        await domain_index.update_index_for(entry)

        results = await domain_index.search("测试")
        assert len(results) >= 1
        assert results[0].id == "d_001"

    @pytest.mark.asyncio
    async def test_single_wiki(self, domain_index, fact_store):
        await domain_index.initialize()
        entry = await fact_store.create_wiki(
            "dev_env", "开发环境", ["d_001"],
            ["profile"], ["开发", "环境"], "## VS Code",
        )
        await domain_index.update_index_for(entry)

        results = await domain_index.search("开发环境")
        assert len(results) >= 1
        assert results[0].id == "dev_env"


class TestSearch:
    @pytest.mark.asyncio
    async def test_wiki_first(self, domain_index, fact_store):
        await domain_index.initialize()

        await fact_store.create_digest(
            "d_001", "ses_1", "Python任务", ["conversation"],
            ["python"], ["fact1"], "## Python开发",
        )
        wiki_entry = await fact_store.create_wiki(
            "python_stack", "Python技术栈", ["d_001"],
            ["profile"], ["python"], "## 完整Python技术栈",
        )
        await domain_index.rebuild_index()

        results = await domain_index.search("python")
        assert len(results) >= 2
        assert results[0].level == "wiki"

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, domain_index):
        await domain_index.initialize()
        results = await domain_index.search("xyzzynotfound123")
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_query(self, domain_index):
        await domain_index.initialize()
        results = await domain_index.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_chinese_query(self, domain_index, fact_store):
        await domain_index.initialize()
        await fact_store.create_digest(
            "d_001", "ses_1", "部署任务", ["conversation"],
            ["部署", "django"], ["fact1"], "## 部署 Django 到 AWS",
        )
        await domain_index.rebuild_index()

        results = await domain_index.search("部署")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_top_k_limit(self, domain_index, fact_store):
        await domain_index.initialize()
        for i in range(5):
            entry = FactEntry(
                id=f"d_{i:03d}", level="digest",
                tags=["python"], body=f"Python entry {i}",
            )
            await fact_store.write(entry)
        await domain_index.rebuild_index()

        results = await domain_index.search("python", top_k=2)
        assert len(results) <= 2


class TestDomainOperations:
    @pytest.mark.asyncio
    async def test_add_to_domain(self, domain_index):
        await domain_index.initialize()
        await domain_index.add_to_domain(
            "profile", "技术栈", "wiki", "../wiki/python_stack.md", "Python全貌",
        )
        entries = await domain_index.get_domain_index("profile")
        assert len(entries) == 1
        assert entries[0].topic == "技术栈"
        assert entries[0].summary == "Python全貌"

    @pytest.mark.asyncio
    async def test_remove_from_domain(self, domain_index):
        await domain_index.initialize()
        await domain_index.add_to_domain(
            "conversation", "对话1", "digest", "../digest/d_001.md",
        )
        removed = await domain_index.remove_from_domain("conversation", "../digest/d_001.md")
        assert removed is True
        entries = await domain_index.get_domain_index("conversation")
        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, domain_index):
        await domain_index.initialize()
        removed = await domain_index.remove_from_domain("profile", "../wiki/nope.md")
        assert removed is False

    @pytest.mark.asyncio
    async def test_get_domain_index_empty(self, domain_index):
        await domain_index.initialize()
        entries = await domain_index.get_domain_index("task")
        assert entries == []


class TestListDomains:
    @pytest.mark.asyncio
    async def test_only_domains_with_content(self, domain_index):
        await domain_index.initialize()
        await domain_index.add_to_domain("profile", "技术栈", "wiki", "../wiki/py.md")
        domains = await domain_index.list_domains()
        assert "profile" in domains
        assert "conversation" not in domains


class TestBrowseDomain:
    @pytest.mark.asyncio
    async def test_browse_returns_entries(self, domain_index, fact_store):
        await domain_index.initialize()
        entry = await fact_store.create_digest(
            "d_001", "ses_1", "测试", ["conversation"],
            ["测试"], ["fact1"], "## 测试内容",
        )
        await domain_index.add_to_domain(
            "conversation", "测试对话", "digest", "../digest/d_001.md",
        )
        results = await domain_index.browse_domain("conversation")
        assert len(results) == 1
        assert results[0].id == "d_001"


class TestIndexRoundtrip:
    def test_parse_and_build_index_md(self, domain_index):
        raw = (
            "---\ntype: keyword_index\nupdated: \"2026-05-04T10:00:00\"\n---\n"
            "\n## python\n"
            "- wiki: [python_stack](wiki/python_stack.md)\n"
            "- digest: [d_001](digest/d_001.md)\n"
            "\n## 测试\n"
            "- digest: [d_001](digest/d_001.md)\n\n"
        )
        index = domain_index._parse_index_md(raw)
        assert "python" in index
        assert "wiki/python_stack.md" in index["python"].wiki_refs
        assert "digest/d_001.md" in index["python"].digest_refs

    def test_domain_index_roundtrip(self, domain_index):
        entries = [
            DomainEntry(topic="技术栈", level="wiki", ref_path="../wiki/py.md", summary="Python"),
            DomainEntry(topic="偏好", level="digest", ref_path="../digest/d_001.md"),
        ]
        content = domain_index._build_domain_index("profile", entries)
        parsed = domain_index._parse_domain_index(content)
        assert len(parsed) == 2
        assert parsed[0].topic == "技术栈"
        assert parsed[0].summary == "Python"

    def test_validate_domain_invalid(self, domain_index):
        with pytest.raises(ValueError, match="Invalid domain"):
            DomainIndex._validate_domain("invalid_domain")


class TestRemoveFromIndex:
    @pytest.mark.asyncio
    async def test_remove_digest_from_index(self, domain_index, fact_store):
        await domain_index.initialize()
        entry = await fact_store.create_digest(
            "d_001", "ses_1", "任务", ["conversation"],
            ["python"], ["fact1"], "## Python开发",
        )
        await domain_index.update_index_for(entry)

        results = await domain_index.search("python")
        assert len(results) == 1

        await domain_index.remove_from_index(entry)
        results = await domain_index.search("python")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_remove_wiki_from_index(self, domain_index, fact_store):
        await domain_index.initialize()
        wiki_entry = await fact_store.create_wiki(
            "python_stack", "Python", ["d_001"],
            ["profile"], ["python"], "## Python full stack",
        )
        await domain_index.update_index_for(wiki_entry)
        await domain_index.remove_from_index(wiki_entry)
        results = await domain_index.search("python")
        assert len(results) == 0


class TestExtractRefRobustness:
    def test_malformed_line_no_crash(self):
        ref = DomainIndex._extract_ref("- no link here", ":")
        assert ref == ""

    def test_empty_line(self):
        ref = DomainIndex._extract_ref("", "wiki:")
        assert ref == ""


class TestMalformedDomainIndex:
    @pytest.mark.asyncio
    async def test_malformed_domain_file_no_crash(self, domain_index, memory_store):
        await domain_index.initialize()
        await memory_store.write(
            "domains/profile/_index.md",
            "---\ntype: domain_index\ndomain: profile\n---\n\n"
            "## Topic\n- no colon or parentheses\n",
        )
        entries = await domain_index.get_domain_index("profile")
        assert isinstance(entries, list)
