# 任务卡：R6H Fusion Managed Provider

## 1. 单一目标

- 本次要完成：将 `/api/fusion/chat` 的原生 Fusion 与应用层候选/裁判调用接入现有 v16 Managed Provider Route Plan、精确 Binding 和脱敏 Receipt。
- 本次明确不做：R6I Route Agent/Team Chat、普通 Chat、RAG、多模态、Coding、多租户、积分、充值、账本和 ModelMirror 计费。

## 2. 基线与证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| R6G 已合并，R6H 基线为 `origin/main@61d855c1` | 已证实事实 | `gh pr view 288`; `git rev-parse origin/main` |
| v16 已预置 `fusion`、`chat_text`、`fusion_native`、Feature Flag 与原生资格，但数据面尚未标记接入 | 已证实事实 | `server/model_router/workload_control.py`; `server/model_router/schemas.py` |
| 原生 Fusion 失败后当前会自动切换应用层 Fusion | 已证实事实 | `server/main.py::fusion_chat` |
| 应用层候选与裁判当前直接使用 legacy gateway；裁判还会自动切换 `TEXT_FALLBACK_MODEL` | 已证实事实 | `server/main.py::collect_chat_completion_text`; `stream_text_with_model_fallback` |
| 最新主线控制面与相关前端基线通过 | 已证实事实 | 后端 18 passed；前端 3 files / 11 tests passed |

## 3. 影响范围

- 允许修改：Fusion 专用 Provider adapter、必要的共享流式 Transport adapter、`server/main.py` Fusion 路由、Fusion/控制面测试、Expert Team Fusion UI、控制面文档。
- 禁止修改：R6I、R5 Chat、RAG、多模态、Coding、Router SQLite Schema、Provider/newAPI 数据与凭据。
- 公共接口：保持 `/api/fusion/chat` 请求和既有 SSE 事件名称；仅加法附带脱敏 `provider_route_receipts`。
- 持久化：复用 v16 `provider_workload_runs/calls`，不新增迁移、不重写旧数据。
- 依赖：不新增或升级生产依赖。
- 风险：涉及真实 Provider 网络调用；Key 只允许存在于 Python Host 内存。

超过五个文件的原因：Provider 执行、API/SSE、前端证据展示和文档属于同一端到端门禁，但拆为后端 adapter、API/UI、文档三批分别验证，每批最多五个文件。

## 4. 执行契约

### 原生 Fusion

- `use_native_fusion=true` 只允许精确 `openrouter/fusion` 的 `fusion_native` Binding。
- 运行时有序候选模型和裁判模型必须与当前资格 Profile 完全一致。
- 一个逻辑运行最多一个 Provider POST；派发后失败不得转应用层 Fusion、第二连接、第二 IP、备用模型或 legacy。

### 应用层 Fusion

- `use_native_fusion=false` 时，在任何 POST 前一次性预检全部候选模型和裁判模型的精确 `chat_text` Binding。
- 每个候选和裁判是独立计划调用，各自最多一个 POST并产生独立 Receipt。
- 单个候选失败不触发备用 Provider，其余已预检候选可以继续。
- 至少一个候选成功时才派发裁判；全部候选失败时裁判记录为未派发失败，不自动选择候选正文。
- 裁判派发失败或返回空流时直接失败，不调用 `TEXT_FALLBACK_MODEL`，也不把候选正文伪装成裁判结果。

## 5. 验收标准

- Flag 关闭或 Policy 为 `legacy` 时，既有 Fusion 行为与 SSE 顺序保持不变。
- Managed Policy 未就绪、Binding/资格/Profile 漂移时在首个 POST 前失败关闭。
- 应用层预检失败时所有 Provider POST 数为零。
- 原生模式最多一个 POST；应用层实际 POST 数等于成功派发的计划调用数。
- 原生 POST 后失败不会启动应用层；应用层候选/裁判失败不会调用第二 Provider 或 legacy。
- 候选并发、部分失败、裁判、取消、空流、非法 SSE、模型不一致和策略漂移均有稳定 Receipt。
- API、SQLite、日志和浏览器状态不保存 Key、Prompt、用户消息、模型正文或完整上游错误。
- 前端显示已纳管/已阻断、调用数、模型和状态，不显示连接、认证 ID或目标 URL。
- R5、R6A-G、Catalog、模型数量和提示词选择器零回归。

## 6. 验证矩阵

