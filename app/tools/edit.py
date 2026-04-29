"""app/tools/edit.py — FileEditTool。

精确字符串替换（old_string→new_string），唯一匹配才执行。
"""

from pathlib import Path

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult


class FileEditTool:
    definition = ToolDefinition(
        name="edit",
        description=(
            "精确替换文件中的字符串。"
            "old_string 必须在文件中唯一匹配（否则失败），"
            "替换为 new_string。"
        ),
        parameters=[
            ToolParameter(name="file_path", type="string",
                          description="文件绝对路径", required=True),
            ToolParameter(name="old_string", type="string",
                          description="要被替换的字符串（必须唯一）", required=True),
            ToolParameter(name="new_string", type="string",
                          description="替换后的新字符串", required=True),
            ToolParameter(name="replace_all", type="boolean",
                          description="替换所有匹配项（默认 false，只替换第一个）",
                          required=False),
        ],
    )
    is_concurrency_safe = False

    async def execute(self, args: dict, context: Context) -> ToolResult:
        path_str = args["file_path"]
        old_string = args["old_string"]
        new_string = args["new_string"]
        replace_all = args.get("replace_all", False)

        try:
            path = Path(path_str)
            if not path.exists():
                return ToolResult(
                    tool_name="edit", success=False,
                    error=f"文件不存在: {path_str}",
                )

            content = path.read_text(encoding="utf-8")

            if replace_all:
                if old_string not in content:
                    return ToolResult(
                        tool_name="edit", success=False,
                        error="old_string 在文件中未找到",
                    )
                new_content = content.replace(old_string, new_string)
            else:
                count = content.count(old_string)
                if count == 0:
                    return ToolResult(
                        tool_name="edit", success=False,
                        error="old_string 在文件中未找到",
                    )
                if count > 1:
                    return ToolResult(
                        tool_name="edit", success=False,
                        error=f"old_string 匹配了 {count} 处，不唯一。请提供更多上下文",
                    )
                new_content = content.replace(old_string, new_string, 1)

            path.write_text(new_content, encoding="utf-8")
            return ToolResult(
                tool_name="edit", success=True,
                content=f"已编辑 {path_str}",
            )
        except Exception as e:
            return ToolResult(
                tool_name="edit", success=False,
                error=str(e),
            )
