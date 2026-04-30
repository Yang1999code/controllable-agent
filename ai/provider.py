"""ai/provider.py — 模型提供商抽象。

统一的 LLM 调用抽象。V1：OpenAI 兼容 + Anthropic。

参考：Pi Agent providers/ / Hermes model_provider.py / CCB query.ts
"""

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

import httpx

from ai.types import Message, ToolDefinition

logger = logging.getLogger(__name__)


# ── 流式事件 ──────────────────────────────────────────

@dataclass
class LLMEvent:
    """流式事件。type 区分内容类型。"""

    type: Literal["text_delta", "tool_call", "tool_call_args", "done", "error"]
    content: str = ""
    tool_name: str = ""
    tool_id: str = ""
    usage: dict = field(default_factory=dict)
    error: str = ""


# ── ABC ───────────────────────────────────────────────

class IModelProvider(ABC):
    """LLM 模型提供商抽象。

    V1 实现：
    - OpenAICompatibleProvider（覆盖 OpenAI/DeepSeek/通义千问/智谱等）
    - AnthropicProvider（Anthropic 原生 Messages API）
    """

    def __init__(self, model: str = ""):
        self.model = model
        self._context_window_cache: int | None = None

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> AsyncIterator[LLMEvent]:
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        system_prompt: str = "",
        max_tokens: int = 4096,
    ) -> list[LLMEvent]:
        ...

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        ...

    # ── 上下文窗口（动态查询，问一次缓存）───────────────

    async def discover_context_window(self) -> int:
        """查询模型上下文窗口大小。4 层优先级，结果缓存。"""
        if self._context_window_cache is not None:
            return self._context_window_cache

        env_val = os.getenv("MY_AGENT_MAX_CONTEXT_TOKENS")
        if env_val:
            self._context_window_cache = int(env_val)
            return self._context_window_cache

        try:
            discovered = await self._fetch_model_context_window()
            if discovered:
                self._context_window_cache = discovered
                return discovered
        except Exception:
            pass

        self._context_window_cache = self._default_context_window()
        return self._context_window_cache

    @abstractmethod
    async def _fetch_model_context_window(self) -> int | None:
        ...

    def _default_context_window(self) -> int:
        return 128000

    @property
    def max_output_tokens(self) -> int:
        return 8192

    @property
    def usable_context(self) -> int:
        cw = self._context_window_cache or self._default_context_window()
        return max(0, cw - self.max_output_tokens - 1000)


# ── OpenAI 兼容实现 ───────────────────────────────────

def _tools_to_openai_format(tools: list[ToolDefinition]) -> list[dict]:
    """将 ToolDefinition 列表转为 OpenAI function calling 格式。"""
    result = []
    for tool in tools:
        properties = {}
        required_list = []
        for param in tool.parameters:
            prop: dict[str, object] = {
                "type": param.type,
                "description": param.description,
            }
            if param.default is not None:
                prop["default"] = param.default
            if param.enum is not None:
                prop["enum"] = param.enum
            properties[param.name] = prop
            if param.required:
                required_list.append(param.name)

        result.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required_list,
                } if properties else {"type": "object", "properties": {}},
            },
        })
    return result


class OpenAICompatibleProvider(IModelProvider):
    """OpenAI 兼容协议实现。

    初始化参数：
    - base_url: API 端点（默认 https://api.openai.com/v1）
    - api_key: 从环境变量读取
    - model: 模型名（默认 gpt-4o）
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        max_retries: int = 3,
    ):
        super().__init__(model)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> AsyncIterator[LLMEvent]:
        client = await self._get_client()

        # 构建 payload
        msgs: list[dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        for m in messages:
            entry: dict = {"role": m.role, "content": m.content}
            if m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            if m.tool_name:
                entry["name"] = m.tool_name
            if m.tool_calls:
                entry["tool_calls"] = m.tool_calls
            msgs.append(entry)

        payload = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = _tools_to_openai_format(tools)

        # 自动重试
        for attempt in range(self.max_retries):
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=payload,
                ) as response:
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", "5"))
                        await asyncio.sleep(retry_after)
                        continue
                    if response.status_code >= 500:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    if response.status_code != 200:
                        text = await response.aread()
                        yield LLMEvent(
                            type="error",
                            error=f"HTTP {response.status_code}: {text.decode()[:500]}",
                        )
                        return

                    current_tool_calls: dict[int, dict] = {}
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})

                        # 文本增量
                        if delta.get("content"):
                            yield LLMEvent(type="text_delta", content=delta["content"])

                        # 工具调用（支持多个 tool call 交错流式传输）
                        tc_list = delta.get("tool_calls")
                        if tc_list:
                            tc0 = tc_list[0]
                            idx = tc0.get("index", 0)
                            if idx not in current_tool_calls:
                                current_tool_calls[idx] = {
                                    "tool_name": tc0.get("function", {}).get("name", ""),
                                    "tool_id": tc0.get("id", ""),
                                    "args": "",
                                }
                                yield LLMEvent(
                                    type="tool_call",
                                    tool_name=current_tool_calls[idx]["tool_name"],
                                    tool_id=current_tool_calls[idx]["tool_id"],
                                )
                            if tc0.get("function", {}).get("arguments"):
                                chunk_args = tc0["function"]["arguments"]
                                current_tool_calls[idx]["args"] += chunk_args
                                yield LLMEvent(
                                    type="tool_call_args",
                                    content=chunk_args,
                                )

                    # 完成
                    usage = chunk.get("usage", {})
                    yield LLMEvent(
                        type="done",
                        usage={
                            "input_tokens": usage.get("prompt_tokens", 0),
                            "output_tokens": usage.get("completion_tokens", 0),
                        },
                    )
                return  # 成功，退出重试
            except httpx.RequestError as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    yield LLMEvent(type="error", error=str(e))

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        system_prompt: str = "",
        max_tokens: int = 4096,
    ) -> list[LLMEvent]:
        """非流式调用——收集所有事件返回。"""
        results: list[LLMEvent] = []
        async for event in self.stream(messages, tools, system_prompt, max_tokens):
            results.append(event)
        return results

    async def _fetch_model_context_window(self) -> int | None:
        """问 API 模型信息端点，提取上下文窗口大小。"""
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/models/{self.model}")
            if resp.status_code != 200:
                return None
            data = resp.json()
            for key in ("max_context_tokens", "context_window", "max_input_tokens", "context_length"):
                if key in data:
                    return int(data[key])
            info = data.get("model_info", {})
            if isinstance(info, dict):
                for key in ("max_context_tokens", "context_window", "max_input_tokens"):
                    if key in info:
                        return int(info[key])
        except Exception:
            pass
        return None

    async def close(self) -> None:
        """关闭 HTTP 客户端，释放连接。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def count_tokens(self, text: str) -> int:
        # 粗略：英文 1 token ≈ 3.5 字符，中文 1 token ≈ 2 字符
        chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
        other_chars = len(text) - chinese_chars
        return int(chinese_chars / 2 + other_chars / 3.5)


