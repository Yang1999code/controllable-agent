"""agent/compaction.py — 四层上下文压缩。

参考 OpenCode compaction.ts（tail_start_id 指针机制）。

四层递进：
0. Truncate Results（无 API）—— 截断超大工具输出
1. Prune（无 API）—— 清除 3 轮前的工具输出
2. Summary（1 次 API）—— LLM 摘要旧消息，保留尾部
3. Emergency Truncation（无 API）—— 从最早消息开始删除
"""

import logging
import uuid
from dataclasses import dataclass, field

from ai.types import Message
from agent.context_window import count_total_tokens

logger = logging.getLogger(__name__)

DEFAULT_KEEP_TURNS = 3  # prune 保留最近 N 轮的工具输出
LARGE_TOOL_RESULT_THRESHOLD = 5000  # 超过此字符数的工具结果会被截断


@dataclass
class CompactionResult:
    messages: list[Message] = field(default_factory=list)
    tail_start_id: str = ""
    tokens_freed: int = 0
    layer_used: str = "none"  # "prune" | "summary" | "truncate" | "truncate_results" | "none"
    summary_text: str = ""


def compact(
    messages: list[Message],
    system_prompt: str,
    tool_defs: list,
    usable_context: int,
    threshold: float,
    emergency_threshold: float,
) -> CompactionResult:
    """应用压缩，按层递进直到 token 预算满足。"""
    if not messages:
        return CompactionResult(messages=list(messages), layer_used="none")

    tokens_before = count_total_tokens(messages, system_prompt, tool_defs)
    limit = int(usable_context * threshold)

    if tokens_before <= limit:
        return CompactionResult(messages=list(messages), layer_used="none")

    # ── Layer 0: 截断大工具输出（不丢消息，只缩短内容）──
    truncated = _truncate_large_tool_results(messages)
    tokens_after_trunc = count_total_tokens(truncated, system_prompt, tool_defs)
    if tokens_after_trunc <= limit:
        return CompactionResult(
            messages=truncated,
            tokens_freed=tokens_before - tokens_after_trunc,
            layer_used="truncate_results",
        )

    # ── Layer 1: Prune ──
    pruned = _prune_observations(truncated)
    tokens_after_prune = count_total_tokens(pruned, system_prompt, tool_defs)
    if tokens_after_prune <= limit:
        tail_id = _find_first_kept(messages, pruned)
        return CompactionResult(
            messages=pruned, tail_start_id=tail_id,
            tokens_freed=tokens_before - tokens_after_prune,
            layer_used="prune",
        )

    # ── Layer 2: Summary ──
    head, tail = _split_for_summary(pruned)
    if head:
        summary_text = _build_summary_text(head)
        summary_msg = Message(
            role="assistant",
            content=summary_text,
            id=_new_id(),
            summary=True,
        )
        compacted = [summary_msg] + tail
        tail_id = tail[0].id if tail else summary_msg.id
        tokens_after = count_total_tokens(compacted, system_prompt, tool_defs)
        if tokens_after <= limit:
            return CompactionResult(
                messages=compacted, tail_start_id=tail_id,
                tokens_freed=tokens_before - tokens_after,
                layer_used="summary", summary_text=summary_text,
            )

    # ── Layer 3: Emergency Truncation ──
    emergency_limit = int(usable_context * emergency_threshold)
    truncated_final = _truncate_head(pruned, system_prompt, tool_defs, emergency_limit)
    tail_id = truncated_final[0].id if truncated_final else ""
    tokens_after = count_total_tokens(truncated_final, system_prompt, tool_defs)
    return CompactionResult(
        messages=truncated_final, tail_start_id=tail_id,
        tokens_freed=tokens_before - tokens_after,
        layer_used="truncate",
    )


