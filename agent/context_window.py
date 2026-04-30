"""agent/context_window.py — Token 估算 + 溢出检测。

不强制依赖 tiktoken，优先使用精确计数，回退到启发式估算。
"""

import hashlib
import json
import logging
from typing import TYPE_CHECKING

from ai.types import Message

if TYPE_CHECKING:
    from ai.provider import IModelProvider

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """估算 token 数。优先 tiktoken，回退到字符/4。"""
    if not text:
        return 0
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        pass
    return max(1, len(text) // 4)


def estimate_message_tokens(msg: Message) -> int:
    """估算单条消息的 token 数。"""
    n = estimate_tokens(msg.content or "")
    if msg.tool_calls:
        for tc in msg.tool_calls:
            args = tc.get("function", {}).get("arguments", "")
            n += estimate_tokens(args)
    return n


def estimate_tool_tokens(tool_defs: list) -> int:
    """估算工具定义的 token 数。"""
    try:
        return estimate_tokens(json.dumps(
            [{"name": getattr(t, "name", str(t)), "description": getattr(t, "description", "")}
             for t in tool_defs],
            ensure_ascii=False,
        ))
    except Exception:
        return 0


def count_total_tokens(
    messages: list[Message],
    system_prompt: str,
    tool_defs: list,
) -> int:
    """计算上下文总 token 数。"""
    total = estimate_tokens(system_prompt)
    total += estimate_tool_tokens(tool_defs)
    for msg in messages:
        total += estimate_message_tokens(msg)
    return total


def is_overflow(total_tokens: int, limit: int, threshold: float = 0.85) -> bool:
    """判断是否超出阈值。"""
    if limit <= 0:
        return False
    return total_tokens > int(limit * threshold)


def compute_prefix_hash(
    system_prompt: str,
    tool_defs: list,
    messages: list[Message],
) -> str:
    """计算可缓存前缀的 hash（用于 KV Cache 断点检测）。"""
    h = hashlib.sha256()
    h.update(system_prompt.encode())
    try:
        tool_str = json.dumps(
            [getattr(t, "name", str(t)) for t in tool_defs],
            sort_keys=True,
            ensure_ascii=False,
        )
    except Exception:
        tool_str = str(tool_defs)
    h.update(tool_str.encode())
    for msg in messages:
        h.update((msg.id or "").encode())
        h.update((msg.content or "").encode())
    return h.hexdigest()
