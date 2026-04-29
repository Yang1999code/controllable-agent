"""app/tools/web_browser_click.py — BrowserClickTool。"""

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult
from agent.web import IWebAutomation


class BrowserClickTool:
    definition = ToolDefinition(
        name="web_browser_click",
        description="点击浏览器页面中的元素。",
        parameters=[
            ToolParameter(name="element_id", type="string",
                          description="元素 ID", required=True),
            ToolParameter(name="session_id", type="string",
                          description="浏览器会话 ID", required=True),
        ],
    )
    is_concurrency_safe = False

    async def execute(self, args: dict, context: Context) -> ToolResult:
        web: IWebAutomation | None = context.metadata.get("_web")
        if not web:
            return ToolResult(tool_name="web_browser_click", success=False,
                              error="WebAutomation not available")
        try:
            result = await web.browser_click(args["element_id"], args["session_id"])
            return ToolResult(tool_name="web_browser_click", success=True,
                              content=str(result))
        except Exception as e:
            return ToolResult(tool_name="web_browser_click", success=False, error=str(e))