def _truncate_large_tool_results(messages: list[Message]) -> list[Message]:
    """截断超大工具输出，保留首尾关键信息。

    对于超过 LARGE_TOOL_RESULT_THRESHOLD 字符的工具结果：
    - 保留前 400 字符（头部信息）
    - 保留后 100 字符（尾部状态）
    - 中间替换为 "[...truncated...]"
    """
    result = []
    for msg in messages:
        if msg.role == "tool" and msg.content and len(msg.content) > LARGE_TOOL_RESULT_THRESHOLD:
            head = msg.content[:400]
            tail = msg.content[-100:]
            truncated_content = f"{head}\n\n[...truncated {len(msg.content) - 500} chars...]\n\n{tail}"
            result.append(Message(
                role=msg.role,
                content=truncated_content,
                id=msg.id,
                tool_call_id=msg.tool_call_id,
                tool_name=msg.tool_name,
            ))
        else:
            result.append(msg)
    return result


def _prune_observations(messages: list[Message]) -> list[Message]:
    """清除超过 DEFAULT_KEEP_TURNS 轮的工具输出。"""
    result: list[Message] = []
    turns_seen = 0

    for msg in reversed(messages):
        if msg.role == "assistant" and msg.tool_calls:
            turns_seen += 1
        if turns_seen >= DEFAULT_KEEP_TURNS and msg.role == "tool":
            result.insert(0, Message(
                role="tool",
                content="[Old tool result content cleared]",
                id=msg.id,
                tool_call_id=msg.tool_call_id,
                tool_name=msg.tool_name,
            ))
        else:
            result.insert(0, msg)

    return result


def _split_for_summary(
    messages: list[Message],
) -> tuple[list[Message], list[Message]]:
    """拆分为 head（待摘要）和 tail（保留）。

    保留最后 2 个有 tool_calls 的 assistant 轮次。
    不在 tool-call/tool-result 对中间切开。
    """
    assistant_turns = [
        i for i, m in enumerate(messages)
        if m.role == "assistant" and m.tool_calls
    ]
    if len(assistant_turns) < 3:
        return [], messages

    cutoff = assistant_turns[-2]
    while cutoff > 0 and messages[cutoff - 1].role == "tool":
        cutoff -= 1
    return messages[:cutoff], messages[cutoff:]


def _build_summary_text(head: list[Message]) -> str:
    """构造给压缩 LLM 的结构化摘要提示词。参考 OpenCode compaction template。"""
    conversation_parts = []
    for msg in head:
        prefix = msg.role.upper()
        content = msg.content or ""
        if msg.tool_calls:
            names = [tc.get("function", {}).get("name", "?") for tc in msg.tool_calls]
            content = f"[tool_calls: {', '.join(names)}] " + content
        if len(content) > 500:
            content = content[:500] + "..."
        conversation_parts.append(f"[{prefix}] {content}")

    return (
        "## 目标\n[分析上文，总结用户想要完成什么目标]\n\n"
        "## 重要指示\n[用户给出的重要指示和偏好]\n\n"
        "## 发现\n[过程中的重要发现和关键信息]\n\n"
        "## 完成情况\n已完成/进行中/待完成\n\n"
        "## 相关文件\n[被读/写/编辑的文件列表]\n\n"
        "---\n"
        "以上是历史对话的摘要。以下是保留的最近对话：\n\n"
        + "\n".join(conversation_parts[-3:])
    )


def _truncate_head(
    messages: list[Message],
    system_prompt: str,
    tool_defs: list,
    emergency_limit: int,
) -> list[Message]:
    """从最早消息开始删除，直到 token 数降到紧急上限以下。"""
    result = list(messages)
    while (count_total_tokens(result, system_prompt, tool_defs) > emergency_limit
           and len(result) > 2):
        result.pop(1)
    return result


def _find_first_kept(
    original: list[Message],
    pruned: list[Message],
) -> str:
    """找到 tail_start_id —— 压缩后第一条与原列表不同的消息 id。"""
    for i in range(min(len(original), len(pruned))):
        if original[i].content != pruned[i].content:
            return pruned[i].id or ""
    return pruned[-1].id if pruned else ""


def _new_id() -> str:
    return uuid.uuid4().hex[:12]
