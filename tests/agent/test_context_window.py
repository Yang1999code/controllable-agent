"""tests/agent/test_context_window.py — Token 估算 + 溢出检测测试。"""

import pytest
from ai.types import Message
from agent.context_window import (
    estimate_tokens, estimate_message_tokens, estimate_tool_tokens,
    count_total_tokens, is_overflow, compute_prefix_hash,
)


class TestEstimateTokens:
    def test_positive(self):
        assert estimate_tokens("hello world") >= 1

    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_scales_with_length(self):
        short = estimate_tokens("hi")
        long = estimate_tokens("hello world " * 100)
        assert long > short

    def test_chinese(self):
        n = estimate_tokens("你好世界")
        assert n >= 1


class TestEstimateMessageTokens:
    def test_simple_message(self):
        msg = Message(role="user", content="hello")
        n = estimate_message_tokens(msg)
        assert n >= 1

    def test_message_with_tool_calls(self):
        msg = Message(role="assistant", content="ok", tool_calls=[
            {"function": {"name": "read", "arguments": '{"path":"/x"}'}},
        ])
        n = estimate_message_tokens(msg)
        assert n >= 1


class TestEstimateToolTokens:
    def test_simple_tools(self):
        from ai.types import ToolDefinition
        tools = [
            ToolDefinition(name="bash", description="run command"),
            ToolDefinition(name="read", description="read file"),
        ]
        n = estimate_tool_tokens(tools)
        assert n >= 1


class TestCountTotalTokens:
    def test_returns_positive(self):
        msgs = [Message(role="user", content="hello")]
        n = count_total_tokens(msgs, "sys prompt", [])
        assert n > 0

    def test_scales_with_messages(self):
        few = count_total_tokens(
            [Message(role="user", content="hi")], "", [],
        )
        many = count_total_tokens(
            [Message(role="user", content="hello world " * 50)], "", [],
        )
        assert many > few


class TestIsOverflow:
    def test_under_limit(self):
        assert is_overflow(80000, 100000, 0.85) is False

    def test_over_limit(self):
        assert is_overflow(90000, 100000, 0.85) is True

    def test_zero_limit(self):
        assert is_overflow(100, 0) is False


class TestComputePrefixHash:
    def test_same_input_same_hash(self):
        msgs = [Message(role="user", content="hello", id="a1")]
        h1 = compute_prefix_hash("sys", [], msgs)
        h2 = compute_prefix_hash("sys", [], msgs)
        assert h1 == h2

    def test_different_system_different_hash(self):
        h1 = compute_prefix_hash("sys A", [], [])
        h2 = compute_prefix_hash("sys B", [], [])
        assert h1 != h2

    def test_different_messages_different_hash(self):
        h1 = compute_prefix_hash("sys", [], [Message(role="user", content="A", id="1")])
        h2 = compute_prefix_hash("sys", [], [Message(role="user", content="B", id="2")])
        assert h1 != h2
