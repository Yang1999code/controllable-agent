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
from agent.memory.fact_store import FactStore, FactEntry
from agent.memory.domain_index import DomainIndex, DomainEntry, IndexEntry
from agent.memory.task_detector import TaskDetector, TaskDetection
from agent.memory.extractor import MemoryExtractor, ExtractionResult
from agent.memory.dedup import Deduplicator, DeduplicationResult, DuplicationVerdict

# ── Phase 3 多 Agent 协作 ──────────────────────────────
from agent.memory.agent_store_factory import AgentStoreFactory, AgentStores
from agent.memory.shared_space import SharedSpace
from agent.role_prompts import load_role_config, load_all_roles, register_roles

# ── 上下文管理 ─────────────────────────────────────────
from agent.context_window import (
    estimate_tokens, count_total_tokens, is_overflow,
)
from agent.cache_break import CacheBreakDetector
from agent.compaction import compact, CompactionResult

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
    "FactStore", "FactEntry", "DomainIndex", "DomainEntry", "IndexEntry",
    "TaskDetector", "TaskDetection",
    "MemoryExtractor", "ExtractionResult",
    "Deduplicator", "DeduplicationResult", "DuplicationVerdict",
    # phase 3 multi-agent
    "AgentStoreFactory", "AgentStores",
    "SharedSpace",
    "load_role_config", "load_all_roles", "register_roles",
    # context management
    "estimate_tokens", "count_total_tokens", "is_overflow",
    "CacheBreakDetector", "compact", "CompactionResult",
]
