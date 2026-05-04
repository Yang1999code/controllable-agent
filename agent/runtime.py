"""agent/runtime.py — IAgentRuntime（★ 需求4 核心增量）。

代理类型注册 + spawn/spawn_parallel + 并发控制 + Agent间通信 + Agent自动选择。

参考：
- Hermes delegate_tool.py (1252行) — 线程池子Agent + 凭证池 + 受限工具集
- multica daemon.go — 有界信号量 + pollLoop + handleTask
- Pi Agent subagent 扩展 — Single/Parallel/Chain + 进程隔离
- CCB forkSubagent.ts (210行) — Fork 缓存共享 + 递归检测
"""

import asyncio
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import yaml

from ai.types import Context, Message, AgentEvent, AgentEventType
from agent.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


# ── 数据类 ─────────────────────────────────────────────

@dataclass
class AgentTypeConfig:
    """代理类型配置。

    参考：
    - oh-my-opencode agent 定义（Sisyphus/Atlas/Oracle）
    - CCB agent YAML frontmatter（name/model/tools/systemPrompt）

    持久化位置：.agent-base/agents/{name}.yaml
    """

    name: str
    description: str
    model: str = ""
    system_prompt: str = ""
    tools_whitelist: list[str] = field(default_factory=list)
    tools_blacklist: list[str] = field(default_factory=lambda: [
        "delegate_task",
        "crystallize",
    ])
    max_tokens: int = 4096
    category: str = "general"
    when_to_use: str = ""
    initial_prompt: str = ""
    omit_context: bool = False

    @classmethod
    def from_yaml(cls, path: str) -> "AgentTypeConfig":
        """从 YAML 文件加载。"""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if v is not None})

    def to_yaml(self, path: str) -> None:
        """持久化为 YAML 文件。"""
        data = {k: v for k, v in self.__dict__.items()
                if not k.startswith("_") and v is not None}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


@dataclass
class SubAgentResult:
    """子Agent执行结果。

    参考 Hermes delegate_tool.py 535-569行的结果结构。
    """

    task_id: str
    agent_type: str
    status: str  # "completed" / "failed" / "timeout" / "rejected"
    output: str
    usage: dict = field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None
    exit_reason: str = ""
    tool_trace: list = field(default_factory=list)


# ── ABC ───────────────────────────────────────────────

class IAgentRuntime(ABC):
    """多 Agent 运行时抽象接口。"""

    @abstractmethod
    def register_agent_type(self, config: AgentTypeConfig) -> None: ...
    @abstractmethod
    def get_agent_type(self, name: str) -> AgentTypeConfig | None: ...
    @abstractmethod
    def list_agent_types(self) -> list[AgentTypeConfig]: ...
    @abstractmethod
    def select_agent(self, task: str) -> str | None: ...
    @abstractmethod
    async def spawn(self, agent_type: str | None = None, task: str = "",
                    context: dict | None = None, current_depth: int = 0) -> SubAgentResult: ...
    @abstractmethod
    async def spawn_parallel(self, tasks: list[dict],
                             max_concurrency: int = 3) -> list[SubAgentResult]: ...
    @abstractmethod
    def send_message(self, from_agent: str, to_agent: str, content: str) -> None: ...
    @abstractmethod
    def check_inbox(self, agent_id: str) -> str | None: ...
    @abstractmethod
    def get_active_children(self) -> list[dict]: ...
    @abstractmethod
    def get_delegation_stats(self) -> dict: ...
    @abstractmethod
    def get_config(self) -> dict: ...


# ── 实现 ──────────────────────────────────────────────

