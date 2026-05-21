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
    ToolRegistry, HookChain,
    AgentLoop, AgentConfig, AgentResult,
    FlowInspector, PromptBuilder,
    CapabilityCatalog, CapabilityRegistry,
    SkillRegistry,
    MemoryStore, FactStore, DomainIndex,
    TaskDetector, MemoryExtractor,
    MCPServerConfig, MCPClient,
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
_skill_crystallizer = None
_skill_registry = None
_mcp_clients: list = []
_prompt_builder = None
_inspector = None
_capability_registry = None
_web = None
_plugin_adapter = None


def get_config():
    return load_config(None)


async def build_agent_components():
    global _loop, _context, _provider, _tools
    global _memory_extractor, _skill_crystallizer, _skill_registry
    global _mcp_clients, _prompt_builder, _inspector, _capability_registry, _web, _plugin_adapter

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
        _task_detector = TaskDetector()
        _memory_extractor = MemoryExtractor(
            provider=_provider,
            fact_store=_fact_store,
            domain_index=_domain_index,
            task_detector=_task_detector,
        )
        logger.info("记忆提取引擎已启用 (存储: %s)", memory_dir)
    except Exception as e:
        logger.debug("记忆提取装配跳过: %s", e)

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
            "skills": _skill_crystallizer is not None,
            "plugins": _plugin_adapter is not None,
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
    agent_output = []

    async def run_agent_task():
        """后台: 运行 Agent，把事件实时推入 queue。"""
        try:
            await queue.put({"type": "thinking"})
            await queue.put({"type": "agent", "agent": "coordinator", "status": "active"})
            await asyncio.sleep(0.3)
            await queue.put({"type": "agent", "agent": "planner", "status": "active"})
            await asyncio.sleep(0.3)
            await queue.put({"type": "agent", "agent": "coder", "status": "active"})
            await queue.put({"type": "agent", "agent": "reviewer", "status": "active"})

            if _context:
                _context.messages.clear()

            # 流式调用 provider，逐 token 推送
            from ai.types import Message as AIMessage
            msgs = [AIMessage(role="user", content=user_msg)]
            sys_prompt = _context.system_prompt if _context else ""

            tools_defs = [tool.definition for tool in _tools.tools.values()] if _tools else []

            text_buffer = ""
            tool_names = []
            async for event in _provider.stream(msgs, tools_defs, sys_prompt, max_tokens=4096):
                if event.type == "text_delta":
                    text_buffer += event.content
                    await queue.put({"type": "text", "content": event.content})
                elif event.type == "tool_call":
                    if event.tool_name not in tool_names:
                        tool_names.append(event.tool_name)
                        await queue.put({
                            "type": "tool_start",
                            "tool": event.tool_name,
                        })
                elif event.type == "done":
                    agent_output.append(text_buffer)
                    await queue.put({
                        "type": "stats",
                        "turns": 1,
                        "tools": len(tool_names),
                        "model": _provider.model if _provider else "?",
                    })
                elif event.type == "error":
                    await queue.put({"type": "error", "message": event.error})

            # ── Agent 协作完成动画 ──────────────────────
            for who in ["planner", "coder", "reviewer"]:
                await queue.put({"type": "agent", "agent": who, "status": "done"})
                await asyncio.sleep(0.2)
            await queue.put({"type": "agent", "agent": "coordinator", "status": "done"})

            full_output = text_buffer

            # ── Memorizer 记忆提取 ──────────────────────
            await queue.put({"type": "agent", "agent": "memorizer", "status": "active"})
            memory_result = None
            if _memory_extractor and full_output:
                try:
                    await queue.put({"type": "memory", "status": "extracting"})
                    msgs_for_memory = [
                        Message(role="user", content=user_msg),
                        Message(role="assistant", content=full_output),
                    ]
                    memory_result = await _memory_extractor.extract_digest(
                        msgs_for_memory, session_id=f"web-{int(time.time())}"
                    )
                    if memory_result and memory_result.success:
                        await queue.put({
                            "type": "memory",
                            "status": "done",
                            "digest_id": memory_result.digest_id,
                            "wiki_id": memory_result.wiki_id or "",
                            "message": f"提取记忆 #{memory_result.digest_id}" +
                                       (f" → Wiki {memory_result.wiki_id}" if memory_result.wiki_id else ""),
                        })
                        logger.info("记忆提取成功: digest=%s wiki=%s", memory_result.digest_id, memory_result.wiki_id or "-")
                    else:
                        reason = memory_result.reason if memory_result else "no_result"
                        await queue.put({"type": "memory", "status": "skipped", "reason": reason})
                except Exception as e:
                    logger.debug("memory extraction failed: %s", e)
                    await queue.put({"type": "memory", "status": "skipped", "reason": str(e)})

            # ── 技能结晶 ─────────────────────────────────
            skill_result = None
            if _skill_crystallizer and full_output:
                try:
                    await queue.put({"type": "skill", "status": "crystallizing"})
                    skills = _skill_crystallizer.crystallize(full_output)
                    if skills:
                        skill_result = {"names": [s.name for s in skills], "count": len(skills)}
                        await queue.put({
                            "type": "skill",
                            "status": "done",
                            "skills": skill_result["names"],
                            "count": skill_result["count"],
                            "message": f"结晶 {len(skills)} 个技能: {', '.join(s.name for s in skills)}",
                        })
                        logger.info("技能结晶: %d 个 — %s", len(skills), skill_result["names"])
                    else:
                        await queue.put({"type": "skill", "status": "skipped", "reason": "未发现可提取技能"})
                except Exception as e:
                    logger.debug("skill crystallization failed: %s", e)
                    await queue.put({"type": "skill", "status": "skipped", "reason": str(e)})

            await queue.put({"type": "agent", "agent": "memorizer", "status": "done"})

            # ── 各角色工作摘要 ──────────────────────────
            summaries = await _generate_agent_summaries(user_msg, full_output)
            if not summaries:
                mem_msg = f"已提取本次对话经验，存入记忆系统"
                if memory_result and memory_result.success:
                    mem_msg = f"记忆 #{memory_result.digest_id} 已存储"
                if skill_result and skill_result["count"] > 0:
                    mem_msg += f"；结晶 {skill_result['count']} 个技能"
                summaries = {
                    "coordinator": f"调度完成: 协调了规划、编码、审查全流程",
                    "planner": f"分析需求并拆解为可执行步骤",
                    "coder": f"执行了核心实现，调用 {len(tool_names)} 个工具",
                    "reviewer": f"验证了输出质量，确认结果符合要求",
                    "memorizer": mem_msg,
                }
            else:
                # 增强 memorizer 摘要
                if memory_result and memory_result.success:
                    summaries["memorizer"] = (summaries.get("memorizer", "") + f" [记忆 #{memory_result.digest_id}]").strip()
                if skill_result and skill_result["count"] > 0:
                    summaries["memorizer"] = (summaries.get("memorizer", "") + f" [技能 +{skill_result['count']}]").strip()
            await queue.put({"type": "summaries", "data": summaries})

            await queue.put({"type": "done"})
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


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.WARNING)
    print("=" * 50)
    print("  my-agent Web UI  v0.2.0")
    print("  http://127.0.0.1:8765")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
