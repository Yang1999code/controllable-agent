"""app/cli.py — CLI 入口。

组装所有组件，提供类 Claude Code / OpenCode 的终端交互界面。

用法：
    python -m app.cli                        # TUI 交互模式（默认）
    python -m app.cli --one-shot "问题"       # 单次执行
    python -m app.cli --legacy               # 旧版 REPL
    python -m app.cli --model gpt-4o         # 指定模型
"""

import argparse
import asyncio
import logging
import sys

from ai.types import Context, Message
from ai.provider import IModelProvider, OpenAICompatibleProvider, AnthropicProvider
from agent.tool_registry import ToolRegistry
from agent.hook import HookChain
from agent.loop import AgentLoop, AgentConfig
from app.tools import register_all_tools
from app.config.loader import load_config, get_provider_config
from app.tui import TuiSession

logger = logging.getLogger(__name__)


def _safe_print(text: str) -> None:
    """安全打印，处理 Windows GBK 编码问题。"""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace")
                  .decode(sys.stdout.encoding or "utf-8", errors="replace"))


def _create_provider(provider_type: str, model: str, api_key: str,
                    base_url: str = "") -> IModelProvider:
    """工厂函数：根据类型创建模型提供商。"""
    if provider_type == "openai_compat":
        kwargs = {"model": model, "api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAICompatibleProvider(**kwargs)
    elif provider_type == "anthropic":
        return AnthropicProvider(model=model, api_key=api_key)
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s [%(name)s] %(message)s",
    )


async def run_legacy_repl(loop: AgentLoop, context: Context):
    """旧版 REPL 交互循环（--legacy 可用）。"""
    print("my-agent REPL. 输入消息，输入 'exit' 或 'quit' 退出。")
    print(f"模型: {loop.provider.model if hasattr(loop.provider, 'model') else 'unknown'}")
    print(f"工具数: {len(loop.tools.tools)}")
    print()

    while True:
        try:
            user_input = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见。")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break

        try:
            result = await loop.run(user_input, context)
            if result.final_output:
                _safe_print(result.final_output)
            elif result.messages:
                last_msg = result.messages[-1]
                if last_msg.role != "user":
                    _safe_print(last_msg.content[:2000])
            _safe_print(f"[turns={result.total_turns}, tools={result.total_tool_calls}, "
                        f"itokens={result.total_input_tokens}, otokens={result.total_output_tokens}]")
        except Exception as e:
            _safe_print(f"错误: {e}")
            logger.exception("REPL error")


async def main():
    parser = argparse.ArgumentParser(description="my-agent — AI Agent 框架")
    parser.add_argument("--model", default="", help="模型名")
    parser.add_argument("--provider", default="", help="提供商类型 (openai_compat / anthropic)")
    parser.add_argument("--api-key", default="", help="API Key")
    parser.add_argument("--config", default="", help="配置文件路径")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    parser.add_argument("--one-shot", "-s", default="", help="单次执行（非交互）")
    parser.add_argument("--legacy", action="store_true", help="使用旧版 REPL")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    # 加载配置
    config = load_config(args.config or None)
    agent_cfg = config.get("agent", {})
    provider_cfg = get_provider_config(config, args.provider)

    # 创建组件
    model = args.model or provider_cfg.get("model", "gpt-4o")
    base_url = provider_cfg.get("base_url", "")
    api_key = args.api_key or provider_cfg.get("api_key", "")
    provider_type = args.provider or config.get("providers", {}).get("default", "openai_compat")
    provider = _create_provider(provider_type, model, api_key, base_url)

    tools = ToolRegistry()
    tools.max_result_chars = agent_cfg.get("max_tool_result_chars", 50000)
    register_all_tools(tools)

    hooks = HookChain()

    loop_config = AgentConfig(
        max_turns=agent_cfg.get("max_turns", 100),
        max_tool_calls_per_turn=agent_cfg.get("max_tool_calls_per_turn", 10),
        max_context_tokens=agent_cfg.get("max_context_tokens", 128000),
    )

    loop = AgentLoop(
        provider=provider,
        tools=tools,
        hooks=hooks,
        config=loop_config,
    )

    context = Context(
        system_prompt=(
            f"你是 my-agent，一个来自 Empire code 开源项目的可控多智能体自迭代 AI Agent 框架。"
            f"你底层运行的模型是 {model}，通过 OpenAI 兼容 API 连接。"
            f"你的能力包括：文件读写与搜索（read/write/edit/glob/grep）、"
            f"Shell 命令执行（bash）、浏览器自动化（web_browser_*）、"
            f"HTTP 请求（web_fetch）、网页搜索（web_search）、"
            f"以及多 Agent 协作（delegate_task/agent_message）。"
            f"你支持流式响应、工具调用、自动记忆管理。"
            f"请用中文回答用户的问题。当被问到你的身份时，如实说明你是 my-agent 框架，运行在 {model} 模型上。"
        ),
        metadata={"project_path": "."},
    )

    # 执行
    if args.one_shot:
        result = await loop.run(args.one_shot, context)
        if result.final_output:
            _safe_print(result.final_output)
        elif result.messages:
            for msg in result.messages:
                if msg.role == "assistant":
                    _safe_print(msg.content[:2000] if msg.content else "(tool calls only)")
    elif args.legacy:
        await run_legacy_repl(loop, context)
    else:
        # 默认：TUI 交互模式
        session = TuiSession(loop, context)
        await session.run()


if __name__ == "__main__":
    asyncio.run(main())
