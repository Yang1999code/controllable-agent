"""agent/mcp/ — MCP (Model Context Protocol) Client。

连接外部 MCP Server，将其工具转换为 my-agent ITool 并注册到 ToolRegistry。

用法：
    from agent.mcp import MCPClient, MCPServerConfig

    config = MCPServerConfig(name="filesystem", command="npx",
                             args=["-y", "@modelcontextprotocol/server-filesystem", "/path"])
    client = MCPClient(config)
    await client.connect()
    for adapter in client.create_adapters():
        registry.register(adapter)
"""

from agent.mcp.client import MCPClient, MCPServerConfig, MCPToolAdapter

__all__ = ["MCPClient", "MCPServerConfig", "MCPToolAdapter"]
