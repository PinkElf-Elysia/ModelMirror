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

R22.5 接手与验收边界：

- Astra 已按用户交接接任主协调；Sol 分别独占启动器、Host 轮换和 Godot UI/探针文件，主协调独占真实源资格和组合实现。不开启两个 Godot 会话。
- 新增 R22 live composition/launcher，只组合公开 R16/R15 精确预览、R20 内存协调器和持久化工具；不调用 `launchR20Bridge`，不监听 43120，不修改旧资格缓存。
- 初始/reset 等待对话；Godot 与 Host 双重限制每个已结束 Turn 最多一个命令。reset 先持久化新 R20 timeline，再释放旧 cognition store 并以同根共享预算打开新 store；失败冻结，不转回旧数据伪装成功。
- 实时模式显式区分 `offline-fake` 与 `official-once`。后者只有内容绑定的当次审批及持久化 dispatch 后才读取指定凭据，跨 reset 不重新武装一次请求门。实际 Godot import/probe、实时启动、物理行动、300 帧和人工观察分开记录。离线资格包的 `readyForPreview`/`r22GodotQualified` 不得因新启动器而翻为真。
- 本批包含四个模块接线点及对应精确 allowlist，超过五文件是已批准 R22.5 同一验收目标所需；不扩大冻结边界。回退 R22-only 新入口及本批 Host 选项即可，默认 R20 行为保持不变。

2026-09-05 实时取证接口修复：

- 一次人工 fake Turn 已产生真实 R19 accepted 记录与匹配的 Godot mirror；但当次物理报告未发布，不能据此声称 300 帧或 R22 完整资格通过。
- 根因为新取证器错误要求 `arrivalEvidence.sequence`。冻结 R20 实际只在 command 上记录 sequence，arrival 是六字段证明。修复仅适配该既有接口，不修改 R20 数据或降低校验。
- 单元夹具改为真实形状后先复现失败，再修复；组合测试直接读取 R20 coordinator 的实际导出并交叉验证 Receipt/Ledger。组合测试的物理 marker 和帧明确为 synthetic，不作为 Godot 运行证据。
- R22 在返程与 300 帧采样完成前保持 busy，防止新 Turn 覆盖旧采样；reset 清空未完成采样。失败保留静态 primary reason 和独立 cleanup failure，不记录原始进程输出。
- `verify:r22` 纳入 live/CLI/qualification/selector 回归与隔离 Godot import/probe，不再仅验证前四批。真实运行报告仍为 `unqualified-manual-observation`，直到全部资格与人工门完成。
- 第二次实测暴露跨语言数字格式差异：Godot 解析 wire command 后的 `sequence/ruleIndex` 是浮点 Variant，直接 stringify 会与 Node canonical 字节及完整 command hash 不同。R22 仅对这两个有界整数域在副本上严格校验并归一，不改原命令、Runtime 或 R20。中性 probe 必须从 JSON.parse_string 进入生产取证函数，并核对完整 marker 与 Node 预计算字节哈希；不再只测试手工构造的整数 Dictionary。
- 物理失败现在只披露固定校验阶段，不记录被拒绝的原始 marker 或异常正文。跨语言 probe 的帧与物理字段属于合成测试，不补写为既往实测成功。

2026-09-05 两案离线实测与恢复补强：

