# 任务卡：R6I Route Agent 与 Team Chat Managed Provider

## 1. 单一目标

- 本次要完成：将 `/api/route-agent` 与 `/api/team/chat` 接入现有 v16 Managed Provider Route Plan、精确 `chat_text` Binding 和脱敏 Receipt。
- 本次明确不做：普通 Agent Workbench、RAG、多模态、Coding、普通 Chat、多租户、积分、充值、账本和 ModelMirror 计费。

## 2. 基线与证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| R6H 已合并，R6I 基线为 `origin/main@2409c032` | 已证实事实 | `gh pr view 292`; `git rev-parse origin/main` |
| v16 已预置 `route_agent`、`team_chat`、`chat_text` 与默认关闭的 Feature Flag，但数据面尚未接入 | 已证实事实 | `server/model_router/workload_control.py`; `.env.example` |
| Route Agent 当前失败后会自动切换 `TEXT_FALLBACK_MODEL` | 已证实事实 | `server/main.py::stream_text_with_model_fallback`; `route_agent` |
| Team Chat 当前每位成员及最终汇总都使用同一 legacy fallback 路径 | 已证实事实 | `server/main.py::team_chat` |
| 最新主线 Route/Team 与 Workload 控制面基线通过 | 已证实事实 | 后端 `15 passed` |

## 3. 影响范围

- 允许修改：Route/Team 专用 Managed adapter、两个 API 的分流与 Receipt、Expert Team 脱敏展示、控制面接入标记、专项测试和文档。
- 禁止修改：R5 Chat、Fusion、RAG、多模态、Coding、Router SQLite Schema、Provider/newAPI 数据与凭据。
- 公共接口：保持两个请求体和既有 SSE 事件名称；仅在阶段结束或错误事件中加法附带 `provider_route_receipts`。
- 持久化：复用 v16 `provider_workload_runs/calls`，不新增迁移、不重写旧数据。
- 依赖：不新增或升级生产依赖。

超过五个文件的原因：Provider adapter、API/SSE、前端证据与文档属于同一端到端门禁，但按后端、前端和文档三批分别实施与验证，每批最多五个文件。

## 4. 执行契约

### Route Agent

- 专家匹配仍由现有本地索引完成，只有最终模型作答进入 Managed Provider。
- 每次运行只允许精确模型的一次 `chat_text` 计划调用。
- 派发后失败不得切换模型、Provider、连接、第二 IP 或 legacy。

### Team Chat

- 在首个 POST 前一次性预检全部成员轮次和一次最终汇总；计划调用数固定为成员数加一。
- 串行与辩论继续沿用现有 Prompt 和 SSE 顺序，每个成员及汇总分别记录调用序号。
- 任一成员或汇总失败时停止本次团队运行；未派发的剩余计划调用写入失败证据，不调用备用模型。
- 用户显式重新运行创建新的逻辑运行，不重放旧运行。

## 5. 验收标准

- Flag 关闭或 Policy 为 `legacy` 时，既有请求、SSE 与自动 fallback 行为保持不变。
- Managed Policy 未就绪、Binding/资格/策略漂移时在对应 POST 前失败关闭。
- Team 任一预检失败时 Provider POST 数为零。
- Route 实际 POST 数最多为一；Team 实际 POST 数等于已派发的计划调用数。
- POST 后错误、空流、非法 SSE、取消或模型不一致均不得产生备用调用。
- SSE 只加法附带脱敏 Receipt，不改变回答正文、匹配结果或成员输出。
- API、SQLite、日志和浏览器状态不保存 Key、Prompt、用户消息、模型正文、连接 ID或 URL。
- R5、R6A-H、Catalog、模型数量、提示词选择器和多模态零回归。

## 6. 验证矩阵

1. Managed Route/Team 网关的全量预检、单次派发、失败与取消测试。
2. 两个 API 的 legacy、managed、degraded 与 SSE 兼容测试。
3. Provider Workload、Transport、SSRF/DNS pinning、Fusion 和 Expert Team 回归。
4. Expert Team Receipt 解析、脱敏展示、前端全量测试、typecheck 和 production build。
5. 后端全量 `server/tests/`，并区分基线、环境与新增失败。
6. Core、新API、Overlay Compose 配置验证。
7. 独立预览验证策略、Binding、阻断、Route 与 Team Receipt。
8. Route 与 Team 真实付费 Smoke 分别需要用户授权；授权前不得声称真实 Provider 已验收。
9. `git diff --check`、敏感信息扫描、最终 Diff 与 Git 状态审查。

## 7. 停止条件与回退

- 出现重复 POST、派发后 fallback、Receipt 与真实目标不一致、需要保存 Prompt/输出、需要迁移数据或扩大到 R7 时立即停止。
- 回退：显式停用对应 Policy，再关闭 `MODEL_CONTROL_ROUTE_AGENT_ENABLED` 或 `MODEL_CONTROL_TEAM_CHAT_ENABLED` 并重启，恢复该入口 legacy；保留 v16 资格和脱敏 Receipt。
- Commit、Push、PR、真实付费调用与生产启用继续分别等待授权。

## 8. 当前实施记录

- 已新增薄的 Route/Team Managed adapter，复用现有 SSRF、DNS pinning、单 IP、零重试、响应关闭和 Receipt 状态机。
- 已将两个入口接入 `managed_required`，同时保证 Flag 关闭时不初始化控制面且继续要求 legacy gateway。
- Team 在任何 POST 前创建全部成员及汇总 Prepared Call；成员失败会关闭剩余未派发调用，不再触发 `TEXT_FALLBACK_MODEL`。
- Expert Team 页面仅在现有结果卡中显示脱敏调用总数、模型与分段状态，不持久化 Receipt，也不修改模型回答正文。
- 定向出口与 R6I 回归为 `36 passed`；R6A-I 受影响后端矩阵为 `190 passed`；最终后端全量为
  `4583 passed, 29 skipped`。新 worktree 初次缺少 Worker build 产物导致的三项环境失败，已按
  `server/orchestration_worker/package-lock.json` 构建后原样复测为 `3 passed`，最终全量也在
  Worker 已构建的同一源树上完整通过。
- 前端全量为 `114 files / 660 tests passed`，typecheck 和 production build 通过；第一次全量
  有一项无关 Skill Creator 用例在 5 秒门槛超时，隔离复测 `9 passed`，完整复跑随后 `660/660`
  通过。build 仅保留既有大 chunk 告警。
- Core、独立 newAPI 与 Overlay Compose 均通过 `config --quiet`；`git diff --check` 通过。
- 用户已分别授权并完成隔离预览与真实付费 Smoke：R6I 预览运行于
  `127.0.0.1:15144` / `127.0.0.1:18144`，Router 数据从 R6H 卷只读克隆到新卷且源/目标
  SHA-256 一致；R6H 原卷、容器和端口保持不动。
- Route Agent 使用 `openai/gpt-4.1-nano` 完成一次真实调用；UI 显示“已纳管 · 1 次
  Provider 调用”，SQLite 只有一条已派发、通过且实际模型一致的 Call。
- 两成员串行 Team Chat 完成两次成员调用和一次汇总调用；UI 显示“已纳管 · 3 次
  Provider 调用”，SQLite 恰有三条已派发、通过且实际模型一致的 Call。
- 后端日志合计恰有四次 Provider Chat POST 且全部为 HTTP 200，精确错误信号为 0；Receipt
  表没有 Prompt、消息、模型正文、Base URL 或凭据列，验收文本标记未写入 Receipt 值。
