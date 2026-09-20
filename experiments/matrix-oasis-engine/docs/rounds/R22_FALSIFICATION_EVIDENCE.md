# R22 证伪与收尾证据

状态：收尾验证中。历史真实对白与 NPC 移动/返回、六项离线窗口补验及三类自动证据的人工审阅均已确认；更新后全量回归与 clean-HEAD extraction 仍须取得成功结果。本文件不单独解除 R22.7 或 R25 声明门。

## 范围与固定身份

- 基线：`e927db557f71db420e07a49818c0d4ae1e0d6ce3`。
- 分支：`codex/matrix-oasis-r22-bounded-cognition`。
- 进入收尾时 HEAD：`9adb5493501a954360edf6525542ffface55653e`，已有五个本地提交。
- 收尾刷新到的 `origin/main`：`61b6eea80152904eb8fb3cc0ebba5e7647594828`。当时分支领先 5、落后 99；主线这些提交没有修改本模块。没有 rebase、merge 或静默更换基线。
- 不修改 R16 默认 Creator、R19 合同、R20 既有预览或 R21 投影算法，不新增生产依赖。全部历史资格源、失败回执和已消费审批保持只读。

## 攻击目标与证据边界

| 待证伪假设 | 对照与拒绝条件 | 能证明什么 / 不能证明什么 |
|---|---|---|
| 格式合法即可执行 Action | 玩家输入、label/cue、历史对白注入；未知 choice、跨 actor/node、不可用 Action、过期 head；R19/R20 候选交集重新求值 | 本地权限与裁决边界；不证明模型语义永远正确 |
| Schema 与 Provider 总能兼容 | typed `const/enum`、精确响应字段、独立协议 oracle、response hypothesis matrix | 锁定请求/响应 profile 的兼容与 fail-closed；不接受未知能力字段 |
| 新增 billing 字段必然是任意费用 | 只接受精确单键 `{"payer":"developer"}` 并丢弃；金额、额外键、其他 payer、工具费用均拒绝 | 已观察形状的窄兼容；不是官方完整 billing 语义或供应商账单审计 |
| 成功或失败可以随意释放预留 | Standard、模型、usage 算术与上下限同时验证；任何独立失败保留完整预留；成功按锁价计算 | 本地预算协议；不把本地 `actualCostMicrousd` 命名误解为账单 API 凭据 |
| 同一审批可以重发 | 并发、重复 Turn、reset、dispatch 后恢复、Receipt 发布窗口、旧诊断 claim 重用 | 单次派发与不确定费用保守记账；新调用仍须独立批准 |
| 整包重签即可伪造执行证明 | 由真实源重建 command、choice、Intent、display ACK、Receipt、Ledger | 对控制器证据做语义交叉验证，而非仅检查 SHA 格式 |
| marker 等同于物理执行 | Godot marker 与 R19 完整重放、实际 command/arrival/mirror、300 帧、归位证据绑定 | 指定实现/二进制/来源的真实观察；不是跨实现资格继承 |
| 绿测可以替代人工 UI 验收 | 单列中性实时场景、拒绝/降级、窄屏键盘、视觉及恢复人工门 | 不翻转离线报告或历史观察中的资格布尔值 |

完整历史矩阵、先红后绿、失败原因与各次单独调用授权记录保存在 `docs/R22_REFERENCE_AUDIT.md` 和 `docs/R22_TASK_CARD.md`。其中重复执行的测试组不能累加为独立样本数。

## 最新成功真实样本与人工确认

这是一份最新成功样本，不代表整个 R22 历史只发生过一次请求。

- 实现身份：`sha256:2b3592e691230ccc706d4ee70c9e77ebef555a19e2f74257a771509cbd18b2f7`。
- 官方 `gpt-5.6-luna`；本次运行根 request=1、retry=0、credential read=1。
- Receipt：`finalized`，包含 `validated → queued_for_r20 → adjudicated`，无 fallback。
- R19 完整重放：accepted=1、rejected=0；choice、Intent、command、Ledger head 和 Godot mirror 一致。
- 真实 Godot：39 ticks / 1900 mm，path/floor/capsule/domain 均通过；walked-home，归位误差 0 mm。
- 300 帧中位 234.796 FPS，仅适用于本机锁定环境。
- usage：输入 527、输出 135、合计 662；本地锁价扣账 268 microusd，预留 10000 microusd。不是已核验供应商账单。
- 用户确认：“我判定对白正常，NPC确实移动后返回”。只覆盖本次对白与移动/返回观察。

仓外目录：`C:\tmp\matrix-oasis-r22-physical-e4caf44b51bae9ccdd84cbe9f0032eba5c286f8240777ac4e4d927827933c1f2`。

| 文件 | SHA-256 |
|---|---|
| observation-report.json | `e4caf44b51bae9ccdd84cbe9f0032eba5c286f8240777ac4e4d927827933c1f2` |
| physical-observation.json | `f9061b97339f2a752cf8ff9e5732fd33263e1a65f2fcf3e083f8893acc5aa9ae` |
| turn-receipt.json | `ddbc09e0144a2d3f86383ecd5a920e9ddcd195ac96a8ae92c532d9b3ea0191ad` |
| world-event-ledger.json | `74fb0c5d4ca416366c8ceb7b4b3bb619a0699fd02d859f7017929c9ee4473adb` |