- 末班地铁和中性案例均已由用户操作 R22 原生对话入口完成一次假 Provider Turn、真实 Godot 移动、R19 accepted Action 和 mirror 复验。分别记录 39 ticks / 1900 mm、44 ticks / 2150 mm；两案 300 帧中位分别为 238.606、240.731 FPS。中性人物实际走回原位，位置误差为 0；末班地铁人物在节点切换后按既有可见性隐藏归位。
- 仓外观察报告分别保存在 `C:\tmp\matrix-oasis-r22-physical-80078d95c98791ffd69d56e9ed3042303d04cb41363d3e6555f15622cde618fd` 和 `C:\tmp\matrix-oasis-r22-physical-342b9f4b9a8c0d81b1eaf1b6f86dd7354420adf49bce9583902ce026c8684ac6`。观察 SHA-256 分别为 `b4517bc539ea8595449e605c7617889986db5ed897459ad8398e211c6ded3c77`、`c58aaa839a0024b4dd5bcd5a39eab1bb971c88e41abd7b2ab6c2a9c8618e3ea1`；源缓存复验未变化，两个测试窗口均正常退出。
- 上述属于已记录实现身份下的真实物理观察，不是后续修改的自动继承资格；报告保持 `unqualified-manual-observation`。真实模型请求、凭据读取均为 0。不得将离线 qualification 中的真实 Godot/可预览声明字段改为 true 来替代新增证据。
- `preview:r22` 新增与 `--run-root` 互斥的 `--resume-run-root`，只恢复同一实现、二进制、来源、Provider 模式和 Cognition Policy 身份下唯一未封存时间线。新的 `cognition-session-manifest.json` 将这些身份固定；旧根缺少该文件、缺失预算/checkpoint、分叉或存在活动 writer 时不推测修补。
- 恢复从初始 R19 Session 完整重放，并使用冻结 R20 公共 reset/command/arrived/mirror 接口恢复 reset 计数和行为状态，不复制第二套调度/裁决规则。历史与当前 R22 预算/Receipt 逐个复验；新 Godot 使用冻结 R20 recovery overlay 镜像已提交历史，不重走历史物理动作。
- 恢复不重新请求 Provider、不读取模型密钥。dispatch 后结果不确定时扣完整预留；已有合法终局 Receipt 保留原始 canonical 字节和预算。Action Receipt 必须由真实 Ledger 重建；非 Action 终局必须证明 Runtime 未改变。Receipt 中的供应商诊断与 usage 是脱敏调用观察，不是独立供应商签名证明，也不构成模型输出可复现声明。
- 只有持久化 display ACK、候选 Intent 身份和当前 R20 规则均重新匹配的待执行命令，才获得一次恢复启动许可；其余恢复停在 idle。`official-once` 根恢复后禁止新付费 Turn，不会重新武装一次请求上限。
- 可识别的单个 Receipt staging/target 发布窗口逐字核对后补齐；多 staging、未知文件、身份换身或 Ledger 不一致均失败。不会自动清理不明目录。Godot 启动前持久化专属 reservation/PID；发现活进程、PID 复用歧义、未知启动窗口时仅拒绝恢复，不终止残留或其他进程。正常退出只清除本次已验证记录。
- 独立 Node 子进程在真实 dispatch、Receipt rename 前后强制退出的组合回归，与合成到达证据的协议测试分开于真实 Godot 物理验收。它们不能替代后续真实 Luna、窄屏、降级体验、extraction 和完整人工验收。R22.7、push、PR 继续关闭。

2026-09-05 全量回归历史停点（后续授权修复见下节）：

- `npm ci --offline --no-audit --fund=false`、`npm prefix` 和 `npm ls --all` 均以 0 退出。未批准或执行 npm 新提示的依赖生命周期脚本，未修改 lockfile。
- R22 专项门完成 327 项测试及隔离 Godot import/probe。随后全量回归发现恢复启动许可的唯一环境变量读取未同步至 R22 Godot 审计白名单；只对固定变量、唯一读取和原 43122 路由放行，补充变量替换、额外读取与端口漂移反例。原 R20 Godot 桥完整重跑 29/29，通过实际 Godot import/probe。
- 第二次 `npm run verify` 在 `verify:r21` 以 1 退出：冻结的历史 verifier 强制当前元数据仍为 R21，与已迁移的 R22 version/boundary/claim 必然冲突。该文件相对固定基线未修改，不将此误报为 R21 投影算法故障，也不删除或跳过历史回归步骤。
- 分别运行原 R21 参考、合同和派生状态诊断：6/6、12/12、57 通过且 1 项仓外源测试按其原条件跳过；三个命令均以 0 退出。这些诊断不能替代失败的整体验证。
- 父 client 在仅含固定 HEAD 归档的独立目录离线安装后，两次既有测试均记录 131 个文件、916 项通过，后续 header 测试 1/1；build 日志记录 3172 modules 和正常完成，根代理重跑 typecheck 以 0 退出。父 client 相对固定基线零差异；未修改其源码或依赖。
- 暂停提交、extraction 收口及真实调用。下一步需用户精确授权历史 verifier 的治理迁移：仅修改 `scripts/verify-r21.mjs`、两处既有 allowlist 及 scope 回归；保留 R21 全部算法、合同、文档、证据和测试，保留当前 R22 scope/boundary/V2 claim 硬门。授权前不触碰该冻结文件。

