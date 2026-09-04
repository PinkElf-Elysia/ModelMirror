# R22任务卡：受限AI对话与权威行动闭环

- `R22_BASE_SHA=e927db557f71db420e07a49818c0d4ae1e0d6ce3`
- 分支：`codex/matrix-oasis-r22-bounded-cognition`
- worktree：`C:\\tmp\\modelmirror-matrix-oasis-r22`
- 版本：`0.22.0-r22`
- 资格profile：`matrix-oasis.bounded-npc-cognition/1`

R22只实现玩家显式触发的单轮AI对白和已有安全Action提案。每个Turn必须先生成确定性上下文和完整Call Plan，再由用户对精确内容逐次批准；模型输出不能直接写Runtime、Ledger或R21派生状态。

进入门：

- `R20_RUNTIME_REMAINS_AUTHORITATIVE`
- `R21_LEDGER_REBUILD_EQUIVALENT`
- `R21_MEMORY_DELETION_VERIFIED`

退出门：

- `R22_DIALOGUE_BUDGET_ENFORCED`
- `R22_FALLBACK_PLAYABLE`
- `R22_UNTRUSTED_OUTPUT_ADJUDICATED`

R22.1只迁移治理、来源锁、威胁模型与唯一R20 selector注入白名单，不实现合同、Provider或Godot预览。R22.2至R22.6只允许离线假Provider与loopback验证。末班地铁一次真实Luna调用必须在离线门全部通过后，另行展示完整外发内容并取得当次批准；当前计划授权不等于该次付费调用授权。

人工验收前不执行R22.7正式资格状态切换，不push、不创建PR。回退方式见ADR 0023。
