"""app/tools/glob_tool.py — GlobTool。

支持 **/*.py 递归模式，按修改时间排序。
"""

from pathlib import Path

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult


class GlobTool:
    definition = ToolDefinition(
        name="glob",
        description="按 glob 模式查找文件。支持 ** 递归匹配。",
        parameters=[
            ToolParameter(name="pattern", type="string",
                          description="glob 模式（如 **/*.py）", required=True),
            ToolParameter(name="path", type="string",
                          description="搜索目录（默认当前目录）", required=False),
        ],
    )
    is_concurrency_safe = True

    async def execute(self, args: dict, context: Context) -> ToolResult:
        pattern = args["pattern"]
        search_path = args.get("path", ".")

        try:
            base = Path(search_path).resolve()
            if not base.exists():
                return ToolResult(
                    tool_name="glob", success=False,
                    error=f"目录不存在: {search_path}",
                )

            files = sorted(
                base.glob(pattern),
                key=lambda p: p.stat().st_mtime if p.exists() else 0,
                reverse=True,
            )
            # 限制结果数
            results = [str(f.relative_to(base)) for f in files[:500]]
            if not results:
                return ToolResult(
                    tool_name="glob", success=True,
                    content="(未找到匹配文件)",
                )
            return ToolResult(
                tool_name="glob", success=True,
                content="\n".join(results),
            )
        except Exception as e:
            return ToolResult(
                tool_name="glob", success=False,
                error=str(e),
            )
