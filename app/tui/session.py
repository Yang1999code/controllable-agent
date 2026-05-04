"""app/tui/session.py — TUI 会话管理（Scrollback 模式）。

连接 AgentLoop 与终端显示，实现类 Claude Code / OpenCode 的交互体验。

Scrollback 模式：
- 所有消息直接输出到终端 stdout，永不消失
- 可通过鼠标滚轮 / ↑↓ 键 / PgUp PgDn 回滚查看历史
- prompt_toolkit 处理底部输入行
- 无 alt screen 切换，无清屏操作

实时渲染：
- 通过 Hook 订阅 AgentLoop 的流式事件（STREAM_TEXT / STREAM_THINKING / TOOL_PROGRESS）
- 流式文本逐字显示
- 思考状态实时指示
- 工具调用折叠展示
- 多 Agent 状态面板

斜杠命令通过 COMMANDS 注册表管理，插件可通过 register_command 装饰器扩展。

参考：CCB scrollback 模式 / OpenCode session 视图。
"""

import asyncio
import logging
import sys
import threading
from typing import TYPE_CHECKING, Callable

from ai.types import AgentEvent, AgentEventType

from app.tui.display import (
    RESET, BOLD, DIM, ITALIC,
    CYAN, GREEN, YELLOW, RED,
    BRIGHT_GREEN, BRIGHT_BLUE, BRIGHT_YELLOW,
    BRIGHT_CYAN, BRIGHT_MAGENTA, GRAY, BRIGHT_RED,
    BG_GRAY,
    _term_width, _divider,
    format_user_message,
    format_tool_call, format_tool_result,
    format_status_line,
    _safe_write,
)
from app.tui.input_area import InputHandler

if TYPE_CHECKING:
    from agent.loop import AgentLoop, AgentResult
    from ai.types import Context
    from agent.hook import HookChain, HookHandler

logger = logging.getLogger(__name__)

# ── 命令注册表 ──────────────────────────────────────────

COMMANDS: dict[str, tuple[str, Callable]] = {}
"""斜杠命令注册表：{命令名: (描述, async_handler)}。插件可通过 register_command 扩展。"""


def register_command(name: str, description: str):
    """装饰器：注册斜杠命令到全局命令表。"""
    def decorator(func):
        COMMANDS[name] = (description, func)
        return func
    return decorator


# ── 工具图标映射 ──────────────────────────────────────────

_TOOL_ICONS = {
    "read": ("R", BRIGHT_CYAN),
    "write": ("W", BRIGHT_GREEN),
    "edit": ("E", BRIGHT_YELLOW),
    "bash": ("$", GREEN),
    "glob": ("G", BRIGHT_MAGENTA),
    "grep": ("S", BRIGHT_MAGENTA),
    "web_fetch": ("H", BRIGHT_BLUE),
    "web_search": ("Q", BRIGHT_BLUE),
    "delegate_task": ("D", BRIGHT_MAGENTA),
    "agent_message": ("M", BRIGHT_CYAN),
    "cross_agent_read": ("X", BRIGHT_CYAN),
}

_DEFAULT_TOOL_ICON = ("*", BRIGHT_GREEN)


def _tool_badge(tool_name: str) -> str:
    icon, color = _TOOL_ICONS.get(tool_name, _DEFAULT_TOOL_ICON)
    return f"{BG_GRAY}{color}{BOLD}[{icon}]{RESET}"


# ── TUI 会话 ──────────────────────────────────────────────

