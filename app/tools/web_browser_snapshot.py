"""app/tools/web_browser_snapshot.py — BrowserSnapshotTool。"""

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult
from agent.web import IWebAutomation


class BrowserSnapshotTool:
    definition = ToolDefinition(
        name="web_browser_snapshot",
        description="获取浏览器页面的无障碍树快照（比截图省 token）。",
        parameters=[
            ToolParameter(name="session_id", type="string",
                          description="浏览器会话 ID", required=True),
        ],
    )
    is_concurrency_safe = True

    async def execute(self, args: dict, context: Context) -> ToolResult:
        web: IWebAutomation | None = context.metadata.get("_web")
        if not web:
            return ToolResult(tool_name="web_browser_snapshot", success=False,
                              error="WebAutomation not available")
        try:
            content = await web.browser_snapshot(args["session_id"])
            return ToolResult(tool_name="web_browser_snapshot", success=True,
                              content=content)
        except Exception as e:
            return ToolResult(tool_name="web_browser_snapshot", success=False,
                              error=str(e))
