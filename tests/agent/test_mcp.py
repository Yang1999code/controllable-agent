"""tests/agent/test_mcp.py — MCP Client 测试。

不依赖真实的 mcp 包，所有 MCP SDK 类型均 mock。
"""

import pytest
from dataclasses import dataclass

from ai.types import Context, ToolResult
from agent.mcp.client import (
    MCPServerConfig, MCPClient, MCPToolAdapter, _convert_json_schema_params,
)


# ── Mock MCP types ──────────────────────────────────────

@dataclass
class MockMCPTool:
    name: str
    description: str = ""
    inputSchema: dict = None

    def __post_init__(self):
        if self.inputSchema is None:
            self.inputSchema = {}


@dataclass
class MockTextContent:
    text: str
    type: str = "text"


@dataclass
class MockCallToolResult:
    content: list
    isError: bool = False


class MockSession:
    """Mock MCP ClientSession."""
    def __init__(self, tools=None, results=None):
        self._tools = tools or []
        self._results = results or {}
        self.initialized = False

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return MockListToolsResult(self._tools)

    async def call_tool(self, name, arguments=None):
        result = self._results.get(name, MockCallToolResult(
            content=[MockTextContent(text=f"ok: {name}")],
        ))
        return result


class MockListToolsResult:
    def __init__(self, tools):
        self.tools = tools


class MockTransportCtx:
    """Mock async context manager for transport."""
    def __init__(self, read=None, write=None):
        self._read = read or object()
        self._write = write or object()
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return (self._read, self._write)

    async def __aexit__(self, *args):
        self.exited = True
        return False


def make_mcp_module(session):
    """创建一个假的 mcp 模块，注入到 sys.modules。"""
    import types
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = type("ClientSession", (), {})
    mcp.StdioServerParameters = type("StdioServerParameters", (), {})
    mcp.client = types.ModuleType("mcp.client")
    mcp.client.stdio = types.ModuleType("mcp.client.stdio")
    mcp.client.sse = types.ModuleType("mcp.client.sse")
    return mcp


# ── JSON Schema 转换测试 ────────────────────────────────

class TestConvertJsonSchemaParams:
    def test_empty_schema(self):
        assert _convert_json_schema_params({}) == []
        assert _convert_json_schema_params(None) == []

    def test_simple_properties(self):
        schema = {
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results"},
            },
            "required": ["query"],
        }
        params = _convert_json_schema_params(schema)
        assert len(params) == 2

        q = [p for p in params if p.name == "query"][0]
        assert q.type == "string"
        assert q.description == "Search query"
        assert q.required is True

        l = [p for p in params if p.name == "limit"][0]
        assert l.type == "integer"
        assert l.required is False

    def test_enum_preserved(self):
        schema = {
            "properties": {
                "format": {"type": "string", "enum": ["json", "xml", "text"]},
            },
        }
        params = _convert_json_schema_params(schema)
        assert params[0].enum == ["json", "xml", "text"]

    def test_non_dict_prop_skipped(self):
        schema = {
            "properties": {
                "ok": {"type": "string"},
                "bad": "not a dict",
            },
        }
        params = _convert_json_schema_params(schema)
        assert len(params) == 1
        assert params[0].name == "ok"


# ── MCPToolAdapter 测试 ──────────────────────────────────

class TestMCPToolAdapter:
    def test_definition_from_mcp_tool(self):
        mcp_tool = MockMCPTool(
            name="search_docs",
            description="Search documentation",
            inputSchema={
                "properties": {
                    "query": {"type": "string", "description": "Query text"},
                },
                "required": ["query"],
            },
        )
        client = MCPClient(MCPServerConfig(name="test", command="echo"))
        adapter = MCPToolAdapter(mcp_tool, client)

        assert adapter.definition.name == "search_docs"
        assert adapter.definition.description == "Search documentation"
        assert len(adapter.definition.parameters) == 1
        assert adapter.definition.parameters[0].name == "query"
        assert adapter.is_concurrency_safe is True

    def test_empty_schema_ok(self):
        mcp_tool = MockMCPTool(name="simple_tool", inputSchema=None)
        client = MCPClient(MCPServerConfig(name="test", command="echo"))
        adapter = MCPToolAdapter(mcp_tool, client)

        assert adapter.definition.name == "simple_tool"
        assert adapter.definition.parameters == []


