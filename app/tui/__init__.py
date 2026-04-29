"""app/tui/ — 终端交互界面模块。

与核心 Agent 代码完全解耦，仅通过 AgentLoop.run() 公共 API 交互。
方便后续迭代替换，不影响核心逻辑。

组件：
- display.py  — ANSI 渲染引擎（颜色/格式/spinner）
- input_area.py — 多行输入处理（prompt_toolkit + 降级）
- session.py   — TUI 会话管理（连接 Loop 与 Display）
"""

from app.tui.session import TuiSession

__all__ = ["TuiSession"]
