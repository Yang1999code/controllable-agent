"""app/tools/web_fetch.py — WebFetchTool。

获取 URL 内容，可选 LLM 摘要。
"""

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult
from agent.web import IWebAutomation


class WebFetchTool:
    definition = ToolDefinition(
        name="web_fetch",
        description="获取 URL 内容并提取文本。支持可选 prompt 做定向摘要。",
        parameters=[
            ToolParameter(name="url", type="string",
                          description="要获取的 URL", required=True),
            ToolParameter(name="prompt", type="string",
                          description="可选的定向摘要提示", required=False),
        ],
    )
    is_concurrency_safe = True

    async def execute(self, args: dict, context: Context) -> ToolResult:
        url = args["url"]
        prompt = args.get("prompt", "")

        web: IWebAutomation | None = context.metadata.get("_web")
        if not web:
            return ToolResult(
                tool_name="web_fetch", success=False,
                error="WebAutomation not available",
            )

        try:
            content = await web.fetch(url, prompt)
            return ToolResult(tool_name="web_fetch", success=True, content=content)
        except Exception as e:
            return ToolResult(tool_name="web_fetch", success=False, error=str(e))
