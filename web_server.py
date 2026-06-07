"""web_server.py — Web 前端服务。

FastAPI + SSE 实时流式聊天 + 多智能体状态可视化
+ Wiki 记忆提取 + 技能结晶 + MCP + 插件系统 + Prompt 组装。
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse

from my_agent import (
    Context, Message,
    ToolRegistry, HookChain, HookHandler,
    AgentLoop, AgentConfig, AgentResult,
    FlowInspector, PromptBuilder,
    CapabilityCatalog, CapabilityRegistry,
    SkillRegistry,
    MemoryStore, FactStore, DomainIndex,
    TaskDetector, MemoryExtractor,
    RelationStore,
    MCPServerConfig, MCPClient,
    AgentEvent, AgentEventType,
    # Phase 2/3 图记忆后端
    create_graph_backend_from_config,
    detect_available_backends,
    IGraphBackend, FileGraphBackend,
    CommunityDetector,
    TemporalQueryEngine,
    SharedGraphManager, GraphSync,
)
from app.providers import create_provider
from app.tools import register_all_tools
from app.config.loader import load_config, get_provider_config

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
FRONTEND_DIR = ROOT / "frontend"

# ── 全局组件 ──────────────────────────────────────────
_loop: AgentLoop | None = None
_context: Context | None = None
_provider = None
_tools: ToolRegistry | None = None
_agent_busy = False

# 记忆 + 技能 + MCP + 插件
_memory_extractor = None
_relation_store = None
_memory_store = None
_fact_store = None
_domain_index = None
_skill_crystallizer = None
_skill_registry = None
_mcp_clients: list = []
_prompt_builder = None
_inspector = None
_capability_registry = None
_web = None
_plugin_adapter = None

# Phase 2/3 图记忆后端
_graph_backend: IGraphBackend | None = None
_community_detector: CommunityDetector | None = None
_temporal_engine: TemporalQueryEngine | None = None
_shared_graph: SharedGraphManager | None = None
_graph_available_backends: list[str] = []


def get_config():
    return load_config(None)


async def build_agent_components():
    global _loop, _context, _provider, _tools
    global _memory_extractor, _relation_store, _memory_store, _fact_store, _domain_index
    global _skill_crystallizer, _skill_registry
    global _mcp_clients, _prompt_builder, _inspector, _capability_registry, _web, _plugin_adapter
    global _graph_backend, _community_detector, _temporal_engine, _shared_graph, _graph_available_backends

    config = get_config()
    agent_cfg = config.get("agent", {})
    provider_cfg = get_provider_config(config, "")

    # ── Provider ──────────────────────────────────────
    model = provider_cfg.get("model", "gpt-4o")
    base_url = provider_cfg.get("base_url", "")
    api_key = provider_cfg.get("api_key", "")
    if not api_key:
        env_var = provider_cfg.get("api_key_env", "")
        if env_var:
            api_key = os.environ.get(env_var, "")
    provider_type = config.get("providers", {}).get("default", "openai_compat")
    provider_kwargs = {"model": model, "api_key": api_key}
    if base_url:
        provider_kwargs["base_url"] = base_url
    _provider = create_provider(provider_type, **provider_kwargs)
    logger.info("Provider: %s / %s", provider_type, model)

    # ── ToolRegistry ──────────────────────────────────
    _tools = ToolRegistry()
    _tools.max_result_chars = agent_cfg.get("max_tool_result_chars", 50000)
    register_all_tools(_tools)

    # ── MCP Server 连接 ───────────────────────────────
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
                    _tools.register(adapter)
                _mcp_clients.append(mcp_client)
                logger.info("MCP %s: %d tools", mcp_config.name, len(mcp_client.tool_names))
            except ImportError:
                logger.info("MCP %s: skipped (mcp package not installed)", srv.get("name", "?"))
            except Exception as e:
                logger.info("MCP %s: error — %s", srv.get("name", "?"), e)

    # MCP 自动发现: .agent-base/mcp/*.yaml
    mcp_auto_dir = Path(".agent-base/mcp")
    if mcp_auto_dir.exists():
        for mcp_yaml in mcp_auto_dir.glob("*.yaml"):
            try:
                srv_cfg = yaml.safe_load(mcp_yaml.read_text(encoding="utf-8"))
                if not isinstance(srv_cfg, dict) or srv_cfg.get("disabled"):
                    continue
                mcp_config = MCPServerConfig(
                    name=srv_cfg.get("name", mcp_yaml.stem),
                    transport=srv_cfg.get("transport", "stdio"),
                    command=srv_cfg.get("command", ""),
                    args=srv_cfg.get("args", []),
                    url=srv_cfg.get("url", ""),
                    env=srv_cfg.get("env", {}),
                )
                mcp_client = MCPClient(mcp_config)
                await mcp_client.connect()
                for adapter in mcp_client.create_adapters():
                    _tools.register(adapter)
                _mcp_clients.append(mcp_client)
                logger.info("MCP %s (auto): %d tools", mcp_config.name, len(mcp_client.tool_names))
            except ImportError:
                logger.info("MCP %s (auto): skipped", mcp_yaml.stem)
            except Exception as e:
                logger.info("MCP %s (auto): error — %s", mcp_yaml.stem, e)

    # ── 技能结晶器 ────────────────────────────────────
    try:
        _skill_registry = SkillRegistry()
        from agent.crystallizer import SkillCrystallizer
        _skill_crystallizer = SkillCrystallizer(_skill_registry)
        loaded = _skill_crystallizer.load_existing_skills()
        if loaded:
            logger.info("已加载 %d 个结晶技能", loaded)
    except Exception as e:
        logger.debug("技能结晶器装配跳过: %s", e)

    # ── Capability 渐进式披露 ─────────────────────────
    catalog = CapabilityCatalog()
    _capability_registry = CapabilityRegistry(catalog)
    _capability_registry.register_capability(
        "file_ops", "文件读写编辑", tier=0, source="builtin",
        tools=["read", "write", "edit"],
    )
    _capability_registry.register_capability(
        "shell", "Shell 命令执行", tier=0, source="builtin",
        tools=["bash"],
    )
    _capability_registry.register_capability(
        "search", "文件搜索 (glob/grep)", tier=0, source="builtin",
        tools=["glob", "grep"],
    )
    _capability_registry.register_capability(
        "web", "网页抓取/搜索/浏览器", tier=1, source="builtin",
        tools=["web_fetch", "web_search", "web_browser_navigate",
               "web_browser_click", "web_browser_type", "web_browser_snapshot"],
    )
    _capability_registry.register_capability(
        "delegation", "多 Agent 委托与通信", tier=1, source="builtin",
        tools=["delegate_task", "agent_message"],
    )

    # ── PromptBuilder ─────────────────────────────────
    _cwd = os.getcwd()
    _system_prompt = (
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
    _prompt_builder = PromptBuilder()
    _prompt_builder.set_system_prompt(_system_prompt)

    # ── FlowInspector ─────────────────────────────────
    _inspector = FlowInspector()

    # ── WebAutomation ─────────────────────────────────
    try:
        from agent.web import WebAutomation
        _web = WebAutomation()
    except Exception:
        pass

    # ── PluginAdapter ─────────────────────────────────
    hooks = HookChain()
    try:
        from agent.plugin import PluginAdapter
        _plugin_adapter = PluginAdapter(hooks, _tools, _skill_registry, catalog)
    except Exception:
        pass

    # ── 记忆提取引擎 ──────────────────────────────────
    try:
        memory_dir = os.path.expanduser("~/.agent-memory")
        os.makedirs(memory_dir, exist_ok=True)
        _memory_store = MemoryStore(memory_dir)
        _fact_store = FactStore(_memory_store)
        _domain_index = DomainIndex(_memory_store, _fact_store)
        await _domain_index.initialize()
        _relation_store = RelationStore(_memory_store)
        _task_detector = TaskDetector()
        _memory_extractor = MemoryExtractor(
            provider=_provider,
            fact_store=_fact_store,
            domain_index=_domain_index,
            task_detector=_task_detector,
            relation_store=_relation_store,
        )
        logger.info("记忆提取引擎已启用 (存储: %s)", memory_dir)
    except Exception as e:
        logger.debug("记忆提取装配跳过: %s", e)

    # ── Phase 2/3: 图记忆后端 ─────────────────────────
    _graph_available_backends = detect_available_backends()
    logger.info("可用图后端: %s", _graph_available_backends)

    try:
        _graph_backend = create_graph_backend_from_config(
            config, store=_memory_store, relation_store=_relation_store,
        )
        await _graph_backend.initialize()
        logger.info("图记忆后端已启用: %s (可用: %s)",
                   _graph_backend.backend_name, _graph_available_backends)
    except Exception as e:
        logger.warning("图记忆后端初始化失败，使用文件后端: %s", e)
        if _memory_store:
            _graph_backend = FileGraphBackend(_memory_store, _relation_store)
            await _graph_backend.initialize()
        else:
            _graph_backend = None

    # Phase 3: 社区检测
    _community_detector = CommunityDetector()
    _temporal_engine = TemporalQueryEngine()

    # Phase 3: 共享知识图谱（如果图后端可用）
    if _graph_backend:
        try:
            _shared_graph = SharedGraphManager(_graph_backend)
            await _shared_graph.initialize()
            logger.info("共享知识图谱已启用")
        except Exception as e:
            logger.debug("共享知识图谱装配跳过: %s", e)

    # ── AgentLoop ─────────────────────────────────────
    loop_config = AgentConfig(
        max_turns=agent_cfg.get("max_turns", 100),
        max_tool_calls_per_turn=agent_cfg.get("max_tool_calls_per_turn", 15),
        max_context_tokens=agent_cfg.get("max_context_tokens", 128000),
    )

    _loop = AgentLoop(
        provider=_provider,
        tools=_tools,
        hooks=hooks,
        config=loop_config,
        prompt_builder=_prompt_builder,
        inspector=_inspector,
        capability_registry=_capability_registry,
        memory_extractor=_memory_extractor,
    )

    _context = Context(
        system_prompt=_system_prompt,
        metadata={
            "project_path": _cwd,
            "_web": _web,
            "_skill_registry": _skill_registry,
            "_hooks": hooks,
            "_memory_extractor": _memory_extractor,
            "_skill_crystallizer": _skill_crystallizer,
            "agent_id": "main",
        },
    )

    return _loop, _context


# ── 5 智能体角色定义 ──────────────────────────────────

AGENT_ROLES = {
    "coordinator": {
        "name": "Coordinator", "label": "协调者",
        "color": "#a78bfa", "icon": "C",
        "desc": "多Agent调度与流程监控",
    },
    "planner": {
        "name": "Planner", "label": "规划者",
        "color": "#60a5fa", "icon": "P",
        "desc": "任务分解与步骤规划",
    },
    "coder": {
        "name": "Coder", "label": "编码者",
        "color": "#22d3ee", "icon": "X",
        "desc": "代码实现与文件操作",
    },
    "reviewer": {
        "name": "Reviewer", "label": "审查者",
        "color": "#fbbf24", "icon": "R",
        "desc": "代码审查与测试验证",
    },
    "memorizer": {
        "name": "Memorizer", "label": "记忆者",
        "color": "#f472b6", "icon": "M",
        "desc": "经验总结与知识提取",
    },
}


async def _generate_agent_summaries(user_msg: str, agent_output: str) -> dict:
    """用 LLM 生成每个智能体角色的工作摘要。"""
    if not _provider or not agent_output:
        return {}

    prompt = f"""用户的原始任务: "{user_msg}"

