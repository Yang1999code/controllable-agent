"""agent/hook.py — IHook 实现。

事件链执行器 + 优先级排序 + 异常隔离。

参考：Hermes 13 hooks / oh-my-opencode 31+ hooks / ECC hooks.json
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from ai.types import AgentEvent, AgentEventType

logger = logging.getLogger(__name__)


# ── ABC ───────────────────────────────────────────────

class IHook(ABC):
    """Hook 系统抽象接口。

    定义事件驱动的插件扩展协议。每个事件点持有一个 Handler 列表，
    按优先级排序执行，异常隔离保证单个 handler 失败不影响其余。
    """

    @abstractmethod
    def register(self, handler: "HookHandler") -> None:
        """注册 hook 处理器。"""
        ...

    @abstractmethod
    def unregister(self, name: str) -> None:
        """按名称注销 hook 处理器。"""
        ...

    @abstractmethod
    async def fire(self, event: AgentEvent, chain_result: bool = False) -> list[Any]:
        """触发事件。依次执行所有匹配的 handler。"""
        ...


# ── Handler ───────────────────────────────────────────

@dataclass
class HookHandler:
    """单个 Hook 处理器。"""

    name: str
    event_type: AgentEventType
    callback: Callable[[AgentEvent], Any]
    priority: int = 50
    block_on_error: bool = False
    enabled: bool = True

    def __post_init__(self):
        if not 0 <= self.priority <= 100:
            raise ValueError(f"priority must be 0-100, got {self.priority}")


# ── 实现 ───────────────────────────────────────────────

class HookChain(IHook):
    """Hook 事件链执行器（IHook 实现）。

    每个事件点持有一个 Handler 列表，按 priority 排序执行。
    核心原则：
    - 每个 handler 独立 try/except，失败记日志继续
    - chain_result=False 时短路（默认行为）
    - chain_result=True 时异常阻断后续 handler
    """

    def __init__(self):
        self._handlers: dict[AgentEventType, list[HookHandler]] = {
            etype: [] for etype in AgentEventType
        }

    def register(self, handler: HookHandler) -> None:
        """注册 hook。"""
        self._handlers[handler.event_type].append(handler)
        self._handlers[handler.event_type].sort(key=lambda h: h.priority)

    def unregister(self, name: str) -> None:
        """按名称注销 hook。"""
        for handlers in self._handlers.values():
            handlers[:] = [h for h in handlers if h.name != name]

    async def fire(self, event: AgentEvent, chain_result: bool = False) -> list[Any]:
        """触发事件。依次执行所有匹配的 handler。

        chain_result=False: 异常不阻断，返回所有成功结果
        chain_result=True: 异常阻断后续 handler
        """
        results = []
        for handler in self._handlers.get(event.type, []):
            if not handler.enabled:
                continue
            try:
                result = handler.callback(event)
                if asyncio.iscoroutine(result):
                    result = await result
                results.append(result)
            except Exception as e:
                logger.warning(
                    f"Hook '{handler.name}' failed on {event.type.value}: {e}"
                )
                if chain_result:
                    raise
        return results


# ── V1 事件点定义 ──────────────────────────────────────

V1_EVENTS = frozenset({
    AgentEventType.TURN_START,
    AgentEventType.TURN_END,
    AgentEventType.TOOL_START,
    AgentEventType.TOOL_END,
    AgentEventType.LOOP_START,
    AgentEventType.LOOP_END,
    AgentEventType.ERROR,
    AgentEventType.TASK_COMPLETE,
})
