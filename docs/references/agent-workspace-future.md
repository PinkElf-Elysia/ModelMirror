# Agent Workspace 后续参考边界

本文件只记录未来适配方向，不代表当前存在对应 API、页面或运行能力。不得用
空壳页面、静态成功响应或提示词声明替代真实实现。

## Round 2：执行面（已交付）

运行时已在独立 `/api/agent-workspace` 命名空间内实现 SQLite Session、Task、
Message、审批和递增事件序号，没有复用或改造 `/api/chat` 的 JSON 工具决策链。
九个工具已经过 Workspace 路径限制、进程归属、审批策略与低权限执行护栏。

模型和凭据继续使用现有 newAPI / OpenRouter 配置；没有新建模型库、价格中心
或密钥保险柜。模型不支持原生 Tool Calling 时返回明确能力错误，不模拟成功。

## Round 3：恢复与硬化

长任务插话、排队、`/compact`、`/goal`、上传、快照导入导出和运行中任务重启
标记在安全约束完成后实现。Round 2 已实现持久化 Transcript、Workspace、审批
与 SSE 事件补发，但不会在进程重启后声称继续执行原命令或模型任务。同容器低
权限执行只能作为 v1 护栏，不宣称是恶意多租户级沙箱。

## 从 Agent State 生成器到 Agent 应用开发工作台

Round 2 的“一句话创建 Agent”只负责生成并原子提升 Agent State：
`system_config.yaml`、`AGENTS.md`、Skill 快照和 manifest。它不是应用开发任务，
不能用更长的 AGENTS.md 冒充小游戏、RAG、网站或服务已经开发完成。

PenguinHarness 实际运行实例体现的价值是持续执行闭环，而不是提示词篇幅：

```text
目标持续推进
→ 文件与依赖操作
→ 长命令与后台服务
→ 真实运行和预览
→ 自动测试
→ 失败诊断、修改与复测
→ 有证据的完成和可访问交付物
```

后续必须把任务语义拆开，避免继续错误优化“一句话生成 Agent”：

| Task kind | 目标 | 完成标志 |
|---|---|---|
| `chat` | 普通对话或一次性执行 | 最终文本或明确失败 |
| `agent_generate` | 创建新的 Agent State | 后端发出 `agent_generated` |
| `app_build` | 开发、运行、验证并交付应用 | 应用 manifest、产物和验收证据同时通过 |

执行方式与 Task kind 正交：`single_task` 只用于普通短任务，`goal` 用于持续推进、
预算和终态管理。`app_build` 必须由 GoalLoop 驱动，不能直接沿用“模型停止 Tool
Call 即完成”的普通循环。

`app_build` 默认由 General Agent 执行，但产物属于 Session/App Workspace，不创建
新的 Agent State。Agent 模板和应用项目可以关联，却不能共用 promotion 协议。

## Round 4：应用项目契约、产物与安全预览

目标：让工作区第一次具备“交付一个可打开的应用”的产品语义。

小批次：

1. 新增由 GoalLoop 驱动的 `app_build` Task kind、独立 staging、失败状态和原子
   提升流程，不改造 `agent_generate`。
2. 增加 `AppBuildPreflight`：在写应用前验证 Skillset/Agent 快照兼容性、所需
   `SKILL.md` 已物化且被读取、条件依赖探测通过，并记录 Skill 内容摘要和版本。
3. 增加严格 `APP.yaml` Schema，至少包含 `app_id`、应用类型、入口、构建/测试/
   启动命令、端口、健康检查、产物和环境需求；未知字段 fail-closed。Manifest 是
   不可信输入，Schema 通过不等于命令获准；所有命令仍受审批、低权限、网络、资源、
   Workspace 和端口租约约束。
4. 固定 App/Session/版本实体关系与目录生命周期：失败 staging 可复盘但不可运行，
   正式服务只读取已提升版本，删除 Session 不隐式删除 App 或历史版本。
5. 增加 Artifact 索引、内容摘要、哈希、下载和应用快照；最终回答中的文件必须
   能映射到真实 Artifact。