原观察报告仍为 `manualAcceptancePassed:false`、`unqualified-manual-observation`。人工确认由文档关联，禁止倒改历史字节。

## 收尾独立审查与测试隔离事故

两项独立审查分别覆盖 Provider/预算/恢复安全和十项人工门。安全审查最初将“未独立核验账单”视为成功路径释放预留的 P1；复核锁定的 Standard、四档 token 计费、无工具、完整 usage 及后续已批准语义后撤回。没有能导致允许响应漏算既有定价维度的反例；上游 payer 语义与实际账单仍作为非阻断、已披露的外部可观察性限制。

确需补证的是 v0.5 billing 诊断的真实进程边界：既有十窗口是同进程文件故障注入，不等于 dispatch 后强杀与全新进程恢复。

新增补测的首次实现出现测试隔离错误：child 顶层静态导入早于 fake fetch/文件操作拦截，而事务模块在求值时捕获这些依赖。两条 child 运行均未命中预期崩溃窗口；最多存在 2 次使用测试生成的合成假密钥向锁定官方端点尝试外发的可能。未读取真实密钥，未保存原始响应；不能证明该首次运行零外网，也不能将其计作通过或供应商账单证据。主代理发现后立即停测并告知用户，两个测试进程已退出。后续必须先复核拦截先于动态导入、最小 OS 环境、底层网络 tripwire、输出上限和超时清理，再执行定向补测；不得通过放宽 boundary 或修改生产事务规避。

隔离修正后的共享网络阻断自测 1/1 通过：原生 fetch 仅尝试固定 loopback 地址，在建立连接前被底层 tripwire 拒绝，标准输出/错误为空。新 child 先安装不可变 fake fetch、文件和环境读取监视，再以固定字面量动态导入事务；只继承七项 OS 变量，输出上限 32 KiB，父进程负责超时与清理。随后定向测试发现未决 Promise 不能独自维持 Node 存活，导致 durable-dispatch 窗口在强杀前自然退出；仅给该测试 child 增加由父进程限时管理的活动句柄，未改变生产事务或断言。

修正后真实 child-process 补测 3/3 通过：durable-dispatch 与 fetch-entered 两窗口分别由父进程强杀，再启动全新恢复进程；恢复 fetch/凭据读取/网络 tripwire 命中均为 0，完整保留 10000 microusd 不确定扣账、旧 v0.2/v0.4 证据和预算前缀，拒绝第四名额。日志：`C:\tmp\matrix-oasis-r22-closeout-crash-test-20260919-b.log`。该结果仅证明隔离假 Provider 进程边界，不把首次隔离事故改写为零外网。

完整 `tests/r22-diagnostic-transport.test.mjs` 重跑 188/188 通过、0 跳过；日志 `C:\tmp\matrix-oasis-r22-closeout-transport-20260919-a.log`，SHA-256 `d46eae74f72925871c672b92fd8ec7e14f19a74d5c1a033a5618f9c40572a24c`。最终测试文件 SHA-256 `f5de9b85840b307cc688ac2f51b016bdcc6cf0ea339e4ee739c7e38f9e8a017f`。独立只读复核确认先拦截后导入、保活、匹配 marker 后强杀、等待式清理及恢复零请求断言，未发现新的可操作 P0/P1/P2；该审查不是另一份执行样本。

## 当前离线与隐私证据

- 重新执行 `qualify:r22`，复用固定三案例，输出 `C:\tmp\matrix-oasis-r22-closeout-offline-20260919-a`；report SHA-256 为 `2ecb8665c3ac267aa43e34394cd59c017d69172297853f79f664290006ac6e8f`，与上一份相同输入的报告一致。保持 `readyForPreview:false`，不冒充实时或人工资格。
- 为中性补验使用既有固定 case spec 重新生成 `C:\tmp\matrix-oasis-r22-closeout-neutral-offline-20260919-a`；报告 SHA-256 `1c18bc08dc33e182510586b33e158f85cf82865b29dde250112bb223ca081aed`，同样保持离线、`readyForPreview:false`。在新增离线人工入口之前，重新计算实时入口及 Godot 资源依赖图仍为最新成功真实样本的 `sha256:2b3592e691230ccc706d4ee70c9e77ebef555a19e2f74257a771509cbd18b2f7`。随后新增入口改变可达实现身份，不能再将这一历史身份称为当前源码。
- 只读扫描最新 NPC 根、cognition 根、物理观察根和两份固定 R21 派生根，共 40 个文件。精确禁止原文字段、凭据格式、非法 JSON、链接及读取中身份变化均为 0。只输出计数与 hash，没有读取真实密钥或回显正文。
- 扫描摘要：`C:\tmp\matrix-oasis-r22-closeout-privacy-20260919-a.log`，SHA-256 `bdf38969b84f163253625cb3d08e928a688332bb672f0ff4ba8e82e3f394f9bf`。范围只限这五个固定产物目录，不证明所有任意 PII 或未纳入的系统日志绝对不存在。
- 新增补测进行中曾触发 `dynamic-import-nonliteral` boundary 失败；冻结 R21 测试本身通过，但该次 `verify:r21` 整体失败，不能记为通过。修正后，完整 `verify` 的原 `verify:r21` 子命令已重新输出 `R21_AUTOMATED_GATES_OK` 和 `VERIFY_STEP_OK r21`；此前失败日志保留。