# ── Anthropic 原生实现 ────────────────────────────────

class AnthropicProvider(IModelProvider):
    """Anthropic Messages API 实现。

    初始化参数：
    - api_key: 从环境变量 ANTHROPIC_API_KEY 读取
    - model: 模型名（默认 claude-sonnet-4-6）
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str = "",
        max_retries: int = 3,
    ):
        super().__init__(model)
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.max_retries = max_retries

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> AsyncIterator[LLMEvent]:
        try:
            import anthropic
        except ImportError:
            yield LLMEvent(
                type="error",
                error="anthropic SDK not installed. Run: pip install anthropic",
            )
            return

        client = anthropic.AsyncAnthropic(api_key=self.api_key)

        # 构建 messages（Anthropic 格式）
        anthropic_msgs: list[dict] = []
        for m in messages:
            if m.role == "system":
                if not system_prompt:
                    system_prompt = m.content
                continue
            if m.tool_call_id:
                anthropic_msgs.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                    ],
                })
            elif m.tool_calls:
                content_blocks = []
                if m.content:
                    content_blocks.append({"type": "text", "text": m.content})
                content_blocks.extend([
                    {"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"],
                     "input": json.loads(tc["function"]["arguments"])}
                    for tc in m.tool_calls
                ])
                anthropic_msgs.append({"role": "assistant", "content": content_blocks})
            else:
                anthropic_msgs.append({"role": m.role, "content": m.content})

        # Anthropic 工具格式
        anthropic_tools = []
        for t in tools:
            input_schema: dict = {"type": "object", "properties": {}, "required": []}
            for param in t.parameters:
                input_schema["properties"][param.name] = {
                    "type": param.type,
                    "description": param.description,
                }
                if param.required:
                    input_schema["required"].append(param.name)
            anthropic_tools.append({
                "name": t.name,
                "description": t.description,
                "input_schema": input_schema,
            })

        for attempt in range(self.max_retries):
            try:
                async with client.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=anthropic_msgs,
                    tools=anthropic_tools or None,
                ) as stream:
                    async for event in stream:
                        if event.type == "content_block_start":
                            if event.content_block.type == "tool_use":
                                yield LLMEvent(
                                    type="tool_call",
                                    tool_name=event.content_block.name,
                                    tool_id=event.content_block.id,
                                )
                        elif event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                yield LLMEvent(type="text_delta", content=event.delta.text)
                            elif event.delta.type == "input_json_delta":
                                yield LLMEvent(
                                    type="tool_call_args",
                                    content=event.delta.partial_json,
                                )
                        elif event.type == "message_delta":
                            usage = event.usage
                            yield LLMEvent(
                                type="done",
                                usage={
                                    "input_tokens": usage.input_tokens,
                                    "output_tokens": usage.output_tokens,
                                },
                            )
                return
            except anthropic.RateLimitError:
                await asyncio.sleep(2 ** attempt)
            except anthropic.APIStatusError as e:
                if attempt < self.max_retries - 1 and e.status_code >= 500:
                    await asyncio.sleep(2 ** attempt)
                else:
                    yield LLMEvent(type="error", error=str(e))
                    return
            except Exception as e:
                yield LLMEvent(type="error", error=str(e))
                return

    async def _fetch_model_context_window(self) -> int | None:
        return None  # Anthropic 没有模型信息查询端点

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        system_prompt: str = "",
        max_tokens: int = 4096,
    ) -> list[LLMEvent]:
        results: list[LLMEvent] = []
        async for event in self.stream(messages, tools, system_prompt, max_tokens):
            results.append(event)
        return results

    def count_tokens(self, text: str) -> int:
        chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
        other_chars = len(text) - chinese_chars
        return int(chinese_chars / 2 + other_chars / 3.5)