class TuiSession:
    """TUI 会话（Scrollback 模式 + 实时 Hook 驱动）。

    所有消息累积在终端滚动缓冲区中，quit 后仍可回看。
    通过 Hook 订阅 AgentLoop 的实时事件，实现：
    - 思考状态指示（◐ 思考中...）
    - 流式文本逐字显示
    - 工具调用折叠展示（工具名 + 状态 + 简短预览）
    - 多 Agent 状态面板
    """

    def __init__(self, loop: "AgentLoop", context: "Context"):
        self._loop = loop
        self._context = context
        self._input = InputHandler()
        self._turns = 0
        self._total_itokens = 0
        self._total_otokens = 0
        self._running = True

        # 实时渲染状态
        self._is_streaming = False
        self._stream_buffer = ""
        self._last_line_was_thinking = False
        self._current_tools: list[str] = []
        self._thinking_shown = False

        # 多 Agent 状态
        self._agent_statuses: dict[str, str] = {}  # agent_name -> status

        # 注册 Hook 监听器
        self._register_hooks()

    def _register_hooks(self):
        from agent.hook import HookHandler

        hooks = self._loop.hooks
        hooks.register(HookHandler(
            name="tui_stream_thinking",
            event_type=AgentEventType.STREAM_THINKING,
            callback=self._on_thinking,
            priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_stream_text",
            event_type=AgentEventType.STREAM_TEXT,
            callback=self._on_stream_text,
            priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_tool_progress",
            event_type=AgentEventType.TOOL_PROGRESS,
            callback=self._on_tool_progress,
            priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_agent_status",
            event_type=AgentEventType.AGENT_STATUS,
            callback=self._on_agent_status,
            priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_turn_start",
            event_type=AgentEventType.TURN_START,
            callback=self._on_turn_start,
            priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_turn_end",
            event_type=AgentEventType.TURN_END,
            callback=self._on_turn_end,
            priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_compaction",
            event_type=AgentEventType.CONTEXT_COMPACTION,
            callback=self._on_compaction,
            priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_subagent",
            event_type=AgentEventType.SUBAGENT_START,
            callback=self._on_subagent,
            priority=99,
        ))

    # ── Hook 回调（实时渲染）──────────────────────────────

    def _on_turn_start(self, event: AgentEvent):
        self._thinking_shown = False
        self._current_tools = []

    def _on_turn_end(self, event: AgentEvent):
        self._flush_stream()
        self._thinking_shown = False

    def _on_thinking(self, event: AgentEvent):
        if not self._thinking_shown:
            self._flush_stream()
            self._println(f"  {DIM}{ITALIC}... 思考中 ...{RESET}")
            self._thinking_shown = True

    def _on_stream_text(self, event: AgentEvent):
        text = event.data.get("text", "")
        if not text:
            return

        # 清除思考指示
        if self._thinking_shown:
            self._thinking_shown = False

        self._stream_buffer += text

        # 遇到换行或积累够一定量就刷新
        if "\n" in self._stream_buffer or len(self._stream_buffer) > 80:
            self._flush_stream()
        else:
            # 逐字追加到当前行（不换行）
            _safe_write(self._stream_buffer)
            sys.stdout.flush()
            self._stream_buffer = ""

    def _on_tool_progress(self, event: AgentEvent):
        tool_name = event.data.get("tool_name", "?")
        status = event.data.get("status", "")

        self._flush_stream()
        self._thinking_shown = False

        if status == "started":
            badge = _tool_badge(tool_name)
            # 提取工具参数中的文件路径（如有）
            args_raw = ""
            tc_list = self._current_tools
            self._current_tools.append(tool_name)
            self._println(f"  {badge} {CYAN}{tool_name}{RESET}{DIM}{args_raw}{RESET}")

        elif status == "executing":
            pass  # 已在 started 时显示

        elif status == "done":
            success = event.data.get("success", True)
            preview = event.data.get("output_preview", "")
            mark = f"{BRIGHT_GREEN}OK{RESET}" if success else f"{BRIGHT_RED}ERR{RESET}"
            # 折叠输出：最多 2 行预览
            if preview:
                lines = preview.strip().split("\n")[:2]
                preview_text = lines[0][:80]
                if len(lines) > 1 or len(preview) > 80:
                    preview_text += f" {DIM}(...){RESET}"
                self._println(f"    {DIM}{TOOL_RESULT_PREFIX}{mark}{RESET} {DIM}{preview_text}{RESET}")
            else:
                self._println(f"    {DIM}{TOOL_RESULT_PREFIX}{mark}{RESET}")

    def _on_agent_status(self, event: AgentEvent):
        name = event.data.get("agent_name", "?")
        status = event.data.get("status", "")
        self._agent_statuses[name] = status
        self._render_agent_panel()

    def _on_subagent(self, event: AgentEvent):
        name = event.data.get("agent_type", "?")
        action = event.data.get("action", "started")
        if action == "started":
            self._agent_statuses[name] = "running"
        else:
            self._agent_statuses[name] = "done"
        self._flush_stream()
        self._render_agent_panel()

    def _on_compaction(self, event: AgentEvent):
        layer = event.data.get("layer", "?")
        freed = event.data.get("freed", 0)
        self._flush_stream()
        self._println(f"  {YELLOW}[compact] {layer} freed {freed} tokens{RESET}")

    # ── 实时渲染辅助 ──────────────────────────────────────

    def _flush_stream(self):
        if self._stream_buffer:
            _safe_write(self._stream_buffer + "\n")
            sys.stdout.flush()
            self._stream_buffer = ""

    def _render_agent_panel(self):
        if not self._agent_statuses:
            return
        status_icons = {"running": f"{BRIGHT_YELLOW}~{RESET}", "done": f"{BRIGHT_GREEN}*{RESET}",
                        "error": f"{BRIGHT_RED}!{RESET}", "waiting": f"{GRAY}-{RESET}"}
        parts = []
        for name, status in self._agent_statuses.items():
            icon = status_icons.get(status, f"{GRAY}?{RESET}")
            parts.append(f"{icon}{CYAN}{name}{RESET}")
        self._println(f"  {DIM}[Agents: {' '.join(parts)}]{RESET}")

    # ── 公共入口 ────────────────────────────────────────

    async def run(self):
        """启动 TUI 会话（scrollback 模式，无 alt screen）。"""
        self._render_welcome()
        await self._input_loop()
        # 注销 Hook
        hooks = self._loop.hooks
        for name in ["tui_stream_thinking", "tui_stream_text", "tui_tool_progress",
                      "tui_agent_status", "tui_turn_start", "tui_turn_end",
                      "tui_compaction", "tui_subagent"]:
            hooks.unregister(name)
        self._println(f"\n{DIM}再见。{RESET}")

    # ── 输入循环 ────────────────────────────────────────

    async def _input_loop(self):
        while self._running:
            self._print_status_line()

            user_input = await self._input.read_input("> ")
            if user_input == "EXIT":
                self._running = False
                self._println()
                break
            if not user_input:
                continue

            if user_input.startswith("/"):
                if await self._handle_command(user_input):
                    continue

            await self._process_turn(user_input)

    # ── 对话处理 ────────────────────────────────────────

    async def _process_turn(self, user_input: str):
        self._turns += 1

        self._println()
        self._println(format_user_message(user_input))
        self._println(_divider("-"))
        sys.stdout.flush()

        try:
            result = await self._loop.run(user_input, self._context)
            self._total_itokens += result.total_input_tokens
            self._total_otokens += result.total_output_tokens

            # 不再重新渲染全部消息（Hook 已经实时渲染了）
            # 只渲染最终文本输出（如果有，且 Hook 未渲染）
            if result.final_output and not self._is_streaming:
                # 如果 Hook 已渲染过流式文本，final_output 已经显示了
                pass

        except Exception as e:
            self._println(f"{RED}Error: {e}{RESET}")
            logger.exception("TUI turn error")

        self._println()
        self._println(_divider("-"))
        self._print_status_line()

    # ── 输出辅助 ────────────────────────────────────────

    def _println(self, text: str = ""):
        _safe_write(text + "\n")
        sys.stdout.flush()

    def _print_status_line(self):
        context_pct = self._compute_context_pct()
        self._println(format_status_line(
            self._loop.model_name, self._turns,
            self._total_itokens, self._total_otokens,
            context_pct,
        ))

    def _compute_context_pct(self) -> float:
        """计算当前上下文占用百分比（而非累计 token）。"""
        try:
            from agent.context_window import count_total_tokens
            tools = self._loop.tools.get_definitions()
            tokens = count_total_tokens(
                self._context.messages,
                self._context.system_prompt,
                tools,
            )
            max_ctx = getattr(self._loop.config, "max_context_tokens", 0)
            if max_ctx <= 0:
                max_ctx = getattr(self._loop.provider, "_context_window_cache", 0) or 128000
            return (tokens / max_ctx) * 100 if max_ctx > 0 else 0.0
        except Exception:
            return 0.0

    def _render_welcome(self):
        logo = f"""
{BOLD}{CYAN}+{'=' * 50}+
|  {BOLD}my-agent {DIM}v0.1.0{CYAN}                                 |
|  {DIM}Empire Code -- 可控多智能体自迭代 Agent 框架{CYAN}     |
+{'=' * 50}+{RESET}
"""
        self._println(logo)
        self._println(f"  {DIM}底层模型:{RESET} {self._loop.model_name}")
        self._println(f"  {DIM}可用工具:{RESET} {self._loop.tool_count} 个")
        self._println(f"  {DIM}/help | /flowchart | /exit{RESET}")
        self._println()

    # ── 斜杠命令 ────────────────────────────────────────

    async def _handle_command(self, text: str) -> bool:
        parts = text.strip().split()
        cmd = parts[0].lower() if parts else ""

        if cmd in ("/exit", "/quit", "/q"):
            self._running = False
            return True

        if cmd in COMMANDS:
            _, handler = COMMANDS[cmd]
            await handler(self)
            return True

        ALIASES = {"/fc": "/flowchart", "/flow": "/flowchart",
                    "/fcd": "/fco", "/h": "/help"}
        resolved = ALIASES.get(cmd)
        if resolved and resolved in COMMANDS:
            _, handler = COMMANDS[resolved]
            await handler(self)
            return True

        self._println(f"  {YELLOW}未知命令: {cmd}{RESET} (输入 /help 查看可用命令)")
        self._println()
        return True


