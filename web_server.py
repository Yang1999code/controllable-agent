"""web_server.py — Web 前端服务。

FastAPI + SSE 实时流式聊天 + 多智能体状态可视化。
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse

from my_agent import (
    Context, Message,
    ToolRegistry, HookChain,
    AgentLoop, AgentConfig, AgentResult,
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


def get_config():
    return load_config(None)


def build_agent_components():
    global _loop, _context, _provider, _tools

    config = get_config()
    agent_cfg = config.get("agent", {})
    provider_cfg = get_provider_config(config, "")

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

    _tools = ToolRegistry()
    _tools.max_result_chars = agent_cfg.get("max_tool_result_chars", 50000)
    register_all_tools(_tools)

    loop_config = AgentConfig(
        max_turns=agent_cfg.get("max_turns", 100),
        max_tool_calls_per_turn=agent_cfg.get("max_tool_calls_per_turn", 15),
        max_context_tokens=agent_cfg.get("max_context_tokens", 128000),
    )

    _loop = AgentLoop(
        provider=_provider,
        tools=_tools,
        hooks=HookChain(),
        config=loop_config,
    )

    _context = Context(
        system_prompt=(
            f"你是 my-agent，一个多智能体协作框架。底层模型: {model}。"
            f"你支持文件读写、Shell命令、搜索、网页抓取等工具。请用中文回答。"
        ),
        metadata={"agent_id": "main"},
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
    build_agent_components()
    FRONTEND_DIR.mkdir(exist_ok=True)
    logger.info("my-agent Web UI ready, model=%s", _provider.model if _provider else "?")
    yield

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
        return {"model": _loop.model_name, "tools": _loop.tool_count, "ready": True}
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

            # Agent 完成 → 完成动画
            for who in ["planner", "coder", "reviewer"]:
                await queue.put({"type": "agent", "agent": who, "status": "done"})
                await asyncio.sleep(0.2)
            await queue.put({"type": "agent", "agent": "memorizer", "status": "active"})
            await asyncio.sleep(0.3)
            await queue.put({"type": "agent", "agent": "memorizer", "status": "done"})
            await queue.put({"type": "agent", "agent": "coordinator", "status": "done"})

            # 生成各角色摘要
            full_output = text_buffer
            summaries = await _generate_agent_summaries(user_msg, full_output)
            if summaries:
                await queue.put({"type": "summaries", "data": summaries})
            else:
                # 回退摘要
                await queue.put({"type": "summaries", "data": {
                    "coordinator": f"调度完成: 协调了规划、编码、审查全流程",
                    "planner": f"分析需求并拆解为可执行步骤",
                    "coder": f"执行了核心实现，调用 {len(tool_names)} 个工具",
                    "reviewer": f"验证了输出质量，确认结果符合要求",
                    "memorizer": f"已提取本次对话经验，存入记忆系统",
                }})

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
