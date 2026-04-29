"""app/tools/web_browser_type.py — BrowserTypeTool。"""

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult
from agent.web import IWebAutomation


class BrowserTypeTool:
    definition = ToolDefinition(
        name="web_browser_type",
        description="在浏览器页面的输入框中输入文本。",
        parameters=[
            ToolParameter(name="element_id", type="string",
                          description="元素 ID", required=True),
            ToolParameter(name="text", type="string",
                          description="要输入的文本", required=True),
            ToolParameter(name="session_id", type="string",
                          description="浏览器会话 ID", required=True),
        ],
    )
    is_concurrency_safe = False

    async def execute(self, args: dict, context: Context) -> ToolResult:
        web: IWebAutomation | None = context.metadata.get("_web")
        if not web:
            return ToolResult(tool_name="web_browser_type", success=False,
                              error="WebAutomation not available")
        try:
            result = await web.browser_type(args["element_id"], args["text"], args["session_id"])
            return ToolResult(tool_name="web_browser_type", success=True,
                              content=str(result))
        except Exception as e:
            return ToolResult(tool_name="web_browser_type", success=False, error=str(e))
