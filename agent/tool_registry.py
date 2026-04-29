"""agent/tool_registry.py — ITool 实现。

工具注册、并发安全分组执行、结果预算裁剪。

参考：CCB tools.ts + StreamingExecutor / GenericAgent BaseHandler.dispatch()
"""

import asyncio
import hashlib
from pathlib import Path
from dataclasses import dataclass, field

from ai.types import ITool, ToolDefinition, ToolResult, Context


@dataclass
class ToolRegistry:
    """工具注册表。

    核心功能：
    1. 注册/注销工具
    2. 并发安全分组执行
    3. 结果预算裁剪（超限写磁盘）
    """

    tools: dict[str, ITool] = field(default_factory=dict)
    max_result_chars: int = 50000

    # ── 注册 ──

    def register(self, tool: ITool) -> None:
        """注册工具。同名工具后者覆盖前者。"""
        if not tool.definition.name:
            raise ValueError("Tool name cannot be empty")
        self.tools[tool.definition.name] = tool

    def unregister(self, name: str) -> None:
        self.tools.pop(name, None)

    def get_definitions(self) -> list[ToolDefinition]:
        """获取所有工具的 JSON Schema 列表（发给 LLM）。"""
        return [t.definition for t in self.tools.values()]

    # ── 执行调度 ──

    async def execute_many(
        self, tool_calls: list[dict], context: Context
    ) -> list[ToolResult]:
        """执行多个工具调用。安全的并行，不安全的串行。

        参考 CCB tool execution 逻辑。
        """
        safe_calls: list[tuple[ITool, dict]] = []
        unsafe_calls: list[tuple[ITool, dict]] = []

        for tc in tool_calls:
            tool = self.tools.get(tc.get("tool_name", ""))
            if tool is None:
                continue
            if tool.is_concurrency_safe:
                safe_calls.append((tool, tc.get("args", {})))
            else:
                unsafe_calls.append((tool, tc.get("args", {})))

        results: list[ToolResult] = []

        # 安全的并行执行
        if safe_calls:
            safe_results = await asyncio.gather(
                *[self._execute_one(tool, args, context) for tool, args in safe_calls],
                return_exceptions=True,
            )
            for r in safe_results:
                if isinstance(r, Exception):
                    results.append(ToolResult(
                        tool_name="unknown", success=False, content="",
                        error=str(r),
                    ))
                else:
                    results.append(r)

        # 不安全的串行执行
        for tool, args in unsafe_calls:
            result = await self._execute_one(tool, args, context)
            results.append(result)

        return results

    async def _execute_one(
        self, tool: ITool, args: dict, context: Context
    ) -> ToolResult:
        """执行单个工具 + 结果预算裁剪。"""
        try:
            result = await tool.execute(args, context)
            return self._apply_budget(result)
        except Exception as e:
            return ToolResult(
                tool_name=tool.definition.name,
                success=False,
                content="",
                error=f"{type(e).__name__}: {str(e)}",
            )

    def _apply_budget(self, result: ToolResult) -> ToolResult:
        """结果超 → 写磁盘，替换为路径引用。

        参考 CCB applyToolResultBudget 逻辑。
        """
        if len(result.content) <= self.max_result_chars:
            return result

        result_hash = hashlib.sha256(result.content.encode()).hexdigest()[:16]
        result_dir = Path(".agent-memory/tool_results")
        result_dir.mkdir(parents=True, exist_ok=True)
        file_path = result_dir / f"{result_hash}.txt"
        file_path.write_text(result.content, encoding="utf-8")

        result.content = (
            f"[结果过长，已写入文件] 路径: {file_path}\n"
            f"前 1000 字符预览:\n{result.content[:1000]}..."
        )
        result.truncated = True
        result.file_path = str(file_path)
        return result

    # ── 工具校验 ──

    @staticmethod
    def validate_args(tool: ITool, args: dict) -> list[str]:
        """校验工具参数。返回错误列表，空列表=合法。

        参考 Pi Agent validateToolArguments()。
        """
        errors = []
        for param in tool.definition.parameters:
            if param.required and param.name not in args:
                errors.append(f"缺少必需参数: {param.name}")
            if param.name in args and param.enum:
                if args[param.name] not in param.enum:
                    errors.append(f"参数 {param.name} 不在允许范围: {param.enum}")
        return errors
