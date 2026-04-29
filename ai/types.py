"""ai/types.py — 核心类型定义。

零依赖，全项目最底层。定义 Message、Tool、Context、AgentEvent。
参考：Pi Agent types.ts / CCB message.ts
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


# ── Message ───────────────────────────────────────────

@dataclass
class Message:
    """统一消息类型。role 区分来源。

    参考 Pi Agent Message 联合类型。
    """

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: list[dict] | None = None  # assistant 消息中的 tool_calls（OpenAI 格式）
    metadata: dict = field(default_factory=dict)


# ── Tool ──────────────────────────────────────────────

@dataclass
class ToolParameter:
    """工具参数定义（JSON Schema 子集）。"""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    enum: list[str] | None = None


@dataclass
class ToolDefinition:
    """工具的静态描述——发给 LLM 的 JSON Schema。"""

    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)


@dataclass
class ToolResult:
    """工具执行结果。"""

    tool_name: str
    success: bool
    content: str = ""
    error: str | None = None
    truncated: bool = False
    file_path: str | None = None


@runtime_checkable
class ITool(Protocol):
    """工具接口（Protocol，鸭子类型，不需要显式继承）。

    参考 GenericAgent BaseHandler.dispatch() 模式。
    """

    definition: ToolDefinition
    is_concurrency_safe: bool

    async def execute(self, args: dict, context: "Context") -> ToolResult:
        """执行工具。args 由 LLM 生成，需要校验。"""
        ...


# ── Context ───────────────────────────────────────────

@dataclass
class Context:
    """Agent 上下文，在循环中传递和修改。

    参考 Pi Agent AgentContext。
    """

    system_prompt: str = ""
    messages: list[Message] = field(default_factory=list)
    tools: dict[str, ITool] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


# ── Agent Event ───────────────────────────────────────

class AgentEventType(Enum):
    # Phase 1（需求2）
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    LOOP_START = "loop_start"
    LOOP_END = "loop_end"
    STEER_INJECT = "steer_inject"
    ERROR = "error"
    # Phase 2（需求3）
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    LLM_CALL = "llm_call"
    PLUGIN_LOADED = "plugin_loaded"
    PLUGIN_UNLOADED = "plugin_unloaded"
    # Phase 3（需求4）
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"


@dataclass
class AgentEvent:
    """Hook 事件数据结构。"""

    type: AgentEventType
    data: dict = field(default_factory=dict)
    timestamp: float = 0.0