新增离线人工入口前，完整 `npm.cmd run verify` 退出 0，最终 `VERIFY_OK steps=31`；R22 2004 通过、0 失败、1 项既有 Windows 文件 symlink 权限跳过，聚合 `npm test` 941/941 通过。日志为 `C:\tmp\matrix-oasis-r22-closeout-verify-20260919-a.log`，SHA-256 `b031d903aaa362bf135faeb336541b9079a6e7571e177eb8da8005fb2c645f1c`，对应待提交 tree `a35447c1690bbd4000107357a9b0107e261749c3`。新入口是在该回归完成后才编辑，旧全绿结果不覆盖新批次。

## 已批准的离线人工入口补齐

- 用户批准有限离线故障入口，不批准新增真实调用。专用 `preview:r22:offline` 固定 `offline-fake`，只接受五种场景和两种尺寸，拒绝凭据或任意响应/脚本输入；不修改原 CLI 的可选 Provider 模式。
- 假响应通过同一 Provider 校验器和完整控制链；timeout 使用原 30 秒超时机制。注入夹具固定为可见纯文本，不解析 HTML、Markdown、BBCode 或资源路径，也不扩大候选 Action。
- 仅在新生成的仓外 project 中调整标题、逻辑 viewport 和窗口尺寸。标题显示 `OFFLINE FAKE QA`、场景及尺寸，实际 Godot 场景源码保持不变。
- 独立 0.2.0 session/observation 记录绑定场景和尺寸；恢复不得跨 profile。既有普通入口记录保持原字段结构，但共享 composition 的修改会改变可达源码身份；不声称新身份已经执行此前真实请求。
- 新批自动测试、实际 Godot 观察和用户人工确认分开记录；始终保留 `unqualified-manual-observation`，不能以 ready marker 或假 Provider 通过解除资格门。此节仅记录批准的范围，不提前填写测试或人工通过。

定向补测结果（2026-09-20）：

- CLI 与 launcher 测试 25/25 通过，覆盖全部 5×2 参数组合、非法控制项、五项 project 配置变更及启动/清理边界。composition 测试 10/10 通过，包含四种非超时情形及真实约 31.3 秒的 fake timeout；最后补充 Symbol 额外键拒绝后，对应边界测试再次通过。以上是 fake/parser/Host 证据，不是 Godot 视觉验收。
- 新进程恢复测试 9/9 通过：手工 profile 成功恢复时零重新派发、零凭据读取；改场景、改尺寸、删 profile 均拒绝，manifest 与预算原字节不变。日志 `C:\tmp\matrix-oasis-r22-offline-manual-recovery-20260919-a.log`，SHA-256 `5166fc769f7d8dad99401b3a34b11e4c2f79dad87e42c2b7f3d194b819873f07`。首次非提权执行被 `C:\tmp` 的 `mkdtemp EPERM` 拦截，未进入业务断言；原命令在已批准本地执行权限下重跑通过。
- 范围门首次发现新增脚本在两份 allowlist 的顺序不一致，机器门正常拒绝。只修正顺序，未放宽集合或校验；原 round/parent 命令重跑通过，`ROUND_SCOPE_OK checked=134 changed=102`、相同 fixed-base parent 范围通过，boundary 与 diff 检查通过。
- 独立只读复核未发现新增可操作 P0/P1/P2。人工入口源码 SHA-256 为 `5dc6f31ea3896ee230df8d4cf94b9fdd9cc59041451f1498ffe9d4c14b51e5e8`，composition 为 `b4fc6a68647c2e85b484f3a8ee5e91857526a0b6bf707fadabc12bf87e533997`。原 Godot cognition 场景为 `ca86a65617b5a7a2ca59b8fe73a162cd4bc16f9591de803b1db4fc56a5f502e1`，未改变。
- 当前普通入口 implementation 为 `sha256:c40f4263d04d88492b7c3ea86f66192e097846cb3a6a26d8e6f365777be4c070`，新离线人工入口为 `sha256:977d29354c96c767a8ca487f5fbdbfc35f7bce447d3063d9becd907e027dd979`。此前成功真实请求只绑定其历史 `2b3592e6…` 身份；没有追加请求来为新身份伪造真实资格。专项完整回归和人工观察另行补录。

人工补验按独立新运行根逐项启动：

