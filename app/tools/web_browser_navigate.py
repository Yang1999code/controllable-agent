"""app/tools/web_browser_navigate.py — BrowserNavigateTool。"""

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult
from agent.web import IWebAutomation


class BrowserNavigateTool:
    definition = ToolDefinition(
        name="web_browser_navigate",
        description="浏览器导航到指定 URL。",
        parameters=[
            ToolParameter(name="url", type="string",
                          description="目标 URL", required=True),
            ToolParameter(name="session_id", type="string",
                          description="浏览器会话 ID", required=True),
        ],
    )
    is_concurrency_safe = False

    async def execute(self, args: dict, context: Context) -> ToolResult:
        web: IWebAutomation | None = context.metadata.get("_web")
        if not web:
            return ToolResult(tool_name="web_browser_navigate", success=False,
                              error="WebAutomation not available")
        try:
            result = await web.browser_navigate(args["url"], args["session_id"])
            return ToolResult(tool_name="web_browser_navigate", success=True,
                              content=str(result))
        except Exception as e:
            return ToolResult(tool_name="web_browser_navigate", success=False, error=str(e))
