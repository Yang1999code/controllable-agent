"""app/tui/session.py — TUI 会话管理（Scrollback 模式 + 实时渲染）。

核心设计：
- Agent 在后台 Task 中运行，不阻塞主循环
- 用户可以随时输入（中途补充信息）
- Hook 实时驱动思考 spinner、工具状态、Agent 面板
- Turn 级别的开始/结束/完成提示

参考：CCB scrollback 模式 / OpenCode session 视图。
"""

import asyncio
import logging
import sys
import time
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
    _safe_write,
    Spinner,
)
from app.tui.input_area import InputHandler

if TYPE_CHECKING:
    from agent.loop import AgentLoop, AgentResult
    from ai.types import Context
    from agent.hook import HookChain, HookHandler

logger = logging.getLogger(__name__)

# ── 命令注册表 ──────────────────────────────────────────

COMMANDS: dict[str, tuple[str, Callable]] = {}


def register_command(name: str, description: str):
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
TOOL_RESULT_PREFIX = "|_ "


def _tool_badge(tool_name: str) -> str:
    icon, color = _TOOL_ICONS.get(tool_name, _DEFAULT_TOOL_ICON)
    return f"{BG_GRAY}{color}{BOLD}[{icon}]{RESET}"


# ── TUI 会话 ──────────────────────────────────────────────

