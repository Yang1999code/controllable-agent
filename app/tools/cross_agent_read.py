"""app/tools/cross_agent_read.py — CrossAgentReadTool。

跨 Agent 只读工具，供 Coordinator 和 Memorizer 使用。
读取其他 Agent 的 agent_view、digest、wiki 目录内容。
路径白名单 + agent_id 格式校验 + 路径穿越防护确保安全。
"""

import re

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult

# agent_id 只允许字母、数字、下划线、连字符
_AGENT_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')
# 路径中不允许出现 ..
_PATH_TRAVERSAL_PATTERN = re.compile(r'\.\.')


class CrossAgentReadTool:
    definition = ToolDefinition(
        name="cross_agent_read",
        description=(
            "读取其他 Agent 的记忆内容（只读）。"
            "可读 agent_view、digest、wiki、domains 目录下的文件。"
            "仅 Coordinator 和 Memorizer 有此工具。"
        ),
        parameters=[
            ToolParameter(
                name="agent_id", type="string",
                description="目标 Agent 的 ID（如 coder_001, reviewer_001）",
                required=True,
            ),
            ToolParameter(
                name="path", type="string",
                description=(
                    "目标 Agent 存储下的相对路径，"
                    "如 agent_view/_index.md 或 digest/d_001.md"
                ),
                required=True,
            ),
        ],
    )
    is_concurrency_safe = True

    async def execute(self, args: dict, context: Context) -> ToolResult:
        agent_id = args["agent_id"]
        path = args["path"]

        # agent_id 格式校验
        if not _AGENT_ID_PATTERN.match(agent_id):
            return ToolResult(
                tool_name="cross_agent_read", success=False,
                error=f"Invalid agent_id: '{agent_id}' (only alphanumeric, underscore, hyphen)",
            )

        # 路径穿越防护
        if _PATH_TRAVERSAL_PATTERN.search(path):
            return ToolResult(
                tool_name="cross_agent_read", success=False,
                error="Access denied: path must not contain '..'",
            )

        # 路径白名单检查
        allowed_prefixes = ("agent_view/", "digest/", "wiki/", "domains/")
        if not any(path.startswith(p) for p in allowed_prefixes):
            return ToolResult(
                tool_name="cross_agent_read", success=False,
                error=f"Access denied: path must start with one of {allowed_prefixes}",
            )

        # 从 context 获取 AgentStoreFactory
        factory = context.metadata.get("_store_factory")
        if not factory:
            return ToolResult(
                tool_name="cross_agent_read", success=False,
                error="AgentStoreFactory not available in context",
            )

        agent_stores = factory.get_agent_stores(agent_id)
        if not agent_stores:
            return ToolResult(
                tool_name="cross_agent_read", success=False,
                error=f"Agent '{agent_id}' not found in factory",
            )

        content = await agent_stores.store.read(path)
        if content is None:
            return ToolResult(
                tool_name="cross_agent_read", success=False,
                error=f"File not found: agents/{agent_id}/{path}",
            )

        return ToolResult(
            tool_name="cross_agent_read", success=True,
            content=content,
        )
