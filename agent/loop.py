"""agent/loop.py — Agent 主循环（★ 最重要模块）。

双层循环（外层 followUp + 内层 tool_calls），带 steer 中断，嵌入 Phase 2/3 扩展点。

参考：
- Pi Agent agent-loop.ts (683行) — 最优雅的双层循环实现
- GenericAgent agent_loop.py (~118行) — 极简 Generator 循环
- CCB query.ts (~1776行) — 企业级循环，参考工具结果预算 + 流式处理
- Hermes run_agent.py (12131行) — 最完整的循环实现
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ai.types import Context, Message, AgentEvent, AgentEventType, ToolResult
from ai.provider import IModelProvider
from agent.step_outcome import StepOutcome
from agent.tool_registry import ToolRegistry
from agent.hook import HookChain

if TYPE_CHECKING:
    from agent.prompt import IPromptBuilder
    from agent.runtime import IAgentRuntime
    from agent.autonomous_memory import IAutonomousMemory
    from agent.inspector import IFlowInspector
    from agent.capability import ICapabilityRegistry

logger = logging.getLogger(__name__)


# ── 配置与结果 ─────────────────────────────────────────

@dataclass
class AgentConfig:
    """Agent 循环配置。"""

    max_turns: int = 100
    max_tool_calls_per_turn: int = 10
    max_context_tokens: int = 128000
    max_tool_result_chars: int = 50000


@dataclass
class AgentResult:
    """Agent 循环的最终返回。"""

    status: str  # "completed" | "max_turns" | "error"
    messages: list[Message] = field(default_factory=list)
    total_turns: int = 0
    total_tool_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    final_output: str = ""


# ── 主循环 ────────────────────────────────────────────

class AgentLoop:
    """Agent 主循环。

    双层结构（参考 Pi Agent）：
    - 外层 followUp + steer 中断
    - 内层 tool_calls 循环

    Phase 2 扩展点（# 注释标注）：prompt 动态构建
    Phase 3 扩展点（★ 标注）：检查点 + Nudge + 结晶 + 委托
    """

    def __init__(
        self,
        provider: IModelProvider,
        tools: ToolRegistry,
        hooks: HookChain,
        config: AgentConfig | None = None,
        # Phase 2 依赖（初始可为 None）
        prompt_builder: "IPromptBuilder | None" = None,
        inspector: "IFlowInspector | None" = None,
        capability_registry: "ICapabilityRegistry | None" = None,
        # Phase 3 依赖（初始可为 None）
        runtime: "IAgentRuntime | None" = None,
        autonomous_memory: "IAutonomousMemory | None" = None,
    ):
        self.provider = provider
        self.tools = tools
        self.hooks = hooks
        self.config = config or AgentConfig()
        self.prompt_builder = prompt_builder
        self.inspector = inspector
        self.capability_registry = capability_registry
        self.runtime = runtime
        self.autonomous_memory = autonomous_memory

    # ── 公共属性（供前端使用，不穿透内部）───────────────

    @property
    def model_name(self) -> str:
        return getattr(self.provider, "model", "unknown")

    @property
    def tool_count(self) -> int:
        return len(self.tools.tools)

    async def run(self, user_input: str, context: Context) -> AgentResult:
        """主入口。"""
        turn_count = 0
        total_tool_calls = 0
        total_input_tokens = 0
        total_output_tokens = 0

        # 添加用户消息
        context.messages.append(Message(role="user", content=user_input))

        # 加载 CLAUDE.md（首次运行，结果缓存到 context.metadata）
        if "claude_md_content" not in context.metadata:
            try:
                from agent.claudemd import discover_claude_mds, assemble_claude_md_content
                claude_files = discover_claude_mds(
                    cwd=context.metadata.get("project_path", ""),
                )
                claude_md_content = assemble_claude_md_content(claude_files)
                context.metadata["claude_md_content"] = claude_md_content
            except Exception as e:
                logger.debug(f"CLAUDE.md discovery skipped: {e}")
                context.metadata["claude_md_content"] = ""

        # 将 CLAUDE.md 注入 prompt_builder（如果有）
        if self.prompt_builder and context.metadata.get("claude_md_content"):
            try:
                from agent.prompt import PromptFragment
                self.prompt_builder.register_fragment(PromptFragment(
                    name="CLAUDE_MD",
                    content=context.metadata["claude_md_content"],
                    priority=0,
                    source="builtin",
                ))
            except Exception:
                pass

        # 外层 followUp 循环
        await self.hooks.fire(AgentEvent(type=AgentEventType.LOOP_START))

        follow_up_queue: asyncio.Queue[str] = asyncio.Queue()
        steer_queue: asyncio.Queue[str] = asyncio.Queue()

        await follow_up_queue.put(user_input)

        last_response_text = ""

        while turn_count < self.config.max_turns:
            # 检查 steer 消息（优先级高于 followUp）
            next_message = None
            try:
                next_message = steer_queue.get_nowait()
            except asyncio.QueueEmpty:
                try:
                    next_message = follow_up_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            if not next_message:
                break

            turn_count += 1
            await self.hooks.fire(AgentEvent(
                type=AgentEventType.TURN_START,
                data={"turn": turn_count, "message": next_message},
            ))

            # ── Phase 2 嵌入：动态构建 system prompt ──
            if self.prompt_builder:
                context.system_prompt = self.prompt_builder.build(context)

            # ── 内层 tool_calls 循环 ──
            tool_call_count = 0
            had_tool_calls = False

            while tool_call_count < self.config.max_tool_calls_per_turn:
                # LLM 调用
                await self.hooks.fire(AgentEvent(
                    type=AgentEventType.LLM_CALL,
                    data={"messages": len(context.messages)},
                ))

                current_text = ""
                current_tool_calls = []
                llm_start = time.monotonic()

                # 按 CapabilityRegistry 过滤可见工具
                tool_defs = self.tools.get_definitions()
                if self.capability_registry:
                    visible_names = set(self.capability_registry.get_visible_tools())
                    tool_defs = [d for d in tool_defs if d.name in visible_names]

                async for event in self.provider.stream(
                    messages=context.messages,
                    tools=tool_defs,
                    system_prompt=context.system_prompt,
                ):
                    if event.type == "text_delta":
                        current_text += event.content
                    elif event.type == "tool_call":
                        current_tool_calls.append({
                            "tool_name": event.tool_name,
                            "tool_id": event.tool_id,
                            "args": {},
                        })
                    elif event.type == "tool_call_args":
                        if current_tool_calls:
                            current_tool_calls[-1]["_args_raw"] = (
                                current_tool_calls[-1].get("_args_raw", "")
                                + event.content
                            )
                    elif event.type == "done":
                        total_input_tokens += event.usage.get("input_tokens", 0)
                        total_output_tokens += event.usage.get("output_tokens", 0)
                    elif event.type == "error":
                        logger.error(f"LLM error: {event.error}")
                        break

                # 没有工具调用 → 退出内层循环
                if not current_tool_calls:
                    context.messages.append(Message(
                        role="assistant", content=current_text,
                    ))
                    last_response_text = current_text
                    break

                had_tool_calls = True

                # 解析累积的 args
                for tc in current_tool_calls:
                    raw = tc.pop("_args_raw", "")
                    if raw:
                        try:
                            tc["args"] = json.loads(raw)
                        except json.JSONDecodeError:
                            tc["args"] = {}

                tool_call_count += len(current_tool_calls)
                total_tool_calls += len(current_tool_calls)

                # 保存有实质内容的文本
                if current_text:
                    last_response_text = current_text

                # 添加 assistant 消息（含 tool_calls）——OpenAI 协议要求
                openai_tool_calls = []
                for tc in current_tool_calls:
                    openai_tool_calls.append({
                        "id": tc.get("tool_id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("tool_name", ""),
                            "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                        },
                    })
                context.messages.append(Message(
                    role="assistant",
                    content=current_text or None,
                    tool_calls=openai_tool_calls,
                ))

                # ★ 旁路监控：记录 LLM 调用
                if self.inspector:
                    try:
                        llm_latency_ms = (time.monotonic() - llm_start) * 1000
                        await self.inspector.push({
                            "event": "llm_call",
                            "latency_ms": llm_latency_ms,
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens,
                            "tool_calls": len(current_tool_calls),
                        })
                    except Exception:
                        pass

                # 执行工具
                results = await self.tools.execute_many(current_tool_calls, context)

                # 注入工具结果到上下文
                for tc, result in zip(current_tool_calls, results):
                    context.messages.append(Message(
                        role="tool",
                        content=result.content if result.success else f"Error: {result.error}",
                        tool_call_id=tc.get("tool_id", ""),
                        tool_name=result.tool_name,
                    ))

                # ★ Phase 3：检查子Agent收件箱（Agent间通信）
                if self.runtime:
                    agent_id = context.metadata.get("agent_id", "main")
                    inbox_msg = self.runtime.check_inbox(agent_id)
                    if inbox_msg:
                        context.messages.append(Message(
                            role="user",
                            content=f"[子Agent消息] {inbox_msg}",
                        ))

            # ── turn_end Hook + 自进化嵌入（Phase 3）──
            await self.hooks.fire(AgentEvent(
                type=AgentEventType.TURN_END,
                data={"turn": turn_count, "tool_calls": tool_call_count},
            ))

            # ★ 旁路监控：记录轮次结束
            if self.inspector:
                try:
                    await self.inspector.push({
                        "event": "turn_end",
                        "turn": turn_count,
                        "tool_calls": tool_call_count,
                        "success": True,
                    })
                except Exception:
                    pass

            # ★ Phase 3：update_working_checkpoint（每轮）
            if self.autonomous_memory:
                try:
                    self.autonomous_memory.update_working_checkpoint(
                        key_info=f"Turn {turn_count}: {tool_call_count} tool calls",
                    )
                except Exception as e:
                    logger.debug(f"checkpoint update skipped: {e}")

                # ★ Phase 3：Nudge 检查（每10轮）
                nudge = self.autonomous_memory.get_nudge_content(turn_count, "memory")
                if nudge:
                    context.messages.append(Message(
                        role="user", content=f"[系统提醒] {nudge}",
                    ))

            # 如果模型被工具调用循环"卡住"，上限后立即退出
            if had_tool_calls and tool_call_count >= self.config.max_tool_calls_per_turn:
                logger.warning(f"Max tool calls ({self.config.max_tool_calls_per_turn}) reached, forcing exit")
                break

            # 没有工具调用且有文本 → 完成
            break

        # 循环结束
        await self.hooks.fire(AgentEvent(type=AgentEventType.LOOP_END))

        return AgentResult(
            status="completed" if turn_count < self.config.max_turns else "max_turns",
            messages=context.messages,
            total_turns=turn_count,
            total_tool_calls=total_tool_calls,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            final_output=last_response_text,
        )