# ── MCPServerConfig 测试 ─────────────────────────────────

class TestMCPServerConfig:
    def test_defaults(self):
        cfg = MCPServerConfig(name="srv")
        assert cfg.name == "srv"
        assert cfg.transport == "stdio"
        assert cfg.command == ""
        assert cfg.args == []

    def test_stdio_full(self):
        cfg = MCPServerConfig(
            name="filesystem",
            transport="stdio",
            command="npx",
            args=["-y", "@mcp/server-filesystem", "/data"],
            env={"NODE_ENV": "production"},
        )
        assert cfg.command == "npx"
        assert len(cfg.args) == 3
        assert cfg.env["NODE_ENV"] == "production"

    def test_sse_config(self):
        cfg = MCPServerConfig(
            name="remote",
            transport="sse",
            url="https://mcp.example.com/sse",
        )
        assert cfg.transport == "sse"
        assert cfg.url == "https://mcp.example.com/sse"

    def test_disabled_flag(self):
        cfg = MCPServerConfig(name="off", disabled=True)
        assert cfg.disabled is True


# ── helpers: 注入假 mcp 模块到 sys.modules ──────────────

def _inject_fake_mcp(monkeypatch, mock_session, mock_transport):
    """向 sys.modules 注入假 mcp 模块树。

    MCPClient.connect() 内部执行局部 import (`from mcp.client.stdio import ...`),
    monkeypatch.setattr 无法截获局部 import。必须预先注入到 sys.modules。
    """
    import sys
    import types

    def _make_module(name):
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    # mcp
    mcp = _make_module("mcp")
    mcp.ClientSession = type("ClientSession", (), {})

    class FakeStdioServerParameters:
        def __init__(self, command="", args=None, env=None):
            self.command = command
            self.args = args or []
            self.env = env
    mcp.StdioServerParameters = FakeStdioServerParameters

    # mcp.client
    _make_module("mcp.client")

    # mcp.client.stdio
    mcps = _make_module("mcp.client.stdio")

    def _stdio_client(server_params):
        return mock_transport
    mcps.stdio_client = _stdio_client

    # mcp.client.sse
    mcpsse = _make_module("mcp.client.sse")

    def _sse_client(url):
        return mock_transport
    mcpsse.sse_client = _sse_client

    # 替换 agent.mcp.client 模块内的全局名称
    # connect() 执行 `from mcp import ClientSession, StdioServerParameters`
    # 由于 mcp 已注入 sys.modules, import 会拿到我们注入的模块

    # 还需要替换 ClientSession 的引用
    class _MockCS:
        def __init__(self, read, write):
            self._mock = mock_session
        async def __aenter__(self):
            await self._mock.initialize()
            return self._mock
        async def __aexit__(self, *args):
            pass

    mcp.ClientSession = _MockCS


def _cleanup_fake_mcp():
    """从 sys.modules 移除假 mcp 模块。"""
    import sys
    for name in list(sys.modules.keys()):
        if name == "mcp" or name.startswith("mcp."):
            del sys.modules[name]


# ── MCPClient 生命周期测试 ───────────────────────────────

