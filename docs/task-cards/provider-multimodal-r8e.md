# 任务卡：R8E 视频理解与异步生成 Provider 控制面

## 1. 单一目标

- 本次要完成：从提交前已再次同步的
  `origin/main@87431327f3ef27843caa7ebe3115454e06d8ffbe`
  接管 `multimodal_video_analysis`、`chat_video` 和 `video_generation`，统一精确
  Managed Binding、Adapter、资格、安全出口、一次派发、Receipt、幂等与异步恢复语义。
- 本次明确不做：Realtime Voice、R9 收敛、newAPI 视频资格猜测、多租户账户、计费、
  legacy 删除、默认 Provider 切换或多模态协议合并。

## 2. 证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| R8A 已声明视频 entry、shape、Adapter 与 Feature Flag，但三个真实数据面仍解析 legacy target | 已证实事实 | `server/model_router/workload_control.py`、`server/model_router/multimodal_control.py`、`server/multimodal/video_analysis.py`、`server/multimodal/video_jobs.py`、`server/main.py` |
| v18 已为 `video_jobs` 建立 Managed 字段，但当前 Repository 创建/更新接口尚未写入这些字段 | 已证实事实 | `server/model_router/repository.py::create_video_job_if_absent`、`update_video_job` |
| 已派发但未取得上游 ID 的视频提交缺少确定的 restart→uncertain 收敛 | 已证实事实 | `server/model_router/repository.py` 启动恢复 SQL 与 `server/multimodal/video_jobs.py` |
| 当前视频与控制面基线专项为 `69 passed` | 已证实事实 | 无网络临时容器执行五个现有视频/控制面测试文件 |
| R8D 合并后主线对相关后端仅在 `server/main.py` 增加结构化输出实验分支 | 已证实事实 | `git diff d246527d..bac37a6e -- server/main.py server/multimodal server/model_router` |
| 实施期间主线前进 6 个提交；同步审计确认 `server/main.py` 的 Decisions/RPG hunk 与 R8E Chat/视频 hunk 不重叠 | 已证实事实 | `git diff bac37a6e..1864ed84 -- server/main.py`；同步后跨轮回归、前端门禁和全量门禁重新执行 |
| 提交前主线再次前进到 `61b6eea8`；RPG Model Selector 只与 R8E 共同触及 `server/main.py`、`repository.py`，自动合并无冲突且交叉专项通过 | 已证实事实 | 在可恢复 stash 下 rebase 最新 `origin/main`；恢复后后端视频/控制面/RPG 联合回归 `303 passed`，前端 R8E 定向 `20 passed` |
| 最终提交前主线前进到 `87431327`；新增内容仅涉及 RPG 历史窗口、客户端帮助和实验目录，与 R8E 文件无交叉 | 已证实事实 | `git diff 61b6eea8..87431327 --name-only`；在可恢复 stash 下 rebase，恢复后全量后端与前端 R8E 定向重新执行 |
| 最新主线自身的前端 typecheck/build 与 RPG Model Selector 测试存在跨目录 React 依赖解析失败；两条价格旧断言也仍失败，R8E 恢复后错误集合完全相同 | 已证实基线问题 | 在干净 `61b6eea8` 与恢复 R8E 后分别执行同一 build/typecheck/定向 Vitest；两次结果一致，MCP 定向测试通过 |
| 最新主线的 Client Dockerfile 仅复制 `client/` 上下文，但主线新增测试静态导入仓库根目录 `experiments/`，因此容器内 `npm run build` 基线失败；宿主机完整仓库上下文的 production build 通过 | 已证实基线问题 | `docker build client` 报 `RpgChatState.test.ts` 无法解析 `experiments/.../session-client`；本轮不扩大范围修复，预览只读挂载同一已验证 `client/dist` |
| OpenRouter 专用视频目录将公开模型 `alibaba/wan-2.6` 精确映射为实际 `canonical_slug=alibaba/wan-2.6-20260327`；通用 `/models` 不能提供这项生成证据 | 已证实上游契约 | 选定连接的只读 `/api/v1/videos/models` 证据；目录契约升级为 `modelmirror-openrouter-video-models-v2` |
| 第一轮异步生成认证只发出一次提交 POST，上游任务完成且输出元数据有效，但旧实现因实际 canonical slug 与请求别名不相等而失败；该历史认证记录保持不变 | 已证实真实证据 | 认证 `workcert_5fd6b71b720a428daef9e6579df458be`；修复后未重放、未重新计费 |
| canonical 修复后的第二轮认证同样只发出一次提交 POST，并在 14 次只读轮询后通过；请求别名与实际 canonical slug 的精确目录映射生效 | 已证实真实证据 | 认证 `workcert_49c62173f58b4d9eb0e9e2f843002d3c`；请求 `alibaba/wan-2.6`，实际 `alibaba/wan-2.6-20260327`；状态 `passed` |
| Settings 旧前端门禁仍把 R8E 三种形态当作基础占位；现已只开放视频分析、视频 Chat 和异步生成，并继续锁定 R8F Realtime | 已证实并修复 | `ProviderWorkloadControlSettings.tsx` 与组件测试；异步 pending 使用警告态且只读刷新不会携带 `Idempotency-Key` |

