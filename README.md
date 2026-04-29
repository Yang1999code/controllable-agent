# Controllable Agent

> AI Agent 框架 —— 20 接口 · 三层架构 · 多 Agent 协作 · 自主学习进化

## 设计理念

现代 AI 编程 Agent（Claude Code、Pi Agent、Hermes）在核心能力上趋同——循环、工具、记忆——但在扩展性、多 Agent 协作、自主学习上走了不同岔路。这个项目融合了 10 个开源 Agent 项目的设计精华，构建一个**结构化可扩展**的参考实现。

**核心信条**：
- **分层单向依赖** —— `ai/` 零依赖 → `agent/` 核心逻辑 → `app/` 具体实现，绝不反向
- **接口先于实现** —— 每个模块先定义 ABC/Protocol，再写具体类。预留接口只保留签名
- **安全网内建而非外挂** —— 工具预算、API 重试、Hook 隔离、token 预算从第一行代码就在
- **进化靠结晶而非训练** —— 成功任务自动提取工具序列 → 质量评分 → 持久化为可复用技能

## 架构

```
用户输入
  │
  ├─ claudemd 层级发现 CLAUDE.md → 注入 system prompt (priority=0)
  ├─ IPromptBuilder 动态组装 → 能力概览 + 记忆摘要 + 检查点 + Nudge
  │
  ├─ Agent 主循环 (双层: followUp + tool_calls)
  │   ├─ IModelProvider.stream() → LLM 推理
  │   ├─ ITool.execute() → 工具并发/串行调度
  │   ├─ IHook 事件链 (18 事件点) → 插件扩展
  │   ├─ IFlowInspector 旁路监控 → 零阻塞
  │   │
  │   ├─ ★ turn_end → 检查点更新 + Nudge + 结晶评估
  │   └─ ★ Agent 委托 → IAgentRuntime.spawn() → 子Agent 隔离执行
  │
  └─ 输出
```

**三层目录**：

```
ai/          ← Message, Tool, Context, AgentEvent (零依赖)
agent/       ← 20 个接口实现 (循环, 工具, 记忆, Hook, 插件, prompt, 运行时...)
app/         ← CLI + 14 内置工具 + 模型适配器 + 配置
```

## 20 接口全景

| 接口 | 职责 | 状态 |
|------|------|------|
| **ITool** | 工具注册 / 并发安全 / 结果预算 | V1 |
| **IModelProvider** | 模型流式推理 (OpenAI 兼容 + Anthropic) | V1 |
| **IMemoryBackend** | L0-L4 分层记忆, 关键词检索, jieba 分词 | V1 |
| **IHook** | 18 事件链, 优先级排序, 异常隔离 | V1 |
| **ISkill** / **ISkillConfig** | 技能注册 / YAML 加载 / 关键词匹配 | V1 |
| **IFlowInspector** | AsyncQueue 旁路, 滑动窗口统计 | V1 |
| **ICapabilityCatalog** / **ICapabilityRegistry** | Tier 渐进式披露, copy-on-read | V2 |
| **IPluginAdapter** | 4 层发现 / yaml manifest / 热重载 | V2 |
| **IPromptBuilder** | 片段式动态组装, token 预算 | V2 |
| **IWebAutomation** | fetch + search + browser (httpx + Playwright) | V2 |
| **IAgentRuntime** ★ | spawn/spawn_parallel, 并发控制, Agent 通信 | V3 |
| **IAutonomousMemory** ★ | 检查点 + 结晶 + Nudge + 长期更新 | V3 |
| **ISelfModification** ★ | quality_score 三维评分 (clarity/completeness/actionability) | V3 |
| **IToolErrorPolicy** | 工具异常策略 | 预留 V2 |
| **IHotLoader** | 运行时热加载 | 预留 V2 |
| **IDiscovery** | 自动发现 | 预留 V2 |
| **IMultiModelOrchestrator** | 多模型协同 | 预留 V3 |
| **IPluginMarketplace** | 插件市场 | 预留 V3 |
| **IMetaAgent** | 元 Agent 自优化 | 预留 V4 |

> ★ = 需求4 核心增量。V1=Phase 1 基座, V2=Phase 2 扩展, V3=Phase 3 多Agent+自进化。

## 快速开始

```bash
# 安装
pip install -e .

# 基础运行 (Phase 1 — 单 Agent 循环 + 6 工具 + 记忆)
python -m app.cli

# Web 工具需要额外安装
pip install playwright && playwright install chromium
```

## 核心特性

### Phase 1 — 基座 (已实现)
- 双层 Agent 循环 (外层 followUp + 内层 tool_calls)
- 6 个文件操作工具 (read/write/edit/bash/glob/grep) + 流式并行执行
- L0-L4 分层记忆 (文件系统后端, jieba 中文分词, bigram 回退)
- 7 基础 Hook 事件 + 异常隔离
- CLAUDE.md 层级发现 (`~/.agent/` → 项目根 → 逐级向上)

### Phase 2 — 扩展 (已实现)
- 能力渐进式披露 (Tier 0 始终可见, Tier 1 按需)
- 动态 Prompt 片段组装 (优先级 0-100, token 预算裁剪)
- 插件系统 (4 层发现: 内置→用户→项目→pip)
- Web 工具 (fetch + search + browser navigate/click/type/snapshot)

### Phase 3 — 多 Agent + 自进化 (已实现)
- **多 Agent 运行时**: spawn 单任务 / spawn_parallel 并行 (Semaphore 并发控制)
- **Agent 自动选择**: Overlap Coefficient 匹配, 中文分词, 阈值 0.3
- **Agent 间通信**: asyncio.Queue 收件箱, send_message/check_inbox
- **工作检查点**: 每轮自动更新, 注入 prompt 防遗忘
- **技能结晶**: 成功任务 → should_crystallize 评估 → quality_score ≥ 60 → 持久化 YAML
- **Nudge 提醒**: 每 10 轮自动提醒使用记忆/技能

### 安全网
- 工具异常不中断循环 (ToolResult(success=False) → LLM 自行处理)
- API 重试 (指数退避, max 3 次)
- Hook 异常隔离 (单 handler 失败不影响其他)
- Prompt token 预算 (低优先级片段优先裁剪)
- 工具结果预算 (50K 字符截断, 超限写磁盘)
- 子 Agent 超时 + 深度限制 (max_depth=2)

## 设计参考

本项目设计基于对 10 个开源 AI Agent 项目的横向对比分析：

| 项目 | 核心借鉴 |
|------|---------|
| **Claude Code** | 双层循环 (query.ts), 59 工具, CLAUDE.md 层级加载 |
| **Pi Agent** | 类型系统 (AgentTool<T>), 子Agent 进程隔离 |
| **Hermes Agent** | 线程池委托, 8 种记忆后端, 30+ 消息平台 |
| **GenericAgent** | L0-L4 记忆公理, 技能结晶, 极简循环 (~100 行) |
| **multica** | Go 信号量并发控制, daemon 轮询 |
| **oh-my-opencode** | 31+ hooks, 动态 prompt 构建, Agent 角色系统 |
| **everything-claude-code** | 48 Agent + 183 Skill 生态级设计 |
| **Superpowers** | 技能即行为代码, 反合理化工程 |
| **andrej-karpathy-skills** | SKILL.md 方法论, 4 编码原则 |
| **KiloCode / OpenCode** | 跨 IDE 协议, 18 语言 i18n |

详细技术规范: [需求4技术2.md](../需求文档/需求4技术2.md)  
完整需求定义: [需求4.md](../需求文档/需求4.md)

## License

MIT
