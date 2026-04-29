"""app/tui/session.py — TUI 会话管理（Scrollback 模式）。

连接 AgentLoop 与终端显示，实现类 Claude Code / OpenCode 的交互体验。

Scrollback 模式：
- 所有消息直接输出到终端 stdout，永不消失
- 可通过鼠标滚轮 / ↑↓ 键 / PgUp PgDn 回滚查看历史
- prompt_toolkit 处理底部输入行
- 无 alt screen 切换，无清屏操作

参考：CCB scrollback 模式 / OpenCode session 视图。
"""

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

from app.tui.display import (
    RESET, BOLD, DIM,
    CYAN, GREEN, YELLOW, RED,
    BRIGHT_GREEN, BRIGHT_BLUE, BRIGHT_YELLOW,
    GRAY,
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

logger = logging.getLogger(__name__)


class TuiSession:
    """TUI 会话（Scrollback 模式）。

    所有消息累积在终端滚动缓冲区中，quit 后仍可回看。
    与核心代码完全解耦，仅使用 AgentLoop.run() 公共 API。
    """

    def __init__(self, loop: "AgentLoop", context: "Context"):
        self._loop = loop
        self._context = context
        self._input = InputHandler()
        self._turns = 0
        self._total_itokens = 0
        self._total_otokens = 0
        self._model = getattr(loop.provider, "model", "unknown")
        self._running = True

    # ── 公共入口 ────────────────────────────────────────

    async def run(self):
        """启动 TUI 会话（scrollback 模式，无 alt screen）。"""
        self._render_welcome()
        await self._input_loop()
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
        """处理一个对话轮次。"""
        self._turns += 1

        # 打印用户消息
        self._println()
        self._println(format_user_message(user_input))
        self._println(_divider("-"))
        sys.stdout.flush()

        try:
            result = await self._loop.run(user_input, self._context)
            self._total_itokens += result.total_input_tokens
            self._total_otokens += result.total_output_tokens

            self._render_messages_from_result(result)

            if result.final_output:
                self._println(result.final_output)

        except Exception as e:
            self._println(f"{RED}Error: {e}{RESET}")
            logger.exception("TUI turn error")

    # ── 消息渲染 ────────────────────────────────────────

    def _render_messages_from_result(self, result: "AgentResult"):
        """从 AgentResult 渲染本轮新增消息（跳过初始 user 消息）。"""
        messages = result.messages
        skip_user = True
        pending_tool_calls: dict[str, str] = {}

        for msg in messages:
            if skip_user and msg.role == "user":
                skip_user = False
                continue

            if msg.role == "assistant":
                if msg.content:
                    self._println(msg.content)
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_name = tc.get("function", {}).get("name", "?")
                        tool_id = tc.get("id", "")
                        pending_tool_calls[tool_id] = tool_name
                        self._println(format_tool_call(tool_name, "running"))

            elif msg.role == "tool":
                tool_id = msg.tool_call_id or ""
                tool_name = msg.tool_name or pending_tool_calls.pop(tool_id, "?")
                output = msg.content or ""
                self._println(format_tool_call(tool_name, "done"))
                self._println(format_tool_result(output, False))

        for tool_id, tool_name in pending_tool_calls.items():
            self._println(format_tool_call(tool_name, "done"))

    # ── 输出辅助 ────────────────────────────────────────

    def _println(self, text: str = ""):
        """安全打印一行。"""
        _safe_write(text + "\n")
        sys.stdout.flush()

    def _print_status_line(self):
        """打印状态行。"""
        context_pct = 0.0
        if hasattr(self._loop, "config"):
            max_ctx = getattr(self._loop.config, "max_context_tokens", 128000)
            if max_ctx > 0:
                context_pct = (self._total_itokens / max_ctx) * 100
        self._println(_divider("-"))
        self._println(format_status_line(
            self._model, self._turns,
            self._total_itokens, self._total_otokens,
            context_pct,
        ))

    def _render_welcome(self):
        """渲染欢迎界面。"""
        logo = f"""
{BOLD}{CYAN}+{'=' * 50}+
|  {BOLD}my-agent {DIM}v0.1.0{CYAN}                                 |
|  {DIM}Empire Code — 可控多智能体自迭代 Agent 框架{CYAN}     |
+{'=' * 50}+{RESET}
"""
        self._println(logo)
        self._println(f"  {DIM}底层模型:{RESET} {self._model}")
        self._println(f"  {DIM}可用工具:{RESET} {len(self._loop.tools.tools)} 个")
        self._println(f"  {DIM}/help 命令 | /flowchart 流程图 | /fcd 浏览器详情 | /exit 退出{RESET}")
        self._println()

    # ── 流程图 ──────────────────────────────────────────

    async def _show_flowchart(self):
        """静态显示流程图，保留在 scrollback 中，按任意键继续对话。"""
        from app.tui.flowchart import FlowchartSession
        fs = FlowchartSession()
        await fs.run_static()
        self._println()
        self._println(_divider("-"))

    def _open_flowchart_detail(self):
        """直接打开浏览器流程图详情页（不显示终端流程图）。"""
        from app.tui.flowchart import FlowchartSession
        fs = FlowchartSession()
        fs._open_html_detail()
        self._println(f"  {GRAY}{DIM}浏览器已打开详情页{RESET}")
        self._println()

    # ── 斜杠命令 ────────────────────────────────────────

    async def _handle_command(self, text: str) -> bool:
        """处理斜杠命令。返回 True 表示已处理。"""
        parts = text.strip().split()
        cmd = parts[0].lower() if parts else ""

        if cmd in ("/exit", "/quit", "/q"):
            self._running = False
            return True

        if cmd in ("/help", "/h"):
            self._println()
            self._println(f"{BOLD}可用命令:{RESET}")
            self._println(f"  {CYAN}/exit{RESET}, {CYAN}/quit{RESET}  — 退出")
            self._println(f"  {CYAN}/flowchart{RESET}     — 查看控制流程图")
            self._println(f"  {CYAN}/fcd{RESET}           — 浏览器打开流程图详解")
            self._println(f"  {CYAN}/clear{RESET}         — 清屏")
            self._println(f"  {CYAN}/model{RESET}         — 显示当前模型")
            self._println(f"  {CYAN}/tools{RESET}         — 列出所有工具")
            self._println(f"  {CYAN}/tokens{RESET}        — 显示 Token 统计")
            self._println(f"  {CYAN}/help{RESET}          — 显示此帮助")
            self._println()
            self._println(f"{DIM}提示：Enter 提交，Esc+Enter 换行，Ctrl+C/D 退出{RESET}")
            self._println()
            return True

        if cmd in ("/fcd", "/fco"):
            self._open_flowchart_detail()
            return True

        if cmd in ("/flowchart", "/flow", "/fc"):
            await self._show_flowchart()
            return True

        if cmd == "/clear":
            self._println("\n" * (_term_width() or 80))
            return True

        if cmd == "/model":
            self._println(f"  {DIM}当前模型:{RESET} {BOLD}{self._model}{RESET}")
            self._println()
            return True

        if cmd == "/tools":
            self._println(f"  {BOLD}已注册工具 ({len(self._loop.tools.tools)}):{RESET}")
            for name, tool in sorted(self._loop.tools.tools.items()):
                desc = getattr(tool.definition, "description", "") if hasattr(tool, "definition") else ""
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                self._println(f"  {CYAN}{name:<25}{RESET} {DIM}{desc}{RESET}")
            self._println()
            return True

        if cmd == "/tokens":
            self._println(f"  {DIM}累计 Token 使用:{RESET}")
            self._println(f"  {BRIGHT_GREEN}输入:{RESET} {self._total_itokens}")
            self._println(f"  {BRIGHT_BLUE}输出:{RESET} {self._total_otokens}")
            self._println(f"  {YELLOW}合计:{RESET} {self._total_itokens + self._total_otokens}")
            self._println()
            return True

        self._println(f"  {YELLOW}未知命令: {cmd}{RESET} (输入 /help 查看可用命令)")
        self._println()
        return True
