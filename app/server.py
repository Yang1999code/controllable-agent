"""app/server.py — FastAPI REST API 服务器。

提供 HTTP 和 WebSocket 接口，支持：
- POST /chat - 单次对话
- POST /run - 运行任务
- GET /status - Agent 状态
- GET /health - 健康检查
- WS /ws - WebSocket 实时通信
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from my_agent import (
    AgentLoop, AgentConfig,
    ToolRegistry, HookChain, Context,
)
from app.providers import create_provider
from app.tools import register_all_tools
from app.config.loader import load_config, get_provider_config

logger = logging.getLogger(__name__)


# ── 请求/响应模型 ──────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    model: str | None = None


class ChatResponse(BaseModel):
    response: str
    turns: int
    tool_calls: int
    input_tokens: int
    output_tokens: int


class TaskRequest(BaseModel):
    task: str
    multi_agent: bool = False


class TaskResponse(BaseModel):
    task_id: str
    status: str
    result: str | None = None


@dataclass
class AppState:
    """应用状态管理。"""
    agent_loop: AgentLoop | None = None
    context: Context | None = None
    tasks: dict[str, dict] = None

    def __post_init__(self):
        if self.tasks is None:
            self.tasks = {}


# ── 全局状态 ────────────────────────────────────────────

app_state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    # 启动时初始化
    logger.info("Initializing Agent...")
    await initialize_agent()
    yield
    # 关闭时清理
    logger.info("Shutting down...")


app = FastAPI(
    title="Controllable Agent API",
    description="可控多智能体自迭代 Agent 框架 - REST API",
    version="0.1.0",
    lifespan=lifespan,
)


# ── 初始化 ──────────────────────────────────────────────

async def initialize_agent():
    """初始化 Agent 组件。"""
    config = load_config()
    agent_cfg = config.get("agent", {})
    provider_cfg = get_provider_config(config)

    model = provider_cfg.get("model", "gpt-4o")
    base_url = provider_cfg.get("base_url", "")
    env_var = provider_cfg.get("api_key_env", "")
    api_key = ""

    if env_var:
        import os
        api_key = os.environ.get(env_var, "")
    if not api_key:
        api_key = provider_cfg.get("api_key", "")

    provider_type = config.get("providers", {}).get("default", "openai_compat")
    provider = create_provider(provider_type, model=model, api_key=api_key, base_url=base_url)

    tools = ToolRegistry()
    tools.max_result_chars = agent_cfg.get("max_tool_result_chars", 50000)
    register_all_tools(tools)

    hooks = HookChain()

    loop_config = AgentConfig(
        max_turns=agent_cfg.get("max_turns", 100),
        max_tool_calls_per_turn=agent_cfg.get("max_tool_calls_per_turn", 10),
        max_context_tokens=agent_cfg.get("max_context_tokens", 128000),
    )

    agent_loop = AgentLoop(
        provider=provider,
        tools=tools,
        hooks=hooks,
        config=loop_config,
    )

    import os
    context = Context(
        system_prompt=f"你是 my-agent 助手，运行在 {model} 模型上。请用中文回答用户的问题。",
        metadata={
            "project_path": os.getcwd(),
            "agent_id": "main",
        },
    )

    app_state.agent_loop = agent_loop
    app_state.context = context
    logger.info(f"Agent initialized with model: {model}")


# ── API 端点 ────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """健康检查端点。"""
    return {
        "status": "healthy",
        "agent_ready": app_state.agent_loop is not None,
    }


@app.get("/status")
async def get_status():
    """获取 Agent 状态。"""
    if not app_state.agent_loop:
        return {"error": "Agent not initialized"}

    ctx_pct = 0.0
    try:
        from agent.context_window import count_total_tokens
        tools = app_state.agent_loop.tools.get_definitions()
        tokens = count_total_tokens(
            app_state.context.messages if app_state.context else [],
            app_state.context.system_prompt if app_state.context else "",
            tools,
        )
        max_ctx = getattr(app_state.agent_loop.config, "max_context_tokens", 0)
        if max_ctx <= 0:
            max_ctx = getattr(app_state.agent_loop.provider, "_context_window_cache", 0) or 128000
        ctx_pct = (tokens / max_ctx) * 100 if max_ctx > 0 else 0.0
    except Exception:
        pass

    return {
        "model": app_state.agent_loop.model_name,
        "tool_count": app_state.agent_loop.tool_count,
        "context_usage_percent": round(ctx_pct, 2),
        "active_tasks": len(app_state.tasks),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """单次对话。"""
    if not app_state.agent_loop:
        return {"error": "Agent not initialized"}

    result = await app_state.agent_loop.run(
        request.message,
        app_state.context,
    )

    return ChatResponse(
        response=result.final_output,
        turns=result.total_turns,
        tool_calls=result.total_tool_calls,
        input_tokens=result.total_input_tokens,
        output_tokens=result.total_output_tokens,
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """流式对话 - Server-Sent Events。"""
    if not app_state.agent_loop:
        return

    async def generate():
        if not app_state.agent_loop or not app_state.context:
            return

        # 注册流式 Hook
        from ai.types import AgentEvent, AgentEventType

        async def on_stream_text(event: AgentEvent):
            text = event.data.get("text", "")
            if text:
                yield f"data: {json.dumps({'type': 'text', 'content': text})}\n\n"

        # 添加 Hook
        from agent.hook import HookHandler
        handler = HookHandler(
            name="stream_handler",
            event_type=AgentEventType.STREAM_TEXT,
            callback=on_stream_text,
            priority=99,
        )
        app_state.agent_loop.hooks.register(handler)

        try:
            result = await app_state.agent_loop.run(request.message, app_state.context)
            yield f"data: {json.dumps({'type': 'done', 'result': result.final_output})}\n\n"
        finally:
            app_state.agent_loop.hooks.unregister("stream_handler")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
    )


@app.post("/run", response_model=TaskResponse)
async def run_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """运行任务（异步）。"""
    task_id = uuid.uuid4().hex[:8]

    app_state.tasks[task_id] = {
        "task": request.task,
        "multi_agent": request.multi_agent,
        "status": "pending",
        "result": None,
    }

    async def run():
        try:
            app_state.tasks[task_id]["status"] = "running"
            if not app_state.agent_loop or not app_state.context:
                app_state.tasks[task_id]["status"] = "failed"
                app_state.tasks[task_id]["result"] = "Agent not initialized"
                return

            result = await app_state.agent_loop.run(
                request.task,
                app_state.context,
            )
            app_state.tasks[task_id]["status"] = "completed"
            app_state.tasks[task_id]["result"] = result.final_output
        except Exception as e:
            app_state.tasks[task_id]["status"] = "failed"
            app_state.tasks[task_id]["result"] = str(e)

    background_tasks.add_task(run)

    return TaskResponse(
        task_id=task_id,
        status="pending",
    )


@app.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态。"""
    if task_id not in app_state.tasks:
        return {"error": "Task not found"}

    task = app_state.tasks[task_id]
    return {
        "task_id": task_id,
        "status": task["status"],
        "result": task["result"],
    }


