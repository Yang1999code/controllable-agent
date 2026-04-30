"""my_agent — AI Agent 框架公共 API。

唯一入口。所有用户代码只 import 这里，不深入内部模块路径。
参考 OpenAI Agents SDK: `from agents import Agent, Runner`
"""

# ── ai/ 核心类型 ──────────────────────────────────────
from ai.types import (
    AgentEvent,
    AgentEventType,
    Context,
    ITool,
    Message,
    ToolDefinition,
    ToolParameter,
    ToolResult,
)
from ai.provider import IModelProvider

# ── agent/ 核心模块 ────────────────────────────────────
from agent.loop import AgentLoop, AgentConfig, AgentResult
from agent.tool_registry import ToolRegistry
from agent.hook import HookChain, HookHandler, IHook
from agent.inspector import FlowInspector, IFlowInspector
from agent.prompt import PromptBuilder, PromptFragment, IPromptBuilder
from agent.capability import (
    Capability, CapabilityCatalog, CapabilityRegistry,
    ICapabilityCatalog, ICapabilityRegistry,
)
from agent.skill import (
    Skill, SkillConfig, SkillRegistry,
    ISkill, ISkillConfig,
)
from agent.web import WebAutomation, IWebAutomation
from agent.mcp import MCPClient, MCPServerConfig, MCPToolAdapter
from agent.memory.store import MemoryStore
from agent.memory.backend import FileSystemMemoryBackend, IMemoryBackend
from agent.memory.index import MemoryIndex

# ── UI 解耦 Protocol ───────────────────────────────────
from ai.types import IUiSession

__all__ = [
    # ai types
    "AgentEvent", "AgentEventType", "Context", "ITool",
    "Message", "ToolDefinition", "ToolParameter", "ToolResult",
    "IModelProvider",
    # agent loop
    "AgentLoop", "AgentConfig", "AgentResult",
    # tools
    "ToolRegistry",
    # hooks
    "HookChain", "HookHandler", "IHook",
    # phase 2
    "FlowInspector", "IFlowInspector",
    "PromptBuilder", "PromptFragment", "IPromptBuilder",
    "Capability", "CapabilityCatalog", "CapabilityRegistry",
    "ICapabilityCatalog", "ICapabilityRegistry",
    "WebAutomation", "IWebAutomation",
    # mcp
    "MCPClient", "MCPServerConfig", "MCPToolAdapter",
    # skills
    "Skill", "SkillConfig", "SkillRegistry",
    "ISkill", "ISkillConfig",
    # memory
    "MemoryStore", "FileSystemMemoryBackend", "IMemoryBackend", "MemoryIndex",
]
