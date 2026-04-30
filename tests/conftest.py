"""tests/conftest.py — 共享 fixtures。"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult
from agent.memory.store import MemoryStore
from agent.hook import HookChain


@pytest.fixture
def tmp_workspace():
    """创建临时工作目录。"""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def memory_store(tmp_workspace):
    """创建指向临时目录的 MemoryStore。"""
    return MemoryStore(str(tmp_workspace / ".agent-memory"))


@pytest.fixture
def context():
    """创建空 Context。"""
    return Context(system_prompt="You are a helpful assistant.")


@pytest.fixture
def hook_chain():
    """创建 HookChain。"""
    return HookChain()


@pytest.fixture
def sample_tool_definition():
    """示例工具定义。"""
    return ToolDefinition(
        name="sample_tool",
        description="A sample tool for testing",
        parameters=[
            ToolParameter(name="input", type="string", description="Input text", required=True),
            ToolParameter(name="mode", type="string", description="Mode", required=False,
                          enum=["fast", "slow"]),
        ],
    )


class MockTool:
    """模拟工具，用于测试 ToolRegistry。"""

    is_concurrency_safe = True

    def __init__(self, return_value: str = "ok", should_fail: bool = False,
                 tool_name: str = "mock_tool"):
        self.definition = ToolDefinition(
            name=tool_name,
            description="A mock tool",
            parameters=[ToolParameter(name="text", type="string", required=True)],
        )
        self.return_value = return_value
        self.should_fail = should_fail
        self.call_count = 0

    async def execute(self, args: dict, context: Context) -> ToolResult:
        self.call_count += 1
        if self.should_fail:
            return ToolResult(tool_name="mock_tool", success=False, content="", error="mock error")
        return ToolResult(tool_name="mock_tool", success=True, content=self.return_value)


class MockProvider:
    """模拟 LLM 提供商，用于测试 AgentLoop。"""

    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or ["Hello, I am a mock AI."]
        self.call_count = 0
        self.model = "mock-model"
        self._context_window_cache: int | None = 128000

    async def stream(self, messages, tools, system_prompt="", max_tokens=4096, temperature=0.0):
        from ai.provider import LLMEvent
        idx = min(self.call_count, len(self.responses) - 1)
        yield LLMEvent(type="text_delta", content=self.responses[idx])
        yield LLMEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5})
        self.call_count += 1

    async def chat(self, messages, tools, system_prompt="", max_tokens=4096):
        results = []
        async for e in self.stream(messages, tools, system_prompt, max_tokens):
            results.append(e)
        return results

    def count_tokens(self, text: str) -> int:
        return len(text) // 3

    async def discover_context_window(self) -> int:
        return self._context_window_cache or 128000

    async def _fetch_model_context_window(self) -> int | None:
        return None

    @property
    def max_output_tokens(self) -> int:
        return 8192

    @property
    def usable_context(self) -> int:
        cw = self._context_window_cache or 128000
        return max(0, cw - self.max_output_tokens - 1000)


@pytest.fixture
def mock_provider():
    return MockProvider()


@pytest.fixture
def mock_tool_factory():
    return MockTool
