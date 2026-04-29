"""app/tools/web_search.py — WebSearchTool。

Web 搜索，返回标题/URL/摘要。
"""

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult
from agent.web import IWebAutomation


class WebSearchTool:
    definition = ToolDefinition(
        name="web_search",
        description="搜索互联网。返回标题、URL 和摘要。",
        parameters=[
            ToolParameter(name="query", type="string",
                          description="搜索查询", required=True),
            ToolParameter(name="num_results", type="integer",
                          description="结果数量（默认 10）", required=False),
        ],
    )
    is_concurrency_safe = True

    async def execute(self, args: dict, context: Context) -> ToolResult:
        query = args["query"]
        num = int(args.get("num_results", 10))

        web: IWebAutomation | None = context.metadata.get("_web")
        if not web:
            return ToolResult(
                tool_name="web_search", success=False,
                error="WebAutomation not available",
            )

        try:
            results = await web.search(query, num)
            if not results:
                return ToolResult(
                    tool_name="web_search", success=True,
                    content="(未找到结果)",
                )
            lines = []
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r.get('title', 'N/A')}")
                lines.append(f"   URL: {r.get('url', 'N/A')}")
                lines.append(f"   {r.get('snippet', '')}")
                lines.append("")
            return ToolResult(
                tool_name="web_search", success=True,
                content="\n".join(lines),
            )
        except Exception as e:
            return ToolResult(tool_name="web_search", success=False, error=str(e))