6. 增加 HTML、Markdown、图片、PDF 和源码预览；HTML 使用短期签名 Token、独立
   Origin、`no-store`、`nosniff` 和受限 Referrer，不能读取 ModelMirror Cookie/API。
7. 增加相对 CSS/JS/图片资源、新标签打开、刷新、窄屏文件面板和恶意预览测试。

人工验收：一句话构建一个单文件小游戏；不创建新 Agent；侧栏和新标签均可实际
游玩；刷新和容器重启后文件仍在；可下载快照；恶意 HTML 无法越权访问主应用。

## Round 5：Goal、计划与证据门禁

目标：模型停止调用工具不再等价于“目标完成”。

小批次：

1. 原生实现 `GOAL.yaml`、`PLAN.md`、轮次/Token/时间预算、人工停止和明确终态。
2. 完整实现 `/goal`、运行中插话、排队和 `/compact`，压缩后仍保留目标、验收项、
   当前状态、失败证据和下一步。
3. 增加严格 `AcceptanceContract`：Harness/用户拥有并冻结 `required_checks`；Agent
   只能建议或追加检查，不能删除、降级或改写必需检查。契约变更必须有用户审批或
   后端策略事件，完成判定只读取后端冻结版本。
4. 增加服务端 `EvidenceLedger`：SQLite/专属目录不向九工具开放写入，只有 Harness
   Runner 能记录 exit code、输出归档、截图、健康结果、产物哈希和时间；Workspace
   报告只是只读投影。通过 Workspace tree hash 和受影响文件哈希使旧证据失效，
   模型文本或脚本输出不能直接把检查标为 passed。
5. 实现有上限的测试—修复—复测循环；检查未通过时只能继续、停止、`blocked`、
   失败或 `budget_limited`，不能进入 `completed`。
6. 对齐 ContextEngine 边界：同轮多 Tool Call 保序回填、流中断重连、工具中断
   carry-over、已完成调用不重复执行、审批和结果 exactly-once，且 compaction 只
   发生在完整工具调用边界。

人工验收：给定含已知缺陷的小游戏，首轮测试失败后自动定位、修复并复测；任一必需
检查失败时不得宣称完成；预算耗尽返回明确终态；损坏 Goal/Plan 文件时 fail-closed。

## Round 6：生产级命令、依赖与服务运行时

目标：支撑 npm/pip 安装、模型下载、索引构建和 Web 服务等长任务。

小批次：

1. 对齐 PenguinHarness Command Session 语义：进程组、Ctrl-C、会话归属、容量、
   TTL/LRU、尾部输出、轮询和只终止本 Session 进程。
2. 对超过前端上限的工具输出建立完整归档和受控读取路径，而不是只保留截断文本。
3. 探测 Node/npm/Python/pip/git/Playwright/浏览器等工具链，向模型注入真实能力清单；
   未安装能力不得由提示词声称可用。
4. 建立依赖供应链记录与策略：Git 固定 Commit、registry/下载来源授权、lockfile、
   包版本和摘要进入交付报告；安装脚本仍需审批，缓存不得泄漏私有凭据。
5. 建立低权限 npm/pip 缓存、CPU/内存/进程/磁盘/超时边界和显式网络策略。
6. 增加端口租约、服务 Supervisor、健康探针、安全反向代理、启动/停止/重启与状态
   恢复；服务不得占用共享栈端口。Workspace 显示服务、租约端口、健康、日志和
   “打开服务”，仅代理本 App/Session 端口；停止后签名链接立即失效。
7. 重启后无法恢复的进程准确标记为 `interrupted`，不伪造后台仍在运行。

人工验收：长安装和索引任务可后台轮询；截断输出可从归档查看；Ctrl-C 仅终止本
Session 进程组；FastAPI 应用取得可访问 URL 并可健康检查、停止和重启；应用进程
不能访问 Docker Socket，也不继承 `LLM_GATEWAY_KEY` 或其他服务端秘密。

## Round 7：浏览器自测、修复闭环与可核验交付

目标：完整复现用户给出的企鹅小游戏开发、自测、修复和预览体验。