## 3. 影响范围

- 允许修改路径：视频服务、视频任务 Repository 字段、Chat 视频分支、对应 API/测试、
  Settings/Help Center 与架构部署文档。
- 禁止修改路径：Realtime、R5/R6/R7 调度语义、普通 Chat 文本、Catalog 数量、提示词选择器、
  newAPI 生命周期、其他预览器与持久化数据。
- 预计文件数：分批每次最多 5 个文件；整体会跨后端、前端与文档多个独立批次。
- 影响路由/API：保留现有路径与成功协议，仅加法返回 `execution_mode`、
  `provider_route_receipts`、`provider_dispatch_state` 与稳定失败原因。
- 影响持久化数据：只使用既有 v18 加法字段；不重写旧任务。Managed 视频任务记录精确
  Workload Run/Call、策略、连接、Adapter、派发与终止状态。
- 新增或升级依赖：无。
- 涉及密钥/网络/文件/子进程/公开访问：Managed Provider 网络调用；Key 仅在后端内存；
  控制面不保存视频、Prompt、字幕或模型正文。

## 4. 验收标准

### 独立视频分析

- Given：入口 Feature Flag 开启、Policy 为 `managed_required`、精确模型拥有当前
  `video_analysis_unary + openrouter_chat_video_v1` 资格与 Binding。
- When：用户提交受支持的视频文件或 HTTPS URL。
- Then：只向 Binding 指定连接发出一个 POST，返回兼容正文和脱敏 Receipt；模型、Adapter、
  连接与 Receipt 一致，失败后不回退。

### Chat 视频

- Given：`chat_video_stream + openrouter_chat_video_v1` 已单独认证并激活。
- When：用户在 `/api/chat` 发送一个合法视频附件。
- Then：保留原 SSE delta、Route Receipt、`[DONE]` 顺序；单一逻辑调用最多一个 Provider POST，
  不借用独立分析资格，不在控制面保存视频或模型正文。

### 异步视频生成

- Given：`video_generation_async + openrouter_video_jobs_v1` 已认证、精确 Binding 激活，
  请求带合法幂等键，且参数精确为 5 秒、720p、16:9、单一输出的纯文本生成。
- When：提交任务并轮询。
- Then：同一幂等键最多一次提交 POST；取得上游 ID 后只通过原连接 GET；GET 不创建新的
  Workload Call。提交结果不确定或重启时保持 `uncertain`、`retry_allowed=false`，不得重放。

### 失败场景

- Given：Policy 漂移、资格 stale、Adapter/连接/模型不匹配、newAPI 无合格 Adapter、Provider
  返回 401/429/5xx、超时、取消或响应丢失。
- When：请求进入 Managed 路径。
- Then：派发前失败关闭；派发后不切换第二 IP、连接、模型、Adapter 或 legacy；确定响应与
  不确定传输结果分别记录，错误不泄露上游正文或凭据。

## 5. 实施顺序

1. 模型/契约：补齐视频资格的固定合成素材、同步/异步认证解析和 Adapter 精确约束。
2. 校验/安全：补齐 `video_jobs` Managed 字段写入、restart uncertain、幂等与脱敏测试。
3. 执行：依次接入独立分析、Chat 视频、异步生成；每条路径保持现有协议。
4. 前端：复用现有 Settings Policy/Binding/认证 UI，只增加必要的视频状态与用户侧 Receipt 展示。
5. 文档：更新 Help Center、Deployment/Architecture 与本任务卡的实际证据。

## 6. 验证矩阵

