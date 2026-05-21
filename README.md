# Controllable Agent

> 一个能自己组织团队、自己记忆、自己进化的 AI Agent 框架。
> 5 个 Agent 角色分工协作 + Wiki 式记忆系统 + 实时终端可视化。

---

## 它能做什么？

想象你给 AI 一个复杂任务，比如"帮我从零搭建一个认证系统"。

普通 Agent 会怎么做？一个模型从头干到尾，边写边忘，写到后面忘了前面。

**Controllable Agent 会这么做**：

```
你："帮我实现用户认证模块"

1. Planner 先分析需求，拆成 3 个子任务，写好计划
2. 3 组 Coder + Reviewer 配对并行开工
   - Coder 写 JWT → Reviewer 验证 → 通过
   - Coder 写路由 → Reviewer 验证 → 有 bug → 修复 → 再验 → 通过
   - Coder 写中间件 → Reviewer 验证 → 通过
3. Coordinator 全程监控，谁卡了就拉一把
4. 总体集成测试 → 通过
5. Memorizer 提炼经验，下次更快

全程你可以随时插话，Coordinator 会接住你的补充信息。
```

**不需要你指挥，5 个 Agent 自己协商、并行、互相审查。**

---

## 两大创新

### 创新 1：5 角色分工的多 Agent 协作

不是把任务丢给一个 Agent，而是像软件团队一样分工：

| 角色 | 干什么 | 一句话 |
|------|--------|--------|
| **Coordinator** | 管理者 | 不干活，专门管人。谁能创建、谁越权了、谁卡住了 |
| **Planner** | 设计师 | 写计划但不走人，全程动态调整，永远记住用户原话 |
| **Coder** | 开发 | 写代码、跑测试，按计划执行 |
| **Reviewer** | 审查 | 和 Coder 配对，写完一个模块立马验，边写边审 |
| **Memorizer** | 记录员 | 记事 + 从经验中提炼可复用技能 |

关键设计：

- **Coder + Reviewer 配对**：不是写完再审查，而是写一个小模块就验一个，快速迭代。多组配对可以并行跑。
- **两层打回**：模块级打回不限次数（配对内自己解决），总体集成测试最多 3 轮。超了就升级给你处理。
- **用户随时插话**：你中途补充的信息由 Coordinator 接收，按紧急度分级处理，不中断主流程。
- **嵌套深度控制**：最多 2 层（主 Agent → Coordinator → 工作 Agent），防止套娃。
- **无硬编码上限**：任务拆得越细，配对越多，并行度越高。用 Semaphore 控制并发。

详见 [多智能体设计.md](多智能体设计.md)。

### 创新 2：记忆提取 + 技能结晶

Agent 不会"聊完就忘"，也不会每次都从零开始——它像写维基百科一样自动整理记忆，像师傅教徒弟一样把成功经验固化成可复用技能。

---

#### 记忆提取：Agent 怎么记住东西

**普通聊天记录的毛病**：是流水账——"用户说了A，AI回了B，用户又问C..."。没法按关键词搜，更没法提炼成知识。聊 10 轮后第 11 轮已经忘了第 1 轮。

**你的 Agent 怎么做**：每次任务完成后，后台自动跑一次记忆提取。

```
你在 Web 问："帮我写个回文判断函数"
    ↓
模型输出代码（流式打字机效果）
    ↓
输出结束 → Memorizer 启动（右侧粉色头像亮起）
    ↓
1. 打包对话（你说了啥 + Agent 回了啥）
2. 调一次 LLM（轻量调用，~200 token）
3. LLM 提取成结构化摘要 → 写入 digest 文件
4. 更新关键词倒排索引（下次搜"回文"能找到）
5. 同一主题攒够 5 条 → 自动合并成 wiki 知识页
```

生成的文件 `d_006.md` 长这样：

```yaml
---
confidence: 0.9
domains:
- conversation
facts:
- 用户要求编写判断回文字符串的Python函数
- 助手已接受任务并准备提供代码
id: d_006
level: digest
tags: [Python, 回文字符串, 函数]
task_summary: 用户请求编写判断回文字符串的Python函数
---

## 任务摘要
用户请求编写回文判断函数，助手提供了实现。
```

**digest → wiki 两层合并**：

```
d_001.md ─┐
d_003.md ─┤  同主题攒够 5 条
d_007.md ─┼──────────────────→ wiki/python_stack.md（完整知识页）
d_011.md ─┤
d_015.md ─┘
```

合并时自动去重、按时间线组织、冲突信息以最新为准。