小批次：

1. 在 Harness 内加入受控 Playwright 验证运行器，不把浏览器自动化伪装成第十个
   任意模型工具；场景由冻结的 `AcceptanceContract` 约束。
2. 建立 finalize 闭环：Agent 提交候选完成后 Harness 自动执行冻结契约；失败证据
   注入同一 Goal，Agent 修复后自动复测，只有全部必需检查通过才允许完成。
3. 收集断言、浏览器 console、page error、网络失败、DOM 快照和截图并由 Runner
   写入服务端证据账本。
4. 源文件变更后使旧浏览器证据失效，并强制对受影响场景复测。
5. 生成后端可信交付报告：启动方式、功能清单、测试结果、截图、产物、已知风险和
   回退方式；模型文本不能覆盖报告状态。
6. 增加应用版本快照、差异和回滚；回滚不删除历史 Session 或证据。

人工验收：构建并真实验证跳跃、难度递增、碰撞、计分和重开；出现 JS Error 或场景
失败时不得完成；最终页面可在 Workspace 直接试玩；每条“通过”均可展开原始证据。

## Round 8：RAG 应用配方与长任务交付

目标：完整复现用户给出的文档采集、索引、服务、双语检索、引用和 E2E 闭环。

小批次：

1. 增加 Git/HTTP 语料获取的显式网络授权、来源记录、内容哈希和更新策略。
2. 增加 RAG Recipe：采集、清洗、分块、嵌入、索引、引用映射和可重复构建参数。
3. 为下载、清洗、分块、嵌入和索引建立可校验 checkpoint；重启后复用完整阶段，
   增量恢复可续跑部分，不可续跑阶段明确从哪个 checkpoint 重来。
4. v1 固定使用短期、作用域受限、可撤销的 Gateway Broker；应用和子进程不接触
   ModelMirror 原始 Key，持久 Credential/Vault 仍单独延后。
5. 增加 FastAPI/SSE 服务模板、引用 UI、健康检查和服务生命周期。
6. 增加中英文查询、检索命中、流式增量和浏览器 E2E 契约；引用验收同时验证
   chunk 对声明的支持关系、URL/标题/锚点映射、无证据时拒答，以及语料更新后旧
   索引与旧证据失效，不能只检查链接可访问。

人工验收：对固定文档仓库构建索引并报告文档数、片段数、向量维度和来源映射；中文
和英文测试均命中正确原文；引用链接可访问；服务保持运行并提供 URL；不配置应用
凭据时明确降级且不暗用网关密钥。

## Round 9：多 Agent 应用开发工作台

目标：把一层子 Agent 升级为可观察、可控制、不会静默覆盖文件的开发团队。

小批次：

1. 子 Agent 使用独立 Session/Event/审批记录，共享 Workspace 时继承父级安全策略。
2. 增加调用拓扑、实时状态、对话、审批、产物和错误面板。
3. 提供 Planner、Builder、Tester、Reviewer 的可版本化 Recipe，但允许单 Agent 降级。
4. 子 Agent 在独立 staging/变更集中工作，合并时基于 Workspace tree hash 检测
   shell、git、格式化器和文件工具造成的全部变更；配合交接清单、检查点和显式
   冲突解决，并发写入不得静默覆盖。
5. 沉淀静态 Web、RAG、数据应用和 CLI 配方，并为每种配方提供最小离线验收集。

人工验收：四角色协作交付一个 RAG 应用；各子 Agent 的用途、状态、审批、输出和
错误可见；冲突写入被阻止并可恢复；父 Session 可停止自己启动的全部子 Agent 与进程。

## Round 4–9 统一门禁

每个编号小批次必须有独立定向测试、Feature Flag 和回退方式；上一小批次通过后
才开始下一项。每整轮仍遵守“重建前”和“人工验收/PR 前”两个停点。真实网络、模型
下载和浏览器链路使用固定 fixture/mock 做自动化门禁，只在人工验收阶段运行受控
真实链路；不得因为后续轮次较大而合并跳过 Harness。