| 顺序 | 场景/尺寸 | 待人工确认事项 |
|---|---|---|
| 1 | 中性 normal，960×540 | 已完成本项人工操作及回执/重放复验，详见下文；不代表整轮资格通过 |
| 2 | 中性 normal，640×540 | 已完成本项人工操作及回执/重放复验，详见下文；不代表整轮资格通过 |
| 3 | 末班地铁 injection，640×540 | 已完成本项人工操作及候选/裁决/物理复验，详见下文；不代表整轮资格通过 |
| 4 | 末班地铁 timeout，960×540 | 已完成超时降级、固定策略恢复及 reset 的人工操作与重放/物理复验，详见下文；不代表整轮资格通过 |
| 5 | 末班地铁 refusal，640×540 | 已完成拒绝响应降级、固定策略恢复及 reset 的人工操作与重放/物理复验，详见下文；不代表整轮资格通过 |
| 6 | 末班地铁 invalid-response，960×540 | 已完成非法响应降级、固定策略恢复及 reset 的人工操作与重放/物理复验，详见下文；不代表整轮资格通过 |

此表区分已完成单项与剩余待执行项，不是整轮通过矩阵。所有预览只使用假 Provider；UI 保留正式审批展示以检验同一路径，窗口标题明确标为 `OFFLINE FAKE QA`，展示的模型与价格不是一次真实发送或实际费用。

新入口完整 `verify:r22` 于 2026-09-20 退出 0，`R22_GOVERNANCE_GATES_OK`。总计 2017 通过、0 失败、1 项既有 Windows 文件 symlink 权限跳过；包含 scope 95/95、实时链 487 通过/1 跳过、参考 4/4、合同 14/14、运行时 13/13、Provider 1266/1266、Host 119/119 和 Godot 19/19，且实际 Godot 4.6.3 import/probe 通过。各组不重复累计。日志 `C:\tmp\matrix-oasis-r22-offline-manual-verify-20260920-a.log`，SHA-256 `8d14ba9097b0185ea7715d3e9d4698c0c2d7e3d7a3af28cb59921b148e7ea3fb`。

该结果完成新入口专项集成检查，不代替待执行的六项人工观察、更新后全模块/clean-HEAD extraction 或最终发布门。`V2_STATUS` 未切换；没有新增真实 Provider 请求、密钥读取、commit、push 或 PR。

第一项中性 `normal / 960x540` 已通过新入口启动，独立运行基名为 `C:\tmp\matrix-oasis-r22-neutral-manual-20260920-a`，日志为同名 `.log`。READY 与 session manifest 均绑定 `offline-fake`、上述 profile 及 `977d2935…` 实现；只有 `127.0.0.1:43122` 监听、43120 无监听。启动时检查点 requests=0、sequence=0、finalized=0、无活动 Turn。此处只证明待人工操作的窗口就绪，尚未记录本项人工通过。

### 中性双尺寸人工补验（2026-09-20）

用户逐批按列明的人工检查项回复“完成”；第一批最初没有拒绝回执，未据此提前判为通过。用户确认尚未在审批面板拒绝并补做后，才取得 `NPC_COGNITION_FALLBACK_APPROVAL_DECLINED` 回执。以下运行记录均经独立只读检查：重开 R20 固定源、校验 manifest/输入身份、从初始 Session 完整执行 R19 重放，再交叉校验实际 commit 中的 trace、Receipt 与 Godot 物理观察。读取结束再次检查文件字节及目录集合未改变，不获取活动 writer、不修改历史证据。

- `normal / 960x540`：3 次不同 Turn 的假调用均为单请求、无 fallback、已裁决；另 1 次拒绝为请求 0、预留 0、扣减 0，拒绝时 Ledger 不变。拒绝后恢复的固定策略动作明确记录为 `cognitionActionVerified:false`，不冒充 AI Action。3 条时间线覆盖 active reset、ending 和 ending 后 reset；57 个稳定文件复验通过。4 组物理观察均完成移动并 walked-home，归位误差 0 mm；每组 300 帧，中位 233.644–236.742 FPS。
- `normal / 640x540`：用户完成输入、审批滚动/键盘/焦点、对白和 reset 检查；1 次拒绝为零请求/零预算，另 1 次假调用完成裁决。原时间线 revision 2 为 ended，reset 后新时间线 revision 0 为 active；33 个稳定文件复验通过。2 组物理观察均 walked-home、归位误差 0 mm，300 帧中位分别为 127.129 与 125.281 FPS。
- 两批均只使用 `offline-fake`，证据中的真实请求与凭据读取计数均为 0。假 usage/扣账只是确定性测试数据，不是供应商消费。两个旧预览均已正常退出：exit 0、`cleanupFailure:null`。没有修改原始 observation 的 `manualAcceptancePassed:false` 或 `unqualified-manual-observation`；用户完成记录只在本文关联。

