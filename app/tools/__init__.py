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


def register_all_tools(registry: ToolRegistry) -> None:
    """注册所有 Phase 1 基础工具。

    Phase 2 注册 Web 工具，Phase 3 注册 delegate_task + agent_message。
    """
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
    registry.register(FileEditTool())
    registry.register(BashTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
