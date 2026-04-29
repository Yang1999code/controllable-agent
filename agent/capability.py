"""agent/capability.py — 能力渐进式披露。

能力目录（合并+缓存+索引）+ Tier 筛选（Tier 0 始终可见，Tier 1 按需注入）。

参考：CCB shouldDefer / oh-my-opencode dynamic-agent-prompt-builder.ts / ECC Profile 门控
"""

import copy
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Capability:
    """一个能力条目。"""

    name: str
    description: str
    tier: int = 0  # 0=始终可见, 1=按需, 2=显式激活
    source: str = ""  # "builtin" / "plugin:{name}" / "skill:{name}"
    tools: list[str] = field(default_factory=list)


# ── ABC/Protocol ──────────────────────────────────────

class ICapabilityCatalog(Protocol):
    """能力目录——聚合所有来源的能力。

    数据流：IPluginAdapter → Catalog → Registry → IPromptBuilder
    Catalog 是中间层，做合并 + 缓存 + 索引。
    """

    def add(self, capability: Capability) -> None: ...
    def remove(self, name: str) -> None: ...
    def get(self, name: str) -> Capability | None: ...
    def list_by_tier(self, tier: int) -> list[Capability]: ...
    def snapshot(self) -> list[Capability]: ...


class ICapabilityRegistry(Protocol):
    """能力注册表——按 Tier 筛选渐进式披露。"""

    def get_visible_tools(self, context_tier: int = 0) -> list[str]: ...
    def should_defer_tool(self, tool_name: str) -> bool: ...


# ── 实现 ──────────────────────────────────────────────

class CapabilityCatalog:
    """ICapabilityCatalog 实现。"""

    def __init__(self):
        self._capabilities: dict[str, Capability] = {}

    def add(self, capability: Capability) -> None:
        self._capabilities[capability.name] = capability

    def remove(self, name: str) -> None:
        self._capabilities.pop(name, None)

    def get(self, name: str) -> Capability | None:
        return self._capabilities.get(name)

    def list_by_tier(self, tier: int) -> list[Capability]:
        return [c for c in self._capabilities.values() if c.tier == tier]

    def snapshot(self) -> list[Capability]:
        """copy-on-read 快照，不暴露可变引用。"""
        return [copy.deepcopy(c) for c in self._capabilities.values()]


class CapabilityRegistry:
    """ICapabilityRegistry 实现。"""

    def __init__(self, catalog: CapabilityCatalog):
        self._catalog = catalog
        self._deferred_tools: set[str] = set()

    def get_visible_tools(self, context_tier: int = 0) -> list[str]:
        """获取当前上下文中可见的工具名列表。"""
        visible: list[str] = []
        for c in self._catalog.snapshot():
            if c.tier <= context_tier:
                visible.extend(c.tools)
        return visible

    def should_defer_tool(self, tool_name: str) -> bool:
        """判断工具是否需要延迟加载。"""
        return tool_name in self._deferred_tools

    def mark_deferred(self, tool_name: str) -> None:
        """标记工具为延迟加载。"""
        self._deferred_tools.add(tool_name)

    def register_capability(self, name: str, description: str,
                            tier: int = 0, source: str = "builtin",
                            tools: list[str] | None = None) -> None:
        """便捷方法：直接注册能力。"""
        self._catalog.add(Capability(
            name=name, description=description,
            tier=tier, source=source,
            tools=tools or [],
        ))
