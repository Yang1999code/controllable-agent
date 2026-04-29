"""app/tui/input_area.py — 多行输入处理。

prompt_toolkit 封装：多行输入、历史记录、键盘绑定。
参考：CCB PromptInput.tsx / OpenCode prompt/index.tsx。

解耦设计：如果 prompt_toolkit 未安装，自动降级为 input()。
"""

import asyncio
import logging
from typing import Callable

logger = logging.getLogger(__name__)

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.formatted_text import HTML
    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False
    logger.debug("prompt_toolkit not installed, falling back to input()")


# ── prompt_toolkit 风格 ──────────────────────────────────

_INPUT_STYLE = Style.from_dict({
    "prompt": "ansicyan bold",
    "input": "",
    "status": "#888888",
})


def _create_keybindings() -> KeyBindings:
    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        """Enter 提交（Alt+Enter / Esc Enter 换行）。"""
        buffer = event.current_buffer
        buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _(event):
        """Esc + Enter 插入换行。"""
        event.current_buffer.insert_text("\n")

    @kb.add("c-c")
    def _(event):
        """Ctrl+C 退出。"""
        event.app.exit(result="EXIT")

    @kb.add("c-d")
    def _(event):
        """Ctrl+D 在空行时退出。"""
        if not event.current_buffer.text.strip():
            event.app.exit(result="EXIT")

    return kb


# ── 输入处理器 ──────────────────────────────────────────

class InputHandler:
    """终端输入处理器。

    特性：
    - 多行输入（Enter 提交，Esc+Enter 换行）
    - 历史记录（↑↓ 浏览）
    - Ctrl+C / Ctrl+D 退出
    - 自动降级为 input()
    """

    def __init__(self):
        self._history: list[str] = []
        self._session: PromptSession | None = None

    def _get_session(self) -> PromptSession:
        if self._session is None and _HAS_PROMPT_TOOLKIT:
            self._session = PromptSession(
                history=InMemoryHistory(),
                key_bindings=_create_keybindings(),
                style=_INPUT_STYLE,
                multiline=True,
                wrap_lines=True,
                complete_while_typing=False,
            )
        return self._session

    async def read_input(self, prompt: str = "❯ ") -> str:
        """异步读取用户输入。

        返回用户输入的文本，"EXIT" 表示退出，"" 表示空输入。
        """
        if _HAS_PROMPT_TOOLKIT:
            return await self._read_ptk(prompt)
        else:
            return await self._read_simple(prompt)

    async def _read_ptk(self, prompt: str) -> str:
        session = self._get_session()
        try:
            text = await session.prompt_async(
                HTML(f"<prompt>{prompt}</prompt> "),
            )
            text = text.strip()
            if text:
                self._history.append(text)
            return text
        except (EOFError, KeyboardInterrupt):
            return "EXIT"

    async def _read_simple(self, prompt: str) -> str:
        """降级：单行 input()。"""
        loop = asyncio.get_running_loop()
        try:
            text = await loop.run_in_executor(None, input, f"{prompt} ")
            text = text.strip()
            if text:
                self._history.append(text)
            return text
        except (EOFError, KeyboardInterrupt):
            return "EXIT"

    def get_history(self) -> list[str]:
        return list(self._history)
