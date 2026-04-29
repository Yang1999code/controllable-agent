"""ai/ — 零依赖层：纯类型 + 抽象接口。

不 import 任何项目内模块。
"""

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
from ai.provider import (
    IModelProvider,
    LLMEvent,
)

__all__ = [
    "AgentEvent",
    "AgentEventType",
    "Context",
    "IModelProvider",
    "ITool",
    "LLMEvent",
    "Message",
    "ToolDefinition",
    "ToolParameter",
    "ToolResult",
]