class TuiSession:
    """TUI 会话（Scrollback + 后台运行 + 实时 Hook 渲染）。"""

    def __init__(self, loop: "AgentLoop", context: "Context"):
        self._loop = loop
        self._context = context
        self._input = InputHandler()
        self._turns = 0
        self._total_itokens = 0
        self._total_otokens = 0
        self._running = True

        # 实时渲染状态
        self._stream_buffer = ""
        self._thinking_shown = False
        self._spinner: Spinner | None = None
        self._current_turn_tools: list[str] = []
        self._last_stream_time: float = 0.0  # 节流用

        # 多 Agent 状态
        self._agent_statuses: dict[str, str] = {}

        # 后台运行控制
        self._agent_done = asyncio.Event()
        self._agent_task: asyncio.Task | None = None
        self._last_result: "AgentResult | None" = None
        self._mid_run_queue: asyncio.Queue[str] = asyncio.Queue()

        # 注册 Hook 监听器
        self._register_hooks()

    def _register_hooks(self):
        from agent.hook import HookHandler

        hooks = self._loop.hooks
        hooks.register(HookHandler(
            name="tui_stream_thinking", event_type=AgentEventType.STREAM_THINKING,
            callback=self._on_thinking, priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_stream_text", event_type=AgentEventType.STREAM_TEXT,
            callback=self._on_stream_text, priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_tool_progress", event_type=AgentEventType.TOOL_PROGRESS,
            callback=self._on_tool_progress, priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_agent_status", event_type=AgentEventType.AGENT_STATUS,
            callback=self._on_agent_status, priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_turn_start", event_type=AgentEventType.TURN_START,
            callback=self._on_turn_start, priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_turn_end", event_type=AgentEventType.TURN_END,
            callback=self._on_turn_end, priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_compaction", event_type=AgentEventType.CONTEXT_COMPACTION,
            callback=self._on_compaction, priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_subagent_start", event_type=AgentEventType.SUBAGENT_START,
            callback=self._on_subagent_start, priority=99,
        ))
        hooks.register(HookHandler(
            name="tui_subagent_stop", event_type=AgentEventType.SUBAGENT_STOP,
            callback=self._on_subagent_stop, priority=99,
        ))

    # ── Hook 回调 ────────────────────────────────────────

    def _on_turn_start(self, event: AgentEvent):
        self._stop_spinner()
        turn = event.data.get("turn", "?")
        self._current_turn_tools = []
        self._println(f"  {BRIGHT_CYAN}>>>{RESET} {DIM}Turn {turn}{RESET}")

    def _on_turn_end(self, event: AgentEvent):
        self._flush_stream()
        turn = event.data.get("turn", "?")
        tool_count = event.data.get("tool_calls", 0)
        if tool_count > 0:
            self._println(f"  {BRIGHT_GREEN}---{RESET} {DIM}Turn {turn} ({tool_count} tool calls){RESET}")
        else:
            self._println(f"  {BRIGHT_GREEN}---{RESET} {DIM}Turn {turn}{RESET}")

    def _on_thinking(self, event: AgentEvent):
        if not self._thinking_shown:
            self._flush_stream()
            self._thinking_shown = True
            # 启动持续 spinner
            self._spinner = Spinner("思考中")
            self._spinner.start()

    def _on_stream_text(self, event: AgentEvent):
        text = event.data.get("text", "")
        if not text:
            return

        self._stop_spinner()
        self._thinking_shown = False

        self._stream_buffer += text

        now = time.monotonic()
        elapsed = now - self._last_stream_time

        # 节流：50ms 内只缓冲，不刷新屏幕
        if "\n" in self._stream_buffer or len(self._stream_buffer) > 80:
            self._flush_stream()
            self._last_stream_time = now
        elif elapsed >= 0.05:
            _safe_write("\r" + " " * (_term_width() or 80) + "\r")
            _safe_write(self._stream_buffer)
            sys.stdout.flush()
            self._stream_buffer = ""
            self._last_stream_time = now

    def _on_tool_progress(self, event: AgentEvent):
        tool_name = event.data.get("tool_name", "?")
        status = event.data.get("status", "")

        self._stop_spinner()
        self._flush_stream()
        self._thinking_shown = False

        if status == "started":
            badge = _tool_badge(tool_name)
            self._current_turn_tools.append(tool_name)
            self._println(f"  {badge} {CYAN}{tool_name}{RESET}")

        elif status == "done":
            success = event.data.get("success", True)
            preview = event.data.get("output_preview", "")
            mark = f"{BRIGHT_GREEN}OK{RESET}" if success else f"{BRIGHT_RED}ERR{RESET}"
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
        self._flush_stream()
        self._render_agent_panel()

    def _on_subagent_start(self, event: AgentEvent):
        name = event.data.get("agent_type", "?")
        task_id = event.data.get("task_id", "")
        self._agent_statuses[name] = "running"
        self._flush_stream()
        self._render_agent_panel()
        self._println(f"    {BRIGHT_YELLOW}> {CYAN}{name}{RESET} started {DIM}({task_id}){RESET}")

    def _on_subagent_stop(self, event: AgentEvent):
        name = event.data.get("agent_type", "?")
        status = event.data.get("status", "completed")
        duration = event.data.get("duration_ms", 0)
        self._agent_statuses[name] = "done" if status == "completed" else "error"
        self._flush_stream()
        self._render_agent_panel()
        mark = f"{BRIGHT_GREEN}OK{RESET}" if status == "completed" else f"{BRIGHT_RED}ERR{RESET}"
        dur_str = f"{duration / 1000:.1f}s" if duration else "?"
        self._println(f"    {mark} {CYAN}{name}{RESET} finished {DIM}({dur_str}){RESET}")

    def _on_compaction(self, event: AgentEvent):
        layer = event.data.get("layer", "?")
        freed = event.data.get("freed", 0)
        self._flush_stream()
        self._println(f"  {YELLOW}[compact] {layer} freed {freed} tokens{RESET}")

    # ── Spinner 控制 ──────────────────────────────────────

    def _stop_spinner(self):
        if self._spinner:
            self._spinner.stop()
            self._spinner = None

    # ── 渲染辅助 ──────────────────────────────────────────

    def _flush_stream(self):
        if self._stream_buffer:
            _safe_write(self._stream_buffer + "\n")
            sys.stdout.flush()
            self._stream_buffer = ""

    def _render_agent_panel(self):
        if not self._agent_statuses:
            return
        status_icons = {
            "running": f"{BRIGHT_YELLOW}~{RESET}",
            "done": f"{BRIGHT_GREEN}*{RESET}",
            "error": f"{BRIGHT_RED}!{RESET}",
            "waiting": f"{GRAY}-{RESET}",
        }
        parts = []
        for name, status in self._agent_statuses.items():
            icon = status_icons.get(status, f"{GRAY}?{RESET}")
            parts.append(f"{icon}{CYAN}{name}{RESET}")
        self._println(f"  {DIM}[Agents: {' '.join(parts)}]{RESET}")

    def _println(self, text: str = ""):
        _safe_write(text + "\n")
        sys.stdout.flush()

    def _compute_context_pct(self) -> float:
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

    def _print_status_line(self):
        ctx_pct = self._compute_context_pct()
        parts = [
            f"{BOLD}{self._loop.model_name}{RESET}",
            f"Context {ctx_pct:.0f}%",
            f"Turns {self._turns}",
            f"Tokens {BRIGHT_GREEN}in:{self._total_itokens}{RESET} {BRIGHT_BLUE}out:{self._total_otokens}{RESET}",
        ]
        self._println(f"  {DIM}{' | '.join(parts)}{RESET}")

    # ── 公共入口 ────────────────────────────────────────

    async def run(self):
        self._render_welcome()
        await self._input_loop()

        # 清理
        self._stop_spinner()
        hooks = self._loop.hooks
        for name in ["tui_stream_thinking", "tui_stream_text", "tui_tool_progress",
                      "tui_agent_status", "tui_turn_start", "tui_turn_end",
                      "tui_compaction", "tui_subagent_start", "tui_subagent_stop"]:
            hooks.unregister(name)
        self._println(f"\n{DIM}再见。{RESET}")

    # ── 主输入循环（后台运行 + 并行输入）──────────────────

    async def _input_loop(self):
        while self._running:
            self._print_status_line()

            user_input = await self._input.read_input("> ")
            if user_input == "EXIT":
                # 如果 Agent 还在跑，先取消
                await self._cancel_agent()
                self._running = False
                self._println()
                break
            if not user_input:
                continue

            if user_input.startswith("/"):
                if await self._handle_command(user_input):
                    continue

            # 如果 Agent 正在运行，用户输入作为补充信息
            if self._agent_task and not self._agent_task.done():
                self._handle_mid_run_input(user_input)
                continue

            # Agent 没在跑，正常启动新 turn
            await self._start_turn(user_input)

    async def _start_turn(self, user_input: str):
        """启动一个新的 Agent turn（后台运行）。"""
        self._turns += 1

        self._println()
        self._println(format_user_message(user_input))
        self._println(_divider("-"))
        sys.stdout.flush()

        # 重置状态
        self._agent_done.clear()
        self._last_result = None
        self._agent_statuses = {}

        # 启动 spinner 等待 Agent 响应
        self._spinner = Spinner("思考中")
        self._spinner.start()

        # 后台运行 Agent
        self._agent_task = asyncio.create_task(
            self._run_agent(user_input)
        )

        # 并行等待：Agent 完成 or 用户输入
        await self._wait_for_agent_or_input()

    async def _run_agent(self, user_input: str):
        """后台运行 AgentLoop.run()。"""
        try:
            result = await self._loop.run(user_input, self._context)
            self._last_result = result

            # 将运行中途收集的用户补充信息注入 context
            while not self._mid_run_queue.empty():
                try:
                    extra = self._mid_run_queue.get_nowait()
                    from ai.types import Message
                    import uuid
                    self._context.messages.append(Message(
                        role="user",
                        content=f"[用户补充] {extra}",
                        id=uuid.uuid4().hex[:12],
                    ))
                except asyncio.QueueEmpty:
                    break
        except Exception as e:
            self._println(f"{RED}Error: {e}{RESET}")
            logger.exception("Agent run error")
        finally:
            self._stop_spinner()
            self._flush_stream()
            self._agent_done.set()

    async def _wait_for_agent_or_input(self):
        """并行等待 Agent 完成或用户输入。"""
        while True:
            # 检查 Agent 是否完成
            if self._agent_done.is_set():
                break

            # 等待 Agent 完成，但每 100ms 检查一次（让 UI 保持响应）
            try:
                await asyncio.wait_for(self._agent_done.wait(), timeout=0.1)
                break
            except asyncio.TimeoutError:
                continue

        # Agent 完成，收尾
        await self._finish_turn()

    async def _finish_turn(self):
        """Agent 完成后的收尾渲染。"""
        try:
            self._stop_spinner()
            self._flush_stream()

            if self._last_result:
                self._total_itokens += self._last_result.total_input_tokens
                self._total_otokens += self._last_result.total_output_tokens

            # 完成提示
            self._println(_divider("-"))
            tools_used = len(set(self._current_turn_tools))
            if self._last_result:
                status = self._last_result.status
                turns = self._last_result.total_turns
                tool_calls = self._last_result.total_tool_calls
                self._println(
                    f"  {BRIGHT_GREEN}==={RESET} "
                    f"{BRIGHT_GREEN}完成{RESET} "
                    f"{DIM}(turns={turns}, tools={tool_calls}, "
                    f"tokens={self._last_result.total_input_tokens}+{self._last_result.total_output_tokens}){RESET}"
                )
            else:
                self._println(f"  {BRIGHT_GREEN}==={RESET} {DIM}结束{RESET}")

            self._println()
            self._print_status_line()
        except Exception as e:
            logger.warning("_finish_turn render error: %s", e)
        finally:
            # 清理
            self._agent_task = None
            self._current_turn_tools = []

    def _handle_mid_run_input(self, user_input: str):
        """Agent 正在运行时用户输入的处理。"""
        self._println()
        self._println(format_user_message(user_input))

        self._mid_run_queue.put_nowait(user_input)
        self._println(f"  {DIM}已记录补充信息，Agent 下一轮会看到{RESET}")
        self._println()

    async def _cancel_agent(self):
        """取消正在运行的 Agent。"""
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
            self._stop_spinner()
            self._flush_stream()
            try:
                await self._agent_task
            except asyncio.CancelledError:
                pass
            self._println(f"  {YELLOW}已取消当前任务{RESET}")

    # ── 欢迎界面 ────────────────────────────────────────

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
        self._println(f"  {DIM}/help | /flowchart | /exit | /多智能体{RESET}")
        self._println(f"  {DIM}运行中随时可输入补充信息{RESET}")
        self._println()

    # ── 斜杠命令 ────────────────────────────────────────

    async def _handle_command(self, text: str) -> bool:
        parts = text.strip().split()
        cmd = parts[0].lower() if parts else ""

        if cmd in ("/exit", "/quit", "/q"):
            await self._cancel_agent()
            self._running = False
            return True

        if cmd in COMMANDS:
            _, handler = COMMANDS[cmd]
            await handler(self)
            return True

        ALIASES = {"/fc": "/flowchart", "/flow": "/flowchart",
                    "/fcd": "/fco", "/h": "/help",
                    "/multi": "/多智能体", "/agents": "/多智能体"}
        resolved = ALIASES.get(cmd)
        if resolved and resolved in COMMANDS:
            _, handler = COMMANDS[resolved]
            await handler(self)
            return True

        self._println(f"  {YELLOW}未知命令: {cmd}{RESET} (输入 /help 查看可用命令)")
        self._println()
        return True