| 离线记录 | 仓外路径或目录尾缀 | SHA-256 |
|---|---|---|
| 960×540 完整日志 | `C:\tmp\matrix-oasis-r22-neutral-manual-20260920-a.log` | `d32b474881dc76d3bc7653ab45f90720a521f4c4e430af8897d5de87fc49a9b2` |
| 640×540 完整日志 | `C:\tmp\matrix-oasis-r22-neutral-manual-640-20260920-a.log` | `bffa68688a0281df7f27b34d7a7702cd79afed4177779a160353c11dc00f36b9` |
| 640×540 拒绝 Receipt | cognition 时间线 `timeline-593fe5944509c77e9c76f177` / sequence 1 | `e45a9a1ff8bfefd39cb7bc2ad65376f7363c34ec0affa85622c46846c53daf84` |
| 640×540 裁决 Receipt | 同一时间线 / sequence 2 | `fedd4d47eec9e23df9fc2f79343e2fbf7f4a207cc2860798dc7f3a307dc0fcf4` |
| 640×540 固定策略观察报告 | `C:\tmp\matrix-oasis-r22-physical-7a9e560385d32f0ed53e69abb40f878324f7464d4212279664f4efc160f6b842` | `7a9e560385d32f0ed53e69abb40f878324f7464d4212279664f4efc160f6b842` |
| 640×540 认知动作观察报告 | `C:\tmp\matrix-oasis-r22-physical-2945accacab451dbdf47ee067ee73d1d45aa2c46539e2c976e0bc05d495f5c3a` | `2945accacab451dbdf47ee067ee73d1d45aa2c46539e2c976e0bc05d495f5c3a` |

准备下一项末班地铁注入补验时，首次启动参数误指向 `matrix-oasis-r15-evidence-last-train-v2` 历史对照根；该目录不含 R16 qualification 绑定的 evidence run，R16 引用校验正确拒绝，未创建 NPC/cognition 根或启动交互 Godot。失败日志 `C:\tmp\matrix-oasis-r22-last-train-injection-640-20260920-a.log` 的 SHA-256 为 `5e2009c85beeb0741a0d19f047d9d70d50ffb9a1c8c54c76b24b0c529f4e7104`，原样保留。只读定位确认该资格绑定的是 `matrix-oasis-r16-last-train-evidence-v1` 中 `48e8af98…0016`，R16 完整引用链及 17 个精确 preview 文件已通过原验证器。仅更正启动目录参数，以 `matrix-oasis-r22-last-train-injection-640-20260920-b` 新运行根继续；代码、资格缓存和 hash 均未修改，也没有新增真实调用。此处不预先记录注入人工通过。

### 末班地铁注入人工补验（2026-09-20）

用户针对固定注入文字原样显示、只执行披露 Action、节点变化后的归位以及 reset 检查回复“完成”。检查中断后恢复，只读重新核验实际产物，没有重发该 Turn。

- 运行基名：`C:\tmp\matrix-oasis-r22-last-train-injection-640-20260920-b`；profile 为 `injection / 640x540`，仍绑定 `977d2935…` 实现与 `offline-fake`。
- 原 timeline `timeline-d594866614e4d5bef030ce22`：1 次假请求、0 retry、无 fallback，Receipt 完整经过 validated、queued、adjudicated、finalized。choice 位于审批的 `candidateChoices` 内，其 intent hash 与映射 Intent、Ledger 中实际 accepted Intent 完全一致；未出现额外或候选外 Action。
- R19 从初始状态重放至 revision 1，Godot command、arrival、mirror 与 entry hash 交叉验证通过。真实移动为 39 ticks / 1900 mm，path/floor/capsule/domain 均通过；节点切换后角色不再可见，因此按冻结规则 `hidden-home` 归位，误差 0 mm。此处不是步行返回的证据。
- 300 帧中位 240.558 FPS，仅证明本机本次样本。reset 后原时间线 sealed，新 timeline `timeline-313555418d12e425852c06ca` 为 revision 0、active、零 Turn/请求。
- 30 个文件读取前后与相关目录集合一致；其中 26 个本次持久化文件检查未发现禁止的原文字段或固定注入对白全文，不扩张为任意 PII 检测保证。真实 Provider 请求和凭据读取均为 0，fake usage/预算仍不代表供应商费用。
- 该预览正常退出：exit 0、`cleanupFailure:null`。历史观察仍保留 `manualAcceptancePassed:false` 与 `unqualified-manual-observation`，没有以本项完成解除整轮门禁。

| 记录 | SHA-256 |
|---|---|
| 同名 `.log` | `e03c3482131977db24b221649ad8e0b17d6e8b1892dd6834e15b5c6dd1e281a4` |
| Turn Receipt | `d9f279253b53456140c842437bcbd52b87ffb57cf7ea8016469bf22740dc951a` |
| physical-observation.json | `a72c15da000553bd30b6380838797d9a4ab022afd9b44cff86e66610736a079f` |
| observation-report.json（目录 `C:\tmp\matrix-oasis-r22-physical-742556b278bb26d914ac9d9c91ef70fa767dd2fffa237bbcd75b7a1ec77f8472`） | `742556b278bb26d914ac9d9c91ef70fa767dd2fffa237bbcd75b7a1ec77f8472` |

### 末班地铁超时人工补验（2026-09-20）