1. Fusion Managed Gateway 单元与并发测试。
2. Provider Workload、Transport、SSRF/DNS pinning 与 Receipt 回归。
3. `/api/fusion/chat` legacy/managed SSE 专项测试。
4. Expert Team Fusion 与 Settings 组件测试。
5. 前端全量测试、typecheck、production build；后端全量 `server/tests/`。
6. Core、新API、Overlay Compose 配置验证。
7. 独立预览验证策略、Binding、阻断、原生/应用层 Receipt。
8. 真实付费 Smoke 需用户逐次授权；授权前不得声称真实 Provider 已验收。
9. `git diff --check`、敏感信息扫描、最终 Diff 与 Git 状态审查。

## 7. 停止条件与回退

- 出现重复 POST、派发后回退、Receipt/实际模型不一致、需要保存 Prompt/输出、需要迁移数据或扩大到 R6I 时立即停止。
- 回退：显式停用 `fusion` Policy，再关闭 `MODEL_CONTROL_FUSION_ENABLED` 并重启，恢复 legacy；保留 v16 资格和脱敏 Receipt。
- Commit、Push、PR、真实付费调用与生产启用继续分别等待授权。

## 8. 实施与证伪记录

- 已实现原生与应用层 Fusion 的精确 Managed Route Plan、单次派发边界、逐调用 Receipt 和前端脱敏展示。
- 定向攻击发现并修复两个问题：共享页面仍承诺 legacy 自动兜底；Flag 关闭时仍会提前初始化 Router Service。前者改为中性控制面边界说明，后者在入口最前端按部署 Flag 短路，确保回滚不依赖控制面可用性。
- 真实认证攻击发现 OpenRouter Fusion 的响应 `model` 是实际裁判模型，而不是虚拟请求模型 `openrouter/fusion`。认证、资格投影和运行时已统一按精确裁判模型验证；返回其他模型时仍失败关闭。修复后专项回归为 `23 passed`，受影响后端回归为 `54 passed`。
- 候选模型池增加全量名称/ID 搜索并保留已选模型；前端最终全量为 `113 files / 650 tests passed`，`typecheck` 与 production build 均通过；build 仅保留现有大 chunk 告警。
- Core、独立 newAPI 与 Core+Overlay Compose 均通过 `config --quiet`；Overlay 未提供显式 `LLM_GATEWAY_URL` 时按设计失败关闭。
- 独立预览运行于 `127.0.0.1:15143` / `127.0.0.1:18143`，Server 健康；浏览器已验证原生/应用层切换、候选搜索与裁判、三页签设置和零 iframe，容器及浏览器日志未见异常或敏感信息。
- 原生 Fusion 资格认证通过：请求模型 `openrouter/fusion`，实际模型 `openai/gpt-4.1-nano`，单次 POST，`241` tokens，无警告。
- 原生 Fusion Smoke 通过：单次 POST，实际模型 `openai/gpt-4.1-nano`，`286` tokens，页面、Route Plan 与 Receipt 一致。
- 应用层 Fusion Smoke 通过：候选 `qwen/qwen3-8b`、`openai/gpt-4.1-nano` 与 GPT-4.1 Nano 裁判严格产生三次计划 POST，三次均通过，共 `1158` tokens，无备用 Provider 或重复派发。
- 一次候选替换顺序错误在首个 POST 前以 `provider_workload_binding_missing` 失败关闭，Receipt 为零调用；随后零付费诊断证明两个合格 Binding 均可进入准备阶段。该事件证明 fail-closed 边界，未计作真实 Smoke。
- 最终代码全量后端运行结果为 `4539 passed, 29 skipped, 3 failed, 6 warnings`；仅有的三个失败均为 Skill 跨语言一致性用例在一次性后端镜像中找不到 `orchestration_worker` 的锁定 TypeScript 开发运行时。按 `server/orchestration_worker/package-lock.json` 补齐依赖后，三项隔离复测为 `3 passed`。因此最终 `4542` 项均有通过证据，但证据分为“全量主体 + 环境隔离补测”，不表述为单次全量绿测。
- 在同一补齐依赖环境发起的统一全量复跑运行至约 69% 时测试会话句柄失效，未取得完整结果，因此不计入验收证据，也未用局部进度替代最终结论。
- 提交前已将分支无冲突变基至 `origin/main@a8258ba6`。新增 MCP 与 Workflow/Handoff 变更不修改 R6H 的 Fusion、共享 Transport 或前端文件；唯一共同文件 `server/main.py` 的上游 hunk 与本轮 import、factory 和 Fusion endpoint hunk 不重叠。
- 变基后 Fusion、Provider Workload 与 Workflow/Handoff 交叉回归为 `91 passed`；前端全量为 `114 files / 659 tests passed`，`typecheck`、production build 和 Core/newAPI/Overlay Compose 配置均通过。
- Merge、生产启用与任何后续真实额度调用尚未完成，继续分别等待授权。