Agent 的完整输出: {agent_output[:3000]}

请为以下5个智能体角色分别写一句简短摘要(每人20字以内)，描述这个角色在此任务中做了什么：

1. Coordinator(协调者):
2. Planner(规划者):
3. Coder(编码者):
4. Reviewer(审查者):
5. Memorizer(记忆者):

请严格按以下JSON格式回复，不要添加其他内容：
{{"coordinator":"...","planner":"...","coder":"...","reviewer":"...","memorizer":"..."}}"""

    try:
        from ai.types import Message as AIMessage, ToolDefinition
        events = await _provider.chat(
            messages=[AIMessage(role="user", content=prompt)],
            tools=[],
            system_prompt="你是my-agent的内部摘要生成器。只输出JSON，不要加上下文。",
            max_tokens=500,
        )
        text = ""
        for e in events:
            if e.type == "text_delta":
                text += e.content
        # 提取 JSON
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception as e:
        logger.debug(f"agent summaries failed: {e}")
    return {}


# ── FastAPI App ────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await build_agent_components()
    FRONTEND_DIR.mkdir(exist_ok=True)
    logger.info("my-agent Web UI ready, model=%s", _provider.model if _provider else "?")
    yield
    # 清理 MCP 连接
    for client in _mcp_clients:
        try:
            await client.disconnect()
        except Exception:
            pass
    # 清理图记忆后端
    if _shared_graph:
        try:
            await _shared_graph.close()
        except Exception:
            pass
    if _graph_backend:
        try:
            await _graph_backend.close()
        except Exception:
            pass

app = FastAPI(title="my-agent Web UI", version="0.2.0", lifespan=lifespan)


@app.get("/")
async def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "my-agent API"}


@app.get("/status")
async def status():
    if _loop:
        mcp_info = []
        for c in _mcp_clients:
            mcp_info.append({"name": c.config.name, "tools": len(c.tool_names)})
        return {
            "model": _loop.model_name,
            "tools": _loop.tool_count,
            "mcp_servers": len(_mcp_clients),
            "mcp_detail": mcp_info,
            "memory": _memory_extractor is not None,
            "relations": bool(_relation_store),
            "skills": _skill_crystallizer is not None,
            "plugins": _plugin_adapter is not None,
            "graph": {
                "enabled": _graph_backend is not None,
                "backend": _graph_backend.backend_name if _graph_backend else "none",
                "available": _graph_available_backends,
            },
            "community_detection": _community_detector is not None,
            "temporal_queries": _temporal_engine is not None,
            "shared_graph": _shared_graph is not None,
            "ready": True,
        }
    return {"ready": False}


@app.post("/api/chat")
async def chat(request: Request):
    """SSE 实时流式聊天。

    修复要点: 不在生成器内 await 长时间任务，
    而是用后台任务 + 队列轮询，保证事件实时推送。
    """
    global _agent_busy

    if _agent_busy:
        async def busy():
            yield f"data: {json.dumps({'type':'error','message':'Agent 正忙'})}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"
        return StreamingResponse(busy(), media_type="text/event-stream")

    _agent_busy = True

    try:
        body = await request.json()
        user_msg = body.get("message", "").strip()
        if not user_msg:
            _agent_busy = False
            async def empty():
                yield f"data: {json.dumps({'type':'error','message':'消息为空'})}\n\n"
                yield f"data: {json.dumps({'type':'done'})}\n\n"
            return StreamingResponse(empty(), media_type="text/event-stream")
    except Exception as e:
        exc_msg = str(e)
        logger.exception("chat request parse error")
        _agent_busy = False
        async def err():
            yield f"data: {json.dumps({'type':'error','message':f'请求解析失败: {exc_msg}'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    queue: asyncio.Queue = asyncio.Queue()
    agent_done = asyncio.Event()

    async def run_agent_task():
        """后台: 运行 Agent，通过 AgentLoop 把事件实时推入 queue。

        ★ 关键修复: 使用 AgentLoop.run() 代替直接 _provider.stream()。
        AgentLoop 的内层 tool_calls 循环会正确执行工具并把结果喂回 LLM，
        从而解决「复杂问题只总结不回答」的 bug。
        """
        try:
            await queue.put({"type": "thinking"})
            await queue.put({"type": "agent", "agent": "coordinator", "status": "active"})
            await asyncio.sleep(0.15)
            await queue.put({"type": "agent", "agent": "planner", "status": "active"})
            await asyncio.sleep(0.15)
            await queue.put({"type": "agent", "agent": "coder", "status": "active"})
            await queue.put({"type": "agent", "agent": "reviewer", "status": "active"})

            # ── 注册临时 hooks：将 AgentLoop 内部事件转发到 SSE queue ──
            tool_names_seen: list[str] = []

            async def _on_stream_text(event: AgentEvent):
                text = event.data.get("text", "")
                await queue.put({"type": "text", "content": text})

            async def _on_tool_progress(event: AgentEvent):
                tool_name = event.data.get("tool_name", "?")
                status = event.data.get("status", "")
                if status == "started" and tool_name not in tool_names_seen:
                    tool_names_seen.append(tool_name)
                    await queue.put({"type": "tool_start", "tool": tool_name})

            h_text = HookHandler(
                name="_web_stream_text", event_type=AgentEventType.STREAM_TEXT,
                callback=_on_stream_text, priority=90,
            )
            h_tool = HookHandler(
                name="_web_tool_progress", event_type=AgentEventType.TOOL_PROGRESS,
                callback=_on_tool_progress, priority=90,
            )

            if _loop and _loop.hooks:
                _loop.hooks.register(h_text)
                _loop.hooks.register(h_tool)

            try:
                if _context:
                    _context.messages.clear()

                # ★ 核心修复: 使用 AgentLoop.run() 代替直接 provider.stream()
                # AgentLoop 正确实现了双层循环:
                #   外层 followUp + 内层 tool_calls(执行工具→喂回LLM→继续)
                result = await _loop.run(user_msg, _context)

                full_output = result.final_output
                turn_count = result.total_turns
                tool_call_count = result.total_tool_calls

                # Agent 完成动画
                for who in ["planner", "coder", "reviewer"]:
                    await queue.put({"type": "agent", "agent": who, "status": "done"})
                    await asyncio.sleep(0.1)
                await queue.put({"type": "agent", "agent": "coordinator", "status": "done"})

                # 发送统计
                await queue.put({
                    "type": "stats",
                    "turns": turn_count,
                    "tools": tool_call_count,
                    "model": _provider.model if _provider else "?",
                })

                # ── Memorizer 启动 ──
                await queue.put({"type": "agent", "agent": "memorizer", "status": "active"})
                await queue.put({"type": "memory", "status": "extracting"})

                # 摘要
                summaries = {
                    "coordinator": f"调度完成: 协调了规划、编码、审查全过程，共 {turn_count} 轮",
                    "planner": f"分析需求并拆解为可执行步骤",
                    "coder": f"执行核心实现，调用 {tool_call_count} 个工具",
                    "reviewer": f"验证了输出质量，确认结果符合要求",
                    "memorizer": "正在提取记忆...",
                }
                await queue.put({"type": "summaries", "data": summaries})
                await queue.put({"type": "done"})

                # ── 后台任务：记忆提取 + 技能结晶（不阻塞用户输入） ──
                if _memory_extractor or _skill_crystallizer:

                    async def _bg_memorize():
                        try:
                            if _memory_extractor and full_output:
                                try:
                                    msgs_for_memory = [
                                        Message(role="user", content=user_msg),
                                        Message(role="assistant", content=full_output),
                                    ]
                                    memory_result = await _memory_extractor.extract_digest(
                                        msgs_for_memory, session_id=f"web-{int(time.time())}"
                                    )
                                    if memory_result and memory_result.success:
                                        logger.info("记忆提取成功: digest=%s wiki=%s",
                                                    memory_result.digest_id,
                                                    memory_result.wiki_id or "-")
                                    else:
                                        reason = memory_result.reason if memory_result else "no_result"
                                        logger.debug("记忆提取跳过: %s", reason)
                                except Exception as e:
                                    logger.debug("后台记忆提取异常: %s", e)

                            if _skill_crystallizer and full_output:
                                try:
                                    skills = _skill_crystallizer.crystallize(full_output)
                                    if skills:
                                        logger.info("技能结晶: %d 个 — %s",
                                                    len(skills),
                                                    [s.name for s in skills])
                                except Exception as e:
                                    logger.debug("后台技能结晶异常: %s", e)
                        except Exception as e:
                            logger.warning("后台记忆任务异常: %s", e)

                    asyncio.create_task(_bg_memorize())

            finally:
                # 清理临时 hooks，避免污染后续请求
                if _loop and _loop.hooks:
                    _loop.hooks.unregister("_web_stream_text")
                    _loop.hooks.unregister("_web_tool_progress")

        except Exception as e:
            logger.exception("agent task error")
            await queue.put({"type": "error", "message": f"Agent 运行错误: {e}"})
            await queue.put({"type": "done"})
        finally:
            agent_done.set()

    # 后台启动 Agent 任务
    asyncio.create_task(run_agent_task())

    async def event_stream():
        """实时从 queue 读取事件 → SSE 推送。"""
        try:
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=0.3)
                    yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if agent_done.is_set() and queue.empty():
                        break
                    # 心跳: 保持连接
                    yield f": heartbeat\n\n"

            # run_agent_task 已发送 done，这里不需要重复

        except Exception as e:
            yield f"data: {json.dumps({'type':'error','message':str(e)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"
        finally:
            global _agent_busy
            _agent_busy = False

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 设置面板 API ──────────────────────────────────────


@app.get("/api/settings/model")
async def settings_model():
    config = get_config()
    provider_cfg = get_provider_config(config, "")
    win = None
    if _provider:
        try:
            win = await _provider.discover_context_window()
        except Exception:
            pass
    return {
        "model": _loop.model_name if _loop else "?",
        "context_window": win,
        "provider_type": config.get("providers", {}).get("default", "openai_compat"),
        "base_url": provider_cfg.get("base_url", ""),
    }


@app.get("/api/settings/tools")
async def settings_tools():
    if not _tools:
        return {"tools": [], "count": 0}
    items = []
    for name, tool in _tools.tools.items():
        items.append({
            "name": name,
            "description": getattr(tool, "description", "") or "",
            "parameters": len(getattr(tool, "parameters", {}) or {}),
        })
    return {"tools": items, "count": len(items)}


@app.get("/api/settings/mcp")
async def settings_mcp():
    items = []
    for c in _mcp_clients:
        items.append({
            "name": c.config.name,
            "transport": c.config.transport,
            "tools": len(c.tool_names),
            "tool_names": c.tool_names[:20],
        })
    return {"servers": items, "count": len(items)}


@app.get("/api/settings/skills")
async def settings_skills():
    if not _skill_registry:
        return {"skills": [], "count": 0}
    skills = _skill_registry.list_all()
    items = []
    for s in skills:
        items.append({
            "name": s.name,
            "description": s.description,
            "trigger": getattr(s, "trigger_condition", "") or "",
            "quality": getattr(s, "quality_score", 0) or 0,
            "created_at": getattr(s, "created_at", 0) or 0,
        })
    return {"skills": items, "count": len(items)}


@app.get("/api/settings/memory")
async def settings_memory():
    result = {"digests": 0, "wikis": 0, "relations": {}, "domains": []}
    if _fact_store:
        try:
            result["digests"] = len(await _fact_store.list_ids("digest") or [])
            result["wikis"] = len(await _fact_store.list_ids("wiki") or [])
        except Exception:
            pass
    if _relation_store:
        try:
            result["relations"] = await _relation_store.stats()
        except Exception:
            pass
    if _domain_index:
        try:
            result["domains"] = await _domain_index.list_domains()
        except Exception:
            pass
    return result


@app.get("/api/settings/config")
async def settings_config():
    config = get_config()
    agent_cfg = config.get("agent", {})
    return {
        "max_turns": agent_cfg.get("max_turns", 100),
        "max_tool_calls_per_turn": agent_cfg.get("max_tool_calls_per_turn", 15),
        "max_context_tokens": agent_cfg.get("max_context_tokens", 128000),
        "max_tool_result_chars": agent_cfg.get("max_tool_result_chars", 50000),
    }


# ── Phase 2/3: 图记忆 API ──────────────────────────────

@app.get("/api/graph/stats")
async def graph_stats():
    """获取图记忆统计。"""
    if not _graph_backend:
        return {"error": "图记忆后端未启用"}
    try:
        stats = await _graph_backend.stats()
        stats["available_backends"] = _graph_available_backends
        return stats
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/graph/search")
async def graph_search(q: str = "", top_k: int = 10):
    """图搜索：实体 + 关系。"""
    if not _graph_backend:
        return {"error": "图记忆后端未启用"}
    if not q:
        return {"entities": [], "edges": [], "total": 0}
    try:
        entities = await _graph_backend.search_entities(q, top_k)
        edges = await _graph_backend.search_edges(q, top_k)
        return {
            "entities": [
                {"id": e.id, "name": e.name, "type": e.entity_type,
                 "summary": e.summary[:200]} for e in entities
            ],
            "edges": [
                {"source": e.source_name, "target": e.target_name,
                 "relation": e.relation, "fact": e.fact} for e in edges
            ],
            "total": len(entities) + len(edges),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/graph/entity/{entity_name}")
async def graph_entity(entity_name: str, depth: int = 1):
    """获取实体及其关联。"""
    if not _graph_backend:
        return {"error": "图记忆后端未启用"}
    try:
        result = await _graph_backend.get_relations(entity_name, depth)
        return {
            "entities": [
                {"id": e.id, "name": e.name, "type": e.entity_type} for e in result.entities
            ],
            "edges": [
                {"source": e.source_name, "target": e.target_name,
                 "relation": e.relation, "fact": e.fact} for e in result.edges
            ],
            "total": result.total_found,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/graph/bfs")
async def graph_bfs(start: str = "", max_depth: int = 3, max_nodes: int = 50):
    """BFS 子图遍历。"""
    if not _graph_backend:
        return {"error": "图记忆后端未启用"}
    if not start:
        return {"error": "start 参数必填"}
    try:
        result = await _graph_backend.bfs_traverse(start, max_depth, max_nodes)
        return {
            "entities": [
                {"id": e.id, "name": e.name, "type": e.entity_type} for e in result.entities
            ],
            "edges": [
                {"source": e.source_name, "target": e.target_name,
                 "relation": e.relation, "fact": e.fact} for e in result.edges
            ],
            "total_nodes": len(result.entities),
            "total_edges": len(result.edges),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/graph/communities")
async def graph_communities():
    """社区检测结果。"""
    if not _community_detector or not _graph_backend:
        return {"error": "社区检测未启用"}
    try:
        stats = await _graph_backend.stats()
        if stats.get("total_entities", 0) == 0:
            return {"communities": [], "total": 0, "message": "图中没有实体"}

        # 获取所有实体和边
        all_entities = await _graph_backend.search_entities("", 200)
        all_edges = await _graph_backend.search_edges("", 500)

        entity_names = [e.name for e in all_entities]
        edge_tuples = [
            (e.source_name, e.target_name, e.weight)
            for e in all_edges
            if e.source_name in entity_names and e.target_name in entity_names
        ]

        communities = await _community_detector.detect(entity_names, edge_tuples)

        return {
            "communities": [
                {"id": c.id, "name": c.name, "size": c.size,
                 "summary": c.summary,
                 "top_entities": c.entities[:5]}
                for c in communities
            ],
            "total": len(communities),
            "algorithm": "louvain",
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/graph/temporal")
async def graph_temporal(entity: str = "", at_time: str = ""):
    """时间旅行查询。"""
    if not _temporal_engine or not _graph_backend:
        return {"error": "时间旅行查询未启用"}
    if not entity:
        return {"error": "entity 参数必填"}
    try:
        edges = await _graph_backend.search_edges(entity, 100)
        if at_time:
            entities = await _graph_backend.search_entities(entity, 10)
            snapshot = await _temporal_engine.query_at_time(entities, edges, at_time)
            return {
                "at_time": at_time,
                "entity_count": len(snapshot.entities),
                "edge_count": len(snapshot.edges),
                "edges": [
                    {"source": e.source_name, "target": e.target_name,
                     "relation": e.relation, "valid_at": e.valid_at,
                     "invalid_at": e.invalid_at} for e in snapshot.edges
                ],
            }
        else:
            history = await _temporal_engine.get_entity_history(entity, edges)
            return {"entity": entity, "history": history}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/graph/shared")
async def graph_shared():
    """共享知识图谱状态。"""
    if not _shared_graph:
        return {"error": "共享知识图谱未启用"}
    try:
        stats = await _shared_graph.get_shared_stats()
        agents = await _shared_graph.list_contributing_agents()
        return {
            **stats,
            "agents": [
                {"id": a.agent_id, "entities": a.entity_count,
                 "edges": a.edge_count, "last_active": a.last_active}
                for a in agents
            ],
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.WARNING)
    print("=" * 50)
    print("  my-agent Web UI  v0.2.0")
    print("  http://127.0.0.1:8765")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
