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
        self._system_prompt: str = ""
        self._hash: str = ""
        self._cached: str = ""

    def set_system_prompt(self, text: str) -> None:
        self._system_prompt = text
        self._hash = ""

    def register_fragment(self, fragment: PromptFragment) -> None:
        self._fragments[fragment.name] = fragment
        self._hash = ""

    def unregister_fragment(self, name: str) -> None:
        self._fragments.pop(name, None)
        self._hash = ""

    def build(self, context: Context, max_tokens: int = 8000) -> str:
        """按优先级组装片段，超 token 预算则裁剪低优先级片段。"""
        # 检查缓存 — 输入没变就返回上次结果
        new_hash = hashlib.sha256(
            (self._system_prompt + str(sorted(self._fragments.keys()))).encode()
        ).hexdigest()
        if new_hash == self._hash and self._cached:
            return self._cached

        # 筛选出满足条件的片段
        fragments = sorted(
            [f for f in self._fragments.values()
             if f.condition is None or f.condition(context)],
            key=lambda f: f.priority,
        )

        # 组装：系统提示词在最前面
        parts: list[str] = []
        if self._system_prompt:
            parts.append(self._system_prompt)
        token_count = len(self._system_prompt) // 3

        for f in fragments:
            frag_tokens = len(f.content) // 3
            if f.priority <= 24:
                parts.append(f.content)
                token_count += frag_tokens
            elif token_count + frag_tokens <= max_tokens:
                parts.append(f.content)
                token_count += frag_tokens

        self._hash = new_hash
        self._cached = "\n\n".join(parts)
        return self._cached

    def refresh_fragments(self, trigger: str) -> None:
        self._hash = ""

    def get_token_usage(self) -> dict:
        """返回各片段的 token 用量统计。"""
        return {
            name: {"chars": len(f.content), "approx_tokens": len(f.content) // 3}
            for name, f in self._fragments.items()
        }