class AgentRuntime(IAgentRuntime):
    """IAgentRuntime 实现。

    V1 采用 Hermes 线程级隔离（asyncio），参考 multica 信号量并发控制。

    Phase 3 增强：
    - store_factory: AgentStoreFactory 注入，为每个子 Agent 创建隔离存储
    - shared_space: SharedSpace 共享区管理
    - orchestrate(): 分阶段串并行编排

    并发安全规则（参考 Hermes delegate_tool.py skip_memory=True）：
    1. Nudge 只在主Agent的 turn_end 触发
    2. 子Agent的 tools_blacklist 默认含 memory 工具
    3. 子Agent不触发、不接收、不响应 Nudge
    """

    def __init__(
        self,
        tools: dict,
        provider,  # IModelProvider（子Agent复用）
        hooks,
        max_concurrent: int = 3,
        max_depth: int = 2,
        default_timeout: int = 300,
        store_factory=None,  # AgentStoreFactory（可选）
        shared_space=None,  # SharedSpace（可选）
    ):
        self._tools = tools
        self._provider = provider
        self._hooks = hooks
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_depth = max_depth
        self._default_timeout = default_timeout
        self._store_factory = store_factory
        self._shared_space = shared_space
        self._agent_types: dict[str, AgentTypeConfig] = {}
        self._active_children: dict[str, dict] = {}
        self._inboxes: dict[str, asyncio.Queue] = {}
        self._stats = {
            "total_spawned": 0, "total_completed": 0, "total_failed": 0,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cost": 0.0, "max_depth_reached": 0,
        }

    # ── 代理类型注册 ──

    def register_agent_type(self, config: AgentTypeConfig) -> None:
        if config.name in self._agent_types:
            raise ValueError(f"Agent type '{config.name}' already registered")
        for tn in config.tools_whitelist:
            if tn not in self._tools:
                raise ValueError(f"Tool '{tn}' in whitelist not found in catalog")
        self._agent_types[config.name] = config
        yaml_path = f".agent-base/agents/{config.name}.yaml"
        if not Path(yaml_path).exists():
            try:
                config.to_yaml(yaml_path)
            except Exception as e:
                logger.warning(f"Failed to persist agent config: {e}")

    def get_agent_type(self, name: str) -> AgentTypeConfig | None:
        return self._agent_types.get(name)

    def list_agent_types(self) -> list[AgentTypeConfig]:
        return list(self._agent_types.values())

    # ── 工具过滤 ──

    def _filter_tools(self, config: AgentTypeConfig) -> dict:
        """黑名单优先 → 白名单筛选 → 返回过滤后的工具集。"""
        filtered = {}
        for name, tool in self._tools.items():
            if name in config.tools_blacklist:
                continue
            if config.tools_whitelist and name not in config.tools_whitelist:
                continue
            filtered[name] = tool
        return filtered

    # ── 分词工具 ──

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中文分词 + 英文分词统一入口。

        规范副本位于 agent/memory/index.py。此为供 select_agent() 使用的副本。
        """
        try:
            import jieba
            words = jieba.lcut(text.lower())
            return [w.strip() for w in words
                    if w.strip() and not re.match(r'^[\s\W_]+$', w)]
        except ImportError:
            pass
        clean = text.lower().strip()
        tokens: list[str] = []
        eng_words = re.findall(r'[a-z0-9]+', clean)
        tokens.extend(eng_words)
        chinese_chars = re.findall(r'[一-鿿]+', clean)
        for segment in chinese_chars:
            for i in range(len(segment) - 1):
                tokens.append(segment[i:i + 2])
            if len(segment) == 1:
                tokens.append(segment)
        return tokens

    # ── Agent 自动选择 ──

    def select_agent(self, task: str) -> str | None:
        """根据任务描述自动选择最匹配的 Agent 类型。

        匹配算法：Overlap Coefficient —— |A ∩ B| / min(|A|, |B|)
        """
        if not self._agent_types or not task:
            return None

        task_tokens = set(self._tokenize(task))
        if not task_tokens:
            return None

        best_name = None
        best_score = 0.0

        for name, config in self._agent_types.items():
            match_text = " ".join([
                name, config.description,
                config.when_to_use, config.category,
            ])
            agent_tokens = set(self._tokenize(match_text))
            if not agent_tokens:
                continue

            intersection = task_tokens & agent_tokens
            overlap = len(intersection) / min(len(task_tokens), len(agent_tokens))

            if overlap > best_score:
                best_score = overlap
                best_name = name

        return best_name if best_score >= 0.3 else None

    # ── Agent间通信 ──

    def send_message(self, from_agent: str, to_agent: str, content: str) -> None:
        """向指定 Agent 发送消息。"""
        if to_agent not in self._inboxes:
            self._inboxes[to_agent] = asyncio.Queue(maxsize=50)
        try:
            self._inboxes[to_agent].put_nowait({
                "from": from_agent,
                "content": content,
                "timestamp": time.time(),
            })
        except asyncio.QueueFull:
            pass

    def check_inbox(self, agent_id: str) -> str | None:
        """检查指定 Agent 的收件箱。非阻塞。"""
        inbox = self._inboxes.get(agent_id)
        if not inbox or inbox.empty():
            return None
        try:
            msg = inbox.get_nowait()
            return f"[{msg['from']}] {msg['content']}"
        except asyncio.QueueEmpty:
            return None

    # ── spawn ──

    async def spawn(
        self, agent_type: str | None = None, task: str = "",
        context: dict | None = None,
        current_depth: int = 0,
    ) -> SubAgentResult:
        """派生子Agent执行任务。

        Phase 3 增强：
        - 自动为子 Agent 创建隔离存储空间（通过 store_factory）
        - 注入 SharedSpace 引用到 context
        - 使用角色特定的 max_turns

        参考 Hermes delegate_tool.py 709-716行 的 spawn 流程。
        """
        task_id = uuid4().hex[:8]

        # 0. Agent 自动选择
        if not agent_type:
            agent_type = self.select_agent(task)
            if not agent_type:
                return SubAgentResult(
                    task_id=task_id, agent_type="unknown",
                    status="rejected", output="",
                    error="agent_type not specified and auto-selection found no match",
                )

        # 0. 深度检查
        if current_depth >= self._max_depth:
            return SubAgentResult(
                task_id=task_id, agent_type=agent_type,
                status="rejected", output="",
                error=f"Max depth reached ({self._max_depth})",
            )

        # 1. 获取配置
        config = self.get_agent_type(agent_type)
        if not config:
            return SubAgentResult(
                task_id=task_id, agent_type=agent_type,
                status="failed", output="",
                error=f"Unknown agent type: {agent_type}",
            )

        # 1.5 为 agent_id 创建 inbox + 隔离存储
        agent_id = context.get("agent_id", task_id) if context else task_id
        if agent_id not in self._inboxes:
            self._inboxes[agent_id] = asyncio.Queue(maxsize=50)

        # 1.6 Phase 3: 创建隔离存储空间
        if self._store_factory:
            self._store_factory.create_agent_stores(agent_id)

        # 2. 并发控制
        async with self._semaphore:
            # 3. 过滤工具
            filtered_tools = self._filter_tools(config)

            # 4. 触发 subagent_start hook
            await self._hooks.fire(AgentEvent(
                type=AgentEventType.SUBAGENT_START,
                data={"task_id": task_id, "agent_type": agent_type, "task": task},
            ))

            # 5. 构建隔离上下文
            child_metadata = {
                "task_id": task_id,
                "agent_type": agent_type,
                "agent_id": agent_id,
                "_runtime": self,
                "depth": current_depth + 1,
                "parent_messages": context.get("parent_messages", []) if context else [],
            }
            # Phase 3: 注入 store_factory 和 shared_space
            if self._store_factory:
                child_metadata["_store_factory"] = self._store_factory
            if self._shared_space:
                child_metadata["_shared_space"] = self._shared_space

            child_context = Context(
                system_prompt=config.system_prompt,
                tools=filtered_tools,
                metadata=child_metadata,
            )

            # 注入 initial_prompt
            if config.initial_prompt:
                child_context.messages.append(Message(
                    role="user", content=config.initial_prompt,
                ))

            # 6. 执行子Agent循环
            start_time = time.time()
            self._active_children[task_id] = {
                "task_id": task_id, "agent_type": agent_type,
                "status": "running", "started_at": start_time,
            }
            self._stats["total_spawned"] += 1
            self._stats["max_depth_reached"] = max(
                self._stats["max_depth_reached"], current_depth + 1,
            )

            try:
                from agent.loop import AgentLoop, AgentConfig

                child_registry = ToolRegistry()
                for name, tool in filtered_tools.items():
                    child_registry.register(tool)

                child_loop = AgentLoop(
                    provider=self._provider,
                    tools=child_registry,
                    hooks=self._hooks,
                    config=AgentConfig(max_turns=self._get_max_turns(agent_type)),
                )
                result = await asyncio.wait_for(
                    child_loop.run(task, child_context),
                    timeout=self._default_timeout,
                )
                status = "completed"
                error = None
                self._stats["total_completed"] += 1
                self._stats["total_input_tokens"] += result.total_input_tokens
                self._stats["total_output_tokens"] += result.total_output_tokens

            except asyncio.TimeoutError:
                status = "timeout"
                error = f"Timeout after {self._default_timeout}s"
                result = None
                self._stats["total_failed"] += 1

            except Exception as e:
                status = "failed"
                error = f"{type(e).__name__}: {str(e)}"
                result = None
                self._stats["total_failed"] += 1

            finally:
                await self._cleanup(task_id)

            duration_ms = int((time.time() - start_time) * 1000)

            # 7. 触发 subagent_stop hook
            await self._hooks.fire(AgentEvent(
                type=AgentEventType.SUBAGENT_STOP,
                data={
                    "task_id": task_id,
                    "agent_type": agent_type,
                    "status": status,
                    "usage": {
                        "input_tokens": result.total_input_tokens if result else 0,
                        "output_tokens": result.total_output_tokens if result else 0,
                    },
                    "duration_ms": duration_ms,
                },
            ))

            return SubAgentResult(
                task_id=task_id,
                agent_type=agent_type,
                status=status,
                output=result.final_output if result else "",
                usage={
                    "input_tokens": result.total_input_tokens if result else 0,
                    "output_tokens": result.total_output_tokens if result else 0,
                },
                duration_ms=duration_ms,
                error=error,
                exit_reason=result.status if result else "error",
            )

    # ── spawn_parallel ──

    async def spawn_parallel(
        self, tasks: list[dict], max_concurrency: int | None = None,
    ) -> list[SubAgentResult]:
        """并行派生多个子Agent。

        使用 runtime 级别的信号量控制并发，不再创建额外的局部信号量。
        max_concurrency 参数保留用于 API 兼容但不再生效。
        """
        async def bounded_spawn(task_dict):
            return await self.spawn(**task_dict)

        results = await asyncio.gather(
            *[bounded_spawn(t) for t in tasks],
            return_exceptions=True,
        )
        final: list[SubAgentResult] = []
        for r in results:
            if isinstance(r, Exception):
                final.append(SubAgentResult(
                    task_id="error", agent_type="unknown",
                    status="failed", output="",
                    error=str(r),
                ))
            else:
                final.append(r)
        return final

    # ── 状态查询 ──

    def get_active_children(self) -> list[dict]:
        return [
            v for v in self._active_children.values()
            if v["status"] == "running"
        ]

    def get_delegation_stats(self) -> dict:
        return {**self._stats, "active_count": len(self.get_active_children())}

    def get_config(self) -> dict:
        return {
            "max_concurrent_children": self._max_concurrent,
            "max_depth": self._max_depth,
            "default_timeout_sec": self._default_timeout,
        }

    # ── 清理 ──

    async def _cleanup(self, task_id: str) -> None:
        """资源清理（参考 Hermes delegate_tool.py 643-684行 finally 块）。"""
        self._active_children.pop(task_id, None)

    # ── Phase 3: 角色特定 max_turns ──

    @staticmethod
    def _get_max_turns(agent_type: str) -> int:
        """获取角色特定的 max_turns，默认 50。"""
        from agent.role_prompts import ROLE_MAX_TURNS
        return ROLE_MAX_TURNS.get(agent_type, 50)

    # ── Phase 3: 分阶段编排 ──

    async def orchestrate(
        self, user_request: str, max_retries: int = 3,
    ) -> list[SubAgentResult]:
        """分阶段串并行编排多 Agent 协作。

        执行流程：
        Phase A: await planner（串行）
        Phase B: gather(coder+reviewer配对, memorizer, coordinator)（并行）
        Phase C: await reviewer_final 总体集成测试（串行）
        Phase D: await memorizer_final 总结（串行）

        打回机制：Phase C 不通过 → 回到 Phase B（最多 max_retries 轮）
        """
        all_results: list[SubAgentResult] = []

        # Phase A: Planner（串行，写 plan.md）
        planner_result = await self.spawn(
            agent_type="planner",
            task=f"分析以下需求并分解为可执行步骤，写入 shared/plan.md：\n{user_request}",
            context={"agent_id": "planner_001"},
            current_depth=1,
        )
        all_results.append(planner_result)

        if planner_result.status != "completed":
            logger.error("Planner failed, aborting orchestration")
            return all_results

        # Phase B + C 循环（带打回机制）
        for attempt in range(max_retries + 1):
            # Phase B: 并行执行（Coder+Reviewer 配对 + Memorizer + Coordinator）
            phase_b_tasks = [
                {"agent_type": "coordinator", "task": "监控多 Agent 协作流程",
                 "context": {"agent_id": "coordinator_001"}, "current_depth": 1},
                {"agent_type": "memorizer", "task": "检索历史经验支持当前任务",
                 "context": {"agent_id": "memorizer_001"}, "current_depth": 1},
                {"agent_type": "coder", "task": "按 plan.md 执行代码实现",
                 "context": {"agent_id": "coder_001"}, "current_depth": 1},
                {"agent_type": "reviewer", "task": "模块级快速验证，和 coder_001 配对",
                 "context": {"agent_id": "reviewer_001"}, "current_depth": 1},
            ]
            phase_b_results = await self.spawn_parallel(phase_b_tasks)
            all_results.extend(phase_b_results)

            # Phase C: 总体集成测试（串行）
            phase_c_result = await self.spawn(
                agent_type="reviewer",
                task="执行总体集成测试：跑全量测试、检查模块间接口、验证整体架构一致性",
                context={"agent_id": "reviewer_final"},
                current_depth=1,
            )
            all_results.append(phase_c_result)

            # 检查是否需要打回
            if phase_c_result.status == "completed":
                break
            if attempt < max_retries:
                logger.info("Integration test failed, retrying (attempt %d/%d)",
                            attempt + 1, max_retries)
            else:
                logger.warning("Max retries reached (%d), marking ESCALATE", max_retries)

        # Phase D: Memorizer 最终总结
        phase_d_result = await self.spawn(
            agent_type="memorizer",
            task="总结本次协作：提取各 Agent 经验、结晶可复用技能提案",
            context={"agent_id": "memorizer_002"},
            current_depth=1,
        )
        all_results.append(phase_d_result)

        return all_results
