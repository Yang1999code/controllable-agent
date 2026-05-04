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

from my_agent import (
    Context, Message,
    ToolRegistry, HookChain,
    AgentLoop, AgentConfig,
    FlowInspector, PromptBuilder,
    CapabilityCatalog, CapabilityRegistry,
    SkillRegistry, IUiSession,
    MCPServerConfig, MCPClient,
    MemoryStore, FactStore, DomainIndex,
    AgentStoreFactory, SharedSpace,
    load_role_config, register_roles,
)
from app.providers import create_provider
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


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s [%(name)s] %(message)s",
    )


async def run_legacy_repl(loop: AgentLoop, context: Context):
    """旧版 REPL 交互循环（--legacy 可用）。"""
    print("my-agent REPL. 输入消息，输入 'exit' 或 'quit' 退出。")
    print(f"模型: {loop.model_name}")
    print(f"工具数: {loop.tool_count}")
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
    # Windows 终端 UTF-8 支持
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
    # 支持从环境变量读取 API Key
    api_key = args.api_key or provider_cfg.get("api_key", "")
    if not api_key:
        env_var = provider_cfg.get("api_key_env", "")
        if env_var:
            import os
            api_key = os.environ.get(env_var, "")
    provider_type = args.provider or config.get("providers", {}).get("default", "openai_compat")
    provider_kwargs = {"model": model, "api_key": api_key}
    if base_url:
        provider_kwargs["base_url"] = base_url
    provider = create_provider(provider_type, **provider_kwargs)

    # 查询模型上下文窗口（问一次，打印提示，后续读缓存）
    try:
        win = await provider.discover_context_window()
        if win >= 1_000_000:
            _safe_print(f"[模型] {model} 上下文窗口: {win // 1_000_000}M tokens")
        else:
            _safe_print(f"[模型] {model} 上下文窗口: {win // 1000}K tokens")
    except Exception:
        pass

    tools = ToolRegistry()
    tools.max_result_chars = agent_cfg.get("max_tool_result_chars", 50000)
    register_all_tools(tools)

    hooks = HookChain()

    # ── MCP Server 连接 ──────────────────────────────────
    mcp_clients: list[MCPClient] = []
    mcp_servers = config.get("mcp_servers", [])
    if mcp_servers:
        for srv in mcp_servers:
            if not isinstance(srv, dict):
                continue
            if srv.get("disabled"):
                continue
            try:
                mcp_config = MCPServerConfig(
                    name=srv.get("name", "unnamed"),
                    transport=srv.get("transport", "stdio"),
                    command=srv.get("command", ""),
                    args=srv.get("args", []),
                    url=srv.get("url", ""),
                    env=srv.get("env", {}),
                )
                mcp_client = MCPClient(mcp_config)
                await mcp_client.connect()
                for adapter in mcp_client.create_adapters():
                    tools.register(adapter)
                mcp_clients.append(mcp_client)
                _safe_print(f"[MCP] {mcp_config.name}: {len(mcp_client.tool_names)} tools")
            except ImportError:
                _safe_print(f"[MCP] {srv.get('name', '?')}: skipped (mcp package not installed)")
            except Exception as e:
                _safe_print(f"[MCP] {srv.get('name', '?')}: error — {e}")

    # ── Phase 2/3 模块装配 ──────────────────────────────
    # SkillRegistry — 技能注册表
    skill_registry = SkillRegistry()

    # SkillCrystallizer — 技能结晶器
    skill_crystallizer = None
    try:
        from agent.crystallizer import SkillCrystallizer
        skill_crystallizer = SkillCrystallizer(skill_registry)
        loaded = skill_crystallizer.load_existing_skills()
        if loaded:
            _safe_print(f"[技能] 已加载 {loaded} 个结晶技能")
    except Exception as e:
        logger.debug("SkillCrystallizer assembly skipped: %s", e)

    # Capability 渐进式披露 — 标记各工具的 Tier
    catalog = CapabilityCatalog()
    capability_registry = CapabilityRegistry(catalog)
    capability_registry.register_capability(
        "file_ops", "文件读写编辑", tier=0, source="builtin",
        tools=["read", "write", "edit"],
    )
    capability_registry.register_capability(
        "shell", "Shell 命令执行", tier=0, source="builtin",
        tools=["bash"],
    )
    capability_registry.register_capability(
        "search", "文件搜索 (glob/grep)", tier=0, source="builtin",
        tools=["glob", "grep"],
    )
    capability_registry.register_capability(
        "web", "网页抓取/搜索/浏览器", tier=1, source="builtin",
        tools=["web_fetch", "web_search", "web_browser_navigate",
               "web_browser_click", "web_browser_type", "web_browser_snapshot"],
    )
    capability_registry.register_capability(
        "delegation", "多 Agent 委托与通信", tier=1, source="builtin",
        tools=["delegate_task", "agent_message"],
    )

    # PromptBuilder — 动态 prompt 片段组装
    import os as _os_cwd
    _cwd = _os_cwd.getcwd()
    prompt_builder = PromptBuilder()
    prompt_builder.set_system_prompt(
        f"你是 my-agent，一个来自 Empire code 开源项目的可控多智能体自迭代 AI Agent 框架。"
        f"你底层运行的模型是 {model}，通过 OpenAI 兼容 API 连接。"
        f"你的能力包括：文件读写与搜索（read/write/edit/glob/grep）、"
        f"Shell 命令执行（bash）、浏览器自动化（web_browser_*）、"
        f"HTTP 请求（web_fetch）、网页搜索（web_search）、"
        f"以及多 Agent 协作（delegate_task/agent_message）。"
        f"你支持流式响应、工具调用、自动记忆管理。"
        f"请用中文回答用户的问题。当被问到你的身份时，如实说明你是 my-agent 框架，运行在 {model} 模型上。"
        f"\n\n重要环境信息："
        f"\n- 当前工作目录: {_cwd}"
        f"\n- 操作系统: Windows"
        f"\n- 使用工具时请用绝对路径或基于工作目录的相对路径"
        f"\n- 不要运行需要用户交互式输入的程序（如 input()），改为接受命令行参数或用管道输入"
    )

    # FlowInspector — 旁路运行监控
    inspector = FlowInspector()

    # WebAutomation — 网页工具后端
    web: object | None = None
    try:
        from agent.web import WebAutomation
        _wa = WebAutomation()
        web = _wa
    except Exception:
        pass

    # PluginAdapter — 4 层插件发现
    try:
        from agent.plugin import PluginAdapter
        plugin_adapter = PluginAdapter(hooks, tools, skill_registry, catalog)
    except Exception:
        plugin_adapter = None

    # ── 记忆提取引擎装配 ──────────────────────────────────
    memory_extractor = None
    try:
        from agent.memory.task_detector import TaskDetector
        from agent.memory.extractor import MemoryExtractor
        import os as _os

        memory_dir = _os.path.expanduser("~/.agent-memory")
        _os.makedirs(memory_dir, exist_ok=True)
        memory_store = MemoryStore(memory_dir)
        fact_store = FactStore(memory_store)
        domain_index = DomainIndex(memory_store, fact_store)

        await domain_index.initialize()

        task_detector = TaskDetector()
        memory_extractor = MemoryExtractor(
            provider=provider,
            fact_store=fact_store,
            domain_index=domain_index,
            task_detector=task_detector,
        )
        _safe_print(f"[记忆] 自动提取引擎已启用 (存储: {memory_dir})")
    except Exception as e:
        logger.debug("memory extractor assembly skipped: %s", e)

    # ── Phase 3 多 Agent 协作装配 ─────────────────────────
    runtime = None
    try:
        from agent.runtime import AgentRuntime

        store_factory = AgentStoreFactory(
            base_path=_os.path.expanduser("~/.agent-memory"),
        )
        shared_store = store_factory.get_shared_store()
        shared_space = SharedSpace(shared_store)
        await shared_space.initialize()

        tools_dict = dict(tools.tools)

        runtime = AgentRuntime(
            tools=tools_dict,
            provider=provider,
            hooks=hooks,
            max_concurrent=agent_cfg.get("max_concurrent", 5),
            store_factory=store_factory,
            shared_space=shared_space,
        )
        registered = register_roles(runtime)
        if registered:
            _safe_print(f"[多Agent] 已注册角色: {', '.join(registered)}")
    except Exception as e:
        logger.debug("Phase 3 assembly skipped: %s", e)

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
        prompt_builder=prompt_builder,
        inspector=inspector,
        capability_registry=capability_registry,
        memory_extractor=memory_extractor,
        runtime=runtime,
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
            f"\n\n重要环境信息："
            f"\n- 当前工作目录: {_cwd}"
            f"\n- 操作系统: Windows"
            f"\n- 使用工具时请用绝对路径或基于工作目录的相对路径"
            f"\n- 不要运行需要用户交互式输入的程序（如 input()），改为接受命令行参数或用管道输入"
        ),
        metadata={
            "project_path": _cwd,
            "_web": web,
            "_skill_registry": skill_registry,
            "_runtime": runtime,
            "_hooks": hooks,
            "_memory_extractor": memory_extractor,
            "_skill_crystallizer": skill_crystallizer,
            "agent_id": "main",
        },
    )

    # 执行
    try:
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
            session: IUiSession = TuiSession(loop, context)
            await session.run()
    finally:
        # 清理 MCP 连接
        for client in mcp_clients:
            try:
                await client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