**四域分类**：Agent 的记忆按用途分四个文件夹——
- `conversation`：对话内容（"上次聊过 Python"）
- `profile`：用户档案（"这个用户偏好中文回答"）
- `agent_view`：Agent 自身视角（"我上次用 grep 搜代码比较快"）
- `task`：任务记录（"完成了回文函数的开发"）

**中文分词检索**：用 jieba 分词（"回文判断函数"拆成"回文"/"判断"/"函数"三个词建索引），没装 jieba 自动降级为每两个字一组。

**存储位置**（人可直接打开看）：
```
~/.agent-memory/
├── digest/           # 每次任务的摘要
│   ├── d_001.md
│   └── ...
├── wiki/             # 合并后的知识页
├── index.md          # 倒排索引（关键词 → 文件路径）
└── domain/           # 四域分类
```

---

#### 技能结晶：Agent 怎么"学会"怎么做

**记忆和技能的区别**：
- 记忆记的是**"发生过什么"** → "上次写了个回文函数，用了双指针法"
- 技能记的是**"该怎么做"** → "写回文函数的步骤：1) 读需求 2) 写函数 3) 写测试 4) 跑测试"

**工作流程**：

```
Agent 回复完你（比如写完一段代码）
    ↓
SkillCrystallizer 扫描回复文本
    ↓
找到 ```skill 代码块？
    ↓ 有
验证三步：
  → 必要字段检查：name（技能名）/ trigger_condition（触发条件）/ steps（步骤）
  → 步骤可执行性：每一步都要指定 tool（用什么工具）
  → 三维质量评分：

    清晰度 (100分)  +  完整性 (100分)  +  可操作性 (100分)
    描述够不够清楚      步骤数够不够         每步有没有指定工具

    总分 ≥ 60 → ✅ 持久化 YAML
    总分 < 60 → ❌ 丢弃
    ↓
写入 .agent-base/skills/回文函数.yaml
    ↓
下次你问类似问题 → 自动匹配 → 直接复用这个技能
```

**动态淘汰机制**：技能不是存了就永远在——

```
用成功了 → +10 分
用失败了 → -20 分
用了很多次 → 分数波动越来越小（越用越稳定）
质量 < 30 且用过 ≥3 次 → 自动删除
```

这保证了**只有真正好用的技能才会长期保留**。

**Web 端实时可见**：你在右侧 Memorizer 卡片能看到每一阶段：

| 阶段 | 显示 |
|------|------|
| 正在提取记忆 | `🩷 正在提取记忆...` |
| 记忆存储完成 | `✅ 提取记忆 #d_006` |
| 正在提取技能 | `🩷 正在提取技能...` |
| 结晶完成 | `✅ 结晶 2 个技能: 回文函数, 字符串测试` |

---

详见 [我的记忆改进.md](我的记忆改进.md)。

---

## 核心组件介绍

你的 Agent 框架由 **13 个核心组件** 协同工作。下面用大白话一个一个说清楚：

### 1. Provider — "大脑"

Agent 的"脑子"，负责调用 AI 模型。你用的是 DeepSeek，通过 OpenAI 兼容 API 连接。换模型就跟换电池一样——改个配置就行，不用动代码。

### 2. ToolRegistry — "工具箱"

Agent 能用的所有工具的登记册。不管是内置的（读文件、跑命令）、MCP 外部工具、还是插件工具，统统注册到这里。Agent 做决策时从这里挑工具。

### 3. AgentLoop — "心脏"

Agent 的运转核心。它是一个双层循环：外层不断追问"还需要做什么？"，内层在每轮里反复调用工具直到任务完成。就像人的"思考→行动→检查→再思考"循环。

### 4. Context — "短期记忆"

保存对话历史和系统提示。Agent 说的每一句话、用户问的每一个问题都在里面，相当于 Agent 的"短期记忆"。

### 5. PromptBuilder — "开场白设计师"

动态组装发给 AI 的系统提示。告诉 Agent 它是什么、能干什么、当前在哪个目录、是什么操作系统。不像普通聊天机器人只有一个固定的"你是某某助手"——这个会根据环境信息动态生成。

### 6. CapabilityRegistry — "能力标签系统"

给工具分 Tier（等级）打标签。Tier 0 是基础能力（文件读写、Shell），Tier 1 是高级能力（网页抓取、多 Agent 协作）。有了这个，系统提示不会一股脑把所有工具列出来——先说常用的，需要时再展示高级的。这叫"渐进式披露"。

### 7. FlowInspector — "行车记录仪"

挂在 Agent 循环旁边的监控器。记录每一步的耗时、Token 消耗、工具调用结果，出问题了能回溯。不影响 Agent 干活，纯旁路记录。

