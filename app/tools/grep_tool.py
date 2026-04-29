"""app/tools/grep_tool.py — GrepTool。

支持正则搜索，支持 glob 过滤文件，支持 -A/-B/-C 上下文。
"""

import re
from pathlib import Path

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult


class GrepTool:
    definition = ToolDefinition(
        name="grep",
        description=(
            "在文件中搜索正则表达式模式。"
            "支持 -A/-B/-C 上下文行数，支持 glob 文件过滤。"
        ),
        parameters=[
            ToolParameter(name="pattern", type="string",
                          description="正则表达式", required=True),
            ToolParameter(name="path", type="string",
                          description="搜索路径（文件或目录）", required=False),
            ToolParameter(name="glob", type="string",
                          description="文件名过滤（如 *.py）", required=False),
            ToolParameter(name="-A", type="integer",
                          description="显示匹配后的 N 行", required=False),
            ToolParameter(name="-B", type="integer",
                          description="显示匹配前的 N 行", required=False),
            ToolParameter(name="-C", type="integer",
                          description="显示匹配前/后的 N 行", required=False),
            ToolParameter(name="-i", type="boolean",
                          description="忽略大小写", required=False),
            ToolParameter(name="-n", type="boolean",
                          description="显示行号", required=False),
        ],
    )
    is_concurrency_safe = True

    async def execute(self, args: dict, context: Context) -> ToolResult:
        pattern = args["pattern"]
        search_path = Path(args.get("path", "."))
        file_glob = args.get("glob")
        context_a = int(args.get("-A", 0))
        context_b = int(args.get("-B", 0))
        context_c = int(args.get("-C", 0))
        ignore_case = args.get("-i", False)
        show_line_num = args.get("-n", True)

        a_lines = max(context_a, context_c)
        b_lines = max(context_b, context_c)

        try:
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(
                tool_name="grep", success=False,
                error=f"正则表达式错误: {e}",
            )

        try:
            if search_path.is_file():
                files = [search_path]
            elif search_path.is_dir():
                glob_pattern = file_glob or "**/*"
                files = list(search_path.glob(glob_pattern))
                files = [f for f in files if f.is_file()][:1000]
            else:
                return ToolResult(
                    tool_name="grep", success=False,
                    error=f"路径不存在: {search_path}",
                )
        except Exception as e:
            return ToolResult(tool_name="grep", success=False, error=str(e))

        output_lines: list[str] = []
        file_count = 0
        match_count = 0

        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception:
                continue

            lines = content.split("\n")
            matched_indices: list[int] = []

            for idx, line in enumerate(lines):
                if regex.search(line):
                    matched_indices.append(idx)

            if not matched_indices:
                continue

            file_count += 1
            match_count += len(matched_indices)

            if file_count > 1:
                output_lines.append("")

            # 计算上下文范围
            ranges: list[tuple[int, int]] = []
            for midx in matched_indices:
                start = max(0, midx - b_lines)
                end = min(len(lines), midx + a_lines + 1)
                ranges.append((start, end))

            # 合并重叠范围
            merged = []
            for rng in ranges:
                if merged and rng[0] <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], rng[1]))
                else:
                    merged.append(rng)

            for start, end in merged:
                output_lines.append(f"--- {file_path} ---")
                for i in range(start, end):
                    prefix = f"{i + 1}: " if show_line_num else ""
                    marker = ">" if i in matched_indices else " "
                    output_lines.append(f"{marker} {prefix}{lines[i]}")

            if match_count >= 1000:
                output_lines.append(f"\n... 已达 1000 条匹配上限，截断")
                break

        if not output_lines:
            return ToolResult(tool_name="grep", success=True, content="(未找到匹配)")

        return ToolResult(
            tool_name="grep", success=True,
            content="\n".join(output_lines[:5000]),  # 限制输出行
        )