2026-09-05 授权最小修复与 R22.5 本地检查点：

- 用户已明确批准四文件最小修复，并确认按“先本地、后收尾、最后 PR”推进。本阶段只形成可验证本地快照；不 push、不创建 PR、不执行 R22.7 声明切换。
- `scripts/verify-r21.mjs` 不再强制当前 package/boundary/claim 仍处于 R21，而是精确冻结 R21 的 29 项派生状态规则；当前轮次继续由真实 scope、boundary 和 V2 claim 检查。六个原始子验证、七组历史文档检查及所有原片段保留。两处 allowlist 仅新增该历史 verifier 的精确路径；R21 算法、合同、CLI、文档与历史证据仍冻结。
- 针对性检查先复现失败，修复后 7/7 通过，覆盖逐项策略漂移、缺失或未知字段、当前声明漂移、七组文档破坏及六个子步骤失败。独立只读复核未发现本次四文件修复的 P0/P1。此前恢复审计的 Action fallback/Ledger 冲突、零请求预算状态及 active stage 映射问题均已在当前代码修复并有回归测试。
- 实际 `npm run verify:r21` 以 0 退出。随后 `npm run verify` 以 0 退出并输出 `VERIFY_OK steps=31`；其中 R22 专项共 334 项，综合测试 941/941，Godot 4.6.3 import/probe、Creator build/smoke 通过。父 client 干净副本测试记录 131 个文件、916 项及 header 1/1 通过；主代理重跑 typecheck/build 均以 0 退出，保留既有大 chunk 警告。范围与 `git diff --check` 通过。这些是工作树检查结果，不冒充之后新 HEAD 的独立拆分资格。
- R21 gate 日志 SHA-256 为 `767dd75f0494a91a83a90d220cd8acb517eba136217449446b2b8c2903a7331f`；完整 verify 日志 SHA-256 为 `24a784cebfe24aded16ef7b790eeff38684cdba5628e1d8ed7807d414ce3ad80`。日志仅留 `C:\tmp`，不提交原始运行日志。
- R22.5 本地提交必须包含当前实时组合、进程恢复、资格 CLI、Godot 资源、治理接线及其直接依赖的测试闭包；不把被 `verify:r22` 或 CLI 直接引用的文件留作未跟踪依赖。新快照再做不带 `--allow-dirty` 的 extraction；现有 extraction 即使带该参数也只基于 HEAD，不包含未提交字节。
- Luna 真实性再次核对：两份真实 Godot 观察报告均为 `providerMode=offline-fake`、`realProviderRequests=0`、`sourceCredentialReads=0`。其 Receipt 中的 `requestCount=1` 是假 Provider 协议计数，不是官方请求或账单凭据。当前仍没有真实 Luna 资格；真实调用前必须展示当次完整 payload 并取得 1 次 / 0.01 美元的内容绑定批准，不自动消费此前泛化授权。
- 当前实现身份下的真实物理复验、窄屏与降级体验、一次官方 Luna、完整人工验收以及 R22.7 均保留待完成。旧观察结果不得跨实现身份继承；`V2_STATUS` 保持进行中且 `claimAllowed=false`，R16 MVP 与默认 Creator 未改动。

已完成的协作交接事项（保留来源）：

- 当前已开始的实现与证伪批次继续由Sol收口，避免在事务宿主、资格证据和实时Godot启动链仍有并行改动时中途更换主模型。
- 本批次通过聚焦测试并形成干净交接点后，输出一份可直接交给Astra的完整接手提示词，列明基线、分支、worktree、提交、未提交Diff、已验证证据、未通过硬门、付费调用边界、文件所有权和下一步命令。
- 后续采用Astra主导：负责跨R20至R22的实时链整合、异常决策、针对性证伪、真实调用前payload审查与最终验收；Sol子智能体只承担边界清楚、结果可独立检查的实现、测试、证据整理和文档任务。
- 禁止两个智能体同时修改同一文件或操作同一Godot/浏览器会话；主智能体负责分配文件所有权、复核子任务结果并执行最终集成验证。