用户针对一次批准后等待超时降级、关闭对白后恢复固定策略及 reset 的检查回复“完成”。运行基名为 `C:\tmp\matrix-oasis-r22-last-train-timeout-960-20260920-a`，profile 为 `timeout / 960x540`，仍绑定 `977d2935…` 实现与 `offline-fake`；没有调用真实 Provider 或读取真实凭据。

- 1 次假请求、0 retry，固定原因 `NPC_COGNITION_FALLBACK_PROVIDER_TIMEOUT`。Receipt 的 choice、mapped Intent 和 Adjudication 均为空，前后 Ledger head 相同，未产生隐藏 AI Action。
- 超时费用不确定分支保留全部 10000 microusd 的模拟预留/扣减；这只是离线预算语义，不是供应商消费或真实账单。
- 关闭降级对白后恢复的固定策略动作明确为 `cognitionActionVerified:false`。完整 R19 重放及 Godot command/arrival/mirror 交叉验证通过：39 ticks / 1900 mm，path/floor/capsule/domain 均通过；节点变化后角色按规则 `hidden-home`，归位误差 0 mm。300 帧中位 239.463 FPS，仅对应本机本次样本。
- 原 timeline `timeline-f8a3514a8f5db8d58622ae97` 为 revision 1、active、sealed；reset 后新 timeline `timeline-ed3bfeea1e5bee307ef2d3a7` 为 revision 0、active、零 Turn/请求。27 个文件读取前后及目录集合一致，其中 23 个本次私有文件的受限原文字段扫描通过；不声称任意 PII 均可检测。
- 预览正常关闭，exit 0、`cleanupFailure:null`，43122 已释放后才进入下一项。保留原始 `manualAcceptancePassed:false` 和 `unqualified-manual-observation`，本项人工确认仅由本文关联。

| 记录 | SHA-256 |
|---|---|
| 同名 `.log` | `dce553c18693e404ad3fc7785540f90ff9227c4b75f01127a03a778a73f875e0` |
| Turn Receipt | `b403ac88d6076a9e18cb1e008e669ede19546c40aa2d21a6401e7dd4595dcf08` |
| physical-observation.json | `f01d46784701c1aa725ef657925b69c78b69b7ac36efdc178912b580dadf6947` |
| observation-report.json（目录 `C:\tmp\matrix-oasis-r22-physical-32a2daa516231a0fb7547a1d1c83f41eaa5889781816e2be8cb80b339d52b3d6`） | `32a2daa516231a0fb7547a1d1c83f41eaa5889781816e2be8cb80b339d52b3d6` |

### 末班地铁拒绝响应人工补验（2026-09-20）

用户针对 `refusal / 640x540` 的一次批准、固定降级提示、后续行动、窄屏键盘及 reset 检查回复“完成”。运行基名为 `C:\tmp\matrix-oasis-r22-last-train-refusal-640-20260920-a`，仍使用 `offline-fake` 与 `977d2935…` 实现。这是 Provider 拒绝响应场景，与中性场景中零请求的人工拒绝审批不同。

- 1 次假请求、0 retry，固定原因 `NPC_COGNITION_FALLBACK_PROVIDER_REFUSED`。Receipt 的 choice、mapped Intent 和 Adjudication 均为空，前后 Ledger head 不变；拒绝响应没有生成 AI Action。
- 假 usage 对应模拟扣减 100 microusd，预留 10000 microusd；仅证明本地测试预算分支，不代表真实供应商费用。
- 关闭提示后固定策略完成一次动作，`cognitionActionVerified:false`。R19 全量重放和 command/arrival/mirror 交叉验证通过：39 ticks / 1900 mm，path/floor/capsule/domain 均通过；节点切换后 `hidden-home` 归位，误差 0 mm。300 帧中位 240.673 FPS，仅对应本机本次样本。
- 原 timeline `timeline-e0d38f565883dfb8c10c2750` 为 revision 1、active、sealed；reset 后新 timeline `timeline-69eb1660d97203a1f8d0e0a7` 为 revision 0、active、零 Turn/请求。27 个稳定文件及目录集合复验通过，其中 23 个本次私有文件的受限原文字段扫描通过，不泛化为任意 PII 检测保证。
- 窗口正常退出：exit 0、`cleanupFailure:null`。仅在确认 43122 释放后启动下一项；没有真实请求、密钥读取或源缓存写入。原观察报告的 `manualAcceptancePassed:false` 与 `unqualified-manual-observation` 保持不变。

| 记录 | SHA-256 |
|---|---|
| 同名 `.log` | `0ecebac893462c0c40fe4b3400ad381edca491cfe93c78ee171fba9f97c5bdfc` |
| Turn Receipt | `c1e789efd192b7aa98eebb584c663f23f441871489329f4e49d904b1f1bd7a60` |
| physical-observation.json | `71770f5bf95fbc1549f7b423d2695a500a3d14b947f8a1b8a418b8fef3b32d35` |
| observation-report.json（目录 `C:\tmp\matrix-oasis-r22-physical-6be510f5cdd41dfb625927831020c57df9929236aa6da22a829031a78a52caec`） | `6be510f5cdd41dfb625927831020c57df9929236aa6da22a829031a78a52caec` |

