"""agent/prompt.py — IPromptBuilder 实现。

片段式动态组装 + 优先级排序 + 条件注入 + token 预算裁剪 + 事件驱动刷新。

参考：oh-my-opencode dynamic-agent-prompt-builder.ts (359行)
"""

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ai.types import Context


@dataclass
class PromptFragment:
    """一个 prompt 片段。"""

    name: str  # 唯一标识，如 "CAPABILITY_OVERVIEW"
    content: str  # 片段文本
    priority: int = 50  # 0-100，越小越核心（0-24 不可裁剪）
    condition: Callable[[Context], bool] | None = None  # 条件注入函数
    source: str = ""  # "builtin" / "plugin:{name}"


class IPromptBuilder(Protocol):
    """动态系统提示词构建器。

    参考 oh-my-opencode 的片段注册 + token 预算模式。
    """

    def register_fragment(self, fragment: PromptFragment) -> None: ...
    def unregister_fragment(self, name: str) -> None: ...
    def build(self, context: Context, max_tokens: int = 8000) -> str: ...
    def refresh_fragments(self, trigger: str) -> None: ...
    def get_token_usage(self) -> dict: ...


class PromptBuilder:
    """IPromptBuilder 的实现。

    组装逻辑：
    1. 按 priority 升序排列片段
    2. 核心片段（0-24）无条件保留
    3. 条件片段检查 condition(context)，不满足则跳过
    4. 从低优先级片段开始裁剪，直到总 token 数 <= max_tokens
    """

    def __init__(self):
        self._fragments: dict[str, PromptFragment] = {}
        self._cached_result: str | None = None
        self._cached_hash: str = ""

    def register_fragment(self, fragment: PromptFragment) -> None:
        self._fragments[fragment.name] = fragment
        self._cached_result = None

    def unregister_fragment(self, name: str) -> None:
        self._fragments.pop(name, None)
        self._cached_result = None

    def build(self, context: Context, max_tokens: int = 8000) -> str:
        """按优先级组装片段，超 token 预算则裁剪低优先级片段。"""
        # 筛选出满足条件的片段
        fragments = sorted(
            [f for f in self._fragments.values()
             if f.condition is None or f.condition(context)],
            key=lambda f: f.priority,
        )

        # 组装
        parts: list[str] = []
        token_count = 0

        for f in fragments:
            frag_tokens = len(f.content) // 3  # 粗略计数
            if f.priority <= 24:
                # 核心片段，必须保留
                parts.append(f.content)
                token_count += frag_tokens
            elif token_count + frag_tokens <= max_tokens:
                parts.append(f.content)
                token_count += frag_tokens
            # else: 裁剪

        return "\n\n".join(parts)

    def refresh_fragments(self, trigger: str) -> None:
        """事件驱动刷新：plugin_loaded / memory_updated / turn_end 时调用。

        V1：直接清缓存，下次 build() 重新计算。
        """
        self._cached_result = None

    def get_token_usage(self) -> dict:
        """返回各片段的 token 用量统计。"""
        return {
            name: {"chars": len(f.content), "approx_tokens": len(f.content) // 3}
            for name, f in self._fragments.items()
        }

    def _compute_hash(self, context: Context) -> str:
        """计算上下文哈希用于缓存判断（V2 预留）。"""
        data = context.system_prompt + str(len(context.messages))
        return hashlib.md5(data.encode()).hexdigest()
