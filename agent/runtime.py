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
                    config=AgentConfig(
                        max_turns=self._get_max_turns(agent_type),
                        max_tool_calls_per_turn=context.get(
                            "_max_tool_calls_override",
                            self._get_max_tool_calls(agent_type),
                        ) if context else self._get_max_tool_calls(agent_type),
                    ),
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

            raw_output = result.final_output if result else ""
            safe_output = raw_output.encode("utf-8", errors="replace").decode("utf-8", errors="replace")

            return SubAgentResult(
                task_id=task_id,
                agent_type=agent_type,
                status=status,
                output=safe_output,
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

    @staticmethod
    def _get_max_tool_calls(agent_type: str) -> int:
        """获取角色特定的 max_tool_calls_per_turn，默认 15。"""
        from agent.role_prompts import ROLE_MAX_TOOL_CALLS
        return ROLE_MAX_TOOL_CALLS.get(agent_type, 15)

    @staticmethod
    def _estimate_task_complexity(plan_content: str) -> dict[str, int]:
        """从 plan.md 内容估算任务复杂度，返回各角色的动态 max_tool_calls。

        规则：
        - 统计文件数（write/create/new file 等关键词）
        - 统计步骤数（### / Step / 步骤 等标记）
        - 文件数 <= 3: 简单 → coder=30, reviewer=20
        - 文件数 4-8: 中等 → coder=50, reviewer=30
        - 文件数 >= 9 或步骤 >= 10: 复杂 → coder=80, reviewer=40
        """
        file_count = 0
        for pattern in [r'\b\w+\.py\b', r'\b\w+\.js\b', r'\b\w+\.ts\b',
                        r'\b\w+\.html\b', r'\b\w+\.css\b', r'\b\w+\.json\b',
                        r'\b\w+\.yaml\b', r'\b\w+\.yml\b', r'\b\w+\.md\b']:
            file_count += len(re.findall(pattern, plan_content))

        step_count = len(re.findall(r'(?:^###?\s|Step\s*\d+|步骤\s*\d+|\d+\.\s)', plan_content))

        if file_count >= 9 or step_count >= 10:
            return {"coder": 80, "reviewer": 40, "planner": 25}
        elif file_count >= 4 or step_count >= 5:
            return {"coder": 50, "reviewer": 30, "planner": 20}
        else:
            return {"coder": 30, "reviewer": 20, "planner": 15}

    # ── Phase 3: 分阶段编排 ──

    async def orchestrate(
        self, user_request: str, max_retries: int = 3,
        memory_extractor=None, skill_crystallizer=None,
    ) -> list[SubAgentResult]:
        """分阶段串并行编排多 Agent 协作。

        执行流程：
        Phase A: await planner（串行）
        Phase A+: 分析 plan.md 复杂度，动态调整 max_tool_calls
        Phase B: gather(coder+reviewer配对, memorizer, coordinator)（并行）
        Phase C: await reviewer_final 总体集成测试（串行）
        Phase C+: await reviewer_adversarial 对抗审查（串行）
        Phase Fix+Verify: 修复+验证（最多 2 轮）
        Phase D: await memorizer_final 总结+技能结晶（串行）
        Phase Memory: 统一记忆提取
        Phase Skill: 技能结晶持久化

        打回机制：Phase C 不通过 → 回到 Phase B（最多 max_retries 轮）
        """
        all_results: list[SubAgentResult] = []
        session_id = uuid4().hex[:8]

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

        # Phase A+: 分析 plan.md 复杂度，动态调整 max_tool_calls
        complexity_overrides = self._read_plan_complexity()
        if complexity_overrides:
            logger.info("Task complexity: %s (dynamic overrides)", complexity_overrides)

        # Phase B + C 循环（带打回机制）
        for attempt in range(max_retries + 1):
            # Phase B: 并行执行（Coder+Reviewer 配对 + Memorizer + Coordinator）
            phase_b_tasks = [
                {"agent_type": "coordinator", "task": "监控多 Agent 协作流程",
                 "context": {"agent_id": "coordinator_001"}, "current_depth": 1},
                {"agent_type": "memorizer", "task": "检索历史经验支持当前任务",
                 "context": {"agent_id": "memorizer_001"}, "current_depth": 1},
                {"agent_type": "coder", "task": "按 plan.md 执行代码实现",
                 "context": {"agent_id": "coder_001",
                             "_max_tool_calls_override": complexity_overrides.get("coder")},
                 "current_depth": 1},
                {"agent_type": "reviewer",
                 "task": ("读取 Coder 已写的代码，检查类型注解和方法名遮蔽问题，"
                          "运行已有测试验证通过，发现问题写入 shared/issues.md"),
                 "context": {"agent_id": "reviewer_001",
                             "_max_tool_calls_override": complexity_overrides.get("reviewer")},
                 "current_depth": 1},
            ]
            phase_b_results = await self.spawn_parallel(phase_b_tasks)
            all_results.extend(phase_b_results)

            # Phase C: 总体集成测试（串行）
            phase_c_result = await self.spawn(
                agent_type="reviewer",
                task=("执行总体集成测试：1) 用 glob 找到项目目录 "
                      "2) 运行 pytest <目录>/tests/ -v "
                      "3) 如果测试失败，分析失败原因并记录到 shared/issues.md "
                      "4) 检查所有模块导入和接口一致性"),
                context={"agent_id": "reviewer_final",
                         "_max_tool_calls_override": complexity_overrides.get("reviewer")},
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

        # Phase C+: 对抗测试（红队审查）——故意找 bug
        adversarial_result = await self.spawn(
            agent_type="reviewer",
            task=(
                "你是红队审查员，目标是用一切手段找到代码中的 bug：\n"
                "1) 运行 pytest 看是否有测试失败\n"
                "2) 检查代码中是否有类型注解 bug（方法名遮蔽内置类型如 list/dict/set/input）\n"
                "3) 检查是否有缺少 from __future__ import annotations 的文件\n"
                "4) 检查测试 fixture 是否正确（Path vs str 问题）\n"
                "5) 检查是否有边界条件未处理（空输入、None、越界）\n"
                "6) 检查是否有安全隐患（路径穿越、命令注入）\n"
                "7) 找到的所有问题记录到 shared/issues.md，标明严重级别"
            ),
            context={"agent_id": "reviewer_adversarial",
                     "_max_tool_calls_override": complexity_overrides.get("reviewer")},
            current_depth=1,
        )
        all_results.append(adversarial_result)

        # Phase Fix+Verify: 修复对抗发现的问题并验证（最多 2 轮）
        fix_rounds = 0
        max_fix_rounds = 2
        while fix_rounds < max_fix_rounds:
            # 检查 shared/issues.md 是否有 CRITICAL/HIGH 问题
            has_critical_issues = await self._check_critical_issues()
            if not has_critical_issues:
                logger.info("No critical issues found, skipping fix round")
                break

            fix_rounds += 1
            logger.info("Fix round %d/%d: addressing critical issues", fix_rounds, max_fix_rounds)

            # Phase Fix: Coder 修复问题
            fix_result = await self.spawn(
                agent_type="coder",
                task=(
                    "读取 shared/issues.md，修复其中标记为 CRITICAL 和 HIGH 的所有问题。\n"
                    "修复后运行 pytest 确认没有引入新问题。\n"
                    "修复完成后清空 shared/issues.md 中的已修复条目。"
                ),
                context={"agent_id": f"coder_fix_{fix_rounds:03d}",
                             "_max_tool_calls_override": complexity_overrides.get("coder")},
                current_depth=1,
            )
            all_results.append(fix_result)

            # Phase Verify: Reviewer 验证修复
            verify_result = await self.spawn(
                agent_type="reviewer",
                task=(
                    "验证刚才的修复：\n"
                    "1) 运行完整测试套件 pytest <项目>/tests/ -v\n"
                    "2) 确认所有测试通过\n"
                    "3) 检查修复是否引入新问题\n"
                    "4) 如果仍有问题，记录到 shared/issues.md"
                ),
                context={"agent_id": f"reviewer_verify_{fix_rounds:03d}",
                         "_max_tool_calls_override": complexity_overrides.get("reviewer")},
                current_depth=1,
            )
            all_results.append(verify_result)

        # Phase D: Memorizer 最终总结（含技能结晶）
        phase_d_result = await self.spawn(
            agent_type="memorizer",
            task=(
                "总结本次协作经验。除了常规总结外，请结晶可复用技能：\n"
                "对每个可复用技能，输出如下 YAML 块：\n"
                "```skill\n"
                "name: 技能名称（英文，下划线分隔）\n"
                "description: 一句话描述\n"
                "trigger_condition: 触发关键词（空格分隔）\n"
                "steps:\n"
                "  - tool: 工具名\n"
                "    args: {参数}\n"
                "    description: 步骤说明\n"
                "config:\n"
                "  category: 分类\n"
                "  priority: 50\n"
                "  tags: [标签1, 标签2]\n"
                "```\n"
                "只结晶真正可复用的模式，不要为简单任务创建技能。"
            ),
            context={"agent_id": "memorizer_002"},
            current_depth=1,
        )
        all_results.append(phase_d_result)

        # Phase Memory: 统一记忆提取（orchestrate 完成后）
        if memory_extractor is not None:
            try:
                extraction = await memory_extractor.extract_from_orchestration(
                    user_request=user_request,
                    results=all_results,
                    session_id=session_id,
                )
                if extraction.success:
                    logger.info("orchestration memory extracted: digest=%s wiki=%s",
                                extraction.digest_id, extraction.wiki_id)
            except Exception as e:
                logger.warning("orchestration memory extraction failed (non-fatal): %s", e)

        # Phase Skill: 技能结晶持久化
        if skill_crystallizer is not None:
            try:
                memorizer_output = phase_d_result.output if phase_d_result.status == "completed" else ""
                if memorizer_output:
                    crystallized = skill_crystallizer.crystallize(memorizer_output)
                    if crystallized:
                        logger.info("Crystallized %d skills from memorizer output", len(crystallized))
            except Exception as e:
                logger.warning("Skill crystallization failed (non-fatal): %s", e)

        return all_results

    async def _check_critical_issues(self) -> bool:
        """检查 shared/issues.md 是否有未修复的 CRITICAL/HIGH 问题。"""
        if not self._shared_space:
            return False
        try:
            store = self._shared_space._store
            issues_path = store.root_path / "shared" / "issues.md"
            if not issues_path.exists():
                return False
            content = issues_path.read_text(encoding="utf-8")
            # 查找 CRITICAL 或 HIGH 标记
            for line in content.split("\n"):
                line_upper = line.upper()
                if "CRITICAL" in line_upper or "HIGH" in line_upper:
                    # 排除已标记为修复的行
                    if "FIXED" not in line_upper and "RESOLVED" not in line_upper:
                        return True
            return False
        except Exception:
            return False

    def _read_plan_complexity(self) -> dict[str, int]:
        """读取 shared/plan.md 分析任务复杂度，返回动态 max_tool_calls 覆盖。

        如果 plan.md 不存在或无法解析，返回空 dict（使用默认值）。
        """
        if not self._shared_space:
            return {}
        try:
            store = self._shared_space._store
            plan_path = store.root_path / "shared" / "plan.md"
            if not plan_path.exists():
                return {}
            plan_content = plan_path.read_text(encoding="utf-8")
            if not plan_content.strip():
                return {}
            overrides = self._estimate_task_complexity(plan_content)
            logger.info("Plan complexity analysis: %s", overrides)
            return overrides
        except Exception as e:
            logger.debug("Failed to read plan for complexity: %s", e)
            return {}
