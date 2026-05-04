# Controllable Agent

> AI Agent 框架 -- 20 接口 · 三层架构 · 多 Agent 协作 · 自主学习进化 · 实时终端可视化

## 设计理念

现代 AI 编程 Agent（Claude Code、Pi Agent、Hermes）在核心能力上趋同——循环、工具、记忆——但在扩展性、多 Agent 协作、自主学习上走了不同岔路。这个项目融合了 10 个开源 Agent 项目的设计精华，构建一个**结构化可扩展**的参考实现。

**核心信条**：
- **分层单向依赖** -- `ai/` 零依赖 -> `agent/` 核心逻辑 -> `app/` 具体实现，绝不反向
- **接口先于实现** -- 每个模块先定义 ABC/Protocol，再写具体类。预留接口只保留签名
- **安全网内建而非外挂** -- 工具预算、API 重试、Hook 隔离、token 预算从第一行代码就在
- **进化靠结晶而非训练** -- 成功任务自动提取工具序列 -> 质量评分 -> 持久化为可复用技能

## 架构

```
用户输入
  |
  +-- claudemd 层级发现 CLAUDE.md -> 注入 system prompt (priority=0)
  +-- IPromptBuilder 动态组装 -> 能力概览 + 记忆摘要 + 检查点 + Nudge
  |
  +-- Agent 主循环 (双层: followUp + tool_calls)
  |   +-- IModelProvider.stream() -> LLM 推理
  |   +-- ITool.execute() -> 工具并发/串行调度
  |   +-- IHook 事件链 (22 事件点) -> 插件扩展 + 实时 UI 渲染
  |   +-- IFlowInspector 旁路监控 -> 零阻塞
  |   |
  |   +-- * 实时 Hook 事件: STREAM_TEXT / STREAM_THINKING / TOOL_PROGRESS
  |   +-- * turn_end -> 检查点更新 + Nudge + 结晶评估
  |   +-- * Agent 委托 -> IAgentRuntime.spawn() -> 子Agent 隔离执行
  |
  +-- 终端 TUI (流式渲染 + 多 Agent 面板)
  |
  +-- 输出
```

**三层目录**：

```
ai/          <- Message, Tool, Context, AgentEvent (零依赖)
agent/       <- 20 个接口实现 (循环, 工具, 记忆, Hook, 插件, prompt, 运行时...)
app/         <- CLI + 15 内置工具 + 模型适配器 + 配置 + TUI
```

## 快速开始

```bash
# 克隆
git clone https://github.com/Yang1999code/controllable-agent.git
cd controllable-agent

# 安装
pip install -e .

# 配置 (复制模板，填入 API Key)
cp app/config/agent.yaml.example app/config/agent.yaml
# 编辑 app/config/agent.yaml，填入你的 API Key 或设置环境变量 DEEPSEEK_API_KEY

# 运行
python -m app.cli

# 单次执行
python -m app.cli --one-shot "帮我写一个快速排序"

# 指定模型
python -m app.cli --model gpt-4o --provider openai_compat
```

## 终端界面

实时终端 UI，对标 Claude Code / OpenCode 体验：

| 功能 | 说明 |
|------|------|
| **流式输出** | 逐字显示 LLM 响应，不再黑屏等待 |
| **思考状态** | `... 思考中 ...` 实时指示当前阶段 |
| **工具调用** | 彩色图标徽章 (R/W/E/$/G/S/H/Q/D/M) + 折叠预览 |
| **多 Agent 面板** | 实时显示子 Agent 运行状态 |
| **上下文监控** | 状态栏显示模型名、turn 数、token 用量、上下文占用% |
| **压缩通知** | 上下文压缩时提示用户 |
| **斜杠命令** | /help /tools /tokens /status /flowchart /clear /exit |

详见 [可视化.md](可视化.md)。

## 实现进度

### Phase 1 -- 基座 (已完成)
- 双层 Agent 循环 (外层 followUp + 内层 tool_calls)
- 6 个文件操作工具 (read/write/edit/bash/glob/grep) + 流式并行执行
- L0-L4 分层记忆 (文件系统后端, jieba 中文分词, bigram 回退)
- 7 基础 Hook 事件 + 异常隔离
- CLAUDE.md 层级发现

### Phase 2 -- 扩展 (已完成)
- 能力渐进式披露 (Tier 0 始终可见, Tier 1 按需)
- 动态 Prompt 片段组装 (优先级 0-100, token 预算裁剪)
- 插件系统 (4 层发现: 内置->用户->项目->pip)
- Web 工具 (fetch + search + browser navigate/click/type/snapshot)
- Digest + Wiki 记忆存储引擎 + 倒排索引检索 (DomainIndex)
- LLM 记忆提取引擎 (自动从对话提取 digest/wiki)
- 三层上下文压缩 (Prune -> Summary -> Emergency Truncation)

### Phase 3 -- 多 Agent + 自进化 (已完成)
- **5 角色协作**: Coordinator / Planner / Coder / Reviewer / Memorizer
- **隔离存储**: 每个 Agent 独立的 MemoryStore + FactStore + DomainIndex
- **共享区**: plan.md / status/ / decisions.md / issues.md / skills/ 结构化通信
- **跨 Agent 读取**: CrossAgentReadTool (路径白名单 + 安全校验)
- **编排引擎**: orchestrate() 分阶段串并行 + 打回机制
- **Agent 自动选择**: Overlap Coefficient 匹配, 中文分词
- **Agent 间通信**: asyncio.Queue 收件箱
- **技能结晶**: 成功任务 -> quality_score 评估 -> 持久化 YAML
- **Nudge 提醒**: 每 10 轮自动提醒使用记忆/技能

