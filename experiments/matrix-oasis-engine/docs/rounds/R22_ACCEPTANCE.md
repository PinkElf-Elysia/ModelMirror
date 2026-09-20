# R22 有界认知验收记录

日期：2026-09-20。固定基线：`e927db557f71db420e07a49818c0d4ae1e0d6ce3`。分支：`codex/matrix-oasis-r22-bounded-cognition`。模块版本：`0.22.0-r22`。

## 结论与声明范围

用户已确认末班地铁真实对白正常、NPC 确实移动后返回，随后完成六项离线窗口补验，并明确接受 head 漂移、跨进程崩溃恢复及有限隐私扫描作为自动证据。R22 的人工验收门据此通过；没有把自动测试记作用户亲自在 Godot 中完成的操作。

本轮状态为 `r22-cognition-qualified`，profile 为 `matrix-oasis.bounded-npc-cognition/1`，仍保持 `claimAllowed=false`、`blockingRound=R25`。R16 MVP 状态与 Creator 默认入口不变。R22 不等于第二版完成，不宣称自由行动、语义长期记忆、关系自动演化、任务、世界事件或动画已经实现。

本记录是人工可读的证据摘要，不是验收签名系统。`verify:r22` 对本文必要字段的检查仅防止治理记录缺失，不能替代实际运行、用户确认或最终 standalone 检查。最终第七提交仍须通过 clean-HEAD extraction 后才可执行已授权的 push / PR；其 source、split、archive 身份记录在仓外拆分回执及 PR，避免本文件自引用自身提交 hash。

## 交付边界

- 一个玩家触发的 Turn、完整外发内容绑定的逐次审批、一次请求与零自动重试；保留预算预留、dispatch、异常费用与恢复证据。
- 模型只返回纯文本对白与本次 opaque choice；安全候选必须同时满足 R19、R20、当前 Runtime 和可见绑定限制。合法 `null` choice 可正常进入 dialogue-only，不必降级。
- 有 Action 时复用 R20 导航、到达物理证明和镜像链，再交 R19 单写者裁决；R21 只从已裁决 Ledger 重新投影，不接收模型直接写入。
- 127.0.0.1:43122 隔离组合预览；保持 R20 原预览和 R16 默认路径。NPC 启动不抢跑，完成对白或降级后只放行一个命令；reset 轮换 timeline/store，保留 host 总预算和旧回执。
- 新离线故障入口明确使用假 Provider，只用于人工验收拒绝、注入、timeout、refusal 和非法响应；不能凭假调用证明真实 Provider 或账单。
- 第七批仅同步治理状态、声明门负测和本文；不再改动 Provider、Runtime、空间算法或 Godot 场景。

## 七个线性提交

| 批次 | 提交 | 内容 |
|---|---|---|
| R22.1 | `db97f902` | 治理、来源核查和有界认知边界 |
| R22.2 | `46e55721` | 闭合合同 |
| R22.3 | `ef0bd98a` | 有界认知运行时 |
| R22.4 | `bbd559cb` | 受控模型调用边界 |
| R22.5 | `9adb5493` | 组合式 Godot 对话预览 |
| R22.6 | `59662a3b` | 响应兼容、证伪矩阵、恢复与离线补验 |
| R22.7 | 本提交 | 人工验收记录、状态和声明门收口 |

## 真实调用与实现身份

历史成功样本在 `C:\tmp\matrix-oasis-r22-luna-billing-compat-20260919-a`。它绑定实现 `sha256:2b3592e691230ccc706d4ee70c9e77ebef555a19e2f74257a771509cbd18b2f7`：官方 `gpt-5.6-luna`，1 次请求、0 retry、1 次已批准凭据读取，接受 1 个 Action 且 Runtime 镜像一致。用户明确认可对白以及移动、返回效果。