### 8. MCP Client — "外设接口"

MCP（Model Context Protocol）让你给 Agent 插"外设"。Agent 原生只会读文件、跑命令，通过 MCP 可以连上 GitHub 服务器让它管理代码、连上数据库让它查数据。你的框架里 MCP 做了两件事：

- **工具发现**：连上 MCP Server 后自动问"你有什么工具？"，然后注册到工具箱
- **调用适配**：把外部工具的 JSON Schema 参数格式转成 Agent 能理解的格式

支持两种连接方式：本地子进程（stdio）和远程 HTTP（SSE）。参考了 **Anthropic 官方 MCP 规范** 和 **Claude Code 的 MCP 集成方式**。

### 9. WebAutomation — "浏览器手"

让 Agent 能操作浏览器——打开网页、点按钮、输入文字、截图。Agent 不再是纯文本交互，它能真的"看到"和"操作"网页。

### 10. PluginAdapter — "插件商店"

4 层插件发现系统。插件可以来自内置目录、用户目录、项目目录、环境变量指定路径。插件可以提供额外工具、钩子、技能——相当于给 Agent 装"App"。

### 11. MemoryExtractor — "学习笔记"

自动从每次对话中提取有价值的经验，存成 digest（摘要卡片）。多个相关 digest 会合并成 wiki（知识条目）。分了四个领域：对话经验、用户画像、Agent 行为偏好、任务模式。下次 Agent 遇到类似问题时能检索到之前的经验。

### 12. SkillCrystallizer — "技能提炼器"

从成功的任务输出中自动提取可复用的"技能模板"（YAML 格式），存成技能文件。每个技能有三维质量评分（清晰度、完整度、可操作性），低于 30 分的自动淘汰。好的技能会被保留，Agent 下次遇到类似任务可以直接调用。

### 13. HookChain — "拦截器链"

在工具执行前后插入自定义逻辑。比如：执行 Shell 命令前检查是不是危险操作、写完文件后自动格式化代码。你可以往链上加任意多个钩子。

---

**一句话总结**：Provider 是大脑，AgentLoop 是心脏，ToolRegistry 是工具箱，Context 是短期记忆，MCP 是外设接口，CapabilityRegistry 是标签系统，PromptBuilder 是开场白，FlowInspector 是行车记录仪，MemoryExtractor 是学习笔记，SkillCrystallizer 是技能提炼器，PluginAdapter 是插件商店，WebAutomation 是浏览器手，HookChain 是拦截器。十三个组件各司其职，拼出了一个完整的多智能体框架。

---

## 技术架构

三层单向依赖，绝不反向：

```
ai/          ← 核心类型（Message, Tool, Context），零依赖
agent/       ← 20 个接口实现（循环, 工具, 记忆, Hook, 运行时...）
app/         ← CLI + 15 内置工具 + 模型适配器 + 配置 + TUI
```

**核心信条**：
- **分层单向依赖** -- `ai/` 零依赖 -> `agent/` 核心逻辑 -> `app/` 具体实现，绝不反向
- **接口先于实现** -- 每个模块先定义 ABC/Protocol，再写具体类
- **安全网内建而非外挂** -- 工具预算、API 重试、Hook 隔离、token 预算从第一行代码就在
- **进化靠结晶而非训练** -- 成功任务自动提取工具序列 -> 质量评分 -> 持久化为可复用技能

### 20 接口全景

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
| **IAgentRuntime** | spawn/spawn_parallel, 并发控制, Agent 通信 | V3 |
| **IAutonomousMemory** | 检查点 + 结晶 + Nudge + 长期更新 | V3 |
| **ISelfModification** | quality_score 三维评分 | V3 |
| **IToolErrorPolicy** | 工具异常策略 | 预留 V2 |
| **IHotLoader** | 运行时热加载 | 预留 V2 |
| **IDiscovery** | 自动发现 | 预留 V2 |
| **IMultiModelOrchestrator** | 多模型协同 | 预留 V3 |
| **IPluginMarketplace** | 插件市场 | 预留 V3 |
| **IMetaAgent** | 元 Agent 自优化 | 预留 V4 |

### 15 内置工具

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

**支持任意 OpenAI 兼容模型**：DeepSeek / 通义千问 / 智谱 / OpenAI / Anthropic 都行。

---

## 快速开始

### 1. 下载安装

```bash
git clone https://github.com/Yang1999code/controllable-agent.git
cd controllable-agent
pip install -e .
```

> 需要 Python 3.12+，建议使用 conda 或 venv 隔离环境。