# ── WebSocket ──────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 实时通信。"""
    await websocket.accept()

    # 广播队列
    broadcast_queue: asyncio.Queue = asyncio.Queue()

    # 注册 Hook 广播
    from ai.types import AgentEvent, AgentEventType
    from agent.hook import HookHandler

    async def broadcast(event: AgentEvent):
        await broadcast_queue.put({
            "type": event.type.value,
            "data": event.data,
        })

    hooks = [
        HookHandler("ws_stream", AgentEventType.STREAM_TEXT, broadcast, 99),
        HookHandler("ws_tool", AgentEventType.TOOL_PROGRESS, broadcast, 99),
        HookHandler("ws_turn", AgentEventType.TURN_START, broadcast, 99),
    ]

    if app_state.agent_loop:
        for hook in hooks:
            app_state.agent_loop.hooks.register(hook)

    try:
        while True:
            # 等待消息或事件
            try:
                event = await asyncio.wait_for(
                    broadcast_queue.get(),
                    timeout=0.1
                )
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                pass

            # 检查用户消息
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=0.1
                )
                if data.get("type") == "message" and app_state.agent_loop:
                    # 异步运行 Agent
                    asyncio.create_task(
                        app_state.agent_loop.run(
                            data.get("content", ""),
                            app_state.context,
                        )
                    )
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break

    finally:
        if app_state.agent_loop:
            for hook in hooks:
                app_state.agent_loop.hooks.unregister(hook.name)


# ── 启动 ────────────────────────────────────────────────

def main():
    """命令行启动。"""
    import uvicorn
    uvicorn.run(
        "app.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
