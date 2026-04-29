"""tests/agent/test_loop.py — AgentLoop 主循环测试。"""

import pytest
from ai.types import Context
from agent.loop import AgentLoop, AgentConfig
from agent.tool_registry import ToolRegistry
from agent.hook import HookChain
from tests.conftest import MockProvider, MockTool


@pytest.fixture
def loop():
    provider = MockProvider(responses=["I will help you with that."])
    tools = ToolRegistry()
    hooks = HookChain()
    return AgentLoop(provider=provider, tools=tools, hooks=hooks)


@pytest.fixture
def loop_with_tools():
    provider = MockProvider(responses=["Using tool now."])
    tools = ToolRegistry()
    tools.register(MockTool(return_value="tool result"))
    hooks = HookChain()
    return AgentLoop(provider=provider, tools=tools, hooks=hooks)


class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_simple_run(self, loop):
        ctx = Context(system_prompt="You are helpful.")
        result = await loop.run("Hello", ctx)

        assert result.status in ("completed", "max_turns")
        assert result.total_turns >= 1
        assert len(result.messages) > 0

    @pytest.mark.asyncio
    async def test_run_returns_agent_result(self, loop):
        ctx = Context()
        result = await loop.run("test input", ctx)

        assert result.total_turns >= 0
        assert isinstance(result.total_input_tokens, int)
        assert isinstance(result.total_output_tokens, int)

    @pytest.mark.asyncio
    async def test_run_adds_user_message(self, loop):
        ctx = Context()
        result = await loop.run("my question", ctx)
        user_msgs = [m for m in result.messages if m.role == "user"]
        assert len(user_msgs) >= 1
        assert user_msgs[0].content == "my question"


class TestAgentConfig:
    def test_default_config(self):
        cfg = AgentConfig()
        assert cfg.max_turns == 100
        assert cfg.max_tool_calls_per_turn == 10

    def test_custom_config(self):
        cfg = AgentConfig(max_turns=50, max_tool_calls_per_turn=5)
        assert cfg.max_turns == 50