| 检查 | 命令或步骤 | 预期 | 状态 |
| --- | --- | --- | --- |
| 语法/类型 | Docker `py_compile`；前端 `npm.cmd run typecheck` | 无新增错误 | 后端通过；前端最新主线已有 RPG 跨目录依赖错误，R8E 上结果相同、无新增错误 |
| 目标测试 | R8E 专测；R8B—R8E 与视频链路联合回归 | 单 POST、精确 Binding、恢复和协议通过 | canonical 与 profile 指纹修复后 R8E 97 通过；联合回归 600 通过 |
| 回归测试 | 前端全量；后端全量；同 SHA 干净基线复现 | 零新增失败 | `1864ed84`：前端 1020 通过/2 基线失败，后端 6830 通过/27 基线失败/29 跳过；干净 `61b6eea8` 后端为 6777 通过/27 基线失败/29 跳过；最终 `87431327 + R8E` 后端为 6874 通过/相同 27 个基线失败/29 跳过。一次中间全量曾出现 2 条 RAG 顺序异常，但定向、半集、组合与最终全量均未复现；未将该次异常隐去。最新前端 R8E 定向 20 通过，Server Header 1 通过；价格两条失败和 RPG import/build 问题均已在干净主线复现 |
| 主线交叉回归 | Decisions/RPG 路径专项 | R8E 同步不影响主线新增接口 | 原 Decisions 4 通过；最新视频/控制面/RPG 联合回归 303 通过 |
| 构建 | 前端 typecheck 与 production build | 无新增失败 | `1864ed84` 宿主 build 通过；`61b6eea8` build 因 RPG 跨目录 React 依赖解析失败，干净主线与 R8E 结果一致 |
| Compose | Core、独立 newAPI、可选 Overlay `config --quiet` | 配置可解析 | 通过 |
| Docker/人工验收 | 独立预览、Settings、三入口 Smoke | 每入口单独授权后通过 | 视频分析与 Chat 视频真实认证已通过；异步生成认证通过后，已按精确 Binding 激活 `managed_required`，并完成一次真实用户 Smoke。任务 `local_bc8482bb75e54544bb3d05ebf76e9a41` 终态 `succeeded`，请求 `alibaba/wan-2.6`，实际 `alibaba/wan-2.6-20260327`，输出 1 个可播放视频，实际费用 `$0.4000`。预览 `15156/18156` 健康，旧容器和 SQLite 备份保留 |
| 敏感信息扫描 | Diff、日志、API、SQLite 扫描 | 无 Key、媒体、Prompt、正文 | 静态扫描通过；最终认证日志中 1 次认证 POST、14 次只读刷新、0 Prompt/配对密钥/Key/Traceback。真实 Smoke 只有 1 个已派发 Workload Call；后续刷新仅执行上游 GET，Receipt 为 `passed/confirmed`；固定 Smoke Prompt 不存在于 Router SQLite、WAL 或预览数据目录 |

## 7. 风险与停止条件

- 主要风险：异步提交在响应前中断导致未知计费结果；Chat SSE 兼容；Catalog 声明被误当资格。
- canonical 模型规则只接受同一专用目录记录中的精确 `id` 或精确 `canonical_slug`；不允许前缀、
  正则或模糊匹配。目录契约升级会使旧生成资格 stale，避免旧证据静默进入 Binding。
- 兼容风险：不得改变 legacy Feature Flag 关闭时的请求、响应、SSE 或任务轮询行为。
- 安全风险：用户视频/Prompt 或 Provider 错误正文进入 Receipt、SQLite 或日志。
- 触发停止的条件：重复 POST、派发后 fallback、模型/连接/Adapter/Receipt 不一致、newAPI
  未经真实认证进入候选、v18/回滚损坏、需要新增生产依赖或无法区分基线失败。
- 需要用户确认的问题：每个真实认证与用户 Smoke 的额度、PR、合并与生产启用分别确认。

## 8. 回退

1. 显式停用三个视频 Policy，并关闭对应 `MODEL_CONTROL_*` Feature Flag。
2. 回退 R8E 代码；旧代码继续忽略 v18 Managed 字段。
3. 保留 Router SQLite、Video Job、认证和 Receipt；不删除已派发任务或 Provider 数据。
4. 有上游 ID 的任务保留只读轮询；无上游 ID 的 uncertain 任务不得再次 POST。

## 9. 完成定义

- [x] 实现只覆盖声明范围。
- [x] 正常与失败路径均有验证。
- [x] 公共接口和数据影响已说明。
- [x] Diff 已审查，无用户改动被覆盖。
- [x] 无密钥、运行存储或构建产物进入提交。
- [x] 文档与 Harness 已同步。
- [x] 未知产品信息仍明确标为待确认。
- [ ] R8F 仅在 R8E 合并后从最新主线创建。
- [ ] R8 全部完成后重新审计 R9；旧 R9 不作为默认实施依据。
