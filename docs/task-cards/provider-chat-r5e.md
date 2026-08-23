# 任务卡：Provider Chat R5E

## 1. 单一目标

- 将 v15 中已预留的资格纪元、真实请求 Receipt、故障演练与人工批准收敛为
  `newapi_required_default` 的可审核 Go/No-Go 门禁。
- 只实现安全激活能力；合并、预览或测试均不得自动切换 required。

## 2. 已证实基线

| 结论 | 证据 |
| --- | --- |
| 当前基线 | `origin/main@0ad98935e8b0865b2c20bf6a990dac69260d5cce` |
| R5D 已进入主线 | 合并提交 `Merge pull request #255`，R5D 提交为其祖先 |
| v15 已预留 Gate 表 | `server/model_router/repository.py` |
| 当前 Gate 仍是 R5E 占位 | `ProviderChatControlService.gate()` 固定返回 pending blocker |
| required 当前不可保存 | `update_policy()` 固定拒绝 `newapi_required_default` |
| 受管文本/工具/文件已写入 Receipt | `chat_stable.py` 与 R5D `/api/chat` 集成 |
| 开工基线测试 | R5D Server 镜像内 37 passed；宿主机无可用 pytest，仅属环境差异 |

## 实施状态

- 已实现真实资格聚合、逐模型进度、故障演练清单和 revision 保护的原子人工激活。
- required 只允许首选 newAPI；硬失败会关闭纪元、撤销批准并保持 required 失败关闭。
- 设置页使用内联 Go/No-Go 确认；验收关联引用只在组件内存短暂存在并以哈希入库。
- 当前实现没有自动激活 required，也没有执行默认数据面切换；经用户逐次授权，
  已完成一次有界真实付费普通文本验收。

## 当前验证证据

- R5E Repository、Service、API 与 Stable Chat 专项：30 passed。
- R5A—R5D、Certification、Canary、Auto 与文件输出交叉回归：110 passed。
- 前端组件专项：2 passed；typecheck 与 production build 通过。
- 前端全量：533 passed，1 个无交叉 Skill Browser 用例在并行运行中达到 5 秒超时；
  该文件独立重跑 2 passed。
- 最新主线后端全量：4005 passed、29 skipped、20 failed；同一未修改基线 SHA 对失败文件复现为
  相同 20 failed、64 passed。失败均由挂载镜像缺少 Agency Worker 构建输出或 TypeScript
  运行依赖造成，不属于 R5E 新增失败。
- Core、新API 与 Overlay Compose 配置通过；Overlay 使用非敏感占位 URL 注入其必填变量。
- 隔离预览完成管理配对与未达门槛页面验收：required 选项不能直接保存、Go/No-Go 入口
  不会提前出现、页面和控制台均未回显预览配对值，Server 日志无异常。
- 严格证伪聚焦 required、Gate 竞态、硬失败、派发后零回退、显式回退和关联引用脱敏：
  24 passed，22 deselected；只有 4 条既有 FastAPI 生命周期弃用警告。
- 真实 `openai/gpt-4o-mini` 调用只新增 1 个逻辑 run、1 个 position-0 newAPI attempt 和
  1 条 newAPI 目标模型日志；token 增量为 18 input / 9 output，Receipt 与实际模型一致。
- Server 重启后 run、attempt、newAPI 日志和 Gate 样本计数保持不变，没有自动重放；
  当前纪元仍为 `collecting`，只累计 1 个成功样本，没有自动升级 required。
- Router SQLite 二进制复核未发现该次固定合成 Prompt 或模型输出正文。
- 真实调用完成于初始 R5D 基线 `7e543d8c`；验收期间主线前进到 `0ad98935`，
  交叉 Diff 只涉及 Coding Substrate、MCP 远程认证注册和独立文档章节。R5E Diff 已无冲突
  恢复到最新主线，并重新通过 30 项 R5E 专项、110 项 Chat 交叉回归、前端组件专项、
  typecheck、production build 及三组 Compose 配置检查；未为无交叉漂移重复消耗额度。

## 3. 范围与风险

- 允许修改：v15 Gate Repository、独立 Gate Service、Chat 控制 API/Schema、稳定
  Managed Chat required 语义、Settings Go/No-Go 区域、专项测试和控制面文档。
- 禁止修改：公开 Catalog 数量口径、多模态、RAG、Workflow、Agent、Coding、租户、
  积分、充值、ModelMirror 计费、newAPI UI 与默认部署值。
- 数据影响：不升版、不重写 v15；只向既有 Gate/Receipt 表增加运行和批准记录。
- 依赖影响：不新增或升级生产依赖。
- 主要风险：样本误计入、硬失败后自动恢复、激活与并发运行竞态、required 静默回退、
  把用户正文或 newAPI 日志保存为证据。

## 4. 固定门禁

1. 仅统计当前资格纪元、真实用户、`chat_text`、首选 newAPI position 0 已派发、
   非 Canary/认证/Auto/预检备用/客户端取消的请求。
2. 至少 500 个合格请求，首末跨度至少 14 天，总成功率至少 99%。
3. 每个稳定模型至少 10 次成功；认证错误、模型不一致、非法/空 SSE 和缺失终止为零。
4. 硬失败立即关闭纪元并撤销批准；required 保持 required 且失败关闭，不自动降级。
5. 新纪元必须绑定新策略/资格指纹；硬失败后的旧认证不能重新开启同一纪元。
6. 激活要求全部固定故障演练、无未解决 P0/P1、失败关闭确认，以及一次脱敏的
   newAPI 额度扣减、用量日志关联和重启持久化结论。
7. 激活在单个 SQLite 事务中重新检查 revision、纪元、指标和硬失败，再保存批准并
   切换模式；PR 合并不等于激活授权。

## 5. 验收

- 资格计数、14 天跨度、99% 成功率、逐模型样本和零硬失败真值表通过。
- Auto、Canary、认证、预检备用、客户端取消和非文本能力均不计入 Gate。
- 并发激活、revision 漂移、策略/认证变化和硬失败竞态均失败关闭。
- preferred 仍只允许派发前备用；required 只尝试首选 newAPI，任何阶段不得进入
  第二连接、第二 IP 或 legacy。
- Settings 显示证据、阻塞项和人工确认，不能通过普通策略保存直接进入 required。
- R5A—R5D、Router、认证、Canary、Auto、工具、文件和前端全量回归通过。
- 最终完成 Compose、Diff、Git 状态和敏感信息扫描；真实付费验收另行逐次授权。

## 6. 回退

- 管理员可显式把策略退回 `newapi_preferred`；系统不得自动退回。
- 必要时关闭 `MODEL_CONTROL_CHAT_ENABLED` 并重启，立即恢复 legacy 路径。
- 保留 v15 Gate、Receipt、Provider 凭据和 newAPI 数据，不执行降级或删除。