# ── 内置斜杠命令 ──────────────────────────────────────────

@register_command("/help", "显示帮助")
async def _cmd_help(session: TuiSession):
    session._println()
    session._println(f"{BOLD}可用命令:{RESET}")
    for name, (desc, _) in sorted(COMMANDS.items()):
        session._println(f"  {CYAN}{name:<16}{RESET} -- {desc}")
    session._println()
    session._println(f"{DIM}运行中随时可输入补充信息（Agent 下一轮会看到）{RESET}")
    session._println(f"{DIM}Enter 提交，Esc+Enter 换行，Ctrl+C/D 退出{RESET}")
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
    import shutil
    lines = shutil.get_terminal_size().lines or 24
    session._println("\n" * lines)


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
    if session._agent_task and not session._agent_task.done():
        session._println(f"    {BRIGHT_YELLOW}主 Agent 正在运行中{RESET}")
    else:
        session._println(f"    {BRIGHT_GREEN}主 Agent 空闲{RESET}")
    session._println()


@register_command("/多智能体", "启动多智能体协作模式")
async def _cmd_multi_agent(session: TuiSession):
    runtime = session._context.metadata.get("_runtime")
    if not runtime:
        session._println(f"  {RED}多 Agent 运行时未装配。请检查启动日志。{RESET}")
        session._println(f"  {DIM}提示：启动时加 -v 参数查看详细日志{RESET}")
        session._println()
        return

    # 检查是否已有任务在跑
    if session._agent_task and not session._agent_task.done():
        session._println(f"  {YELLOW}当前有任务正在运行，请等待完成后再启动多智能体{RESET}")
        session._println()
        return

    session._println()
    session._println(f"  {BOLD}{BRIGHT_MAGENTA}=== 多智能体协作模式 ==={RESET}")
    session._println()

    # 显示已注册角色
    roles = list(runtime._agent_types.keys()) if hasattr(runtime, "_agent_types") else []
    if roles:
        role_desc = {
            "coordinator": ("协调者", "多 Agent 调度、流程监控"),
            "planner": ("规划者", "任务分解、步骤规划"),
            "coder": ("编码者", "代码实现、文件操作"),
            "reviewer": ("审查者", "代码审查、测试验证"),
            "memorizer": ("记忆者", "经验总结、知识提取"),
        }
        session._println(f"  {BOLD}已注册角色 ({len(roles)}):{RESET}")
        for role in roles:
            label, desc = role_desc.get(role, (role, ""))
            badge_map = {
                "coordinator": f"{BG_GRAY}{BRIGHT_MAGENTA}{BOLD}[C]{RESET}",
                "planner": f"{BG_GRAY}{BRIGHT_BLUE}{BOLD}[P]{RESET}",
                "coder": f"{BG_GRAY}{BRIGHT_GREEN}{BOLD}[X]{RESET}",
                "reviewer": f"{BG_GRAY}{BRIGHT_YELLOW}{BOLD}[R]{RESET}",
                "memorizer": f"{BG_GRAY}{BRIGHT_CYAN}{BOLD}[M]{RESET}",
            }
            badge = badge_map.get(role, f"{BG_GRAY}{GRAY}{BOLD}[?]{RESET}")
            session._println(f"    {badge} {CYAN}{role:<14}{RESET} {DIM}{label} — {desc}{RESET}")
    session._println()

    # 提示用户输入任务
    session._println(f"  {DIM}请描述你要多智能体协作完成的任务：{RESET}")
    session._println(f"  {DIM}(输入任务后，5 个角色会自动分工执行){RESET}")
    session._println()

    # 读取用户任务
    task = await session._input.read_input("  任务> ")
    if not task or task.strip().lower() in ("exit", "quit", "cancel", "取消"):
        session._println(f"  {DIM}已取消{RESET}")
        session._println()
        return

    task = task.strip()
    session._println()
    session._println(_divider("-"))
    session._println(f"  {BOLD}{BRIGHT_MAGENTA}开始多智能体协作...{RESET}")
    session._println(f"  {DIM}任务: {task}{RESET}")
    session._println()

    # 初始化所有角色状态为 waiting
    for role in roles:
        session._agent_statuses[role] = "waiting"
    session._render_agent_panel()

    # 后台运行编排
    session._agent_done.clear()
    session._last_result = None
    session._turns += 1

    session._agent_task = asyncio.create_task(
        _run_orchestration(session, runtime, task)
    )
    await session._wait_for_agent_or_input()


