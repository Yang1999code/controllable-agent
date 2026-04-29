"""app/tui/flowchart.py — 终端交互式流程图 + HTML 详情页。

双层设计：
- 终端层：5 节点粗粒度流程图，ANSI 字符绘制，方向键选择
- 详情层：选中节点 Enter → 浏览器打开 HTML 完整流程图（Mermaid.js）

与核心 Agent 代码完全解耦，零新依赖。
键盘处理 Windows(msvcrt) / Unix(termios) 自动适配。
"""

import asyncio
import os
import sys
import tempfile
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── ANSI 常量 ──────────────────────────────────────────

_ESC = "\033"
R = f"{_ESC}[0m";    B = f"{_ESC}[1m";   D = f"{_ESC}[2m"
C = f"{_ESC}[36m";   G = f"{_ESC}[92m";  Y = f"{_ESC}[93m"
MG = f"{_ESC}[95m";  GR = f"{_ESC}[90m"; W = f"{_ESC}[37m"
BG_C = f"{_ESC}[46m"  # cyan bg (selected)
BG_W = f"{_ESC}[47m"; BLK = f"{_ESC}[30m"  # white bg + black fg (detail)
BEEP = "\a"

# ── 键盘常量 ────────────────────────────────────────────

if sys.platform == "win32":
    import msvcrt as _msvcrt

    def _get_key() -> str:
        ch = _msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):  # arrow key prefix
            ch2 = _msvcrt.getch()
            return {b"H": "UP", b"P": "DOWN", b"K": "LEFT", b"M": "RIGHT"}.get(ch2, "")
        if ch == b"\r":
            return "ENTER"
        if ch == b"\x1b":
            return "ESC"
        try:
            return ch.decode("utf-8", errors="replace").lower()
        except Exception:
            return ""

else:
    import termios as _termios
    import tty as _tty
    import select as _select

    def _get_key() -> str:
        fd = sys.stdin.fileno()
        old = _termios.tcgetattr(fd)
        try:
            _tty.setraw(fd)
            if _select.select([sys.stdin], [], [], 0.1)[0]:
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    seq = sys.stdin.read(2)
                    if len(seq) >= 2:
                        return {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}.get(seq, "ESC")
                    return "ESC"
                if ch == "\r" or ch == "\n":
                    return "ENTER"
                return ch.lower()
            return ""
        finally:
            _termios.tcsetattr(fd, _termios.TCSADRAIN, old)


def _disp_width(s: str) -> int:
    """计算字符串终端显示宽度（CJK 等宽字符占2列）。"""
    import unicodedata
    w = 0
    for ch in s:
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ('W', 'F') else 1
    return w

@dataclass
class FlowNode:
    id: str          # "1"-"5", "A"-"F"
    layer: str       # "outer" | "inner" | "delegate"
    label: str       # 短名
    short: str       # 一行描述
    detail: str      # 详细说明
    phase: str       # "Phase 1" | "Phase 2" | "Phase 3"
    files: str       # 相关源文件


# 终端 5 节点（粗粒度）
TERMINAL_NODES: list[FlowNode] = [
    FlowNode("1", "outer",  "用户输入",
             "消息进入，加载CLAUDE.md",
             "用户消息追加到 context.messages。CLAUDE.md 从项目目录层级发现（_find_project_root → .git 标记），注入 prompt_builder 为 priority-0 片段。",
             "Phase 1",
             "agent/loop.py:91-125\nagent/claudemd.py"),
    FlowNode("2", "outer",  "LLM流式调用",
             "provider.stream() 返回事件流",
             "调用 IModelProvider.stream() 返回 AsyncIterator[LLMEvent]。4种事件：text_delta(文本增量) → tool_call(工具名/id) → tool_call_args(参数JSON增量) → done(token统计)。OpenAI/Anthropic 双协议适配。",
             "Phase 1",
             "ai/provider.py:149-265\nai/types.py (Message/LLMEvent)"),
    FlowNode("3", "inner",  "工具判断",
             "有工具调用? →执行 / 否→退出内层",
             "核心分叉点。检查 stream 返回是否有 tool_calls。无 → 纯文本回答，内层循环结束。有 → 进入工具执行 N4。had_tool_calls 标志跟踪状态，max_tool_calls 上限防无限循环。",
             "Phase 1",
             "agent/loop.py:198-220"),
    FlowNode("4", "inner",  "执行工具+收尾",
             "并行/串行 → 子Agent收件箱 → 回到LLM",
             "execute_many(): is_concurrency_safe 工具并行(gather)，不安全串行。结果>50K字符写磁盘、替换为截断预览+路径。check_inbox() 非阻塞poll子Agent消息。TURN_END hook + checkpoint + nudge(每10轮)。",
             "Phase 1-3",
             "agent/loop.py:225-291\nagent/tool_registry.py\nagent/autonomous_memory.py"),
    FlowNode("5", "outer",  "返回结果",
             "AgentResult: 状态+消息+Token统计",
             "循环结束，LOOP_END hook触发。返回 AgentResult(status/messages/turns/tool_calls/tokens/final_output)。V1 单轮后即退出，V2+ follow_up_queue 可继续。",
             "Phase 1",
             "agent/loop.py:297-307\nagent/step_outcome.py"),
]