## Apache-2.0 复用优先级

允许按 ModelMirror 的 Python/FastAPI 与 React 架构原生移植 PenguinHarness 的算法、
测试语义和前端交互，不运行其服务或引入侧车。优先对照：

- Goal/ReAct/恢复：`packages/core/src/engine/context-engine.ts`、`session.ts`、
  `goal/goal-loop.ts`、`goal-prompts.ts`；
- 命令与后台：`environment/tools/command/`、`environment/tools/background/`、
  `truncated-tool-output-archive.ts`；
- 子 Agent：`environment/tools/subagent/`、`run-subagent.ts`、`input-subagent.ts`；
- Server：`runtime/session-manager.ts`、`services/workspace-files-service.ts`、
  `workspace-guard.ts`、`preview-token.ts`、`http/routes/preview.ts`；
- Web：`workspace-browser.tsx`、`files-panel.tsx`、`goal-banner.tsx`、
  `compaction-banner.tsx`、`subagents-panel.tsx`、`agent-topology*.tsx`。

每轮在 `THIRD_PARTY_NOTICES.md` 记录上游 Commit、复用文件映射和本地修改，保留
Apache-2.0 归属；不复用 Penguin 名称、Logo 或产品视觉资产。

## 第二套 Agent 路线的继续 / 降级决策门

继续或降级不得再以 AGENTS.md 字数、章节数或连续两次提示词生成质量为主要依据。
至少完成 Round 4–7 的小游戏闭环和 Round 8 的 RAG 闭环后，按以下应用级指标判断：

- 是否产生真实可运行产物并由后端确认启动；
- 是否自动执行验收，失败后修复并在最后一次修改后复测；
- 是否为每个完成声明提供可展开证据；
- 是否能安全管理依赖、长进程、端口、凭据和预览；
- 是否能在重启、停止、超时和预算耗尽时给出准确终态；
- 是否在两类差异明显的应用中重复达到同等可靠性。

若应用级验收仍连续两个周期失败，再停止扩张独立入口：General Agent 降级为
可版本化模板或经典工作流节点；Skill、Skillset、工具执行器、审批、Workspace、
Goal 和事件接口按稳定边界融入现有智能体、工作流和 Agent stdio 体系。降级不得
删除已有 Agent State、Session Workspace、App Workspace 或用户数据，也不得用
空壳入口维持“第二套 Agent 已交付”的表象。

## Semantic Router

本阶段不引入 Semantic Router。未来评估时只允许它作为模型/意图路由策略层，
不得接管 Agent State、Session、工具审批或 Workspace 权限。接入前至少确定：

- 路由输入是否只包含最小必要的脱敏特征；
- 路由失败、超时和低置信度时的确定性回退；
- 路由结果是否可解释、可记录但不演变为成本/Trace 空壳模块；
- 不支持工具调用的模型不会被分配到需要九工具的任务。

## 未来画布 Agent 节点契约

第三轮只允许定义稳定契约，不修改经典工作流节点或 `NativeNodeKind`：

```text
input:
  agent_id: string
  session_id?: string
  workspace_id?: string
  message: string

events:
  session_id: string
  sequence: integer
  status: queued | running | awaiting_approval | awaiting_evidence | testing | completed | failed | blocked | stopped | budget_limited | interrupted
  type: task_status | text_delta | thinking_delta | tool_call | approval | tool_output | subagent | final

output:
  session_id: string
  workspace_id: string
  final_text: string
  terminal_status: completed | failed | blocked | stopped | budget_limited | interrupted
```

画布节点只能调用稳定 Agent Workspace API；不能读取 Agent State 内部路径，
不能绕过审批或直接控制命令进程。实际节点实现必须等第三轮整体能力完成并经过
独立 PR 与验收。

## 继续延后

以下能力保持延后：MCP 工具接入、新模型供应商管理、模型定价、成本中心、
Trace 观测、Benchmark、评估、Agent 自进化、Vault、定时任务，以及外部第 17
个 Skill。若未来启动，必须各自建立任务卡、验收命令、开关与回退方案。