### 末班地铁非法响应人工补验（2026-09-20）

用户针对 `invalid-response / 960x540` 的一次批准、固定降级提示、后续行动和 reset 检查回复“完成”。运行基名为 `C:\tmp\matrix-oasis-r22-last-train-invalid-960-20260920-a`，绑定 `offline-fake`、`977d2935…` 实现及与前几项相同的固定资格源。

- 1 次假请求、0 retry，固定原因 `NPC_COGNITION_FALLBACK_PROVIDER_RESPONSE_INVALID`。Receipt 的 choice、mapped Intent 和 Adjudication 均为空，前后 Ledger head 相同；非法响应没有生成 AI Action。
- 该假响应没有可验证 usage，因此本地模拟扣减保留全部 10000 microusd 预留；不是一次真实请求或供应商费用。
- 关闭提示后固定策略完成一次动作，`cognitionActionVerified:false`。完整 R19 重放及 command/arrival/mirror 交叉验证通过：39 ticks / 1900 mm，path/floor/capsule/domain 均通过；节点切换后 `hidden-home` 归位，误差 0 mm。300 帧中位 242.013 FPS，仅对应本机本次样本。
- 原 timeline `timeline-df290c82eb666e89db7b5119` 为 revision 1、active、sealed；reset 后新 timeline `timeline-3704c34455a2f5647b972268` 为 revision 0、active、零 Turn/请求。27 个稳定文件及目录集合复验通过，其中 23 个本次私有文件的受限原文字段扫描通过；不扩大为任意 PII 检测保证。
- 预览正常退出：exit 0、`cleanupFailure:null`。没有新增真实请求、读取真实凭据或写入源缓存；原观察的 `manualAcceptancePassed:false` 与 `unqualified-manual-observation` 保持原始字节。

| 记录 | SHA-256 |
|---|---|
| 同名 `.log` | `bf33215880a7df438cf652d63ae8392d44788b7db514bf47677760a963dd8281` |
| Turn Receipt | `2bf55c0ae5be07be87e9de91ce1dd10d69ddeee832e782dd1ea95049c63827ea` |
| physical-observation.json | `c68cc25bcaa9708f5e7e6758475af10278d52d3168b4240fae09955ca1b810f5` |
| observation-report.json（目录 `C:\tmp\matrix-oasis-r22-physical-758bad0f182319e86b25f1929d8cc0917d94428a5fd10b218e74c9fd53a14575`） | `758bad0f182319e86b25f1929d8cc0917d94428a5fd10b218e74c9fd53a14575` |

## 父仓验证与未解除的门

父 `client` 在最新主线 `61b6eea80152904eb8fb3cc0ebba5e7647594828` 的仓外干净源码上检查；没有修改父源码、依赖锁或任何断言。

- 首轮只归档 `client`，遗漏其测试引用的 `experiments/ai-rpg-engine/card-replica`；类型/构建和一个测试文件因此失败。这属于验证副本不完整，不作为父基线或 R22 缺陷归因。
- 从同一提交补齐该目录及锁定离线依赖后，原类型检查和构建通过；header 1/1 通过。构建保留既有大 chunk 提示。
- 三个失败文件定向重跑：24 通过、2 失败；RpgModelSelector 已通过。剩余为 `ModelCard.test.ts:139` 的分时价格文案和 `tokenPricing.test.ts:62` 的 UTC 价格窗口断言。它们在未含 R22 修改的主线源码上可复现，不能通过修改矩阵绿洲或悄悄改父断言解决。
- 补全同提交源码后重跑原 `npm.cmd run test:run`：143 个文件中 141 通过、2 失败；1035 项中 1033 通过、2 失败，仍是上述两个价格断言。完整日志 `C:\tmp\matrix-oasis-r22-parent-client-closeout-20260919-a\test-run-complete-source.log`，SHA-256 `73406eb395d2d89582f1577f80b4148873a905c58d665a60d010fe044bd91527`。这属于可复现父主线基线失败，不声称父仓全绿。
- 用户在看到上述结果后明确选择：“如实披露为基线失败，允许继续 R22 收尾和 PR，不修改父代码”。发布时保留这个有范围的基线例外及失败证据；它不豁免 R22 自身检查或尚缺的人工观察。
- 新离线入口前的跨轮 `verify` 已通过，见上节固定 tree 和日志；新增入口后的回归、clean-HEAD extraction 和剩余人工门仍需各自明确结果，不能由旧快照绿测替代。

六项窗口补验均已按表完成，对应人工操作和回执/重放/物理观察的实际范围见上文。随后向用户明确披露：head 漂移由真实 R19 状态推进后的离线响应/choice 拒绝测试证明，跨进程恢复由独立子进程故障注入证明，隐私检查只覆盖固定产物及诱饵，不保证任意 PII 全局不存在。用户明确回答“接受这些证据，继续收尾”，因此这三项的人工证据审阅门通过；不写成用户亲自在 Godot 操作过同样故障。