- Provider usage：527 input、135 output、662 total、0 cached tokens。
- 本地锁价估算扣减 268 microusd（0.000268 美元），预留 10000 microusd；没有供应商账单核对，不将估算写成审计后的实际收费。
- Godot：39 ticks、1900 mm、`walked-home`、归位误差 0 mm；300 帧中位 234.796 FPS，只代表本机该次样本。
- Receipt SHA-256：`ddbc09e0144a2d3f86383ecd5a920e9ddcd195ac96a8ae92c532d9b3ea0191ad`。
- Ledger SHA-256：`74fb0c5d4ca416366c8ceb7b4b3bb619a0699fd02d859f7017929c9ee4473adb`。
- `observation-report.json` 观察报告 SHA-256：`e4caf44b51bae9ccdd84cbe9f0032eba5c286f8240777ac4e4d927827933c1f2`。
- `physical-observation.json` 物理观察 SHA-256：`f9061b97339f2a752cf8ff9e5732fd33263e1a65f2fcf3e083f8893acc5aa9ae`。

这里的 1 次仅指这一份成功样本，绝不是整个 R22 历史请求总数。后续补充离线入口形成普通预览实现身份 `sha256:c40f4263d04d88492b7c3ea86f66192e097846cb3a6a26d8e6f365777be4c070` 与离线验收身份 `sha256:977d29354c96c767a8ca487f5fbdbfc35f7bce447d3063d9becd907e027dd979`。历史真实身份与当前离线身份分开记录，不将原真实样本重签为当前实现再次调用成功；收尾期间没有新增真实请求或读取真实密钥。

经当次批准，Provider 显式锁定 `service_tier:"default"`，旧审批不得复用。兼容范围仅限已观测并负测覆盖的精确零计数 `tool_usage` 形态与 `{"payer":"developer"}` billing 形态；未知字段/形态继续 fail closed。billing 被校验后丢弃，不冒充官方完整 billing Schema、ZDR 或收费凭证。

## 人工补验及自动证据边界

所有六项窗口使用离线假 Provider，原始观察报告保持 `manualAcceptancePassed:false` 与 `unqualified-manual-observation`。用户确认由本记录和详细证据文档关联，未通过翻转原布尔值伪造资格。

| 窗口 | 用户观察与机器核对范围 |
|---|---|
| 中性 960×540 | 审批/键盘、3 次假批准、补做 1 次审批面板 Esc 拒绝；ending、active/ending reset；4 次移动返回 |
| 中性 640×540 | 窄屏/键盘、1 次零请求拒绝、1 次假批准；ending 后 reset；2 次移动返回 |
| 末班地铁注入 640×540 | 纯文本注入展示、仅披露候选中的 Action；移动、节点隐藏归位及 reset |
| 末班地铁 timeout 960×540 | 1 次假请求/0 retry、固定降级、无 AI Intent；随后 1 次固定策略 Action 及 reset |
| 末班地铁 refusal 640×540 | 拒绝响应降级、无 AI Intent；随后 1 次固定策略 Action、窄屏键盘及 reset |
| 末班地铁非法响应 960×540 | 非法响应降级、无 AI Intent；随后 1 次固定策略 Action 及 reset |

六窗均正常退出、清理无失败，且前一窗口释放端口后再启动下一项。300 帧中位均高于 30 FPS，范围约 125.281–242.013 FPS，不建立跨 GPU 性能保证。末班地铁离线节点切换后的归位为 `hidden-home`，与历史真实样本的 `walked-home` 不混淆。故障窗口仅观察表中一次恢复行动与 reset，不扩写成每个故障窗口都由用户走完所有 ending。

用户另明确接受以下三项自动证据，但不记作其亲自操作：

1. 真实 R19 推进后，旧 head/snapshot 对应的响应与 choice 被离线测试拒绝。
2. 独立子进程在 dispatch、Ledger 追加、Receipt 发布窗口崩溃后恢复，不重发模型、不重复 Action。
3. 固定产物的禁止字段扫描和诱饵测试通过；不保证任意 PII 全局不存在，也不声称文本 hash 匿名化。

一次早期子进程测试先导入事务模块、后安装 fake 拦截，曾存在最多 2 次携带合成假 key 的潜在外发尝试；没有读取真实 key。已改为导入前拦截、OS 最小环境、native-network tripwire 与超时/输出/清理限制，再通过 3/3 进程恢复和 188/188 transport 检查。不能把该历史风险抹写为全部历史测试零外部尝试。

