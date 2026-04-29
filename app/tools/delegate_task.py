"""app/tools/delegate_task.py — DelegateTaskTool。

任务委托工具 —— 连接 AgentLoop 和 IAgentRuntime 的桥梁。

参考：Hermes delegate_tool.py / oh-my-opencode delegate_task
"""

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult


class DelegateTaskTool:
    definition = ToolDefinition(
        name="delegate_task",
        description=(
            "将任务委托给一个子Agent执行。"
            "子Agent有独立的上下文和受限的工具集，可以并行工作。"
            "适用于：跨文件重构、独立子任务、需要隔离上下文的操作。"
            "子Agent 默认禁止再委托（黑名单含 delegate_task）。"
        ),
        parameters=[
            ToolParameter(
                name="task", type="string",
                description="要委托的任务描述（必须具体、完整）",
                required=True,
            ),
            ToolParameter(
                name="agent_type", type="string",
                description=(
                    "代理类型：'coder' / 'reviewer' / 'explorer'。"
                    "留空则根据 task 内容自动选择最匹配的类型（Overlap Coefficient ≥ 0.3）"
                ),
                required=False,
            ),
        ],
    )
    is_concurrency_safe = False

    async def execute(self, args: dict, context: Context) -> ToolResult:
        task = args["task"]
        agent_type = args.get("agent_type")

        runtime = context.metadata.get("_runtime")
        if not runtime:
            return ToolResult(
                tool_name="delegate_task", success=False,
                error="AgentRuntime not available (tool only works in agent context)",
            )

        try:
            result = await runtime.spawn(
                agent_type=agent_type,
                task=task,
                context={"metadata": context.metadata},
            )
            return ToolResult(
                tool_name="delegate_task", success=True,
                content=(
                    f"子Agent [{result.agent_type}] 完成 (status={result.status}):\n"
                    f"{result.output[:3000]}"
                ),
            )
        except Exception as e:
            return ToolResult(
                tool_name="delegate_task", success=False,
                error=f"委托失败: {type(e).__name__}: {str(e)[:500]}",
            )