独立只读复核直接核对历史真实样本、六份脱敏日志、各物理报告和最新非法响应 Receipt，未发现可行动 P0/P1/P2；历史真实实现身份、fake 计数/预算、hidden-home 与 walked-home 区别以及不改写原资格布尔值均与文件一致。该复核没有运行测试或重新计算普通入口实现身份，不替代主代理的集成回归。

真实 Luna 成功样本不需要重调来补这些离线项。人工门通过后，更新后跨轮回归与 clean-HEAD extraction 仍須分别执行；只有它们成功才继续完成本地收口和已授权 PR，不能由人工确认替代。

此前用户针对缺项明确回答“尚未完整检查，安排缺项的离线补验”。本节按实际六项窗口和后续明确证据审阅逐项解除，而不是从“收尾后 PR”授权反推出已检查通过。

收尾前 `V2_STATUS` 继续为 `r22-bounded-cognition-in-progress / claimAllowed=false / blockingRound=R25`。用户已授权收尾后 PR，但未完成上述门禁前不把本文件当作整轮通过或提前发布的依据。

### 最终刷新与收口顺序（2026-09-20）

- 再次 fetch 后 `origin/main` 为 `87431327f3ef27843caa7ebe3115454e06d8ffbe`，比此前父验证提交新增 RPG 历史窗口提交及其 merge；R22 当时领先 5、落后 101。自固定基线以来的上游修改与本模块没有路径交叉，已向用户报告，不 rebase、不更换 R22 基线。
- 新主线改到了父 client，因此在新的仓外干净副本重跑父验证；旧 `61b6eea8…` 结果只保留为对应快照的证据，不冒充新主线结果。
- 先完成更新后工作树全量回归并形成第六提交；第七批只同步治理状态、最小声明负测及验收文档，不再修改运行逻辑。随后对完整第七提交的 clean HEAD 执行 standalone extraction，成功前不 push 或创建 PR。最终 source/split/archive 身份留在该次拆分回执及 PR 说明，避免文档自引用。

新主线父验证已完成：仓外副本 `C:\tmp\matrix-oasis-r22-parent-client-closeout-20260920-a` 只从 `87431327…` 同一提交归档 `client` 与其测试需要的 `experiments/ai-rpg-engine/card-replica`；560 个 tracked 文件，验证前后 source drift 为 0，没有复制旧源码或改动父工作树。两包均离线 `npm ci` 成功，类型检查、header 1/1 和 build 均退出 0；`test:run` 退出 1，144 个文件中 142 通过、2 失败，1043 项中 1041 通过、2 失败。

仍只有上述 `ModelCard.test.ts:139` 与 `tokenPricing.test.ts:62` 两项已批准披露的定价基线失败，没有新增失败。两份测试及其对应实现文件在 `61b6eea8…` 与 `87431327…` 间 Git blob 完全相同；此结果延续既有基线例外，不将父测试标为全绿。构建保留既有大 chunk 提示。首次 `git archive` 因 PowerShell 参数组装错误退出 128、未生成归档；改正命令参数后从同一 SHA 重新成功归档，未改变源码或断言。

| 最新父验证记录 | SHA-256 |
|---|---|
| `test-run.log` | `013432d3c7f58dbecc18ac15441467e330bcd3b077c2919958baf08862041d1f` |
| `build.log` | `930849971a34fec223e38c642ee930e2286e311af979e4b904a48a1c21eec595` |
| `source-87431327.tar` | `e8093053a459d04cfe886168bdcfe122f3e63c0824a80e17893aa49465b42318` |

### 更新后全量回归（2026-09-20，第六提交前）

`npm.cmd ci`（离线缓存）、`npm.cmd prefix`、`npm.cmd ls --all` 和 `doctor:godot` 均通过，未批准新的依赖生命周期脚本，锁文件未改变。预检日志为 `C:\tmp\matrix-oasis-r22-final-preflight-20260920-a.log`，SHA-256 `3b3c9e930d7b02823f43997d71fbeb070b6f93ca3e8d3f495bccbf67909fd122`。

包含离线故障验收入口和修正后的子进程隔离测试在内，完整 `npm.cmd run verify` 已退出 0，最终输出 `VERIFY_OK steps=31`。R22 分组共 2017 通过、0 失败、1 项 Windows 文件 symlink 权限条件跳过；历史聚合测试 941/941 通过。R13/R14 真实 Godot 分析与物理验证、确定性求解、Creator 构建和 smoke 均通过，没有把 mock 替代为 Godot 证据。完整日志为 `C:\tmp\matrix-oasis-r22-final-verify-20260920-a.log`，SHA-256 `24524fbdc0aea6551e70d66965e01032eb5505de712455763c822dccaa274e42`。

这解除更新后工作树全量回归门，不代替最终第七提交的 clean-HEAD extraction。原始人工观察文件、历史真实调用身份与已接受的父仓两项基线失败均保持前述边界；本次收尾没有新增真实模型请求或读取真实密钥。