逐窗日志、Receipt、物理观察、保护源 hash 及全部限制详见 [R22 证伪证据](R22_FALSIFICATION_EVIDENCE.md)。该文保留初始失败、修复与验收顺序，不用最后一次成功覆盖之前证据。

## 验证与发布门

更新后、第六提交前的工作树已执行：离线 `npm.cmd ci`、`npm.cmd prefix`、`npm.cmd ls --all`、`doctor:godot`、`npm.cmd run verify`、round/parent scope 与 `git diff --check`。全量结果为 `VERIFY_OK steps=31`：R22 2017 通过、0 失败、1 项 Windows 文件 symlink 权限条件跳过；历史聚合测试 941/941 通过；Godot 分析/物理验证、Creator build/smoke 通过。未批准新的依赖生命周期脚本，锁文件未漂移。

- 全量日志：`C:\tmp\matrix-oasis-r22-final-verify-20260920-a.log`。
- SHA-256：`24524fbdc0aea6551e70d66965e01032eb5505de712455763c822dccaa274e42`。
- 第七批新增同步篡改 policy/status 的三项声明门负测；本提交须先通过定向检查，再由最终 clean-HEAD extraction 重跑完整 31 步。上面的 2017/941 是第六批快照结果，不冒充未执行的最终计数。
- extraction 必须同时给出 `sourceDirtyIgnored:false`、source HEAD、split commit/tree、archive SHA-256；失败则不 push、不创建 PR。仓外日志与 PR 保存最终结果，本文不预填成功或虚构 hash。

第七批定向复验已完成：声明门 5/5、`BOUNDARY_OK`、round/parent scope 与完整 `verify:r22` 均退出 0；后者为 2020 通过、0 失败、1 项相同 Windows symlink 条件跳过，官方 Godot import/probe 通过。日志 `C:\tmp\matrix-oasis-r22-final-governance-verify-20260920-a.log`，SHA-256 `154ee00458db72072af235a18deb804072de54118b2896f0f2f52385009f08e3`。重新计算普通/离线入口的各 1024 文件实现清单，仍为前述 `c40f4263…` / `977d2935…`，治理收口未改变已观察运行身份。独立只读复核提出的一处报告 hash 标签已修正并复核解除；最终 clean-HEAD extraction 仍是提交后发布门。

最新父主线为 `87431327f3ef27843caa7ebe3115454e06d8ffbe`，固定基线未改变，无 rebase；上游与本模块没有路径交叉。其仓外干净 client 副本执行 typecheck、header 1/1 与 build 均通过；1043 项测试中 1041 通过、2 失败：`ModelCard.test.ts:139` 分时价格文案及 `tokenPricing.test.ts:62` UTC 窗口。两份测试及对应实现与此前基线 blob 一致，source 前后漂移为 0。用户已明确授权如实披露这两个基线失败并继续 PR，不修改父代码；不能因此将父仓测试写成全绿。

父验证目录：`C:\tmp\matrix-oasis-r22-parent-client-closeout-20260920-a`。测试日志 SHA-256：`013432d3c7f58dbecc18ac15441467e330bcd3b077c2919958baf08862041d1f`；build 日志 SHA-256：`930849971a34fec223e38c642ee930e2286e311af979e4b904a48a1c21eec595`。归档参数错误的初次失败及同 SHA 修正过程也已在详细证据中披露。

## 回退与未实现项

禁用 R22 profile 后可逆序 revert 七个 R22 提交；R16、R19、R20、R21 独立路径保留。Git 回退不会删除仓外资格、不可变旧 Ledger、Receipt 或预算账本，也不能撤销已发生的供应商费用；不得重置历史预算以复用旧审批。

仍不包含 Creator 认知入口、自由模型工具、任务/世界事件、语音/动画、外部记忆服务或跨 timeline 记忆。声明限于锁定 Windows / Godot 4.6.3 profile 下、玩家逐次审批的单轮对白与既有安全 Action 提案，经 R20 物理到达和 R19 权威裁决闭合。
