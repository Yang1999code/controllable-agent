"""app/tools/ — 内置工具集。

所有工具实现 ITool Protocol（鸭子类型），无需显式继承。
"""

from agent.tool_registry import ToolRegistry
from app.tools.read import FileReadTool
from app.tools.write import FileWriteTool
from app.tools.edit import FileEditTool
from app.tools.bash import BashTool
from app.tools.glob_tool import GlobTool
from app.tools.grep_tool import GrepTool
from app.tools.web_fetch import WebFetchTool
from app.tools.web_search import WebSearchTool
from app.tools.web_browser_navigate import BrowserNavigateTool
from app.tools.web_browser_click import BrowserClickTool
from app.tools.web_browser_type import BrowserTypeTool
from app.tools.web_browser_snapshot import BrowserSnapshotTool
from app.tools.delegate_task import DelegateTaskTool
from app.tools.agent_message import AgentMessageTool
from app.tools.cross_agent_read import CrossAgentReadTool


def register_all_tools(registry: ToolRegistry) -> None:
    """注册全部 15 个内置工具。

    Phase 1 (6): read, write, edit, bash, glob, grep
    Phase 2 (6): web_fetch, web_search, web_browser_*
    Phase 3 (3): delegate_task, agent_message, cross_agent_read
    """
    # Phase 1 — 基础文件/Shell 工具
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
    registry.register(FileEditTool())
    registry.register(BashTool())
    registry.register(GlobTool())
    registry.register(GrepTool())

    # Phase 2 — Web 工具
    registry.register(WebFetchTool())
    registry.register(WebSearchTool())
    registry.register(BrowserNavigateTool())
    registry.register(BrowserClickTool())
    registry.register(BrowserTypeTool())
    registry.register(BrowserSnapshotTool())

    # Phase 3 — 多 Agent 协作工具
    registry.register(DelegateTaskTool())
    registry.register(AgentMessageTool())
    registry.register(CrossAgentReadTool())
