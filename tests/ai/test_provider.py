"""tests/ai/test_provider.py — IModelProvider / LLMEvent 测试。"""

import pytest
from ai.provider import (
    LLMEvent,
    OpenAICompatibleProvider,
    AnthropicProvider,
    _tools_to_openai_format,
)
from ai.types import ToolDefinition, ToolParameter


class TestLLMEvent:
    def test_default_values(self):
        e = LLMEvent(type="done")
        assert e.content == ""
        assert e.tool_name == ""
        assert e.error == ""

    def test_text_delta_event(self):
        e = LLMEvent(type="text_delta", content="Hello")
        assert e.type == "text_delta"
        assert e.content == "Hello"

    def test_error_event(self):
        e = LLMEvent(type="error", error="Something went wrong")
        assert e.error == "Something went wrong"


class TestToolsToOpenAIFormat:
    def test_empty_tools(self):
        assert _tools_to_openai_format([]) == []

    def test_single_tool(self):
        tools = [
            ToolDefinition(
                name="read",
                description="Read a file",
                parameters=[
                    ToolParameter(name="file_path", type="string",
                                  description="Path to file", required=True),
                ],
            )
        ]
        result = _tools_to_openai_format(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "read"
        assert "file_path" in result[0]["function"]["parameters"]["properties"]

    def test_tool_with_enum(self):
        tools = [
            ToolDefinition(
                name="set_mode",
                description="Set mode",
                parameters=[
                    ToolParameter(name="mode", type="string",
                                  description="The mode", required=True,
                                  enum=["fast", "slow"]),
                ],
            )
        ]
        result = _tools_to_openai_format(tools)
        props = result[0]["function"]["parameters"]["properties"]
        assert props["mode"]["enum"] == ["fast", "slow"]


class TestOpenAICompatibleProvider:
    def test_count_tokens_english(self):
        provider = OpenAICompatibleProvider(model="test", api_key="sk-test")
        tokens = provider.count_tokens("Hello world")
        assert tokens > 0

    def test_count_tokens_chinese(self):
        provider = OpenAICompatibleProvider(model="test", api_key="sk-test")
        tokens = provider.count_tokens("你好世界")
        assert tokens > 0

    def test_count_tokens_mixed(self):
        provider = OpenAICompatibleProvider(model="test", api_key="sk-test")
        tokens = provider.count_tokens("Hello 你好")
        assert tokens > 0

    def test_custom_base_url(self):
        provider = OpenAICompatibleProvider(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            api_key="sk-test",
        )
        assert provider.base_url == "https://api.deepseek.com/v1"
        assert provider.model == "deepseek-chat"


class TestAnthropicProvider:
    def test_count_tokens(self):
        provider = AnthropicProvider(model="claude-test", api_key="sk-test")
        tokens = provider.count_tokens("Hello world")
        assert tokens > 0

    def test_default_model(self):
        provider = AnthropicProvider(api_key="sk-test")
        assert "claude" in provider.model
