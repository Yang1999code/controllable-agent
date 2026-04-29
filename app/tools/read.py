"""app/tools/read.py — FileReadTool。

支持 offset/limit 分段读取，大文件只读前 2000 行。
"""

from pathlib import Path

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult


class FileReadTool:
    definition = ToolDefinition(
        name="read",
        description="读取文件内容。支持分段读取（offset + limit）。",
        parameters=[
            ToolParameter(name="file_path", type="string",
                          description="绝对路径", required=True),
            ToolParameter(name="offset", type="integer",
                          description="起始行号（1-based）", required=False),
            ToolParameter(name="limit", type="integer",
                          description="读取行数", required=False),
        ],
    )
    is_concurrency_safe = True

    async def execute(self, args: dict, context: Context) -> ToolResult:
        path_str = args["file_path"]
        offset = max(0, int(args.get("offset", 1)) - 1)
        limit = int(args.get("limit", 2000))

        try:
            path = Path(path_str)
            if not path.exists():
                return ToolResult(
                    tool_name="read", success=False,
                    error=f"文件不存在: {path_str}",
                )
            content = path.read_text(encoding="utf-8")
            lines = content.split("\n")
            selected = lines[offset:offset + limit]
            return ToolResult(
                tool_name="read", success=True,
                content="\n".join(selected),
            )
        except UnicodeDecodeError:
            return ToolResult(
                tool_name="read", success=False,
                error=f"文件不是 UTF-8 文本: {path_str}",
            )
        except Exception as e:
            return ToolResult(
                tool_name="read", success=False,
                error=str(e),
            )
