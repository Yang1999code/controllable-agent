"""app/tools/agent_message.py — AgentMessageTool。

子Agent 间通信工具。通过 AgentRuntime._inboxes 异步传递。

设计意图：
- 主Agent → 子Agent：spawn 后通过 send_message 传递额外指令
- 子Agent → 主Agent：回传中间发现或请求更多上下文
- 消息通过 AgentRuntime._inboxes 异步传递，不阻塞
"""

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult


class AgentMessageTool:
    definition = ToolDefinition(
        name="agent_message",
        description=(
            "向另一个 Agent 发送消息。"
            "用于子Agent之间协调、向父Agent汇报中间发现、"
            "或请求父Agent提供更多上下文。"
        ),
        parameters=[
            ToolParameter(
                name="to_agent", type="string",
                description="目标 Agent ID（'main'=主Agent, 或 task_id）",
                required=True,
            ),
            ToolParameter(
                name="content", type="string",
                description="消息内容（如发现、请求、摘要）",
                required=True,
            ),
            ToolParameter(
                name="message_type", type="string",
                description="消息类型：'info' / 'request' / 'result'",
                required=False, default="info",
                enum=["info", "request", "result"],
            ),
        ],
    )
    is_concurrency_safe = True

    async def execute(self, args: dict, context: Context) -> ToolResult:
        to_agent = args["to_agent"]
        content = args["content"]
        msg_type = args.get("message_type", "info")

        runtime = context.metadata.get("_runtime")
        if not runtime:
            return ToolResult(
                tool_name="agent_message", success=False,
                error="AgentRuntime not available (tool only works in agent context)",
            )

        current_agent = context.metadata.get("agent_id", "main")
        try:
            runtime.send_message(
                from_agent=current_agent,
                to_agent=to_agent,
                content=f"[{msg_type}] {content}",
            )
            return ToolResult(
                tool_name="agent_message", success=True,
                content=f"消息已发送到 {to_agent}",
            )
        except ValueError as e:
            return ToolResult(
                tool_name="agent_message", success=False,
                error=str(e),
            )