# ── 工具结果前缀 ──────────────────────────────────────────

TOOL_RESULT_PREFIX = "|_ "


# ── 内置斜杠命令（装饰器注册，插件可扩展）─────────────────

@register_command("/help", "显示帮助")
async def _cmd_help(session: TuiSession):
    session._println()
    session._println(f"{BOLD}可用命令:{RESET}")
    for name, (desc, _) in sorted(COMMANDS.items()):
        session._println(f"  {CYAN}{name:<16}{RESET} -- {desc}")
    session._println()
    session._println(f"{DIM}提示：Enter 提交，Esc+Enter 换行，Ctrl+C/D 退出{RESET}")
    session._println()


@register_command("/flowchart", "查看控制流程图")
async def _cmd_flowchart(session: TuiSession):
    from app.tui.flowchart import FlowchartSession
    fs = FlowchartSession()
    await fs.run_static()
    session._println()
    session._println(_divider("-"))


@register_command("/fco", "浏览器打开流程图详解")
async def _cmd_fco(session: TuiSession):
    from app.tui.flowchart import FlowchartSession
    fs = FlowchartSession()
    fs._open_html_detail()
    session._println(f"  {GRAY}{DIM}浏览器已打开详情页{RESET}")
    session._println()


@register_command("/clear", "清屏")
async def _cmd_clear(session: TuiSession):
    session._println("\n" * (_term_width() or 80))


