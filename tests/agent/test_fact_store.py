"""tests/agent/test_fact_store.py — FactStore 测试。"""

import pytest
from agent.memory.fact_store import FactStore, FactEntry


@pytest.fixture
def fact_store(memory_store):
    return FactStore(memory_store)


class TestFactEntry:
    def test_default_values(self):
        entry = FactEntry(id="d_001", level="digest")
        assert entry.body == ""
        assert entry.tags == ()
        assert entry.domains == ()
        assert entry.metadata == {}

    def test_frozen(self):
        entry = FactEntry(id="d_001", level="digest")
        with pytest.raises(AttributeError):
            entry.id = "d_002"


class TestFactStoreWriteRead:
    @pytest.mark.asyncio
    async def test_write_and_read_digest(self, fact_store):
        entry = FactEntry(
            id="d_001",
            level="digest",
            metadata={"source_session": "ses_abc", "task_summary": "测试任务"},
            body="## 要点\n- 第一个要点",
            tags=["测试", "python"],
            domains=["conversation"],
        )
        await fact_store.write(entry)

        result = await fact_store.read("digest", "d_001")
        assert result is not None
        assert result.id == "d_001"
        assert result.level == "digest"
        assert "第一个要点" in result.body
        assert "测试" in result.tags
        assert "conversation" in result.domains

    @pytest.mark.asyncio
    async def test_write_and_read_wiki(self, fact_store):
        entry = FactEntry(
            id="w_python_stack",
            level="wiki",
            metadata={"title": "Python技术栈", "source_digests": ["d_001", "d_005"]},
            body="## 编程语言\n- Python 3.12",
            tags=["python", "技术栈"],
            domains=["profile"],
        )
        await fact_store.write(entry)

        result = await fact_store.read("wiki", "w_python_stack")
        assert result is not None
        assert result.id == "w_python_stack"
        assert result.level == "wiki"
        assert "Python技术栈" == result.metadata["title"]

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, fact_store):
        result = await fact_store.read("digest", "d_999")
        assert result is None


class TestCreateConvenience:
    @pytest.mark.asyncio
    async def test_create_digest(self, fact_store):
        entry = await fact_store.create_digest(
            digest_id="d_010",
            source_session="ses_xyz",
            task_summary="修复打包配置",
            domains=["conversation"],
            tags=["打包", "修复"],
            facts=["pyproject.toml配置有误"],
            body="## 要点\n- 修复了打包配置",
            confidence=0.95,
        )
        assert entry.id == "d_010"
        assert entry.level == "digest"

        result = await fact_store.read("digest", "d_010")
        assert result is not None
        assert result.metadata["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_create_wiki(self, fact_store):
        entry = await fact_store.create_wiki(
            wiki_id="dev_environment",
            title="开发环境",
            source_digests=["d_001", "d_003"],
            domains=["profile"],
            tags=["环境", "工具"],
            body="## 编辑器\n- VS Code",
        )
        assert entry.id == "dev_environment"
        assert entry.level == "wiki"

        result = await fact_store.read("wiki", "dev_environment")
        assert result is not None
        assert result.metadata["title"] == "开发环境"


class TestListIds:
    @pytest.mark.asyncio
    async def test_list_ids_empty(self, fact_store):
        ids = await fact_store.list_ids("digest")
        assert ids == []

    @pytest.mark.asyncio
    async def test_list_ids_returns_sorted(self, fact_store):
        for i in [3, 1, 2]:
            await fact_store.write(FactEntry(id=f"d_{i:03d}", level="digest", body=f"entry {i}"))
        ids = await fact_store.list_ids("digest")
        assert ids == ["d_001", "d_002", "d_003"]

    @pytest.mark.asyncio
    async def test_read_all(self, fact_store):
        for i in range(1, 4):
            await fact_store.write(FactEntry(id=f"d_{i:03d}", level="digest", body=f"entry {i}"))
        entries = await fact_store.read_all("digest")
        assert len(entries) == 3


class TestDeleteAndExists:
    @pytest.mark.asyncio
    async def test_delete(self, fact_store):
        await fact_store.write(FactEntry(id="d_050", level="digest", body="to delete"))
        assert await fact_store.exists("digest", "d_050") is True
        assert await fact_store.delete("digest", "d_050") is True
        assert await fact_store.exists("digest", "d_050") is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, fact_store):
        assert await fact_store.delete("digest", "d_999") is False

    @pytest.mark.asyncio
    async def test_exists_nonexistent(self, fact_store):
        assert await fact_store.exists("wiki", "no_such_page") is False


class TestNextDigestId:
    @pytest.mark.asyncio
    async def test_first_id(self, fact_store):
        next_id = await fact_store.next_digest_id()
        assert next_id == "d_001"

    @pytest.mark.asyncio
    async def test_increments(self, fact_store):
        await fact_store.write(FactEntry(id="d_001", level="digest", body="first"))
        await fact_store.write(FactEntry(id="d_002", level="digest", body="second"))
        next_id = await fact_store.next_digest_id()
        assert next_id == "d_003"


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_parse_malformed_frontmatter(self, fact_store):
        raw = "---\ninvalid: yaml: content\n---\n## Body"
        result = FactStore._parse_frontmatter(raw, "bad_file")
        assert result is None

    def test_parse_valid_frontmatter(self, fact_store):
        raw = "---\nid: d_001\nlevel: digest\n---\n## Body text"
        result = FactStore._parse_frontmatter(raw, "d_001")
        assert result is not None
        assert result.id == "d_001"

    @pytest.mark.asyncio
    async def test_write_preserves_chinese_body(self, fact_store):
        entry = FactEntry(id="d_020", level="digest", body="## 要点\n- 中文内容测试通过")
        await fact_store.write(entry)

        result = await fact_store.read("digest", "d_020")
        assert result is not None
        assert "中文内容测试通过" in result.body

    @pytest.mark.asyncio
    async def test_read_after_overwrite(self, fact_store):
        await fact_store.write(FactEntry(id="d_030", level="digest", body="version 1"))
        await fact_store.write(FactEntry(id="d_030", level="digest", body="version 2"))

        result = await fact_store.read("digest", "d_030")
        assert result is not None
        assert result.body == "version 2"

    def test_validate_level_invalid(self, fact_store):
        with pytest.raises(ValueError, match="Invalid level"):
            FactStore._validate_level("L99")

    @pytest.mark.asyncio
    async def test_metadata_no_key_shadowing(self, fact_store):
        """确保 metadata 中的 id/level 不会覆盖显式字段。"""
        entry = FactEntry(
            id="d_040", level="digest",
            metadata={"id": "wrong_id", "level": "wrong_level", "custom": "value"},
            body="test",
        )
        await fact_store.write(entry)

        result = await fact_store.read("digest", "d_040")
        assert result is not None
        assert result.id == "d_040"
        assert result.level == "digest"
        assert result.metadata["custom"] == "value"

    @pytest.mark.asyncio
    async def test_deep_immutability(self, fact_store):
        """确保 tags/domains 是 tuple，不可追加。"""
        entry = FactEntry(id="d_050", level="digest", tags=["a", "b"], domains=["x"])
        assert isinstance(entry.tags, tuple)
        assert isinstance(entry.domains, tuple)
        with pytest.raises(AttributeError):
            entry.tags = ("c",)
