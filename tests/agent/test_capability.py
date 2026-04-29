"""tests/agent/test_capability.py — Capability / CapabilityCatalog / CapabilityRegistry 测试。"""

import pytest
from agent.capability import Capability, CapabilityCatalog, CapabilityRegistry


class TestCapability:
    def test_default_values(self):
        cap = Capability(name="test", description="A test capability")
        assert cap.tier == 0
        assert cap.source == ""
        assert cap.tools == []

    def test_custom_values(self):
        cap = Capability(
            name="web",
            description="Web browsing",
            tier=1,
            source="plugin:web",
            tools=["web_fetch", "web_search"],
        )
        assert cap.tier == 1
        assert "web_fetch" in cap.tools


class TestCapabilityCatalog:
    @pytest.fixture
    def catalog(self):
        return CapabilityCatalog()

    def test_add_and_get(self, catalog):
        cap = Capability(name="files", description="File tools", tools=["read", "write"])
        catalog.add(cap)
        assert catalog.get("files") is cap

    def test_remove(self, catalog):
        catalog.add(Capability(name="temp", description="temp"))
        catalog.remove("temp")
        assert catalog.get("temp") is None

    def test_list_by_tier(self, catalog):
        catalog.add(Capability(name="t0", description="t0", tier=0))
        catalog.add(Capability(name="t1", description="t1", tier=1))
        assert len(catalog.list_by_tier(0)) == 1
        assert len(catalog.list_by_tier(1)) == 1

    def test_snapshot_is_copy(self, catalog):
        catalog.add(Capability(name="files", description="Files", tools=["read"]))
        snap = catalog.snapshot()
        snap[0].tools.append("write")
        # 原始不受影响
        assert len(catalog.get("files").tools) == 1


class TestCapabilityRegistry:
    @pytest.fixture
    def registry(self):
        catalog = CapabilityCatalog()
        return CapabilityRegistry(catalog)

    def test_get_visible_tools(self, registry):
        registry.register_capability("basic", "Basic tools", tier=0, tools=["read", "write"])
        registry.register_capability("web", "Web tools", tier=1, tools=["web_fetch"])
        visible = registry.get_visible_tools(context_tier=0)
        assert "read" in visible
        assert "web_fetch" not in visible

    def test_get_visible_tools_higher_tier(self, registry):
        registry.register_capability("basic", "Basic", tier=0, tools=["read"])
        registry.register_capability("web", "Web", tier=1, tools=["web_fetch"])
        visible = registry.get_visible_tools(context_tier=1)
        assert "read" in visible
        assert "web_fetch" in visible

    def test_should_defer_tool(self, registry):
        registry.mark_deferred("expensive_tool")
        assert registry.should_defer_tool("expensive_tool") is True
        assert registry.should_defer_tool("normal_tool") is False