@register_command("/model", "显示当前模型")
async def _cmd_model(session: TuiSession):
    session._println(f"  {DIM}当前模型:{RESET} {BOLD}{session._loop.model_name}{RESET}")
    session._println()


@register_command("/tools", "列出所有工具")
async def _cmd_tools(session: TuiSession):
    session._println(f"  {BOLD}已注册工具 ({session._loop.tool_count}):{RESET}")
    for name, tool in sorted(session._loop.tools.tools.items()):
        desc = getattr(tool.definition, "description", "") if hasattr(tool, "definition") else ""
        if len(desc) > 60:
            desc = desc[:57] + "..."
        badge = _tool_badge(name)
        session._println(f"  {badge} {CYAN}{name:<22}{RESET} {DIM}{desc}{RESET}")
    session._println()


@register_command("/tokens", "显示 Token 统计")
async def _cmd_tokens(session: TuiSession):
    ctx_pct = session._compute_context_pct()
    session._println(f"  {DIM}累计 Token 使用:{RESET}")
    session._println(f"  {BRIGHT_GREEN}输入:{RESET} {session._total_itokens}")
    session._println(f"  {BRIGHT_BLUE}输出:{RESET} {session._total_otokens}")
    session._println(f"  {YELLOW}合计:{RESET} {session._total_itokens + session._total_otokens}")
    session._println(f"  {CYAN}上下文占用:{RESET} {ctx_pct:.1f}%")
    session._println()


@register_command("/status", "显示 Agent 状态")
async def _cmd_status(session: TuiSession):
    session._println(f"  {BOLD}Agent 状态:{RESET}")
    if session._agent_statuses:
        for name, status in session._agent_statuses.items():
            icons = {"running": f"{BRIGHT_YELLOW}~ running{RESET}",
                     "done": f"{BRIGHT_GREEN}* done{RESET}",
                     "error": f"{BRIGHT_RED}! error{RESET}",
                     "waiting": f"{GRAY}- waiting{RESET}"}
            session._println(f"    {CYAN}{name:<15}{RESET} {icons.get(status, status)}")
    else:
        session._println(f"    {DIM}无活跃子 Agent{RESET}")
    session._println()
