"""agent/cache_break.py — KV Cache 断点检测。

追踪可缓存前缀的 hash，检测前缀变化。

设计参考 Claude Code promptCacheBreakDetection.ts（两阶段检测的简化版）。
"""

import hashlib
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.types import Message

logger = logging.getLogger(__name__)


class CacheBreakDetector:
    """追踪可缓存前缀 hash 的变化。

    前缀 = system_prompt + tool_defs + tail_start_id 之前的 messages。
    前缀没变 → cache 命中；前缀变了 → cache break。
    """

    def __init__(self):
        self._baseline_hash: str = ""
        self._tail_start_id: str = ""

    def is_break(
        self,
        system_prompt: str,
        tool_defs: list,
        messages: "list[Message]",
    ) -> bool:
        """检测前缀是否发生变化。"""
        new_hash = self._compute_prefix_hash(system_prompt, tool_defs, messages)
        if new_hash != self._baseline_hash:
            self._baseline_hash = new_hash
            return True
        return False

    def notify_compaction(self, tail_start_id: str) -> None:
        """压缩后重置基线。下次 is_break() 会重建。"""
        self._tail_start_id = tail_start_id
        self._baseline_hash = ""

    @property
    def tail_start_id(self) -> str:
        return self._tail_start_id

    def _compute_prefix_hash(
        self,
        system_prompt: str,
        tool_defs: list,
        messages: "list[Message]",
    ) -> str:
        idx = self._find_tail_index(messages)
        prefix_msgs = messages[:idx] if idx >= 0 else messages

        h = hashlib.sha256()
        h.update(system_prompt.encode())
        try:
            h.update(json.dumps(
                [getattr(t, "name", str(t)) for t in tool_defs],
                sort_keys=True, ensure_ascii=False,
            ).encode())
        except Exception:
            h.update(str(tool_defs).encode())
        for msg in prefix_msgs:
            h.update((msg.id or "").encode())
            h.update((msg.content or "").encode())
        return h.hexdigest()

    def _find_tail_index(self, messages: "list[Message]") -> int:
        if not self._tail_start_id:
            return -1
        for i, msg in enumerate(messages):
            if msg.id == self._tail_start_id:
                return i
        return -1