### 2. 配置 API Key

```bash
# 复制配置模板
cp app/config/agent.yaml.example app/config/agent.yaml
```

编辑 `app/config/agent.yaml`，填入你的 API Key：

```yaml
providers:
  default: openai_compat
  openai_compat:
    base_url: https://api.deepseek.com/v1    # 或其他 OpenAI 兼容 API
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY             # 从环境变量读取
    # api_key: sk-xxx                         # 或直接填（不要提交到 git）
```

设置环境变量：

```bash
# Linux / macOS
export DEEPSEEK_API_KEY=sk-your-key-here

# Windows PowerShell
$env:DEEPSEEK_API_KEY = "sk-your-key-here"

# Windows CMD
set DEEPSEEK_API_KEY=sk-your-key-here
```

> 支持 **DeepSeek / 通义千问 / 智谱 / OpenAI / Anthropic** 等任何 OpenAI 兼容模型。

### 3. 启动

```bash
python -m app.cli
```

你会看到欢迎界面：

```
+==================================================+
|  my-agent v0.1.0                                 |
|  Empire Code -- 可控多智能体自迭代 Agent 框架     |
+==================================================+

  底层模型: deepseek-chat
  可用工具: 15 个
  /help | /flowchart | /exit | /多智能体
  运行中随时可输入补充信息
```

其他启动方式：

```bash
# 单次执行（非交互）
python -m app.cli --one-shot "你的问题"

# 指定模型
python -m app.cli --model gpt-4o

# 详细日志（调试用）
python -m app.cli -v

# 旧版简单 REPL
python -m app.cli --legacy
```

### Web UI 一键启动（推荐）

双击 `start.bat` 或运行：

```bash
python launch.py
```

浏览器会自动打开 `http://127.0.0.1:8765`，你会看到：

- **左侧聊天区**：输入任务，Agent 实时流式回复
- **右侧协作面板**：
  - 5 智能体协作拓扑图（SVG 动画连线，实时显示谁在工作）
  - 每个 Agent 的像素头像 + 工作状态 + 完成后自动生成的工作摘要
- 全程 SSE 流式推送，不需要刷新页面

```
双击 start.bat
    ↓
浏览器自动打开 http://127.0.0.1:8765
    ↓
输入任务 → 5 个 Agent 自动协作 → 实时动画 + 流式输出
```

### 4. 使用多智能体协作

进入 TUI 后，输入 `/多智能体` 启动多 Agent 模式：

```
> /多智能体

  === 多智能体协作模式 ===

  已注册角色 (5):
    [C] coordinator    协调者 — 多 Agent 调度、流程监控
    [P] planner        规划者 — 任务分解、步骤规划
    [X] coder          编码者 — 代码实现、文件操作
    [R] reviewer       审查者 — 代码审查、测试验证
    [M] memorizer      记忆者 — 经验总结、知识提取

  请描述你要多智能体协作完成的任务：
  任务> 写一个 Python 文本统计工具，统计字符数、词数、行数，并写测试
```

然后 5 个 Agent 自动分工：

```
  [Agents: ~planner -coordinator -coder -reviewer -memorizer]
    > planner started (planner_001)
    OK planner finished (2.3s)
  [Agents: *planner ~coordinator ~coder ~reviewer ~memorizer]
    > coordinator started (coordinator_001)
    > coder started (coder_001)
    ...
```

面板图标含义：`~` 运行中、`*` 已完成、`!` 出错、`-` 等待中。

也可以让模型自动调用——在普通对话中描述复杂任务，模型会自行决定是否使用多 Agent。

### 5. 记忆系统管理

Agent 会自动从对话中提取记忆，存储在 `~/.agent-memory/` 目录下：

```
~/.agent-memory/
├── digest/           # 任务摘要（每次完成后自动提取）
│   ├── d_001.md
│   ├── d_002.md
│   └── ...
├── wiki/             # 知识页面（同主题摘要自动合并）
│   ├── python_stack.md
│   └── ...
├── domain/           # 四域分类索引
│   ├── conversational/
│   ├── personal/
│   ├── agent/
│   └── task/
└── index.md          # 倒排索引（关键词 → 摘要/知识页）
```

**查看记忆内容**：直接用任何编辑器打开 Markdown 文件，人可读。

```
---
id: d_001
level: digest
tags: [python, file-io]
domains: [conversational]
confidence: 0.85
---

## 实现了文本统计工具

- 使用 collections.Counter 统计字符频率
- 支持自定义编码检测
- 写了 10 个 unittest 用例全部通过
```

