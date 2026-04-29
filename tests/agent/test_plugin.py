"""tests/agent/test_plugin.py — PluginManifest / IPluginAdapter 测试。"""

import pytest
from agent.plugin import PluginManifest, PluginAdapter
from agent.capability import CapabilityCatalog


@pytest.fixture
def adapter(hook_chain, memory_store):
    from agent.tool_registry import ToolRegistry
    from agent.skill import SkillRegistry
    from agent.memory.backend import FileSystemMemoryBackend

    tools = ToolRegistry()
    skills = SkillRegistry()
    catalog = CapabilityCatalog()
    backend = FileSystemMemoryBackend(memory_store)
    return PluginAdapter(
        hooks=hook_chain,
        tools=tools,
        skills=skills,
        catalog=catalog,
    )


class TestPluginManifest:
    def test_default_values(self):
        m = PluginManifest(name="test_plugin", version="1.0.0")
        assert m.description == ""
        assert m.tools == []
        assert m.hooks == []

    def test_full_manifest(self):
        m = PluginManifest(
            name="web_plugin",
            version="2.0.0",
            description="Adds web tools",
            tools=["app.tools.web_fetch"],
            hooks=["agent.hooks.web_hook"],
            skills=[".agent-base/skills/web/"],
            agents=["explorer"],
            dependencies=["requests"],
        )
        assert len(m.tools) == 1
        assert len(m.dependencies) == 1


class TestPluginAdapter:
    def test_merge_manifests_single(self, adapter):
        manifests = [
            PluginManifest(name="p1", version="1.0", tools=["tool_a"]),
        ]
        result = adapter.merge_manifests(manifests)
        assert "p1" in result
        assert result["p1"].tools == ["tool_a"]

    def test_merge_manifests_duplicate(self, adapter):
        manifests = [
            PluginManifest(name="p1", version="1.0", tools=["tool_a"]),
            PluginManifest(name="p1", version="2.0", tools=["tool_b"]),
        ]
        result = adapter.merge_manifests(manifests)
        assert "p1" in result
        assert "tool_a" in result["p1"].tools
        assert "tool_b" in result["p1"].tools
        assert result["p1"].version == "2.0"

    def test_is_loaded_initially_false(self, adapter):
        assert not adapter.is_loaded("any")

    def test_list_loaded_initially_empty(self, adapter):
        assert adapter.list_loaded() == []

    @pytest.mark.asyncio
    async def test_load_and_unload(self, adapter):
        manifest = PluginManifest(name="test_plugin", version="1.0")
        await adapter.load(manifest)
        assert adapter.is_loaded("test_plugin")
        assert "test_plugin" in adapter.list_loaded()

        await adapter.unload("test_plugin")
        assert not adapter.is_loaded("test_plugin")

    @pytest.mark.asyncio
    async def test_discover_empty(self, adapter):
        manifests = await adapter.discover()
        assert isinstance(manifests, dict)
