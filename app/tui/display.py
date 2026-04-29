"""app/tui/display.py — 终端渲染引擎。

消息格式化、流式文本显示、工具调用可视化。
参考：CCB Messages.tsx / OpenCode session/index.tsx 消息渲染模式。

字符选择：全部 ASCII 安全，兼容 Windows GBK / 所有终端。
"""

import os
import shutil
import sys
from dataclasses import dataclass, field

# ── ANSI 颜色常量 ──────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"

# 前景色
RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
BLUE = "\033[34m"; MAGENTA = "\033[35m"; CYAN = "\033[36m"; WHITE = "\033[37m"
GRAY = "\033[90m"; BRIGHT_RED = "\033[91m"; BRIGHT_GREEN = "\033[92m"
BRIGHT_YELLOW = "\033[93m"; BRIGHT_BLUE = "\033[94m"; BRIGHT_MAGENTA = "\033[95m"
BRIGHT_CYAN = "\033[96m"

# 背景色
BG_GRAY = "\033[100m"

# ── ASCII 安全符号 ─────────────────────────────────────
# 全部使用 ASCII 避免 Windows GBK 编码问题

USER_PREFIX = ">"      # 用户消息前缀
TOOL_PREFIX = "[*]"    # 工具调用前缀
TOOL_RESULT = "|_"     # 工具结果前缀
DIVIDER_CHAR = "-"     # 分隔线字符
SPINNER_CHARS = ["|", "/", "-", "\\"]  # ASCII spinner 帧
CHECK_MARK = "OK"      # 成功标记
CROSS_MARK = "ERR"     # 失败标记
ARROW_UP = "in"        # 输入 token
ARROW_DOWN = "out"     # 输出 token
BULLET = "-"           # 列表项目符号

# ── 显示状态 ────────────────────────────────────────────

@dataclass
class DisplayState:
    """TUI 显示状态。"""
    messages: list[str] = field(default_factory=list)
    streaming_text: str = ""
    active_tool: str = ""
    tool_status: str = ""  # "running" | "done" | "error"
    status_line: str = ""
    input_prompt: str = ""
    input_active: bool = True


# ── 安全输出 ────────────────────────────────────────────

def _safe_write(text: str):
    """安全写入 stdout，处理编码问题。"""
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        safe = text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8", errors="replace")
        sys.stdout.write(safe)


# ── 格式化函数 ──────────────────────────────────────────

def _term_width() -> int:
    return shutil.get_terminal_size().columns or 80


def _divider(char: str = DIVIDER_CHAR) -> str:
    return DIM + char * _term_width() + RESET


def format_user_message(content: str) -> str:
    """用户消息：> 前缀（加粗青色），参考 CCB/OpenCode。"""
    prefix = f"{BOLD}{BRIGHT_CYAN}{USER_PREFIX}{RESET}"
    indent = "  "
    lines = content.strip().split("\n")
    result = f"{prefix} {BOLD}{lines[0]}{RESET}"
    for line in lines[1:]:
        result += f"\n{indent}{DIM}{line}{RESET}"
    return result


def format_assistant_text(content: str, stream: bool = False) -> str:
    """助手文本。"""
    return content


def format_tool_call(tool_name: str, status: str = "running") -> str:
    """工具调用：[*] 前缀，running=spinner, done=OK, error=ERR。

    参考 CCB：工具名带颜色徽章 + spinner。
    """
    if status == "running":
        spinner = _spinner_frame()
        return f"  {BRIGHT_GREEN}{TOOL_PREFIX}{RESET} {CYAN}{tool_name}{RESET} {YELLOW}{spinner}{RESET}"
    elif status == "done":
        return f"  {BRIGHT_GREEN}{TOOL_PREFIX}{RESET} {CYAN}{tool_name}{RESET} {GREEN}[{CHECK_MARK}]{RESET}"
    else:
        return f"  {BRIGHT_GREEN}{TOOL_PREFIX}{RESET} {CYAN}{tool_name}{RESET} {RED}[{CROSS_MARK}]{RESET}"


def format_tool_result(content: str, truncated: bool = False) -> str:
    """工具结果：|_ 前缀，dim 样式。参考 CCB MessageResponse。"""
    prefix = f"  {DIM}{TOOL_RESULT}{RESET} "
    lines = content.strip().split("\n")
    result = ""
    for i, line in enumerate(lines[:10]):  # 最多显示 10 行
        if i == 0:
            result += f"{prefix}{DIM}{line}{RESET}"
        else:
            result += f"\n     {DIM}{line}{RESET}"
    if len(lines) > 10:
        result += f"\n     {DIM}... (+{len(lines) - 10} more lines){RESET}"
    if truncated:
        result += f"\n     {YELLOW}(output truncated){RESET}"
    return result


def format_status_line(model: str, turns: int, itokens: int, otokens: int,
                       context_pct: float = 0.0) -> str:
    """底部状态栏。参考 CCB StatusLine。

    格式：Model | Context 12% | Turns 3 | Tokens in:500 out:300
    """
    parts = [
        f"{BOLD}{model}{RESET}",
        f"Context {context_pct:.0f}%",
        f"Turns {turns}",
        f"Tokens {BRIGHT_GREEN}{ARROW_UP}:{itokens}{RESET} {BRIGHT_BLUE}{ARROW_DOWN}:{otokens}{RESET}",
    ]
    return f"  {DIM}{' | '.join(parts)}{RESET}"


def format_loading_spinner(message: str = "Thinking") -> str:
    """加载动画。参考 CCB SpinnerWithVerb。"""
    spinner = _spinner_frame()
    return f"  {YELLOW}{spinner}{RESET} {DIM}{message}...{RESET}"


# ── spinner 帧动画 ──────────────────────────────────────

_spinner_idx = 0


def _spinner_frame() -> str:
    global _spinner_idx
    frame = SPINNER_CHARS[_spinner_idx % len(SPINNER_CHARS)]
    _spinner_idx += 1
    return frame


# ── 终端控制 ────────────────────────────────────────────

def clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def hide_cursor():
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()


def show_cursor():
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()


def enable_alt_screen():
    sys.stdout.write("\033[?1049h")
    sys.stdout.flush()


def disable_alt_screen():
    sys.stdout.write("\033[?1049l")
    sys.stdout.flush()


def reset_terminal():
    """恢复终端状态。"""
    show_cursor()
    disable_alt_screen()
    sys.stdout.write("\033[0m")
    sys.stdout.flush()
