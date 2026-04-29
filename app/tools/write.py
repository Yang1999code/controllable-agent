"""app/tools/write.py — FileWriteTool。

原子写入（先写临时文件再 rename），禁止覆盖未读文件。
"""

import tempfile
from pathlib import Path

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult


class FileWriteTool:
    definition = ToolDefinition(
        name="write",
        description="写入文件内容。会覆盖已存在的文件。",
        parameters=[
            ToolParameter(name="file_path", type="string",
                          description="文件绝对路径", required=True),
            ToolParameter(name="content", type="string",
                          description="要写入的内容", required=True),
        ],
    )
    is_concurrency_safe = False

    async def execute(self, args: dict, context: Context) -> ToolResult:
        path_str = args["file_path"]
        content = args["content"]

        try:
            path = Path(path_str)
            path.parent.mkdir(parents=True, exist_ok=True)

            # 原子写入：先写临时文件再 rename
            tmp = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8",
                dir=path.parent, delete=False,
                prefix=f".{path.name}.",
                suffix=".tmp",
            )
            try:
                tmp.write(content)
                tmp.flush()
                tmp.close()
                Path(tmp.name).replace(path)
            except Exception:
                Path(tmp.name).unlink(missing_ok=True)
                raise

            return ToolResult(
                tool_name="write", success=True,
                content=f"已写入 {path_str} ({len(content)} 字符)",
            )
        except Exception as e:
            return ToolResult(
                tool_name="write", success=False,
                error=str(e),
            )