### Phase 3.5 -- 终端可视化 (已完成)
- 流式 Hook 事件驱动实时渲染 (STREAM_TEXT / STREAM_THINKING / TOOL_PROGRESS)
- 逐字流式文本显示
- 工具调用彩色图标徽章 + 折叠输出
- 多 Agent 状态面板
- 上下文占用% 实时计算 + 压缩通知

## 20 接口全景

| 接口 | 职责 | 状态 |
|------|------|------|
| **ITool** | 工具注册 / 并发安全 / 结果预算 | V1 |
| **IModelProvider** | 模型流式推理 (OpenAI 兼容 + Anthropic) | V1 |
| **IMemoryBackend** | L0-L4 分层记忆, 关键词检索, jieba 分词 | V1 |
| **IHook** | 22 事件链, 优先级排序, 异常隔离 | V1 |
| **ISkill** / **ISkillConfig** | 技能注册 / YAML 加载 / 关键词匹配 | V1 |
| **IFlowInspector** | AsyncQueue 旁路, 滑动窗口统计 | V1 |
| **ICapabilityCatalog** / **ICapabilityRegistry** | Tier 渐进式披露, copy-on-read | V2 |
| **IPluginAdapter** | 4 层发现 / yaml manifest / 热重载 | V2 |
| **IPromptBuilder** | 片段式动态组装, token 预算 | V2 |
| **IWebAutomation** | fetch + search + browser (httpx + Playwright) | V2 |
| **IAgentRuntime** * | spawn/spawn_parallel, 并发控制, Agent 通信 | V3 |
| **IAutonomousMemory** * | 检查点 + 结晶 + Nudge + 长期更新 | V3 |
| **ISelfModification** * | quality_score 三维评分 | V3 |
| **IToolErrorPolicy** | 工具异常策略 | 预留 V2 |
| **IHotLoader** | 运行时热加载 | 预留 V2 |
| **IDiscovery** | 自动发现 | 预留 V2 |
| **IMultiModelOrchestrator** | 多模型协同 | 预留 V3 |
| **IPluginMarketplace** | 插件市场 | 预留 V3 |
| **IMetaAgent** | 元 Agent 自优化 | 预留 V4 |

> * = 需求4 核心增量。

## 15 内置工具

| 工具 | 说明 | 图标 |
|------|------|------|
| read | 文件读取 | R |
| write | 文件写入 | W |
| edit | 文件编辑（字符串替换） | E |
| bash | Shell 命令执行 | $ |
| glob | 文件名模式搜索 | G |
| grep | 文件内容搜索 | S |
| web_fetch | HTTP 请求 | H |
| web_search | 网页搜索 | Q |
| web_browser_* | 浏览器自动化 (6 个子工具) | - |
| delegate_task | 多 Agent 任务委托 | D |
| agent_message | Agent 间通信 | M |
| cross_agent_read | 跨 Agent 只读访问 | X |
| memory_store | 记忆存储 | - |
| memory_search | 记忆搜索 | - |
| skill_lookup | 技能查找 | - |

## 文档索引

### 设计文档
- [多智能体设计.md](多智能体设计.md) -- Phase 3 多 Agent 系统完整设计方案
- [可视化.md](可视化.md) -- 终端 UI 设计文档（对标 Claude Code / OpenCode）
- [记忆系统设计实现.md](记忆系统设计实现.md) -- Phase 1/2 记忆系统设计
- [架构总结.md](架构总结.md) -- 三层架构详细分析

### 实现记录
- [多智能实现记录.md](多智能实现记录.md) -- Phase 3 实现过程、问题分析与测试结果 (449 tests)
- [记忆系统更新记录.md](记忆系统更新记录.md) -- 记忆系统实现记录
- [修改记录.md](修改记录.md) -- 累计修改日志

## 安全网

- 工具异常不中断循环 (ToolResult(success=False) -> LLM 自行处理)
- API 重试 (指数退避, max 3 次)
- Hook 异常隔离 (单 handler 失败不影响其他)
- Prompt token 预算 (低优先级片段优先裁剪)
- 工具结果预算 (50K 字符截断)
- 子 Agent 超时 + 深度限制 (max_depth=2)
- 跨 Agent 读取路径白名单 + `..` 穿越防护
- API Key 不入库 (agent.yaml 在 .gitignore 中)

## 设计参考

| 项目 | 核心借鉴 |
|------|---------|
| **Claude Code** | 双层循环, 59 工具, CLAUDE.md 层级, 实时终端 UI |
| **Pi Agent** | 类型系统 (AgentTool<T>), 子Agent 进程隔离 |
| **Hermes Agent** | 线程池委托, 8 种记忆后端, 30+ 消息平台 |
| **GenericAgent** | L0-L4 记忆公理, 技能结晶, 极简循环 |
| **multica** | Go 信号量并发控制, daemon 轮询 |
| **oh-my-opencode** | 31+ hooks, 动态 prompt 构建, Agent 角色系统 |
| **everything-claude-code** | 48 Agent + 183 Skill 生态级设计 |
| **OpenCode** | 终端 TUI 设计, 上下文压缩, 会话管理 |

## 测试

```bash
# 全量测试
pytest tests/ -v

# 449 tests, 0 failures
```

## License

MIT
