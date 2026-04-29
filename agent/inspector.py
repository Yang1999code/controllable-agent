"""agent/inspector.py — IFlowInspector 实现。

AsyncQueue + 旁路协程，O(1) 零阻塞地监控 Agent 运行状态。

参考：需求2 原始设计 — AsyncQueue + 旁路协程模式
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class InspectionSnapshot:
    """一次检查快照。"""

    turn_count: int = 0
    tool_success_rate: float = 1.0
    avg_llm_latency_ms: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    active_tools: list[str] = field(default_factory=list)
    timestamp: float = 0.0


class IFlowInspector(Protocol):
    """运行流程检测器。"""

    async def push(self, event: dict) -> None: ...
    async def start(self, interval_sec: int = 5) -> None: ...
    async def get_recent_stats(self, window: int = 100) -> InspectionSnapshot: ...
    async def stop(self) -> None: ...


class FlowInspector:
    """IFlowInspector 实现。

    旁路模式：
    1. Agent 循环通过 push() 推送事件（非阻塞）
    2. 独立协程定期 drain 队列 → 聚合计算 → 写入文件
    3. 前端（CLI/Web UI）通过 get_recent_stats() 轮询

    滑动窗口：最近 100 轮
    """

    def __init__(self, queue_size: int = 1000):
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=queue_size)
        self._window: deque[dict] = deque(maxlen=100)
        self._running = False
        self._task: asyncio.Task | None = None

    async def push(self, event: dict) -> None:
        """非阻塞入队。队列满时静默丢弃。"""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    async def start(self, interval_sec: int = 5) -> None:
        """启动旁路消费协程。"""
        self._running = True
        self._task = asyncio.create_task(self._drain_loop(interval_sec))

    async def _drain_loop(self, interval_sec: int) -> None:
        while self._running:
            batch: list[dict] = []
            while not self._queue.empty():
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            # 聚合到滑动窗口
            for event in batch:
                event["timestamp"] = event.get("timestamp", time.time())
                self._window.append(event)
            await asyncio.sleep(interval_sec)

    async def get_recent_stats(self, window: int = 100) -> InspectionSnapshot:
        """获取滑动窗口统计。"""
        recent = list(self._window)[-window:]
        if not recent:
            return InspectionSnapshot()

        successes = sum(1 for e in recent if e.get("success"))
        return InspectionSnapshot(
            turn_count=len(recent),
            tool_success_rate=successes / len(recent) if recent else 1.0,
            avg_llm_latency_ms=sum(
                e.get("latency_ms", 0) for e in recent
            ) / len(recent) if recent else 0,
            total_input_tokens=sum(e.get("input_tokens", 0) for e in recent),
            total_output_tokens=sum(e.get("output_tokens", 0) for e in recent),
            active_tools=list(set(
                e.get("tool_name", "") for e in recent if e.get("tool_name")
            )),
            timestamp=recent[-1].get("timestamp", time.time()) if recent else time.time(),
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