async def _run_orchestration(session: TuiSession, runtime, task: str):
    """后台运行多智能体编排。"""
    try:
        memory_extractor = session._context.metadata.get("_memory_extractor")
        results = await runtime.orchestrate(task, memory_extractor=memory_extractor)

        # 汇总结果
        session._stop_spinner()
        session._flush_stream()

        completed = sum(1 for r in results if r.status == "completed")
        failed = sum(1 for r in results if r.status != "completed")
        total_tokens = sum(
            getattr(r, "usage", {}).get("total_tokens", 0)
            for r in results if hasattr(r, "usage")
        )

        session._println(_divider("-"))
        session._println(
            f"  {BOLD}{BRIGHT_MAGENTA}=== 多智能体协作完成{RESET} "
            f"{BRIGHT_GREEN}{completed} 成功{RESET}"
            f"{f' {BRIGHT_RED}{failed} 失败' if failed else ''}{RESET}"
            f"{DIM} (共 {len(results)} 个子任务){RESET}"
        )

        # 显示各角色结果
        for r in results:
            if hasattr(r, "agent_type"):
                status_mark = f"{BRIGHT_GREEN}OK{RESET}" if r.status == "completed" else f"{BRIGHT_RED}ERR{RESET}"
                preview = (r.output or "")[:120].replace("\n", " ")
                session._println(
                    f"    {status_mark} {CYAN}{r.agent_type:<14}{RESET} "
                    f"{DIM}{preview}{'...' if len(r.output or '') > 120 else ''}{RESET}"
                )

        session._println()
    except Exception as e:
        session._stop_spinner()
        session._flush_stream()
        session._println(f"  {RED}多智能体协作出错: {e}{RESET}")
        logger.exception("Orchestration error")
    finally:
        session._agent_done.set()