# 委托入口节点（终端第6个，特殊标记可打开HTML）
DELEGATION_ENTRY = FlowNode(
    "D", "delegate", "子Agent委托",
    "spawn→选Agent→过滤→执行→回传",
    """子Agent委托完整流程 6 步：
A.spawn() — 入口，创建子Agent生命周期
B.选Agent — _tokenize() 中文分词 + Overlap Coefficient >= 0.3 匹配
C.过滤工具 — 黑名单(delegate_task/crystallize) + 可配白名单
D.子Context — 独立 messages 列表，注入 agent 初始 prompt
E.子循环 — 复用 provider，max_turns=50, asyncio.wait_for(300s)
F.返回结果 — send_message() 通过 asyncio.Queue 回传父Agent""",
    "Phase 3",
    "agent/runtime.py:281-433\nagent/tool_registry.py",
)

# HTML 完整 16 节点
HTML_NODES = TERMINAL_NODES + [
    FlowNode("A", "delegate", "spawn入口",
             "runtime.spawn(agent_type, task, context, depth)",
             "子Agent生命周期入口。支持 spawn/spawn_parallel/select_agent/send_message/check_inbox 四个核心操作。spawn_parallel 用 asyncio.Semaphore(3) 控制并发上限。",
             "Phase 3", "agent/runtime.py:281-330"),
    FlowNode("B", "delegate", "自动选Agent",
             "_tokenize() 分词 + Overlap Coefficient >= 0.3",
             "对任务描述和Agent配置中文分词（jieba主引擎 / bigram回退）。计算 Overlap Coefficient = |交集|/min(|A|,|B|)，最大且>=0.3者选中。",
             "Phase 3", "agent/runtime.py:170-210"),
    FlowNode("C", "delegate", "过滤工具",
             "黑名单排除 + 白名单限制",
             "默认黑名单排除 delegate_task 和 crystallize（防递归委托链）。可配置 whitelist 进一步限制子Agent可用工具范围。",
             "Phase 3", "agent/runtime.py:332-350"),
    FlowNode("D", "delegate", "构建子Context",
             "独立Context，隔离消息列表",
             "创建新 Context 对象，独立 messages 列表。注入 agent 配置中的 initial_prompt。depth+1 控制嵌套深度（max_depth=2）。",
             "Phase 3", "agent/runtime.py:352-390"),
    FlowNode("E", "delegate", "运行子循环",
             "复用provider，max_turns=50，300s超时",
             "创建子 AgentLoop，复用同一 provider。max_turns=50（较父Agent更保守）。asyncio.wait_for(300s超时)。子Agent失败不影响父和其他并行子Agent。",
             "Phase 3", "agent/runtime.py:392-420"),
    FlowNode("F", "delegate", "返回结果",
             "SubAgentResult → send_message → 父收件箱",
             "返回 SubAgentResult(status/output/usage/duration/error)。通过 send_message() 写入父Agent的 asyncio.Queue，父在 N4 执行后的 inbox check 中感知结果。",
             "Phase 3", "agent/runtime.py:422-433"),
]

# ── 流程图会话 ──────────────────────────────────────────