class TestMCPClientLifecycle:
    @pytest.mark.asyncio
    async def test_connect_stdio(self, monkeypatch):
        """测试 stdio 连接和工具发现。"""
        mock_session = MockSession(tools=[
            MockMCPTool(name="tool_a", description="Tool A"),
            MockMCPTool(name="tool_b", description="Tool B"),
        ])
        mock_transport = MockTransportCtx()

        _inject_fake_mcp(monkeypatch, mock_session, mock_transport)

        client = MCPClient(MCPServerConfig(
            name="test-mcp", command="python", args=["-m", "test_server"],
        ))

        try:
            await client.connect()
            assert client.connected is True
            assert client.tool_names == ["tool_a", "tool_b"]
            assert len(client.create_adapters()) == 2
            assert mock_transport.entered is True

            await client.disconnect()
            assert client.connected is False
            assert mock_transport.exited is True
        finally:
            _cleanup_fake_mcp()

    @pytest.mark.asyncio
    async def test_connect_sse(self, monkeypatch):
        """测试 SSE 连接。"""
        mock_session = MockSession(tools=[
            MockMCPTool(name="remote_tool"),
        ])
        mock_transport = MockTransportCtx()

        _inject_fake_mcp(monkeypatch, mock_session, mock_transport)

        client = MCPClient(MCPServerConfig(
            name="remote", transport="sse", url="https://mcp.example.com/sse",
        ))

        try:
            await client.connect()
            assert client.connected is True
            assert client.tool_names == ["remote_tool"]
            await client.disconnect()
        finally:
            _cleanup_fake_mcp()

    @pytest.mark.asyncio
    async def test_call_tool(self, monkeypatch):
        """测试工具调用。"""
        mock_session = MockSession(
            tools=[MockMCPTool(name="greet")],
            results={
                "greet": MockCallToolResult(
                    content=[
                        MockTextContent(text="Hello, World!"),
                        MockTextContent(text="How are you?"),
                    ],
                ),
            },
        )
        mock_transport = MockTransportCtx()

        _inject_fake_mcp(monkeypatch, mock_session, mock_transport)

        client = MCPClient(MCPServerConfig(
            name="test", command="python", args=["-m", "test"],
        ))
        try:
            await client.connect()
            result = await client.call_tool("greet", {"name": "World"})
            assert result.success is True
            assert "Hello, World!" in result.content
            assert "How are you?" in result.content
            await client.disconnect()
        finally:
            _cleanup_fake_mcp()

    @pytest.mark.asyncio
    async def test_call_tool_error(self, monkeypatch):
        """测试 MCP 工具调用返回错误。"""
        mock_session = MockSession(
            tools=[MockMCPTool(name="failing_tool")],
            results={
                "failing_tool": MockCallToolResult(
                    content=[MockTextContent(text="Something went wrong")],
                    isError=True,
                ),
            },
        )
        mock_transport = MockTransportCtx()

        _inject_fake_mcp(monkeypatch, mock_session, mock_transport)

        client = MCPClient(MCPServerConfig(
            name="test", command="python", args=["-m", "test"],
        ))
        try:
            await client.connect()
            result = await client.call_tool("failing_tool", {})
            assert result.success is False
            assert "Something went wrong" in result.content
            await client.disconnect()
        finally:
            _cleanup_fake_mcp()

    @pytest.mark.asyncio
    async def test_call_tool_not_connected(self):
        """测试未连接时调用工具。"""
        client = MCPClient(MCPServerConfig(
            name="test", command="python", args=["-m", "test"],
        ))
        result = await client.call_tool("any_tool", {})
        assert result.success is False
        assert "not connected" in result.error

    @pytest.mark.asyncio
    async def test_adapter_execute(self, monkeypatch):
        """测试通过适配器执行工具。"""
        mock_session = MockSession(
            tools=[MockMCPTool(name="add", description="Add two numbers")],
            results={
                "add": MockCallToolResult(
                    content=[MockTextContent(text="42")],
                ),
            },
        )
        mock_transport = MockTransportCtx()

        _inject_fake_mcp(monkeypatch, mock_session, mock_transport)

        client = MCPClient(MCPServerConfig(
            name="math", command="python", args=["-m", "math_server"],
        ))
        try:
            await client.connect()
            adapters = client.create_adapters()
            assert len(adapters) == 1
            adapter = adapters[0]

            result = await adapter.execute({"a": 1, "b": 2}, Context())
            assert result.success is True
            assert result.content == "42"
            await client.disconnect()
        finally:
            _cleanup_fake_mcp()