**搜索记忆**：在对话中提问，Agent 会自动检索相关记忆。

**记忆生命周期**：

```
对话完成 → 自动提取 digest（任务摘要）
    ↓ 积累 5+ 个同主题 digest
自动合并为 wiki（知识页面，更完整）
    ↓ 下次遇到类似问题
优先查 wiki → 查不到再查 digest → 都没有就不编造
```

### 6. 常用命令速查

| 命令 | 说明 |
|------|------|
| `/help` | 显示所有命令 |
| `/多智能体` | 启动多 Agent 协作模式 |
| `/tools` | 列出所有工具 |
| `/tokens` | 查看 Token 使用统计 |
| `/status` | 查看 Agent 运行状态 |
| `/model` | 显示当前模型 |
| `/flowchart` | 查看控制流程图 |
| `/clear` | 清屏 |
| `/exit` | 退出 |

运行中随时可以直接打字输入补充信息，Agent 下一轮会看到。

---

## 终端体验

实时终端 UI，对标 Claude Code / OpenCode：

| 功能 | 说明 |
|------|------|
| 流式输出 | 逐字显示，不再黑屏等半天 |
| 思考状态 | `... 思考中 ...` 实时指示 |
| 工具调用 | 彩色图标徽章 + 折叠预览，不再几十行乱码 |
| 多 Agent 面板 | 实时显示子 Agent 运行状态 |
| 上下文监控 | 状态栏显示模型名、turn 数、token、上下文占用% |
| 斜杠命令 | /help /tools /tokens /status /clear /exit |

---

## 设计参考

融合了 10 个开源 AI Agent 项目的设计精华：

| 项目 | 借鉴了什么 |
|------|-----------|
| Claude Code | 双层循环、CLAUDE.md 层级、实时终端 UI |
| Pi Agent | 子 Agent 进程隔离、类型系统 |
| Hermes Agent | 线程池委托、多记忆后端 |
| GenericAgent | L0-L4 记忆公理、技能结晶 |
| oh-my-opencode | Hook 事件系统、Agent 角色系统 |
| OpenCode | 终端 TUI、上下文压缩 |
| everything-claude-code | 48 Agent + 183 Skill 生态设计 |
| multica | 信号量并发控制 |
| Synthius-Mem 论文 | 记忆提取 pipeline |
| Karpathy Wiki 思想 | Wiki 式知识组织 |

---

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
- 5 角色协作 (Coordinator / Planner / Coder / Reviewer / Memorizer)
- 隔离存储 (每个 Agent 独立的 MemoryStore + FactStore + DomainIndex)
- 共享区 (plan.md / status/ / decisions.md / issues.md / skills/)
- 跨 Agent 读取 (CrossAgentReadTool + 路径白名单 + 安全校验)
- 编排引擎 (orchestrate() 分阶段串并行 + 打回机制)
- Agent 自动选择 (Overlap Coefficient 匹配 + 中文分词)
- Agent 间通信 (asyncio.Queue 收件箱)
- 技能结晶 (成功任务 -> quality_score 评估 -> 持久化 YAML)
- Nudge 提醒 (每 10 轮自动提醒使用记忆/技能)

### Phase 3.5 -- 终端可视化 (已完成)
- 流式 Hook 事件驱动实时渲染
- 逐字流式文本显示
- 工具调用彩色图标徽章 + 折叠输出
- 多 Agent 状态面板
- 上下文占用% 实时计算 + 压缩通知

---

## 安全网

- 工具异常不中断循环 (ToolResult(success=False) -> LLM 自行处理)
- API 重试 (指数退避, max 3 次)
- Hook 异常隔离 (单 handler 失败不影响其他)
- Prompt token 预算 (低优先级片段优先裁剪)
- 工具结果预算 (50K 字符截断)
- 子 Agent 超时 + 深度限制 (max_depth=2)
- 跨 Agent 读取路径白名单 + `..` 穿越防护
- API Key 不入库 (agent.yaml 在 .gitignore 中)

---

## 测试

```bash
pytest tests/ -v
# 449 tests, 0 failures
```

---

## 文档

| 文档 | 内容 |
|------|------|
| [多智能体设计.md](多智能体设计.md) | Phase 3 多 Agent 系统完整设计 |
| [我的记忆改进.md](我的记忆改进.md) | Wiki 式记忆系统设计 |
| [多智能实现记录.md](多智能实现记录.md) | Phase 3 实现过程 (449 tests) |
| [可视化.md](可视化.md) | 终端 UI 设计文档 |
| [架构总结.md](架构总结.md) | 三层架构分析 |

---

## License

MIT