class FlowchartSession:
    """终端流程图交互会话。

    完全独立，接收按键输入直到 Q/Esc 退出。
    不依赖 prompt_toolkit，纯 ANSI + msvcrt/termios。
    """

    def __init__(self, open_detail_on_start: bool = False):
        self._nodes = TERMINAL_NODES
        self._selected = 0
        self._open_detail = open_detail_on_start

    async def run(self):
        """启动流程图交互。返回 'detail' 表示用户打开了详情页。"""
        self._render_all()      # 首次渲染
        try:
            return await self._key_loop()
        finally:
            self._erase()       # 清除流程图区域

    async def run_static(self):
        """静态展示流程图，立即返回，流程图保留在 scrollback 中。"""
        self._render_all()
        sys.stdout.write(f" {GR}{D}[/fcd 打开浏览器详情页]  |  直接输入消息开始对话{R}\n")
        sys.stdout.flush()
        return "quit"

    # ── 渲染 ─────────────────────────────────────────────

    _RENDER_LINES = 28  # 固定渲染行数（不含 trailing newline）

    def _clear_and_render(self):
        """光标上移后重绘（覆盖上次渲染）。"""
        # 上移 N 行 + 清除光标到屏尾
        sys.stdout.write(f"{_ESC}[{self._RENDER_LINES}A{_ESC}[J")
        sys.stdout.flush()
        self._render_all()
        sys.stdout.flush()

    def _erase(self):
        """清除流程图区域（光标上移后清空）。"""
        sys.stdout.write(f"{_ESC}[{self._RENDER_LINES}A{_ESC}[J")
        sys.stdout.flush()

    def _render_all(self):
        self._render_header()
        self._render_main_flow()
        self._render_delegation()
        self._render_footer()

    def _render_header(self):
        print(f"\n {B}{C}my-agent 控制流程{R}  {GR}{D}(方向键/数字键选择 | Enter浏览器详情 | Q/Esc返回){R}")
        print()

    def _render_main_flow(self):
        sel = self._selected

        def h(idx):
            return BG_C if sel == idx else ""

        # N1
        self._box_small("N1", "用户输入", "消息进入，加载CLAUDE.md", h(0))
        print(f"       {GR}▼{R}")
        # N2
        self._box_small("N2", "LLM流式调用", "provider.stream() 返回事件流", h(1))
        print(f"       {GR}│{R}")
        print(f"       {GR}▼{R}")
        # N3 (fork)
        self._box_small("N3", "工具判断?", "有tool_calls→执行 / 无→退出内层", h(2), MG)
        print(f"       {GR}│{G}是(有工具){R}        {MG}│{GR}否(纯文本){R}")
        print(f"       {GR}▼{R}                {MG}▼{R}")
        # N4 and N5 side by side
        self._box_pair(
            ("N4", "执行工具+收尾", "并行串行→收件箱→nudge", h(3), C),
            ("N5", "返回结果", "AgentResult(status/tokens)", h(4), C),
        )
        print(f"       {GR}│{R}")
        print(f"       {GR}└──→ {G}回到 N2 (内层循环){R}")

    def _render_delegation(self):
        sel = self._selected
        is_sel = sel == 5
        n = DELEGATION_ENTRY
        print()
        if is_sel:
            print(f" {BG_C}{B} [D] 子Agent委托 {R}{BG_C}: {n.short}{R}")
            print(f" {BG_C} spawn→选Agent→过滤→子Context→子循环→回传 {R}")
            print(f" {BG_C}{D} [Enter] 打开浏览器查看完整Mermaid流程图及详解 {R}")
        else:
            print(f" {GR}{D}[D] 子Agent委托:{R} {GR}{n.short}{R}")
            print()  # 占位，保持行数一致
            print()

    def _render_footer(self):
        print(f"\n {GR}{D}1-5选择节点 | D=委托 | Enter浏览器详情 | Q/Esc返回对话{R}")

    def _box_small(self, nid, title, desc, highlight, border_color=C):
        """紧凑单节点框。"""
        bc = border_color
        w = 44
        title_line = f"{nid} {title}"
        tw = _disp_width(title_line)
        dw = _disp_width(desc)
        pad_t = max(0, w - tw - 3)  # ┌─ + ─┐
        pad_d = max(0, w - dw - 2)  # two │ borders
        pre = f"  {highlight} " if highlight else "  "
        post = f" {R}" if highlight else ""
        print(f"{pre}{bc}┌─{B}{title_line}{R}{bc}{'─' * pad_t}┐{post}")
        print(f"{pre}{bc}│{R} {desc}{' ' * pad_d}{bc}│{post}")
        print(f"{pre}{bc}└{'─' * (w - 2)}┘{post}")

    def _box_pair(self, left, right):
        """两个节点并排（N4 + N5）。"""
        nid_l, title_l, desc_l, hl_l, bc_l = left
        nid_r, title_r, desc_r, hr_r, bc_r = right
        w = 28
        # Left
        tlw = _disp_width(f"{nid_l} {title_l}")
        dlw = _disp_width(desc_l)
        pt_l = max(0, w - tlw - 3)
        pd_l = max(0, w - dlw - 2)
        lb, lp, lc = bc_l, (f"{hl_l} " if hl_l else "  "), (f" {R}" if hl_l else "")
        # Right
        trw = _disp_width(f"{nid_r} {title_r}")
        drw = _disp_width(desc_r)
        pt_r = max(0, w - trw - 3)
        pd_r = max(0, w - drw - 2)
        rb, rp, rc = bc_r, (f"{hr_r} " if hr_r else "  "), (f" {R}" if hr_r else "")

        print(f"{lp}{lb}┌─{B}{nid_l} {title_l}{R}{lb}{'─' * pt_l}┐{lc}  "
              f"{rp}{rb}┌─{B}{nid_r} {title_r}{R}{rb}{'─' * pt_r}┐{rc}")
        print(f"{lp}{lb}│{R} {desc_l}{' ' * pd_l}{lb}│{lc}  "
              f"{rp}{rb}│{R} {desc_r}{' ' * pd_r}{rb}│{rc}")
        print(f"{lp}{lb}└{'─' * (w - 2)}┘{lc}  "
              f"{rp}{rb}└{'─' * (w - 2)}┘{rc}")

    # ── 键盘循环 ─────────────────────────────────────────

    async def _key_loop(self):
        while True:
            key = await asyncio.get_event_loop().run_in_executor(None, _get_key)
            if not key:
                await asyncio.sleep(0.05)
                continue

            if key in ("q", "ESC"):
                return "quit"

            if key == "ENTER":
                self._open_html_detail()
                return "detail"

            if key == "UP":
                self._selected = (self._selected - 1) % 6
                self._clear_and_render()
            elif key == "DOWN":
                self._selected = (self._selected + 1) % 6
                self._clear_and_render()
            elif key == "LEFT":
                self._selected = max(0, self._selected - 1)
                self._clear_and_render()
            elif key == "RIGHT":
                self._selected = min(5, self._selected + 1)
                self._clear_and_render()
            elif key in ("1", "2", "3", "4", "5"):
                self._selected = int(key) - 1
                self._clear_and_render()
            elif key == "d":
                self._selected = 5  # 委托入口
                self._clear_and_render()

    # ── HTML 详情页 ──────────────────────────────────────

    def _open_html_detail(self):
        """生成 HTML 并打开浏览器。"""
        html = self._build_html()
        tmpdir = Path(tempfile.gettempdir()) / "my-agent-html"
        tmpdir.mkdir(exist_ok=True)
        path = tmpdir / "flowchart.html"
        path.write_text(html, encoding="utf-8")
        webbrowser.open(f"file://{path.absolute()}")
        sys.stdout.write(f"\n {G}[OK]{R} 浏览器已打开详情页: {path}\n")
        sys.stdout.write(f" {GR}{D}按任意键返回对话...{R}\n")
        sys.stdout.flush()

    def _build_html(self) -> str:
        selected_node = TERMINAL_NODES[self._selected] if self._selected < 5 else DELEGATION_ENTRY
        # Map selected node to its parent block
        _NODE_BLOCK = {
            "1":"outer","2":"outer","3":"outer",
            "4":"inner","5":"outer2",
            "D":"delegate",
        }
        sel_block_id = _NODE_BLOCK.get(selected_node.id, "outer")
        sel_block = next((b for b in _BLOCK_DATA if b["id"] == sel_block_id), _BLOCK_DATA[0])
        fine_code = self._build_mermaid()
        coarse_code = self._build_coarse_mermaid()
        detail_cards = _build_detail_cards()

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>my-agent 控制流程详情</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    background: #0d1117; color: #c9d1d9; line-height: 1.6;
    padding: 20px; scroll-behavior: smooth;
}}
.container {{ max-width: 1100px; margin: 0 auto; }}
h1 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom:0.5em; margin-bottom:0.5em; }}
h2 {{ color: #7ee787; margin: 1em 0 0.5em; }}
h3 {{ color: #d2a8ff; margin: 0.5em 0; }}
.card {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 16px; margin: 12px 0; transition: border-color 0.3s;
}}
.card.selected {{ border-color: #58a6ff; background: #1a2233; }}
.card.highlight {{ border-color: #d2991d; background: #1a2233; box-shadow: 0 0 12px rgba(210,153,29,0.3); }}
.badge {{
    display: inline-block; padding: 2px 8px; border-radius: 12px;
    font-size: 0.8em; font-weight: bold; margin-right: 8px;
}}
.badge.p1 {{ background: #238636; color: #fff; }}
.badge.p2 {{ background: #b62324; color: #fff; }}
.badge.p3 {{ background: #1f6feb; color: #fff; }}
.node-id {{ color: #8b949e; font-size: 0.9em; }}
.files {{ color: #8b949e; font-size: 0.85em; margin-top: 4px; font-family: monospace; }}
.detail {{ margin-top: 8px; white-space: pre-wrap; }}
.detail-section {{ margin: 10px 0; padding: 10px; background: #0d1117; border-radius: 6px;
    border-left: 3px solid #30363d; }}
.detail-section.philosophy {{ border-left-color: #58a6ff; }}
.detail-section.architecture {{ border-left-color: #3fb950; }}
.detail-section.interaction {{ border-left-color: #d2991d; }}
.detail-section.flow {{ border-left-color: #d2991d; }}
.detail-section h4 {{ color: #c9d1d9; margin: 0 0 6px 0; font-size: 0.95em; }}
.detail-section p {{ color: #8b949e; font-size: 0.9em; margin: 0; }}
.mermaid-box {{ background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
    padding: 20px; margin: 16px 0; overflow-x: auto; }}
.mermaid-svg svg {{ max-width: 100%; cursor: default; }}
.mermaid-svg svg g.node {{ cursor: pointer; }}
.mermaid-svg svg g.node:hover {{ opacity: 0.85; }}
#mermaid-error {{ color: #f85149; padding: 16px; display: none; }}
.tabs {{ display: flex; gap: 8px; margin: 16px 0 0; flex-wrap: wrap; }}
.tab {{
    padding: 8px 16px; border: 1px solid #30363d; border-radius: 6px 6px 0 0;
    cursor: pointer; background: #161b22; color: #8b949e;
}}
.tab.active {{ background: #1a2233; color: #58a6ff; border-bottom-color: #1a2233; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.toggle-bar {{ display: flex; gap: 8px; margin: 8px 0; }}
.toggle-btn {{
    padding: 6px 14px; border: 1px solid #30363d; border-radius: 6px;
    cursor: pointer; background: #161b22; color: #8b949e; font-size: 0.85em;
}}
.toggle-btn.active {{ background: #1a2233; color: #58a6ff; border-color: #58a6ff; }}
</style>
</head>
<body>
<div class="container">
<h1>my-agent 控制流程图 -- 完整版</h1>
<p style="color:#8b949e;">
    来自 <strong>Empire Code</strong> 开源项目 · 三层架构（外层循环 + 内层循环 + 子Agent委托）
    · 终端选中: <span style="color:#58a6ff;font-weight:bold;">{selected_node.id}. {selected_node.label}</span>
    · 所属模块: <span style="color:{sel_block['color']};font-weight:bold;">{sel_block['title']}</span>
</p>

<div class="card selected block-card" id="card-{sel_block_id}" style="border-left: 4px solid {sel_block['color']};">
    <h2 style="color:{sel_block['color']};">{sel_block['title']}</h2>
    <p class="node-id">{sel_block['subtitle']} <span class="badge p3">{sel_block['phase']}</span></p>
    <p class="node-id" style="color:#c9d1d9;">包含节点: {sel_block['nodes']}</p>
    {_section('philosophy', '核心思想', sel_block['philosophy'])}
    {_section('interaction', '前后交互关系', sel_block['interaction'])}
    <div class="files">相关文件: {sel_block['files']}</div>
</div>

<div class="tabs">
    <div class="tab active" onclick="switchTab('mermaid')">Mermaid 流程图</div>
    <div class="tab" onclick="switchTab('cards')">架构模块 (4 大块)</div>
</div>

<div id="mermaid" class="tab-content active">
    <div class="toggle-bar">
        <button id="btn-coarse" class="toggle-btn active" onclick="switchLevel('coarse')">粗粒度 (6节点)</button>
        <button id="btn-fine" class="toggle-btn" onclick="switchLevel('fine')">细粒度 (11节点)</button>
        <span style="color:#8b949e;font-size:0.85em;margin-left:auto;align-self:center;">
            点击节点跳转到详情卡片
        </span>
    </div>
    <div class="mermaid-box">
        <div id="mermaid-coarse" class="mermaid-svg"></div>
        <div id="mermaid-fine" class="mermaid-svg" style="display:none;"></div>
        <div id="mermaid-error"></div>
    </div>
</div>

<div id="cards" class="tab-content">
{detail_cards}
</div>

</div>

<script>
mermaid.initialize({{ theme: 'dark', securityLevel: 'loose',
    flowchart: {{ useMaxWidth: true, curve: 'basis' }} }});

let currentLevel = 'coarse';

const coarseCode = `{coarse_code}`;
const fineCode = `{fine_code}`;

async function renderCoarse() {{
    try {{
        const {{ svg }} = await mermaid.render('mermaid-coarse-diagram', coarseCode);
        document.getElementById('mermaid-coarse').innerHTML = svg;
        attachNodeClicks('mermaid-coarse');
    }} catch (e) {{ showError(e, 'coarse'); }}
}}

async function renderFine() {{
    try {{
        const {{ svg }} = await mermaid.render('mermaid-fine-diagram', fineCode);
        document.getElementById('mermaid-fine').innerHTML = svg;
        attachNodeClicks('mermaid-fine');
    }} catch (e) {{ showError(e, 'fine'); }}
}}

function attachNodeClicks(containerId) {{
    const container = document.getElementById(containerId);
    if (!container) return;
    const svg = container.querySelector('svg');
    if (!svg) return;
    // Mermaid nodes have class 'node' with an ID we can map
    const gNodes = svg.querySelectorAll('g.node, g[class*=\"node\"]');
    gNodes.forEach(function(g) {{
        g.style.cursor = 'pointer';
        g.addEventListener('click', function() {{
            // Extract node label from text content
            const text = (g.textContent || '').trim();
            // Match known node IDs: N1-N10, DA-DF, D
            const match = text.match(/^(N\\d+|D[A-F]?|[A-F])\\b/);
            if (match) {{
                const nodeId = match[1];
                scrollToCard(nodeId);
            }}
        }});
    }});
}}

function scrollToCard(nodeId) {{
    // Map node IDs to parent block IDs (4 blocks)
    var blockId = 'outer';
    if (['N1','N2','N3'].includes(nodeId)) blockId = 'outer';
    else if (['N4','N5','N6','N7'].includes(nodeId)) blockId = 'inner';
    else if (['N8','N9','N10'].includes(nodeId)) blockId = 'outer2';
    else if (/^(D[A-F]?|[A-F]|D1)$/.test(nodeId)) blockId = 'delegate';
    // Switch to cards tab
    switchTab('cards');
    // Find and highlight block card
    var card = document.getElementById('card-' + blockId);
    if (card) {{
        card.classList.add('highlight');
        card.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        setTimeout(function() {{ card.classList.remove('highlight'); }}, 2500);
    }}
}}

function showError(e, level) {{
    const err = document.getElementById('mermaid-error');
    err.style.display = 'block';
    err.innerHTML = '<h3>Mermaid (' + level + ') 渲染失败</h3><pre>' + e.message + '</pre>';
}}

function switchLevel(level) {{
    currentLevel = level;
    document.getElementById('mermaid-coarse').style.display = level === 'coarse' ? '' : 'none';
    document.getElementById('mermaid-fine').style.display = level === 'fine' ? '' : 'none';
    document.getElementById('btn-coarse').classList.toggle('active', level === 'coarse');
    document.getElementById('btn-fine').classList.toggle('active', level === 'fine');
}}

function switchTab(name) {{
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const tab = document.querySelector('.tab[onclick*=\"'+name+'\"]');
    if (tab) tab.classList.add('active');
    document.getElementById(name).classList.add('active');
}}

// Global showDetail for Mermaid click callbacks
window.showDetail = function(nodeId) {{
    scrollToCard(nodeId);
}};

// Render both diagrams on load
renderCoarse();
renderFine();
</script>
</body>
</html>"""

    def _build_coarse_mermaid(self) -> str:
        """粗粒度 6 节点流程图（N1-N5 + 委托入口）。点开展开为细粒度。"""
        return (
            "flowchart TB\n"
            "    N1[\"N1 用户输入<br/>消息进入，加载CLAUDE.md\"]\n"
            "    N2[\"N2 LLM流式调用<br/>provider.stream() 返回事件流\"]\n"
            '    N3{"N3 工具判断?<br/>有调用→执行 / 无→退出"}\n'
            "    N4[\"N4 执行工具+收尾<br/>并行串行→收件箱→nudge\"]\n"
            "    N5[\"N5 返回结果<br/>AgentResult 聚合输出\"]\n"
            '    D1["子Agent委托<br/>spawn→选Agent→过滤→执行→回传"]\n'
            "    N1 --> N2\n"
            "    N2 --> N3\n"
            "    N3 -->|是-有工具| N4\n"
            "    N3 -->|否-纯文本| N5\n"
            "    N4 -->|回到LLM| N2\n"
            "    D1 -.->|send_message| N4\n"
            "    click N1 call showDetail(\"N1\")\n"
            "    click N2 call showDetail(\"N2\")\n"
            "    click N3 call showDetail(\"N3\")\n"
            "    click N4 call showDetail(\"N4\")\n"
            "    click N5 call showDetail(\"N5\")\n"
            "    click D1 call showDetail(\"D\")\n"
        )

    def _build_mermaid(self) -> str:
        """构建细粒度 Mermaid 流程图（无空行，Mermaid 解析器对空行敏感）。"""
        return (
            "flowchart TB\n"
            "    subgraph outer[\"外层循环 Outer FollowUp Loop\"]\n"
            "        N1[\"N1 用户输入<br/>消息进入 - 加载CLAUDE.md<br/>注入prompt_builder\"]\n"
            "        N2[\"N2 队列判断<br/>steer优先于followUp<br/>TURN_START hook\"]\n"
            "        N3[\"N3 动态Prompt<br/>优先级排序 - token预算裁剪<br/>conditional_filters\"]\n"
            "    end\n"
            "    subgraph inner[\"内层循环 Inner Tool Loop\"]\n"
            "        N4[\"N4 LLM流式<br/>provider.stream()<br/>4种事件: text/tool/args/done\"]\n"
            '        N5{"N5 工具调用?<br/>有tool_calls?"}\n'
            "        N6[\"N6 执行工具<br/>并行(gather)+串行<br/>结果>50K写磁盘\"]\n"
            "        N7[\"N7 子Agent收件箱<br/>check_inbox非阻塞poll<br/>注入子Agent消息\"]\n"
            "    end\n"
            "    subgraph outer2[\"外层收尾+退出\"]\n"
            "        N8[\"N8 轮次收尾<br/>TURN_END hook<br/>checkpoint+nudge-10轮\"]\n"
            '        N9{"N9 继续-退出?<br/>三路分支"}\n'
            "        N10[\"N10 返回结果<br/>AgentResult<br/>status - messages - tokens\"]\n"
            "    end\n"
            "    subgraph delegate[\"子Agent委托 Delegation\"]\n"
            "        DA[\"A spawn入口<br/>runtime.spawn()<br/>spawn_parallel-3并发\"]\n"
            "        DB[\"B 自动选Agent<br/>_tokenize()分词<br/>Overlap>=0.3\"]\n"
            "        DC[\"C 过滤工具<br/>黑名单: delegate/crystallize<br/>可选白名单\"]\n"
            "        DD[\"D 构建子Context<br/>独立messages<br/>depth+1, max_depth=2\"]\n"
            "        DE[\"E 运行子循环<br/>复用provider<br/>max_turns=50, 300s超时\"]\n"
            '        DF["F 返回结果<br/>SubAgentResult<br/>send_message -> 父Queue"]\n'
            "    end\n"
            "    N1 --> N2\n"
            "    N2 --> N3\n"
            "    N3 --> N4\n"
            "    N4 --> N5\n"
            "    N5 -->|否-纯文本| N8\n"
            "    N5 -->|是-有工具| N6\n"
            "    N6 --> N7\n"
            "    N7 -->|回到LLM| N4\n"
            "    N8 --> N9\n"
            "    N9 -->|继续| N2\n"
            "    N9 -->|退出| N10\n"
            "    DA --> DB --> DC --> DD --> DE --> DF\n"
            "    DF -.->|send_message -> Queue| N7\n"
            "    style N1 fill:#1a3344,stroke:#58a6ff,color:#e6edf3\n"
            "    style N2 fill:#1a3344,stroke:#58a6ff,color:#e6edf3\n"
            "    style N3 fill:#1a3344,stroke:#58a6ff,color:#e6edf3\n"
            "    style N4 fill:#1a3a2a,stroke:#3fb950,color:#e6edf3\n"
            "    style N5 fill:#332211,stroke:#d2991d,color:#e6edf3\n"
            "    style N6 fill:#1a3a2a,stroke:#3fb950,color:#e6edf3\n"
            "    style N7 fill:#1a3a2a,stroke:#3fb950,color:#e6edf3\n"
            "    style N8 fill:#221a33,stroke:#a371f7,color:#e6edf3\n"
            "    style N9 fill:#221a33,stroke:#a371f7,color:#e6edf3\n"
            "    style N10 fill:#221a33,stroke:#a371f7,color:#e6edf3\n"
            "    style DA fill:#331a22,stroke:#f778ba,color:#e6edf3\n"
            "    style DB fill:#331a22,stroke:#f778ba,color:#e6edf3\n"
            "    style DC fill:#331a22,stroke:#f778ba,color:#e6edf3\n"
            "    style DD fill:#331a22,stroke:#f778ba,color:#e6edf3\n"
            "    style DE fill:#331a22,stroke:#f778ba,color:#e6edf3\n"
            "    style DF fill:#331a22,stroke:#f778ba,color:#e6edf3\n"
            "    click N1 call showDetail(\"N1\")\n"
            "    click N2 call showDetail(\"N2\")\n"
            "    click N3 call showDetail(\"N3\")\n"
            "    click N4 call showDetail(\"N4\")\n"
            "    click N5 call showDetail(\"N5\")\n"
            "    click N6 call showDetail(\"N6\")\n"
            "    click N7 call showDetail(\"N7\")\n"
            "    click N8 call showDetail(\"N8\")\n"
            "    click N9 call showDetail(\"N9\")\n"
            "    click N10 call showDetail(\"N10\")\n"
            "    click DA call showDetail(\"D\")\n"
            "    click DB call showDetail(\"D\")\n"
            "    click DC call showDetail(\"D\")\n"
            "    click DD call showDetail(\"D\")\n"
            "    click DE call showDetail(\"D\")\n"
            "    click DF call showDetail(\"D\")\n"
        )

# -- 四大块数据（对应 Mermaid 子图分组）-----------------
_BLOCK_DATA = [
    {
        'id': 'outer',
        'title': '外层循环 -- 输入与准备',
        'subtitle': 'Outer FollowUp Loop (前段)',
        'nodes': 'N1 用户输入, N2 队列判断, N3 动态Prompt',
        'phase': 'Phase 1-2',
        'color': '#58a6ff',
        'philosophy': (
            '外层循环是 Agent 的感知与准备阶段，遵循一切皆消息原则。'
            '用户输入从 N1 进入后，经双队列路由 (N2) -- steer(外部中断通道)'
            '优先级高于 follow_up(正常消息通道)，实现可控制的核心理念。'
            'N3 动态 Prompt 构建按 priority 排序所有上下文片段，'
            'core(0-24) 无条件保留，其余按 token 预算裁剪，实现按需注入。'
        ),
        'interaction': (
            '上游：用户输入 / 上一轮 N9 的继续分支 / steer 外部中断\n'
            '下游：将组装好的 messages 传入内层循环 N4 (LLM 流式调用)\n'
            '关键交互：TURN_START hook 在 N2 触发，允许外部插件修改上下文'
        ),
        'files': 'agent/loop.py:91-125 (消息注入), agent/loop.py:150-190 (队列判断), agent/prompt_builder.py (动态Prompt)',
    },
    {
        'id': 'inner',
        'title': '内层循环 -- LLM调用与工具执行',
        'subtitle': 'Inner Tool Loop',
        'nodes': 'N4 LLM流式, N5 工具判断, N6 执行工具, N7 子Agent收件箱',
        'phase': 'Phase 1-3',
        'color': '#3fb950',
        'philosophy': (
            '内层循环是 Agent 的思考与行动阶段，是核心执行引擎。'
            'N4 通过 AsyncIterator[LLMEvent] 抽象屏蔽 OpenAI/Anthropic 协议差异，'
            '4 种事件 (text_delta/tool_call/tool_call_args/done) 覆盖完整流式生命周期。'
            'N5 是核心分叉点：无 tool_calls -> 纯文本回答，退出内层；'
            '有 tool_calls -> 进入 N6 执行。N6 采用并行优先策略：'
            'is_concurrency_safe 的工具 asyncio.gather 并发，不安全的串行。'
            '结果 >50K 字符自动写磁盘防 token 爆炸。'
            'N7 非阻塞 poll 子 Agent 消息，松耦合多 Agent 协作。'
        ),
        'interaction': (
            '上游：N3 组装好的 messages 进入 N4 流式调用\n'
            '下游：纯文本 -> N8 轮次收尾；工具调用 -> N6 执行后 N7 inbox check -> 回到 N4\n'
            '关键交互：N7 -> N4 的回边是内层循环的核心，子 Agent 消息以 user 形式注入下轮 LLM'
        ),
        'files': 'agent/loop.py:198-291, ai/provider.py:149-265, agent/tool_registry.py',
    },
    {
        'id': 'outer2',
        'title': '外层收尾 -- 判断与返回',
        'subtitle': 'Outer FollowUp Loop (后段)',
        'nodes': 'N8 轮次收尾, N9 继续/退出判断, N10 返回结果',
        'phase': 'Phase 3',
        'color': '#a371f7',
        'philosophy': (
            '外层收尾是 Agent 的反思与交付阶段，承担状态持久化和方向引导职责。'
            'N8 触发 TURN_END hook(允许外部插件记录/修改状态)，'
            'update_working_checkpoint 写入 .agent-memory 实现跨会话记忆。'
            '每 10 轮注入 nudge 提醒，防止 Agent 偏离目标。'
            'N9 三路分支：(a) 卡住检测 -> 强制退出 (b) 正常完成 -> 退出 '
            '(c) follow_up 有消息 -> 继续循环。N10 聚合 AgentResult '
            '(status/messages/turns/tool_calls/tokens/final_output)。'
        ),
        'interaction': (
            '上游：内层循环结束(纯文本输出或工具循环终止)后进入 N8\n'
            '下游：N9 决定继续 -> 回到 N2(外层次轮)；退出 -> N10 返回结果\n'
            '关键交互：LOOP_END hook 在 N10 触发；nudge 机制连接 autonomous_memory'
        ),
        'files': 'agent/loop.py:297-307, agent/step_outcome.py, agent/autonomous_memory.py',
    },
    {
        'id': 'delegate',
        'title': '子Agent委托 -- 多智能体协作',
        'subtitle': 'Delegation Sub-flow',
        'nodes': 'A spawn入口, B 自动选Agent, C 过滤工具, D 构建子Context, E 运行子循环, F 返回结果',
        'phase': 'Phase 3',
        'color': '#f778ba',
        'philosophy': (
            '子 Agent 委托是框架多智能体协作的核心机制，采用父委托 -> 子独立执行 -> '
            '结果异步回传三段式模式。A spawn() 创建子 Agent 生命周期，支持 '
            'spawn_parallel (Semaphore(3) 控并发)。B 中文分词 + Overlap Coefficient '
            '(>=0.3) 自动匹配最合适的 Agent。C 黑名单 (delegate_task/crystallize 防递归) '
            '+ 可配白名单实现最小权限。D 独立 Context + depth+1 控制嵌套深度 (max_depth=2)。'
            'E 复用 provider，max_turns=50 + 300s 超时实现故障隔离。'
            'F send_message() -> asyncio.Queue 异步回传父 Agent。'
        ),
        'interaction': (
            '上游：由 N6(执行工具)中的 delegate_task 工具触发 spawn\n'
            '下游：DF -> N7(父 Agent 收件箱)，父 Agent 在下轮 N7 inbox check 中感知结果\n'
            '关键交互：子 Agent 与父 Agent 通过 asyncio.Queue 异步松耦合通信；'
            '子 Agent 失败不影响父 Agent 和其他并行子 Agent'
        ),
        'files': 'agent/runtime.py:281-433 (spawn/select/send/check), agent/tool_registry.py',
    },
]


def _section(sec_class: str, title: str, body: str) -> str:
    return (
        f'<div class="detail-section {sec_class}">'
        f'<h4>{title}</h4>'
        f'<p>{body}</p>'
        f'</div>'
    )


def _build_detail_cards() -> str:
    cards = []
    for blk in _BLOCK_DATA:
        sections = (
            _section('philosophy', '核心思想', blk['philosophy']) +
            _section('interaction', '前后交互关系', blk['interaction'])
        )
        cards.append(f'''<div class="card block-card" id="card-{blk['id']}" style="border-left: 4px solid {blk['color']};">
    <h3 style="color:{blk['color']};">{blk['title']}</h3>
    <p class="node-id">{blk['subtitle']} <span class="badge p3">{blk['phase']}</span></p>
    <p class="node-id" style="color:#c9d1d9;">包含节点: {blk['nodes']}</p>
    {sections}
    <div class="files">相关文件: {blk['files']}</div>
</div>''')
    return '\n'.join(cards)

async def run_flowchart(open_detail: bool = False) -> str:
    """启动流程图会话。返回 'detail' 或 'quit'。"""
    session = FlowchartSession(open_detail_on_start=open_detail)
    return await session.run()
