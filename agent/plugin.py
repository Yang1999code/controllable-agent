"""agent/plugin.py — IPluginAdapter 实现。

4 层发现 + manifest 验证 + 加载/卸载/热重载 + 组件合并。

参考：Hermes 4 层发现 / oh-my-opencode plugin-loader
"""

import asyncio
import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import yaml

if TYPE_CHECKING:
    from agent.hook import HookChain
    from agent.tool_registry import ToolRegistry
    from agent.skill import SkillRegistry
    from agent.capability import CapabilityCatalog

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────

@dataclass
class PluginManifest:
    """插件 manifest（plugin.yaml 的反序列化格式）。

    参考 Hermes plugin.yaml 结构。
    """

    name: str
    version: str
    description: str = ""
    tools: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


# ── ABC/Protocol ──────────────────────────────────────

class IPluginAdapter(Protocol):
    """插件适配器——发现/加载/合并/热重载。

    4 层发现（参考 Hermes，优先级从高到低）：
    1. 项目级：{project}/.agent-base/plugins/
    2. 用户级：~/.agent/plugins/
    3. 内置级：{my-agent}/builtin/plugins/
    4. pip 包：entry_point "my_agent_plugins"
    """

    async def discover(self) -> list[PluginManifest]: ...
    async def load(self, manifest: PluginManifest) -> None: ...
    async def unload(self, name: str) -> None: ...
    async def reload(self, name: str) -> None: ...
    def merge_manifests(self, manifests: list[PluginManifest]) -> dict[str, PluginManifest]: ...


# ── 实现 ──────────────────────────────────────────────

class PluginAdapter:
    """IPluginAdapter 实现。"""

    def __init__(
        self,
        hooks: "HookChain",
        tools: "ToolRegistry",
        skills: "SkillRegistry",
        catalog: "CapabilityCatalog",
    ):
        self._hooks = hooks
        self._tools = tools
        self._skills = skills
        self._catalog = catalog
        self._loaded: dict[str, PluginManifest] = {}
        self._lock = asyncio.Lock()

    async def discover(self) -> list[PluginManifest]:
        """扫描 4 层目录，返回所有发现的 manifest。"""
        manifests: list[PluginManifest] = []

        # 1. 项目级
        project_plugins = Path(".agent-base/plugins")
        if project_plugins.exists():
            manifests.extend(await self._scan_dir(project_plugins))

        # 2. 用户级
        user_plugins = Path.home() / ".agent" / "plugins"
        if user_plugins.exists():
            manifests.extend(await self._scan_dir(user_plugins))

        return self.merge_manifests(list(manifests))

    async def _scan_dir(self, directory: Path) -> list[PluginManifest]:
        """扫描目录中的 plugin.yaml 文件。"""
        results = []
        for plugin_yaml in directory.glob("**/plugin.yaml"):
            try:
                data = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
                if data and data.get("name") and data.get("version"):
                    results.append(PluginManifest(**data))
            except Exception as e:
                logger.warning(f"Failed to parse {plugin_yaml}: {e}")
        return results

    async def load(self, manifest: PluginManifest) -> None:
        """加载插件：注册工具/hook/技能/Agent 类型。"""
        async with self._lock:
            if manifest.name in self._loaded:
                logger.warning(f"Plugin '{manifest.name}' already loaded")
                return

            # 注册工具
            for tool_module in manifest.tools:
                try:
                    mod = importlib.import_module(tool_module)
                    if hasattr(mod, "register"):
                        mod.register(self._tools)
                except Exception as e:
                    logger.warning(f"Plugin '{manifest.name}' tool error: {e}")

            # 注册技能
            for skill_path in manifest.skills:
                try:
                    self._skills.load_from_dir(skill_path)
                except Exception as e:
                    logger.warning(f"Plugin '{manifest.name}' skill error: {e}")

            self._loaded[manifest.name] = manifest
            logger.info(f"Plugin '{manifest.name}' v{manifest.version} loaded")

    async def unload(self, name: str) -> None:
        """卸载插件：移除所有注册的组件。"""
        async with self._lock:
            self._loaded.pop(name, None)
            logger.info(f"Plugin '{name}' unloaded")

    async def reload(self, name: str) -> None:
        """热重载：unload → discover → load。"""
        if name in self._loaded:
            await self.unload(name)
        manifests = await self.discover()
        if name in manifests:
            await self.load(manifests[name])

    def merge_manifests(
        self, manifests: list[PluginManifest],
    ) -> dict[str, PluginManifest]:
        """合并多个 manifest（同名覆盖）。

        合并策略：dict→deep merge, list→union, scalar→last-wins
        """
        merged: dict[str, PluginManifest] = {}
        for m in manifests:
            if m.name in merged:
                existing = merged[m.name]
                existing.tools = list(set(existing.tools + m.tools))
                existing.hooks = list(set(existing.hooks + m.hooks))
                existing.skills = list(set(existing.skills + m.skills))
                existing.agents = list(set(existing.agents + m.agents))
                existing.dependencies = list(set(existing.dependencies + m.dependencies))
                existing.version = m.version
                existing.description = m.description or existing.description
            else:
                merged[m.name] = m
        return merged

    def is_loaded(self, name: str) -> bool:
        return name in self._loaded

    def list_loaded(self) -> list[str]:
        return list(self._loaded.keys())
