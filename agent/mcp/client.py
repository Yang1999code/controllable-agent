"""agent/mcp/client.py — MCP (Model Context Protocol) Client。

管理 MCP Server 的持久连接、工具发现、调用适配。
支持 stdio（子进程）和 SSE（HTTP）两种传输方式。

MCP 是可选依赖：pip install mcp
"""

import asyncio
import logging
from dataclasses import dataclass, field

from ai.types import Context, ITool, ToolDefinition, ToolParameter, ToolResult

logger = logging.getLogger(__name__)


# ── 配置 ────────────────────────────────────────────────

@dataclass
class MCPServerConfig:
    """单个 MCP Server 的连接配置。

    两种传输模式：
    - stdio：启动子进程，通过 stdin/stdout 通信
    - sse：连接 HTTP SSE 端点
    """

    name: str                      # 唯一名称（用于日志和工具前缀）
    transport: str = "stdio"       # "stdio" | "sse"
    command: str = ""              # stdio: 启动命令（如 "npx", "python"）
    args: list[str] = field(default_factory=list)   # stdio: 命令参数
    url: str = ""                  # sse: SSE 端点 URL
    env: dict[str, str] = field(default_factory=dict)  # 额外环境变量
    disabled: bool = False         # 是否跳过此服务器


# ── 工具适配器 ──────────────────────────────────────────

class MCPToolAdapter:
    """将 MCP 工具包装为 ITool Protocol 兼容对象。

    鸭子类型，不显式继承 ITool。ToolRegistry 可直接注册。
    """

    def __init__(self, mcp_tool, client: "MCPClient"):
        self.definition = ToolDefinition(
            name=mcp_tool.name,
            description=mcp_tool.description or "",
            parameters=_convert_json_schema_params(
                getattr(mcp_tool, "inputSchema", {})
            ),
        )
        self.is_concurrency_safe = True
        self._mcp_tool = mcp_tool
        self._client = client

    async def execute(self, args: dict, context: Context) -> ToolResult:
        return await self._client.call_tool(self.definition.name, args)


# ── MCP Client ──────────────────────────────────────────

class MCPClient:
    """单个 MCP Server 的持久客户端。

    管理传输生命周期（stdio 子进程 / SSE 长连接）、
    会话初始化、工具发现和工具调用。

    用法：
        config = MCPServerConfig(name="my-server", command="python", args=["-m", "my_mcp"])
        client = MCPClient(config)
        await client.connect()
        for adapter in client.create_adapters():
            registry.register(adapter)
        # ... 使用 ...
        await client.disconnect()
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._session = None          # mcp.ClientSession
        self._transport_ctx = None    # async context manager for transport
        self._session_ctx = None      # async context manager for session
        self._tools: list = []        # discovered MCP tools
        self._connected = False
        self._lock = asyncio.Lock()   # serialize tool calls (MCP sessions aren't thread-safe)

    # ── 状态 ──

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self._tools]

    # ── 连接 / 断开 ──

    async def connect(self) -> None:
        """连接到 MCP Server 并发现工具。

        Raises:
            ImportError: 未安装 mcp 包
        """
        try:
            from mcp import ClientSession, StdioServerParameters
        except ImportError:
            raise ImportError(
                "MCP support requires the 'mcp' package. "
                "Install with: pip install mcp"
            )

        if self.config.transport == "stdio":
            if not self.config.command:
                raise ValueError(
                    f"MCP server '{self.config.name}': 'command' is required for stdio transport"
                )
            from mcp.client.stdio import stdio_client

            server_params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args,
                env=self.config.env if self.config.env else None,
            )
            self._transport_ctx = stdio_client(server_params)
            read, write = await self._transport_ctx.__aenter__()

        elif self.config.transport == "sse":
            if not self.config.url:
                raise ValueError(
                    f"MCP server '{self.config.name}': 'url' is required for SSE transport"
                )
            from mcp.client.sse import sse_client

            self._transport_ctx = sse_client(url=self.config.url)
            read, write = await self._transport_ctx.__aenter__()

        else:
            raise ValueError(
                f"MCP server '{self.config.name}': unknown transport '{self.config.transport}'"
            )

        self._session_ctx = ClientSession(read, write)
        self._session = await self._session_ctx.__aenter__()
        await self._session.initialize()

        response = await self._session.list_tools()
        self._tools = response.tools
        self._connected = True
        logger.info(
            f"MCP '{self.config.name}': connected, "
            f"{len(self._tools)} tools: {[t.name for t in self._tools]}"
        )

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        """调用 MCP 工具并返回 ToolResult。

        串行化工具调用（asyncio.Lock），因为 MCP session 不支持并发调用。
        """
        if not self._connected or not self._session:
            return ToolResult(
                tool_name=name, success=False,
                error=f"MCP server '{self.config.name}' is not connected",
            )

        try:
            async with self._lock:
                result = await self._session.call_tool(name, arguments=arguments)

            texts = []
            for content in result.content:
                if hasattr(content, "text"):
                    texts.append(content.text)

            return ToolResult(
                tool_name=name,
                success=not result.isError,
                content="\n".join(texts) if texts else "(no text content)",
            )
        except Exception as e:
            logger.error(f"MCP tool '{name}' on '{self.config.name}': {e}")
            return ToolResult(
                tool_name=name, success=False,
                error=f"{type(e).__name__}: {str(e)}",
            )

    def create_adapters(self) -> list:
        """为所有已发现工具创建 ITool 适配器。"""
        return [MCPToolAdapter(tool, self) for tool in self._tools]

    async def disconnect(self) -> None:
        """断开 MCP 连接，释放资源。"""
        self._connected = False
        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"MCP session close error: {e}")
        if self._transport_ctx:
            try:
                await self._transport_ctx.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"MCP transport close error: {e}")
        self._session = None
        self._session_ctx = None
        self._transport_ctx = None
        logger.info(f"MCP '{self.config.name}': disconnected")


# ── JSON Schema → ToolParameter 转换 ────────────────────

def _convert_json_schema_params(schema: dict) -> list[ToolParameter]:
    """将 MCP 工具的 inputSchema (JSON Schema) 转为 my-agent ToolParameter 列表。"""
    if not schema or not isinstance(schema, dict):
        return []

    required: set = set(schema.get("required", []))
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return []

    params = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        params.append(ToolParameter(
            name=name,
            type=prop.get("type", "string"),
            description=prop.get("description", ""),
            required=name in required,
            enum=prop.get("enum"),
        ))
    return params
