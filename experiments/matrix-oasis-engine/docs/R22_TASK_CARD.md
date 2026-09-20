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

2026-09-05 官方直连凭据入口补充（用户单独授权）：

- 用户提供仓外官方密钥文件并批准脱敏权限查询；查询返回 HTTP 200、目标模型可见，推理请求仍为 0。此查询只证明鉴权/模型可见性，不证明生成成功、余额或 R22 资格通过。用户随后明确选择恢复官方直连，本次不增加 OpenRouter Provider 或修改 Call Plan/Receipt 合同。
- `preview:r22` 可在新 `official-once` 会话显式附加 `--credential-file <C:\\tmp直接子文件>`。未提供时仍只使用既有 `MATRIX_OASIS_R22_OPENAI_API_KEY` 环境变量。文件必须为普通、非链接、1–8192 bytes 的单行官方密钥；不解析 `.env`，不复制到仓库，不记录原值、路径或密钥 hash。
- 准备只固定路径与文件身份，不读取内容；仅在内容审批、预算预留和 dispatch 持久化后，经一次性读取门读取并复验 FileHandle/realpath/bigint 身份。文件缺失、换身、修改或格式非法均降级，不改用环境变量重试；恢复模式、假 Provider 禁止附加此入口。Godot 环境、argv、overlay、Call Plan、Receipt 和证据均不携带该路径或密钥。
- 这项单一安全接入目标需要同时修改凭据读取、CLI、组合接线、三份对应测试及本任务卡；不调整模型、端点、价格、Schema、R19/R20/R21 或 Godot 产品场景。回退本项可选入口恢复环境变量方式，已记录时间线不变。所有自动测试只使用假密钥，正式调用仍须在 Godot 查看完整当次 payload 后批准，最多 1 次 / 0.01 美元、零重试。
- 补丁经独立只读审查后增加 `nlink===1n` 硬门，拒绝准备前及准备后新增的硬链接；有界读取最多分配 8193 bytes，临时 Buffer 在退出时清零。`npm run verify:r22` 以 0 退出：349 项中 348 通过，1 项 Windows 文件符号链接权限限制跳过；junction 与真实 NTFS hardlink 反例通过，Godot 4.6.3 import/probe、scope/boundary/V2 claim 通过。日志 SHA-256 为 `868218bfe1f901ad91283c375a69081bd243587cff3f98fbab7ab06c7025ad0d`。三案例新离线资格报告 SHA-256 为 `b002e05a086a3483151bd160910b9525e3d89ef8e56f8636f3640b25e3ee4c34`，保持 `readyForPreview=false`；这些结果不代替真实 Luna 请求或新实现的人工验收。

2026-09-05 官方 4xx 诊断修复（用户批准修复后重新调用）：

- 旧 official-once Turn 实际发出 1 次请求、零重试，返回 `PROVIDER_REFUSED`，没有有效 Proposal 或 AI Action。旧 Receipt SHA-256 为 `aa61920447066a4d07a8f7d8ec4ae1dc95388fd930f971f32c3ff5689afcb469`；随后发生的是固定策略降级，不能算 Luna 成功。10000 microusd 是不确定费用的保守本地占账，不是供应商实际账单。
- 已证实缺口是所有 HTTP 4xx 被折叠且错误体立即丢弃。修复仅补充有界、有限枚举的 HTTP status/type/code/parameter 诊断；不保留 message、response ID、原始错误、正文、凭据或任意供应商字符串。旧 Call Plan、Receipt、预算、fallback 与一次调用语义保持不变。
- 本目标包含 Provider、对应类型和测试、live 输出接线、对应测试及任务卡共六文件，超过五文件是为同一安全诊断目标保持接口与回归完整。新增诊断不是权威或资格合同，只在宿主输出内容绑定的脱敏 marker。回退此补充不更改旧持久化数据。
- 先用 400/401/403/404/429/5xx、模型 refusal、恶意字段、重复键、UTF-8、超限、超时、并发和 20 次一致性假响应证伪，再运行 `verify:r22`。自动测试不读取真实密钥、不联网；既有失败证据只读保留。缺少具体 HTTP 原因时不得把 Schema、权限或额度假设写成定因，不以猜测放宽严格校验。
- 独立审查发现并修复诊断输出影响原失败结果的风险；同步 throw、异步 write error 和 backpressure 以无凭据、10 秒 / 16 KiB 有界子进程注入，证明仍只请求一次、返回原结果、不重试，诊断不会改变 Receipt 收口。首次补测误触 Node 测试器自身 stdout IPC，已改为隔离注入；另一次完整回归仅因旧预览占用 43122 失败，关闭经身份复核的本任务旧进程后原命令重跑，未放宽测试。
- 最终 `npm run verify:r22` 退出 0：359 项中 358 通过，1 项仅因 Windows 文件 symlink 权限跳过；真实 junction/hardlink、Godot 4.6.3 import/probe、scope、boundary 和 V2 声明门通过。仓外完整日志 SHA-256 为 `1ecf4b503b9547f5876fe37746816894ce225934a0b615a86ad04a839f5fa1e2`。三案例新离线资格报告 SHA-256 为 `b002e05a086a3483151bd160910b9525e3d89ef8e56f8636f3640b25e3ee4c34`，保持 `readyForPreview=false`；不冒充真实模型或物理 Action 成功。
- 当日经 OpenAI Docs / Context7 核查 Responses 文档，并重新读取[官方价格](https://developers.openai.com/api/docs/pricing)：普通输入 / 缓存读 / 缓存写 / 输出仍分别为每百万 token $0.20 / $0.02 / $0.25 / $1.20，与锁一致；[数据控制](https://developers.openai.com/api/docs/guides/your-data)仍不允许把 `store:false` 宣称为 ZDR。未证实请求参数定因，未更改 Schema、usage 校验或价格锁。
- 重调仍须展示新的完整外发 JSON 并经内容绑定批准，仅 1 次 / 0.01 美元、零重试；不复用已消费 Call Plan，不执行 R22.7、不提交 PR。

2026-09-05 真实 `invalid_json_schema` 后的最小修复：

- 新一次官方请求的脱敏诊断已定位为 HTTP 400 / `invalid_request_error` / `invalid_json_schema` / `text.format.schema`，绑定 Call Plan `19725fc337c5bb215457125538213a9d3c60e4ef06d7ed54492c0b17b59f0c5d`。Receipt SHA-256 为 `9f0fd517c2359666ff1affa07b49d2f59e638d58b46d6ef723b071bd6d43a190`；合同、审批 hash、dispatch、唯一 Turn 与 requestCount=1 均复验通过。无 Proposal、无 AI Intent，Receipt 的 Ledger 前后不变；300 帧约 239.291 FPS 的后续动作仍是固定策略降级，不是 AI 资格成功。10000 microusd 仍是本地不确定费用占账，未获得供应商账单。
- 对照[官方 Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas)的显式类型与 nullable enum 示例，发现生成器与适配器共同使用了缺 `type` 的 `const/enum` 叶节点，旧假响应未检查这一服务端格式边界。仅将 `contextSha256` 补为 `type:string + const`，`actionChoiceId` 补为 `type:[string,null] + enum`；保留 null、候选顺序、全部 required、additionalProperties=false、对白 min/max 及本地 UTF-8/字节校验。未改变 Prompt、价格、模型、权限或 Godot。
- 本目标涉及两份生产文件、三份对应测试和本任务卡共六文件，超过五文件仅为生成、发送守卫和实际入口回归闭合。先执行新断言复现旧问题：旧 untyped 模板可到达 fake fetch，typed 模板却被本地拒绝；再做两处类型补全与 Provider exactKeys/type 收紧。针对 0/1/64 候选、九类重签类型攻击、Schema/payload/hash 一致性、旧审批不得复用和原有注入/预算/零重试门进行离线验证；未放宽任何输出语义门。
- 修复经独立只读复核。最终 `npm run verify:r22` 退出 0，374 项中 373 通过，1 项仅因 Windows 文件 symlink 权限跳过；Godot 4.6.3 import/probe、scope/boundary/V2 claim 均通过。完整仓外日志 SHA-256 为 `b80d675219a90e287e6a527ddb26845c4b41dd8effcc0d364a86ca12467dc77e`；同一中性、末班地铁、合成双 actor 源重新生成的离线资格报告 SHA-256 为 `95a0129b27847b18bc0137148fc37c3ce051d25861dbcf0011872d3c3bdfd19d`，仍为 `readyForPreview=false`。旧失败 Receipt 在回归后 hash 不变。
- 原官方审批已经消费；不重写旧 Call Plan、Receipt、预算或已发布证据。此修复会改变 Schema、payload 和 approval 身份，须新建资格产物并在下一次请求前重新展示完整 payload、取得独立批准。离线通过不证明官方服务已接受修复；R22.7 仍未完成。

2026-09-07 恢复复核 typed-schema 实测与响应阶段诊断：

- `matrix-oasis-r22-luna-typed-schema-20260905-a` 的一次批准已经消费：Call Plan SHA-256 为 `d424569a24bd6617b7541509b62d3c9e74536a89ea3d6a1592740e8fc1af7793`，Receipt SHA-256 为 `cf8b3a89bfe53dcf39c99baf8009378bf180dce6104931d13ac2e15e70862e8e`。合同、canonical 字节、approval、dispatch、唯一请求和零重试交叉复验通过；结果是 `PROVIDER_RESPONSE_INVALID`，returnedModel 为 null，无合法 Proposal/Intent，认知 Receipt 的 Ledger 前后相同。10000 microusd 仅为费用不确定时的本地保守占账，不是已核对供应商账单。
- 真实物理观察 SHA-256 为 `32e02a499cff3586c4c9db6ce41289db4df7e9eabd0bd0e4888ccc16e521ae93`，300 帧中位为 237.247 FPS；`cognitionActionVerified=false`，后续动作来自固定策略降级。该观察不证明模型生成、AI Action 或 R22 人工资格成功。
- 已证实的排障缺口是响应 body/JSON/root/echo 等失败没有保存可区分的校验阶段。原始响应已按隐私边界丢弃，旧回执不能证明本次 HTTP 状态或具体失败字段。Astra 与 Sol 只读复核均未获得 `description:null` 或 `phase:null` 的充分一手合法性证据，因此不将其假设写为根因，不修改对应接受条件或其他未知字段门。
- 本次只增加 Provider 可选 `responseDiagnostic`，内容限 HTTP 200 与内部固定阶段枚举；live 输出以 Call Plan SHA 绑定。非 2xx HTTP 诊断、旧 fallback、请求/重试/预算、Schema、模型、payload 和成功路径保持不变。诊断不复制响应字段名、值、正文/hash、ID、对白或异常，不进入 Receipt、预算、checkpoint、Ledger 或派生状态。输出仍使用原有不阻塞、失败不影响调用结果的观察通道；不是资格或裁决依据。
- 修改范围为 Provider 实现/类型/测试、live 诊断接线/测试、Host 持久化不变性测试及本任务卡共七文件，均属于单一诊断目标；不改 Host 生产逻辑、R19/R20/R21、Godot 或 Creator。先新增反例确认原实现缺失阶段，再补观察字段。覆盖 17 个阶段、原文诱饵、20 次确定性、零重试、成功结果无诊断和无凭据假 Provider。Host 比较中先发现夹具进程 epoch 未锁定，只通过既有测试 seam 固定该随机量，未修改生产令牌；三种诊断输入下全部持久化文件字节完全相同。
- 最终 `npm run verify:r22` 退出 0：379 项中 378 通过、0 失败，1 项仅因 Windows 文件 symlink 权限跳过；新增 Host 持久化字节对照、Godot 4.6.3 import/probe、scope/boundary 和 V2 声明门均通过。仓外完整日志 SHA-256 为 `e549e1b0438f5808817621dab64f75c6ef4eb0189bb448d809a9671ca42f1cc8`。三案例向全新目录执行离线资格成功，报告 SHA-256 仍为 `95a0129b27847b18bc0137148fc37c3ce051d25861dbcf0011872d3c3bdfd19d`，与本次诊断修复前完全相同；`readyForPreview=false`，不冒充真实模型或物理 Action 资格。
- 当前修复仍不证明真实响应已可被接受；本轮恢复没有读取真实密钥或新增模型请求。原失败证据只读保留，不翻转资格字段、不提交、不执行 R22.7 或 PR。后续真实验证必须新建独立运行根、展示完整 payload 并重新取得当次一次请求 / 0.01 美元批准，不能复用本次已经消费的审批。

2026-09-07 `root_fields` 定位与最小补测（离线通过，真实具体字段未定）：

- 用户授权定位、精准完善和补测；本批不包含新的付费请求、密钥读取、重试、提交、PR 或 R22.7 状态切换。当前 HEAD 为 `9adb5493501a954360edf6525542ffface55653e`，保留开工前十三项未提交修改及全部既有资格目录。
- 最新单次官方 Call Plan SHA-256 为 `6d87e8e63ca454a76230c21843322fd3ef459490b67ce5a7681d8bdc74b1cd8d`，Receipt SHA-256 为 `8a73737b02c3fc38eb9dc14521ac26c5c4b71672109a8b5513e48144b75877cf`。已观测 HTTP 200 / `root_fields`；审批、dispatch、1 请求 / 0 重试和 Receipt 前后 Ledger 相同已复验。没有合法 Proposal/Intent，239.005 FPS / 300 帧观察仍为固定策略降级；10000 microusd 是不确定费用的保守占账，不是供应商实际账单。
- 根因证据边界：原生 JSON 解析后，`root_fields` 可由非对象/数组、缺少固定必需键或存在未知根键触发。旧响应已丢弃，不能识别本次具体键，也不能把后续 message/usage 阶段假设归为该次失败。[官方文本指南](https://developers.openai.com/api/docs/guides/text)将 `output_text` 说明为部分 SDK 的便利属性；CLI/SDK 示例不足以证明本次原生 HTTP body 含该字段，因此不扩大根键 allowlist。合法 JSON `null` 继续保持原 `json` 阶段诊断。
- 本批唯一目标是补全根结构诊断：固定 `rootKind` 枚举、八个已知必需键的 `missingRequiredMask`、`unknownKeysPresent` 布尔值。不记录未知键名/数量/长度/hash、字段值、正文、response ID 或异常；诊断仍只在内存及内容绑定的 stdout marker 中观察，不参与接受、预算、Receipt、Ledger 或资格判断。
- 诊断补丁范围精确为 Provider 实现、对应类型和测试、live 输出测试、Host 持久化不变性测试及本任务卡共六文件；超过五文件是同一诊断接口需要同时覆盖类型、生产解析、输出通道和权威持久化边界。完整回归另外暴露并最小修复一个既有 R22 组合测试等待器，合计七个文件，根因与反例见下文。不修改通用 `captureRecord`、发送参数、响应 allowlist、Runtime/Host 生产逻辑、R19–R21、Godot、Creator 或依赖。
- 最小验收先以根形状、八个单键及组合缺失、未知键投毒和排序/数量变化反例复现诊断缺口，再运行 Provider/live/Host 聚焦测试及 `npm run verify:r22`。三案例新离线资格须保持全部 canonical artifact 字节不变；既有真实回执 hash 不变。风险限于错误诊断泄漏或干扰拒绝路径，回退本批诊断与对应测试即可；不修改历史产物。
- 新增根结构反例先在旧实现复现 2 项失败；最小补丁后聚焦 Provider/live/Host 原命令重跑为 154 通过、0 失败、1 项 Windows 文件 symlink 权限跳过。真实 junction/hardlink、未知根键名/数量/排序不泄漏、stdout throw/异步 error/backpressure、一次请求和持久化字节不变性均通过。独立只读复核未发现该增量的具体缺陷；这不是旧真实响应兼容性证明。
- 环境失败如实保留：第一次聚焦组仅因本任务旧实测预览占用 43122 失败；复核 Godot PID、父进程及精确运行根后只请求该窗口正常关闭，退出 0、无 cleanup failure，再重跑原命令。第一次完整 `verify:r22` 因新 shell 未设置 `GODOT_BIN` 停在 `GODOT_4_6_3_NOT_AVAILABLE`；未改断言或代码，给当前测试进程指定已批准的官方 4.6.3 后，`doctor:godot`、Godot 19/19 和实际 import/probe 均通过；完整原门禁结果见下文。
- 第二次完整门禁发现组合夹具 `settled()` 把合法异步完成错误限定为 200 次 / 5 ms、名义约 1 秒，而产品 Provider 期限是 30 秒。仅给本地 fake fetch 增加 1.5 秒合法延迟，在旧 helper 上稳定复现同一失败；不是通过重跑推定环境抖动。只修改 `tests/r22-live-composition.test.mjs` 的等待器，以单调时钟观察既有公共 status 接口、固定 30 秒总期限 / 25 ms 轮询，未暴露私有 Promise、阻塞 approve 或通过 close 强制终局。保留该延迟及全部一次请求、审批前零读取和隐私断言；新增虚拟时钟的 5 秒成功、永久 dispatching 超时、29999/30000 ms 边界、fallback 与路由异常不提升反例。该文件 3/3 通过，独立只读复核通过；未修改生产超时、持久化或清理路径。
- 中性、末班地铁及合成双 actor 向 `C:\tmp\matrix-oasis-r22-root-structure-offline-20260907-a` 重新执行离线资格成功。八个产物与前一离线资格目录逐文件 Buffer 字节相同；报告 SHA-256 仍为 `95a0129b27847b18bc0137148fc37c3ce051d25861dbcf0011872d3c3bdfd19d`，真实 Godot/物理资格字段保持 false、CLI 仍为 `readyForPreview=false`。最新失败 Receipt、R20 current 和 R21 Bundle 的已记录 hash 不变；父范围及 `git diff --check` 通过。
- 最终完整原命令 `npm run verify:r22` 退出 0：386 项中 385 通过、0 失败、1 项仅因 Windows 文件 symlink 权限跳过。分组为治理 95、实时/CLI 120、参考 4、合同 14、Runtime 13、Provider 50、Host 71、Godot 19；真实 Godot 4.6.3 import/probe、round-scope、boundary、V2 claim 均通过。完整日志仅位于 `C:\tmp\matrix-oasis-r22-root-structure-verify-20260907-c.log`，SHA-256 为 `741d9d569276c3b49f08803499bfff043a7a9f4dea9bca24224867cd84ffe323`。此前失败日志保留，不用本次通过倒改历史结果。
- 当前状态：诊断精度和测试等待器的最小修复已获离线验证；真实响应具体字段仍未证实、未宣称兼容问题解决。本批真实模型请求与真实密钥读取均为 0，未提交或发布；后续请求仍须重新展示完整 payload 并取得当次独立批准。本批未重新执行跨 R1–R22 的完整 `verify`、extraction 或父 client build，不以本批专项结果替代后续收尾门。R22.7、真实 AI Action 及完整人工资格继续未通过。

2026-09-07 未知根字段名称与类型诊断（用户单独授权，离线补测）：

- 最近一次真实复测 Call Plan SHA-256 为 `d86982653f593a7fedf44526a5695c6485a11c91fb6b4a709171cda2afc65952`，Receipt 为 `ddec04f1a94d321e78c4c827ea4cd517e292a426d01971ef731fe6447ce12847`。只执行一次官方请求，HTTP 200 后在 `root_fields` 拒绝；固定必需键缺失 mask 为 0、未知键存在为 true。没有合法 Proposal、AI Intent 或 AI Ledger 追加；238.322 FPS / 300 帧只证明固定策略降级的物理观察，不是 AI 行动成功。10000 microusd 仍为费用不确定的保守占账，非已核对实际账单。
- 原始响应已按隐私约束丢弃，不能从旧回执恢复未知键名。用户本次单独批准增加受限原始字段名及浅层 JSON 类型诊断；此授权不包含再次调用模型或读取真实密钥。不因未知字段存在就扩展接受 allowlist，也不推测具体字段、字段值或后续拒绝阶段。
- 仅在既有 `root_fields` 拒绝且 `unknownKeysPresent=true` 时新增 `unknownFields` 和 `unknownFieldsOmitted`：最多 16 个名称，按 ASCII 代码单元排序，每个名称匹配 `^[a-z][a-z0-9_]{0,63}$`；类型固定为 null/array/object/string/number/boolean。非法名或超限项只标记省略，不记录省略数量或截断名称。仅从 own data descriptor 判定类型，不触发 getter、不递归数组或对象、不复制值、嵌套键、长度、正文 hash、ID、对白、异常或密钥。
- 此处明确收窄此前“不记录任何未知字段名”的历史诊断限制：简单字段名本身仍是受用户授权、Provider 控制的有限文本，不能称为匿名化或纯固定枚举。该例外只存在于内存诊断及 Call Plan hash 绑定的受限 stdout marker，不进入 Receipt、Ledger、checkpoint、预算、R21 或资格结论；其余内容保密限制不变。
- 精确修改六文件：Provider 实现/类型/测试、live 输出测试、Host 持久化对照测试及本任务卡。超过五文件仅因同一诊断接口需同时证明类型、解析、观察通道及权威持久化边界；不修改 live/Host 生产逻辑、合同、允许字段集合、请求、Schema、model、价格、重试、预算、Runtime、Godot、Creator 或依赖。风险为字段名泄露或观察干扰拒绝，回退本批诊断增量即可，历史回执保持不变。
- 先加入反例，在旧实现稳定得到 2 项失败；补丁后 Provider 52/52 通过。覆盖六种 JSON 类型、16/17/32 个字段、64/65 字符、非 ASCII/控制字符/路径/邮箱/原型键、转义与重复键、20 次乱序输入确定性、深冻结和拒绝不变。后续 live/Host 与完整 R22 原命令的结果在本批验收记录中追加；当前不提前宣称整体通过。
- 聚焦 Provider/live/Host 158 项中 157 通过、0 失败、1 项 Windows 文件 symlink 权限跳过；Host 中无诊断、合法名称诊断及恶意诊断产生全部字节相同的持久化文件。独立只读审查未发现本补丁可行动缺陷。第一遍完整 R22 门禁的全部测试及 Godot import/probe 已通过，但源码边界识别到新增拒绝夹具中的绝对路径和外部 URL 字面量；未发生真实网络执行。仅将两个合成负例替换为独立冒号、斜杠及反斜杠拒绝用例，不改安全扫描或断言，原失败日志保留，随后重跑原门禁。
- 最终原命令 `npm run verify:r22` 退出 0：389 项中 388 通过、0 失败、1 项 Windows 文件 symlink 权限跳过。Godot 4.6.3 实际 import/probe、round-scope、boundary、V2 声明门全部通过；完整日志位于 `C:\tmp\matrix-oasis-r22-root-name-type-verify-20260907-b.log`，SHA-256 为 `ceecedaaedb69174b07d6bd21810966e4035ba116da60b46d4f0ae4f63a5a84d`。第一遍门禁失败日志未覆盖，未修改或放宽源码边界。
- 三案例向全新 `C:\tmp\matrix-oasis-r22-root-name-type-offline-20260907-a` 发布离线资格；8 个产物与上一目录逐文件 Buffer 字节一致，报告 hash 仍为 `95a0129b27847b18bc0137148fc37c3ce051d25861dbcf0011872d3c3bdfd19d`，`readyForPreview=false`。三份 R20 current、R21 Bundle 及上次真实 Receipt 均通过原 hash 复验。本次离线命令曾因自动权限审查超时未启动，按工具许可重试一次后成功；不是供应商重试。
- 已核对并正常关闭本任务旧 Godot 窗口，退出 0、`cleanupFailure=null`；最终 43120/43122 无监听、旧 Godot/Node PID 不存在。父范围检查与 `git diff --check` 通过，十三项开工前未提交工作全部保留，staged 为空。本批没有真实模型请求或真实凭据读取，没有 commit/push/PR，不执行 R22.7。跨 R1–R22 完整 `verify`、extraction 及父 client build 留待收尾，不能以本轮专项门禁替代。
- 下一步仍需对新的实际 payload 取得单次独立批准，方可用本诊断观察真实未知字段；不得复用已消费审批、追认原始响应或将当前离线通过宣称为真实兼容修复成功。任何允许字段调整仍须依据真实名称/类型及官方合同另行审查，不能由本诊断自动决定。

2026-09-08 三个已观测根字段的窄兼容修复（离线通过，真实兼容待验证）：

- 开工点仍为 `9adb5493501a954360edf6525542ffface55653e` / R22 分支，保留十三项既有脏文件。本次只修改 Provider 实现、Provider 测试、live Provider 测试及本任务卡四文件；不改公开合同、类型、请求或 Schema、模型与价格锁、usage/预算、Host、Runtime、Godot、Creator、依赖或旧证据。
- 最新真实 Call Plan hash 为 `398afe61bc882c906058e7ee09b7694db79a1180187de0fa8ce1ab14c202352a`，Receipt hash 为 `11976c85cd2925e8c44ec994ed990c4d355f9f04e9f0e8671990de88be23be59`：HTTP 200、1 请求、0 重试，在 `root_fields` 拒绝；三个未知键仅观测到 `frequency_penalty:number`、`presence_penalty:number`、`tool_usage:object`，值未保存。没有有效 Proposal/Intent，认知 Receipt 前后 Ledger 不变；118.525 FPS / 300 帧属于固定策略降级。10000 microusd 是不确定费用保守占账，不是实际账单。
- OpenAI Docs 的 Responses endpoint spec 与 Context7 本次查询仍未给出三个根字段的精确定义；[官方工具指南](https://developers.openai.com/api/docs/guides/tools)说明工具需显式配置，但不能据此发明 `tool_usage` 结构。本补丁只建立本地窄扩展，不宣称是完整官方 Schema：两个 penalty 可选且仅接受 JSON 解析后的数值正零（`Object.is(value, 0)`，拒绝负零），`tool_usage` 可选且只接受无 own key 的普通空 JSON 对象。`0.0`/`0e0` 解析后等同正零，不宣称词法只准字符 `0`。
- 非零、负零、错误类型、非空 `tool_usage` 和其他未知根字段全部拒绝；空对象不是供应商未使用工具的独立证明。原有无工具请求、空 tools echo、工具输出拒绝、严格 usage/费用、model、completion、对白/choice 全部继续检查。兼容字段不得发送、持久化或复制进 Proposal/Receipt，非法值只使用既有静态 `root_echo` 诊断，不扩展原文观察范围。
- 先加入离线反例复现旧拒绝，再最小修改解析器；验证命令为 Provider 单测、Provider/live/Host 聚焦测试及完整 `npm run verify:r22`，另复验源缓存及旧 Receipt hash。风险为错误接受新元数据或绕过后续安全门；回退仅本次三键 allowlist/严格 echo 检查和对应测试，不清理历史。已核对并正常关闭本任务旧预览，退出 0、`cleanupFailure=null`，不影响其他服务。
- 用户本次只批准本地处理；不读真实密钥、不调用真实模型、不提交、不 push/PR、不执行 R22.7。旧真实字段值不可恢复，离线成功也不能证明下一次真实响应必然通过；如实际值不符合窄规则则继续失败，不能猜测或自动放宽。真实复测仍需展示新的完整 payload 并取得当次单独批准。
- 新反例在补丁前确认旧解析器于 `root_fields` 拒绝；补丁后四项定向测试通过。覆盖八种字段组合各二十次、顺序与数值正零表示差异、负零/类型/非空对象、重复键和与工具/usage/费用/model/Proposal/choice 的组合攻击。成功结果保留真实 `responseBytes` 差异，其余结果与无扩展基线一致；请求、审批及一次消费未改变。聚焦 Provider/live/Host 共 162 项，161 通过、0 失败、1 项 Windows 文件 symlink 权限跳过；junction/hardlink 反例通过。独立 Sol 只读复核未发现本增量的可行动缺陷，不代替实际运行证据。
- 完整 `npm run verify:r22` 退出 0：393 项中 392 通过、0 失败、1 项仅因 Windows 文件 symlink 权限跳过。分组为治理 95、实时/CLI 122、参考 4、合同 14、Runtime 13、Provider 55、Host 71、Godot 19；官方 Godot 4.6.3 import/probe、round-scope、boundary、V2 claim 均通过。日志只保存在 `C:\tmp\matrix-oasis-r22-neutral-extension-verify-20260908-a.log`，SHA-256 为 `19c52b78a1ca4ac734dabe11ebc26519a50ab6289a480fa42b7960611d5d8c6c`。
- 三案例向 `C:\tmp\matrix-oasis-r22-neutral-extension-offline-20260908-a` 重新执行离线资格；八个文件与前一目录逐字节相同，报告 SHA-256 仍为 `95a0129b27847b18bc0137148fc37c3ce051d25861dbcf0011872d3c3bdfd19d`。`readyForPreview` 及真实 Godot/物理资格字段仍为 false；不将假 Provider 结果替代真实 AI 行动。三份 R20 current、三份 R21 Bundle 和上述旧失败 Receipt hash 均未改变；九个其他既有脏文件也与开工 hash 相同。父范围与 `git diff --check` 通过，43120/43122 无遗留监听，已关闭的本任务旧 Godot/Node 进程不再存在。
- 本次完成的是四文件窄兼容及离线证伪，不是 R22 收尾或真实模型资格；没有新增真实请求或真实密钥读取，未提交/发布。本次未重跑跨 R1–R22 的完整 `verify`、extraction 或父 client clean build，后续收尾仍须执行。R22.7、一次成功真实 AI Action 和完整人工验收继续待完成。

2026-09-12 响应兼容、逐规则诊断与 Standard 锁定（本地门禁通过，真实兼容待验证）：

- 开工仍为 R22 分支 / `9adb5493501a954360edf6525542ffface55653e`，保留十三项既有未提交工作。上一真实请求只定位到 `root_echo`；原值未保留，不能认定某个具体 nullable 字段就是该次失败原因。本次不读取真实凭据、不发起模型请求、不提交或发布。
- 当日通过 OpenAI Docs / Context7 和[官方 Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/create)复核：`metadata` 可为 null，output message 的 `phase` 可缺省/null/final_answer，JSON Schema format 可含可选 string description。仅兼容这三种形态；description 限合法 NFC 纯文本和 2048 UTF-8 bytes，不接受 null，commentary 仍拒绝。instructions 数组尚未建立严格等价规则，继续拒绝；不改工具、Schema、choice、model 或 usage 安全门。
- 用户另行明确批准新请求固定 `service_tier:"default"`，修订原计划省略该参数的约定。生成器和发送守卫同步强制 Standard；实际响应档位必须为 default，否则失败并按费用不确定处理，不能按 Standard 冒算其他档位账单。新 payload/approval 身份必须重新生成，旧审批不可复用。响应校验不能撤销已发生的供应商费用，实际调用仍需新内容绑定批准。
- 分两组小步实施同一安全修复：Provider 实现/类型/原测试及独立矩阵；请求生成/测试、live 组合测试、Host 诊断隔离测试及本记录。超过五文件仅为跨生成器、发送守卫和观察通道的接口/回归闭合；不改 R16/R19/R20/R21、Godot、Creator、生产依赖或历史证据。Astra 独占解析整合，Sol 分别只写请求生成侧和新矩阵，避免并发编辑同一文件。
- 矩阵使用独立构造的合成响应，经公开 Provider 接口和注入 fetch 执行：正例、单项反例、多项组合、审批/预算/Schema/权限、20 次字节确定性及隐私诱饵。新增诊断限固定 rule ID 与 passed/failed/not_checked；保留第一拒绝优先级及保守费用证据，同时不让早期 root_echo 遮住后续可独立检查的失败。不能安全解析的依赖项标 not_checked，不复制原文、字段值、模型对白、ID 或正文 hash。
- 先记录新反例在旧实现失败，再执行 Provider 矩阵、Runtime、live/Host 和 `verify:r22`；真实请求数及凭据读取均为 0。风险为兼容放宽、诊断泄露、价格档位漂移或旧审批复用；回退仅本批 diff，新审批作废、旧回执保持只读。通过离线矩阵不等于真实 Luna / AI Action 资格，R22.7 不提前完成。
- 预算交叉审查进一步证伪了旧断言：即使 service_tier 为 default，异模型/非法模型名仍曾按 Luna 锁价记已知费用，Host 反例实际只占账 8 microusd。现仅增加模型身份与价格锁的耦合守卫：Provider 保留独立 usage 形状检查，但对不匹配模型向外返回 usage/cost=null、costUncertain=true；Host 同样防御性拒绝异模型已知计费，将未知 usage 归零并按全预留占账。该零值仅是既有未知 usage 哨兵，不能解读为实际零 token 或免费调用。拒绝码及正确模型的 refusal 精确费用不变，无需改既有合同。
- 第一次完整回归因两处离线 Provider-shaped 响应缺少 service_tier 而失败；只在 `r22-live-composition` 与 `r22-qualification-core` 的假响应构造中各补一个 default 字段，不修改真实执行、导航、资格证明或 display ACK 检查。原失败的三缓存与独立进程恢复测试重跑 12/12 通过；保留第一次失败日志，不将夹具修改冒充真实供应商证据。
- 独立 `response-matrix.test.mjs` 共 65 个 Node 测试条目（60 个场景/断言用例和 5 个父容器），与原 Provider 测试合计 125/125 通过。不是全部 65 项各跑 20 次；20 次字节一致性针对代表性成功、失败和根键乱序，既有八种中性字段组合亦各执行 20 次。矩阵如下，所有响应均由本地假请求函数返回：

  | 验证面 | 通过条件及证伪目标 |
  | --- | --- |
  | 窄兼容 | metadata 缺省/null/空对象；phase 缺省/null/final_answer；description 空串、NFC 和 2048 UTF-8 bytes 边界 |
  | 拒绝不放宽 | 非 Standard、非空 metadata、commentary、2049 bytes、控制字符、instructions 数组、Schema 改写、缺 usage、错误 model、工具及伪 choice 均失败 |
  | 全链路观察 | 固定 28 条规则逐项记录；多处独立错误全部可见；依赖无法安全解析时 not_checked；原第一拒绝码和费用策略仍为权威 |
  | 审批与预算 | 旧审批、新 payload 漂移或非 default 请求在发送前失败；异模型不按 Luna 计费；Host 伪造已知小额费用反例占账完整预留 |
  | 隐私与确定性 | 不复制诱饵凭据、正文、response/message ID；一次消费不重发；代表性输入 20 次 canonical 结果一致 |

- 同一聚焦命令曾在默认文件权限下报 C:\tmp EPERM，获权限后原命令复跑：Runtime/Host/live Provider 共 120 项，119 通过、0 失败、1 项 Windows 文件 symlink 权限跳过。后续完整门禁第二遍因未设置 GODOT_BIN 停报 Godot 不可用；按已锁定路径设置进程内变量后重跑，实际 import/probe 通过。第三遍在最后源码边界命中新增矩阵三个描述字符串内的网络函数单词；实际仅使用注入的假请求函数。只把该测试标题和两个异常字面量改为 dispatch/request，不改扫描器、allowlist、请求计数或拒绝断言。
- 三案例分别向全新 `C:\tmp\matrix-oasis-r22-response-matrix-offline-20260912-a` 和 `C:\tmp\matrix-oasis-r22-response-matrix-offline-20260912-b` 发布离线资格。八个产物两两逐字节相同，资格报告 SHA-256 为 `2ecb8665c3ac267aa43e34394cd59c017d69172297853f79f664290006ac6e8f`。每次仅 fake=3、real=0、replay=0；readyForPreview、r22GodotExecuted、r22GodotQualified、physicalMovementVerified 均保持 false。Godot import/probe 的单独成功不能升级该离线资格证据。
- 三份 R20 current 与三份 R21 Bundle 均与开工身份一致；上次失败真实 Receipt SHA-256 仍为 `3c8f25ab62f80bacaff92dfc0e1d777763f0fe97b6a1c0b2a130d4dc147d3747`。本次没有修改既有资格根或已消费审批，未读取真实凭据或发出真实模型请求。新的 Standard payload 需重新展示并取得一次独立批准，不能以本次修复授权代替付费调用审批。
- 最终原命令 `npm.cmd run verify:r22` 退出 0：463 项中 462 通过、0 失败、1 项 Windows 文件 symlink 权限跳过。分组为治理 95、实时/CLI 122、参考 4、合同 14、Runtime 13、Provider 125、Host 71、Godot 19。Godot 4.6.3 实际 import/probe、round-scope、boundary 和 V2 声明门全部通过；完整日志 `C:\tmp\matrix-oasis-r22-response-matrix-verify-20260912-d.log`，SHA-256 为 `bd4278b32cc65a0ae71c8acf1557acb194be98d9f6b4396e85086afb1c6d7edc`。a/b/c 失败日志保持原样，未放宽断言或安全扫描。
- `check:parent-scope -- --base e927db557f71db420e07a49818c0d4ae1e0d6ce3` 与 `git diff --check` 通过；Godot 产品目录、Creator、MVP/V2 状态无本批差异。最终 43120/43122 无监听、无本任务 Godot 子进程。HEAD 仍为开工提交，staged 为空，保留全部既有未提交工作；本批最终为 15 项 modified 加 1 项新增矩阵文件，其中 3 项既有脏文件本批未改动。
- 本批仅完成本地最小修复与矩阵验证，未 commit/push/PR、未执行真实模型调用或 R22.7。跨 R1–R22 完整 `verify`、extraction、父 client clean 测试/build 和成功的真实 AI Action 人工验收尚未重跑，不能以本轮专项门禁代替。下一次真实调用仍应使用新生成并逐项披露的 payload/approval 身份，失败不得自动重试。

2026-09-12 两项 echo 子规则诊断（仅观察，不改变接受范围）：

- 本次真实复测 b 的 Receipt SHA-256 为 `15b431cf880e420b896348d5230283680fd8b1c017ff4ddb7d31c9be92257eed`。一次官方 Standard 请求返回 HTTP 200，固定 28 条响应规则中仅 `echo_tool_usage`、`echo_text_format` 失败；调用未形成 AI Intent，认知 Receipt 的 Ledger 前后 revision 均为 0。随后一次 R20 固定策略降级移动及 R19 追加成功，但 `cognitionActionVerified=false`；不能用该物理成功冒充 AI Action。10000 microusd 是费用不确定的保守占账，不是已核对实际账单。
- 旧原始响应已丢弃，不能由失败名称推断为 null、非空 record 或 description 错误。用户“补足”仅授权本次脱敏诊断与离线补测，不授权新的真实请求或真实凭据读取。旧 Godot 预览已正常退出 0、无 cleanup failure，历史目录及回执保持原样。
- 安全 root 捕获后的失败分支新增 `echoDetails`：`toolUsage` 只输出固定 JSON 类型、record/empty-record 结果；`textFormat` 只输出 text/format/description 类型、required/allowed keys、type/name/strict、description 字符串/安全文本/UTF-8 上限结果。类型与规则状态均为闭合枚举，父级不通过 capture 时后代为 unavailable/not_checked，缺省字段与 null 不混淆。不输出新增键名、值、计数、长度、文本摘要、Schema、上下文、对白或异常。
- 原 28 条规则及其第一拒绝、Schema/usage/model/档位/预算校验完全保留；观察失败也不得覆盖原拒绝。成功响应不增加诊断。请求、prompt、payload、动态 Schema、Standard 锁和审批身份算法均未修改。新增细节仅经原有 Call Plan hash 绑定观察 marker 输出，不进入 Receipt、Ledger、预算、checkpoint 或 R21。
- 先在旧实现运行双失败合成反例，因缺少 `echoDetails` 稳定失败；补丁后响应矩阵 121/121、全部 Provider 181/181 通过。新增矩阵包含 tool_usage 10 种情况、text/format/description 41 种情况、20 次键顺序与未知字段数量变化字节一致，以及诊断构造故障；这些是假响应，不是已丢弃真实响应的复原。新增 live 传播测试起初缺少合法 usage 详情，按原校验被额外拒绝；只补齐假夹具的 usage 详情后原测试 1/1 通过，未改生产校验。
- 本批修改限 Provider 实现/类型/原测试、独立响应矩阵、live 传播测试及本任务卡六文件；六文件是同一观察接口的类型、拒绝兼容、隐私与实际输出验证闭包，不修改 Host/Runtime/Godot/Creator 或依赖。风险为诊断泄漏、误标或干扰原拒绝，回退本批观察增量及相应测试即可；不清理历史产物。独立只读复核和完整 R22 门禁结果另行追加，不提前宣称真实兼容或 R22.7 通过。
- 独立只读审查未发现本批可行动缺陷；其未重复执行测试，实际运行均由主智能体完成。原 Provider 全部 181/181 通过，live/Host 聚焦组 108 项中 107 通过、0 失败、1 项 Windows 文件 symlink 权限跳过。新的三案例离线资格目录为 `C:\tmp\matrix-oasis-r22-echo-details-offline-20260912-a`；8 个产物与补充前目录逐文件 Buffer 字节一致，报告 SHA-256 仍为 `2ecb8665c3ac267aa43e34394cd59c017d69172297853f79f664290006ac6e8f`。`readyForPreview` 及离线真实 Godot/物理资格字段保持 false。
- 最终 `npm.cmd run verify:r22` 退出 0：520 项中 519 通过、0 失败、1 项 Windows 文件 symlink 权限跳过。分组为治理 95、实时/CLI 123、参考 4、合同 14、Runtime 13、Provider 181、Host 71、Godot 19；实际 Godot 4.6.3 import/probe、round-scope、boundary 及 V2 声明门均通过。完整日志 `C:\tmp\matrix-oasis-r22-echo-details-verify-20260912-a.log`，SHA-256 为 `297aeb3d4c878030b2e6480ae7586d51ca7f6005839fa43fb9548e83a52e1266`。
- 旧真实 b Receipt、R20 current 及 R21 Bundle 仍符合已记录 hash；父范围检查 `PARENT_SCOPE_OK checked=99 changed=84`、`git diff --check` 通过。43120/43122 均无监听。HEAD 与 staged 未变，全部既有脏工作保留。本批真实请求、真实凭据读取、commit/push/PR 均为 0；跨 R1–R22 全量 `verify`、extraction、父 client clean 测试/build 及 R22.7 尚未收尾。下一次真实取证仍须展示完整实际 payload 并取得全新单次审批，不得复用 b 的已消费审批；旧响应的两个具体值仍然未知。

2026-09-12 响应契约三步整改开工（真实调用暂停）：

- 用户在真实复测 c 后确认失败次数已达到可接受上限，批准“字段依据与风险分类 → 响应适配整改 → 离线分层验证”；本批不再使用真实调用获取诊断，不读取凭据、不启动新的付费预览、不复用旧审批。
- 开工为 `codex/matrix-oasis-r22-bounded-cognition / 9adb5493501a954360edf6525542ffface55653e`，固定基线不变。保留 15 个既有 modified 文件与未跟踪响应矩阵，staged 为空。新增改动只在 R22 Provider、其测试和 R22 文档中；R16/R19/R20/R21、Godot、Creator、请求 payload/Prompt/Schema/价格锁均不改。
- c 的 Call Plan/Receipt 通过当前合同验证；Receipt SHA-256 为 `438b11f432ff988d2deedc3b0374504219d189555b5297901deaee47a28f2c59`。一次请求、零重试、HTTP 200，`description` 明确为 null，`tool_usage` 明确为非空普通对象，其内部键值没有保存。其余 26 项本地检查通过不等于 AI Action 成功；proposal/intent/adjudication hash 均为 null，认知回执的 Ledger revision 0→0。10000 microusd 为保守占账，不是发票金额。
- 验收分层：先用独立来源与已知形态建立反例，再运行 Provider 正反契约、Host/事务/恢复及三份固定缓存的离线链，最后运行 `verify:r22`、父范围与 diff 检查。普通测试通过、Godot import/probe、固定策略降级和真实认知成功分别记账；不能把测试“预期拒绝通过”改写成供应商兼容成功。
- 主要风险为将未知工具/计费数据当作无害、把请求 Schema 当作完整响应 Schema、扩大原文留存、放宽 Action 权限或借绿测再次付费。已证明无权威影响的兼容元数据可受限归一化后丢弃；未知能力/计费信号继续 fail closed。若 `tool_usage` 的真实结构或正式定义仍不能解释，明确停止在真实资格前，不声称三步已经消除该阻塞。
- 拆成三个可独立复核的小批：来源与任务记录；响应适配、类型与独立/回归测试；离线全链与结果记录。超过五文件仅为跨类型、诊断与独立测试的同一接口闭包，不扩大产品功能。Astra 独占生产整合；Sol 分别承担只读来源分类和单独的 `response-conformance.test.mjs`。回退只移除本批增量，不覆盖开工脏文件，不改旧缓存或已消费审批。

2026-09-12 响应契约三步整改本地结果（尚未重开实测）：

- 第一步完成：`R22_REFERENCE_AUDIT.md` 新增固定 OpenAPI/官方兼容性依据、42 个既有 root 字段及嵌套输出的逐项风险处理。区分供应商 Schema、R22 本地能力 profile 与真实 c 的仅形态观察；非空 `tool_usage` 没有规范或原始值证据，仍是明确的真实资格阻塞。
- 第二步完成：`description:null` 受限归一化并丢弃；模型结果依旧只有三键。新增 `matrix-oasis.responses-envelope/1`，对 19 项原来未充分校验的能力、身份与元数据执行显式策略，不新增接受的 root 名称。独立 Sol 先用 17 个被旧实现错误接受的反例复现；整改后均被拒绝。Prompt、payload、Schema、模型、Standard 档位、价格、调用次数与审批算法不改。
- 独立复核另抓到新增字段抢占旧拒绝优先级的问题。补 10 个交叉反例（id/billing × usage/model/completion/refusal/choice）先得到 RED，再修正为旧规则先选主码/阶段、新字段补充固定详情；存在未核清字段时仍保守占满预留，不能用已知语言 token 价格冲销未知费用。`effort:none` 没有被错误扩大成“服务内部 reasoning token 必为零”的保证，原 output 总价与禁止 reasoning 输出项门保留。
- 第三步已执行，结果分层：Provider 原测试、响应矩阵、独立 conformance **291/291 通过**（其中独立 conformance 110/110）；实时 one-shot 三种新增场景覆盖 null 归一化成功、未知 tool_usage 拒绝、未知 agent 拒绝的实际传播，均保持一次派发、消费后不重试及脱敏。独立静态复核未再发现本批可行动缺陷；该复核不替代主智能体实际测试。
- 三份固定 source/R21 缓存从同一 Host/Controller 假 Provider 链完成资格，新目录 `C:\tmp\matrix-oasis-r22-response-profile-offline-20260912-a`。8 个输出与 `matrix-oasis-r22-echo-details-offline-20260912-a` 逐文件 Buffer 字节一致；报告 SHA-256 为 `2ecb8665c3ac267aa43e34394cd59c017d69172297853f79f664290006ac6e8f`。`r22GodotExecuted/r22GodotQualified/physicalMovementVerified/readyForPreview` 均继续 false，不是新的真实物理/AI 成功证据。
- `verify:r22` 实际运行至 Host 后因 `EADDRINUSE 127.0.0.1:43122` 退出 1：治理 95/95、实时/CLI 127 项中 126 通过/1 Windows 文件 symlink 权限跳过、参考 4/4、合同 14/14、Runtime 13/13、Provider 291/291、Host 70/71。没有将这次运行称为全量通过。只读确认占用者是上一轮 c 次预览（Node PID 56756、父 PID 53724），本批未终止它；已请用户关闭旧 Godot 后再复跑同一失败检查。
- 不占端口的 `verify:npc-cognition-godot` 单独完成 19/19，实际 Godot 4.6.3 import/probe 均通过；这不是用户场景实测。`check:boundary`、`check:round-scope`、`check:parent-scope -- --base e927db557f71db420e07a49818c0d4ae1e0d6ce3`、`check:v2-claim` 和 `git diff --check` 通过。全量 R1–R22 verify、extraction、父 client clean 测试/build 未在本批重跑，不提前收尾 R22.7。
- 仓外日志：`matrix-oasis-r22-response-profile-provider-20260912-a.log` SHA-256 `acc422d53b1d569d996db0a738a564c6ac410099889d04b3a18d5ff1cf436c71`；包含端口失败的完整专项日志 `matrix-oasis-r22-response-profile-verify-20260912-a.log` SHA-256 `97cfa72a1fc5ec8c6aeb4c42c9755c61cfb1a5b48fb136ff3c1a01c9a54e8847`。旧失败证据均保留。
- 本批只改 Provider 实现/类型、独立 conformance、既有响应矩阵两项 null 期望、live-provider 测试、参考审计与本任务卡共七文件；均为同一响应边界的实现/类型/回归/整合/证据闭包。Host、live-preview、qualification-core 和 cognition-runtime 与开工 SHA-256 完全相同，Godot/Creator/MVP/V2 状态零差异，既有脏工作均保留。HEAD 仍为 `9adb5493501a954360edf6525542ffface55653e`，staged 为空；本批 commit/push/PR、真实请求与真实凭据读取均为零。
- 后续首先释放旧预览端口并重跑被阻塞的门禁。即使通过，也不能仅凭绿测重开付费调用：必须先解释非空 `tool_usage` 的能力/费用语义，取得无需新真实请求的正反证据；之后仍须展示实际 payload 并取得全新单次审批。回退仅移除本批七文件增量，不覆写此前未提交工作、不删源缓存或旧 Receipt。

2026-09-12 旧预览关闭后的完整复跑与 tool_usage 证据核查：

- 用户确认关闭旧预览；执行前 43120/43122 监听数为 0，未终止其它进程。对同一失败命令重新运行 `npm.cmd run verify:r22`，退出 0：共 634 项，633 通过、0 失败、1 项 Windows 文件 symlink 权限跳过。分组为治理 95、实时/CLI 127、参考 4、合同 14、Runtime 13、Provider 291、Host 71、Godot 19；实际 Godot 4.6.3 import/probe 及范围、边界、V2 声明门通过。
- 本次完整日志 `C:\tmp\matrix-oasis-r22-response-profile-verify-20260912-b.log`，SHA-256 `64711dee226d4a8511835f6a4b0ad4115fc0a83d92ae355b10ba19ad9547c773`。先前端口失败日志与真实 c 回执保留，不覆盖为成功。此结果解除本地端口测试阻塞，不等于真实 AI Action 通过。
- `R22_REFERENCE_AUDIT.md` 追加 OpenAPI、官方 SDK 和工具费用的独立只读复核，修正原固定来源链接为实际核实的 `openapi.yaml`。没有找到非空 `tool_usage` 的闭合定义或费用映射；旧 c 内部值未留存，无法离线重建。供应商扩展的含义未知，不能宣称发生了额外调用，也不能猜测全部为零并放行。
- 本批仅补充两份 R22 文档，Provider/Host/Runtime/Godot 代码及既有未提交工作保持不变。没有请求字段改动、真实调用、真实凭据读取、外部支持消息、commit/push/PR 或 R22.7 状态切换。未重复全量 R1–R22 `verify`、extraction 和父 client clean 测试/build；它们仍属于收尾门。
- 已整理无需发送私有内容的供应商询问要点，尚未发出。下一步需要可核验字段/费用依据，或另行批准提供已有响应的最小脱敏结构；若无法取得，须由用户决定兼容路线，不能再用付费试错补证据。回退只撤销本批两份文档增量，不改变历史缓存或回执。

2026-09-12 最小脱敏诊断方案制定（仅本地；不是采集启用或付费批准）：

- 用户暂无既有材料，批准先完善最小取证方案。本批只修改 `R22_REFERENCE_AUDIT.md` 和本任务卡，不实现 Provider 开关/CLI，不修改业务接受规则、不读取凭据、不启动 Godot/模型请求、不保存任何真实响应。
- 开工分支、固定基线及 HEAD 不变；保留 16 项 modified、2 项未跟踪文件，staged 为空。先读取实际 Provider 的请求白名单、response capture、现有矩阵及模块范围，明确不能把新观测 profile 偷换成供应商 Schema。
- 方案采用固定完整路径计数词表与未知结构脱敏摘要分层；不记录未知名称/字符串/hash，缺失不补零，超限不发布完整前缀。捕获范围必须另行绑定诊断批准；账单语义和 R22.7 均保持未证明。
- 后续建议使用公开合成输入、候选仅 null 的独立诊断，显式禁用工具、不启动 Godot、不写 R19；仍须单独实现与验证 profile 隔离，展示实际 payload 后取得一次真实请求批准。当前没有添加或发出这个请求。
- 方案中的八组故障矩阵均标为未运行；既有 `verify:r22` 的 633 通过不能挪作新采集器证据。独立审查、文档检查与最终范围复验结果另行追加；不提前宣称工具已经可用。
- 风险是观测扩权、私人内容隐藏于未知字段名、费用误判、普通入口行为漂移和借诊断追加请求。回退仅移除本批两份文档的方案增量，保留先前审计、所有脏代码、旧缓存和 Receipt。没有本批 commit/push/PR 或状态切换。
- 独立只读审查补充了“已知零值加未知正计数”、重复键覆盖、动态异常泄漏等反例；方案明确根深度 0、容器/叶同计节点及 8 KiB 为产物上限。保留全响应 64 KiB 输入门，不为采集引入新原文切片解析器。观察保持单向旁路，所有真实形态最终都不能获得资格成功。该审查只证明方案得到反例检查，没有执行采集器或矩阵。
- 本批文档修改后，`verify:r22-references` 4/4、`check:round-scope`、`check:boundary`、固定基线 `check:parent-scope` 及 `git diff --check` 通过。Provider 实现/类型、Runtime、Host、live-provider、live-preview、qualification-core 和 MVP/V2 状态共 9 个文件的 SHA-256 与开工完全一致，staged 为空。未重跑整个 `verify:r22`；新观察器矩阵仍为 NOT_RUN，不将参考与范围检查冒充其正确性验证。

2026-09-12 最小脱敏观察器与离线矩阵开工：

- 用户授权“开始实现和矩阵”。开工 HEAD 为 `9adb5493501a954360edf6525542ffface55653e`，分支及固定基线未变，16 modified / 2 untracked、暂存为空；全部旧改动保留。
- 目标是默认关闭、观察与业务判定单向隔离的 `tool_usage` 观察器及假响应证伪，不是放行非空 `tool_usage`。本批没有真实调用批准，不读取任何真实凭据、不启动 Godot、不改 R19/Creator/资格状态，不提交或发布。
- 小批 A 限定 Provider 私有观察模块、独立观察器测试及任务记录；小批 B 限定 Provider 解析/诊断 profile 接缝、类型、独立集成矩阵及本记录。Astra负责生产整合；Sol仅编辑独立观察器测试文件，不同时编辑相同文件。新接口只用于隔离诊断，不能改变普通入口、原 Request/Receipt 合同或预算内核。
- 最窄验收为新增观察器/诊断矩阵，再跑整个 Provider 及 live-provider/Host 回归，最后范围和 diff 检查。解析复用现有严格解析器；事务与真实诊断启动的未接通项必须显式保留，不能由本地单测推导真实可调用。
- 风险为隐藏字段泄漏、观察影响业务结果及绕过旧审批。回退仅移除本批观察模块和接缝增量；不覆盖开工脏文件、不删除任何缓存/旧证据、不重置审批或预算。实际测试结果另行追加。

2026-09-12 最小观察器与矩阵的本地阶段结果：

- 完成纯 observer、固定 synthetic/null-only 诊断计划、共用严格 Provider 解析入口的离线 fixture 和类型；普通入口不新增捕获开关、不接受诊断 payload、不改变 `echo_tool_usage` 或保守费用规则。没有真实发送或原子产物 CLI，不会把新根目录作为新的预算账户。
- 主智能体与独立 Sol 分工完成：observer 49/49、diagnostic/parser 43/43（合计92/92）、整个 Provider383/383；Host/live-provider/recovery120项中119通过、0失败、1 Windows symlink权限跳过。类型正反fixture由模块已有TypeScript实际编译，未新增依赖。原不完整响应、-0、重复键、非空全零与未知正子树的业务结果与普通路径逐项比较一致。
- 针对性复核修复了点号 key 冒充完整路径、诊断计划错误继承普通输入、失败结果可携带明细的类型，以及 fixture Proxy 钩子。复核后无新增阻断项。保持类型与运行时分层：TS 不编码恰好八项/顺序/整数上限，运行时硬门负责；未开 exactOptionalPropertyTypes 时显式 undefined 的编译宽松不改变运行时精确键拒绝。
- 最终 Provider 日志 SHA `85053cdfd592203228bce44de08251da470db5d5c00f6c23a7b6b57dea3fd788`、Host 日志 SHA `d28013be9861e96522303e5d295db1bb179508997104e30e94bab670d856deec`，详见 `R22_REFERENCE_AUDIT.md`。首次写日志受沙箱限制未执行该次命令；改用已授权 C:\tmp 权限后重新运行，未把 shell 的偶然退出0当成测试通过。
- 范围检查没有豁免：boundary1535/1528、round/parent scope106/90、diff通过。网络/环境 tripwire 与 URL 诱饵移至已存在的Host测试文件，原断言保留；伪凭据改为明确placeholder；未改任何boundary/scope规则。公开类型、独立编译fixture和宿主tripwire作为第三个至多五文件的小批收口，三批合计十文件均为同一观察器实现/验证/记录闭包。
- 未修改源码的 hash复验：Runtime、Host core、live-provider、live-preview、qualification-core、MVP与V2共七项与开工完全一致；R19/R20/Godot/Creator本批零改动。HEAD仍9adb5493，16 modified/7 untracked，暂存为空；commit/push/PR、真实调用、真实凭据读取、Godot启动均为零。
- 尚未完成：诊断专用的共享历史budget、持久批准/dispatch、崩溃恢复与原子发布矩阵；旧实际c的tool_usage语义仍未知。普通Host回归与20次独立fake fixture不代表该新事务lane通过。完整verify:r22含Godot import/probe，本批未执行；R1–R22全量verify、extraction及父client收尾未运行。R22.7继续阻断。
- 下一步是独立事务小批，不是再实测：复用同一host-budget/writer与恢复核对，拒绝缺失历史预算和普通批准，并完整验证每个故障窗口后才考虑展示真实诊断payload另行申请单次批准。回退仅移除本批新增文件/增量，旧代码改动、证据和已消费预算均保留。
- 文档收口后追加复跑：范围/声明/参考治理99/99（日志 `C:\tmp\matrix-oasis-r22-tool-observer-governance-20260912-a.log`，SHA `1bf062091463a0c0ce351dd4d96db0f7c57aa018c870a0c23857eb3f31d0c5e8`）；独立参考4/4、合同14/14、Runtime13/13、boundary/round-scope/parent-scope/diff均通过。43120/43122监听数最终为0，暂存为空。没有把以上分组当成未执行的完整verify:r22。

### 下一本地批：诊断共享预算与事务矩阵（2026-09-12）

- 基线仍为 e927db55、HEAD 9adb5493；保留开工16 modified/7 untracked及全部既有付费证据。本批不提交/push/PR，不打开Godot、不读取真实凭据、不调用真实模型。
- 目标为复用同根writer及五字段host-budget，独立诊断事务、审批内容绑定、保守记账、可恢复的观察发布与普通恢复的严格双向对账；不能把诊断当成普通Turn或qualification。
- 分三个可验收小批：预算访问器/独立预算测试；诊断事务/恢复/矩阵；精确新增三路径allowlist/文档收口。每批至多五文件；完整闭包不改变R19/R20/Godot/Creator、旧响应与费用硬门。Astra独占生产整合，Sol独占新预算测试，另由只读Sol复核。
- 直连公开诊断发送入口因尚未强制经过持久事务门被安全审查拒绝，补丁未落盘。采用安全范围内的替代实现：本批事务执行仅接收合成响应字节，绑定offline-fixture，拒绝official-once根，不提供key/远程executor参数。共享预算与恢复的测试不代表真实发送入口已开放。
- 最窄验证为新budget/transaction矩阵，随后旧Host/live-provider/recovery、Provider、合同/Runtime、治理/范围与diff；新故障阶段和跳过必须逐项记录。回退仅移除本批增量及三条新路径声明，不删除旧证据、历史预算或开工脏改动。
- 生命周期补充仍在该事务小批内：独立审查抓到目录上限、真实manifest结构、首plan恢复及close瞬态失败四个P1，已分别修复并补反例；未从绿测推断正确。取消/关闭后不接受新审批或业务操作，close-only重试不能重新放行reserve。当时未绑定凭据文件身份的旧模式中，首plan空root/完整pending可从原配置零请求收口；此空root结论不适用于后续v0.3文件模式。损坏或混杂pending仍失败关闭，不用猜测重写内容。
- 精确收口补充包括 `verify-r22.mjs` 的两条新矩阵测试项；普通验证将自动运行新测试。不存在对新文件扫描豁免或修改R19/R20合同；共享预算抽取保留原检查优先级和canonical五字段条目。
- 最终离线矩阵105/105（budget54、transaction40、recovery11），Provider383/383，最新Host/live-provider114项113通过/1 Windows symlink权限跳过，组合与进程恢复34/34，治理99/99，独立contracts14/runtime13/references4通过。日志和SHA详见 `R22_REFERENCE_AUDIT.md`；各组重叠，不累计冒充独立数量。
- 容量fixture两次120秒超时如实保留为104通过/1取消；串行未解决后，定位其128次前缀重审的平方级磁盘准备成本。只改fixture准备，真实128/129入口、全部目录/budget/恢复断言和120秒限时不变，最终容量测试约7.4秒、整组39.4秒；不宣称仍覆盖128次连续生命周期耐久。独立Reviewer确认优化未绕过生产验证器。
- 本批最终boundary1538/1528、round/parent-scope115/93及diff通过；Runtime/Host core/live-provider/live-preview/qualification-core/MVP/V2七项hash与开工一致，43120/43122监听0。HEAD仍9adb5493，22 modified/10 untracked，暂存为空。没有commit/push/PR、真实模型请求、真实凭据读取、Godot启动或状态切换。
- 仍未开放诊断真实sender：当前只允许offline-fake账户、合成字节、realRequestCount0和qualificationEligible false。没有跑完整verify:r22/Godot、全量verify、extraction或父client。损坏/混杂pending、部分output/budget staging和不可恢复cleanup I/O保持fail closed；普通恢复不证明外部诊断副本目前仍存在，显式diagnostic recovery才校验/恢复该副本。下一批先完成受控真实发送接缝审查，不能直接重试Luna。

### 当前本地批：封闭诊断发送接缝（2026-09-12）

- 用户要求完成剩余本地项后等待其重新批准Luna；本批零真实请求、零真实凭据读取，不复用任何旧审批、不提交/push/PR、不切换R22.7状态。
- 先完成模式分离和私有transport，再做独立故障矩阵及整合检查。旧offline-fixture的canonical记录保持不变；新增injected-transport证据永远不能成为真实请求证据，只有official-once账户允许新的官方事务入口。
- 官方入口只接收固定来源/output配置，不接收key、请求回调或文件操作替身。真实发送与固定环境变量读取都封闭在事务模块内部，必须经过内容批准、同账户writer、reserve、dispatch、保守扣账及发送前身份复验；不增加Provider公开直连诊断API。测试替身入口仅允许offline-fake账户。
- 需要精确扩展该事务模块的网络来源检查：仅该路径的一个私有请求接缝及一个固定credential读取点；通用脚本/Provider/Godot网络规则不放宽。新模块仍只能发送固定公开synthetic/null-only请求，观察词表与业务拒绝规则不变。
- 分至多五文件小批：治理/路径与边界门；事务/模式/发送及独立测试；CLI待审批材料与恢复入口；文档/验证注册。普通路径、Runtime、Godot、Creator和价格/资格门不改。Astra负责生产整合，Sol独占新transport矩阵，另一Sol只读证伪。
- 发送后证据缺失只记录请求数不确定（上界1）并保留完整10000 microusd，恢复绝不再调用。完整响应只驻内存并交现有严格解析器，磁盘仅允许固定脱敏观察及静态transport状态，不记录原文、底层异常或remote ID。
- 验收顺序：新transport与旧事务/预算/恢复矩阵 → Provider/Host兼容 → 完整本地verify:r22 → 范围/diff与可行的收尾检查。任何失败先定位，不用真实调用补红测。回退仅移除此批增量，保留全部旧缓存、历史预算、证据及开工脏改动。
- 本批整合 `verify:r22` 已实际退出0：890项中889通过、0失败、1项环境权限跳过，含完整63项新transport矩阵和真实Godot 4.6.3 import/probe；旧事务/预算/恢复105/105另复跑通过，分组不重复累计。日志与SHA见 `R22_REFERENCE_AUDIT.md`。独立审查补齐official单claim恢复、legacy模式、收包身份、能力泄漏和输出抢占窗口后，无新增P0/P1。
- 只对原官方c账户运行plan模式并复验manifest/budget字节未变，未批准/发送/读key，未创建diagnostics/output或留下writer；新disclosure hash只作为待审批材料，不复用旧批准。原账户历史预算不清零；每个官方账户最多一个额外诊断事务目录，取消或崩溃不能偷偷重开。
- 明确拆分限制：现有 `verify:extraction --allow-dirty`仅拆分已提交HEAD，不能验证本批未提交内容。正式source/split/archive收口保留到批准的提交快照；本批不为凑齐绿门提前提交。全模块与父client的最终结果仍须独立记录，不拿专项通过替代。
- 父client已在全新仓外HEAD快照完成验证：461个tracked blob前后零漂移，client tree与R22固定base相同；离线锁定安装及build通过。原test:run为914/916通过、2项日期驱动模型过期断言失败（两条Nex AGI已到2026-09-08到期时间），exit1保留；被短路的server-headers另跑1/1通过。不擅自修改冻结父代码或回拨时间，发布前仍须处理这个父基线失败。详细日志、hash和行号已记入参考审计。
- 全模块 `npm.cmd run verify` 已在同一进程完整结束：exit0、`VERIFY_OK steps=31`，含40次真实空间分析一致性检查、综合测试941/941以及Creator构建/启动检查；未跳过耗时旧用例。日志SHA-256为 `53438cb1b7d49c5129a5e06f207f0fed26590d052a78882b167bffa875f6b5fa`，位置和分层结果见参考审计；不把重叠专项累计为唯一用例数，也不覆盖前述环境权限跳过。
- 本地诊断修复与矩阵阶段完成后停止，等待用户新批准。没有真实请求、真实key读取、commit/push/PR或R22.7切换。下一次必须重新展示并批准公开合成、null-only诊断的完整payload和捕获范围，核查最新价格及来源；上限1请求/0.01美元，旧审批不能复用，诊断不是游戏资格。父基线失败、最终快照extraction与真实/人工资格继续明确保留，不称整轮已完成。
- 最终边界1540/1528、round/parent scope118/95及diff检查通过；43120/43122监听、Godot进程与新transport临时目录均为0。HEAD仍9adb5493，23 modified/12 untracked、暂存为空，所有未提交工作保留。

### 当次批准后的执行结果（本地2026-09-12，UTC 2026-09-13）

- 用户批准完整披露身份 `sha256:17b70f36f28be91ace9957f65d0342b84656c755e51c66bf08f9f4c33ab183b3` 后，仅执行一次官方诊断入口；最新官方价格与源码hash复验未漂移。执行退出0只代表失败记录成功发布。
- 结果为 `transport_failed / credential_unavailable`，真实请求0、重试0、无observation、不可计为资格。固定环境变量读取未取得可用凭据，入口不自动加载旧key文件；静态记录不足以判断是缺失、格式、读取失败还是发送前中止，不判断账户或模型无效。
- 没有本次模型调用费用；本地仍按已批准事务规则保守扣账10000 microusd，原账户总记账20000 microusd，并保留已消费的唯一诊断claim。不得自动退款、重置、换账户或复用旧审批补发。
- 一次零网络recover得到相同terminal/report字节和budget hash，writer已释放。证据目录为 `C:\tmp\matrix-oasis-r22-tool-usage-diagnostic-20260912-a`；报告SHA-256为 `93693a3632dcc5e1a72e49bfb4fd8f3b2ef029b49265d32e48a975af22850338`，详细身份链见参考审计。本批仅更新这两份文档，不改源码、不启动Godot、不提交/push/PR。
- 下一步须先明确并修复凭据接线，再讨论新请求的独立授权；当前不自动实施或重试。tool_usage真实语义、R22.7、最终extraction、父基线失败及人工验收仍未收口。

### 当前修复批：诊断凭据接线（2026-09-13）

- 用户仅授权修复。固定base e927db55、HEAD 9adb5493、23 modified/12 untracked均保留；本批不调用模型、不读取真实key内容、不打开实时Godot预览、不提交/push/PR、不重开诊断claim或重置预算。完整回归中的Godot导入/探测单独记录，不冒充人工游戏验收。
- 当前子进程只查询专用环境变量名称是否存在，结果为false；没有读取变量值。代码证实原实时预览已有显式credential-file和延迟身份校验reader，新诊断CLI/工厂却没有该接线。此证据不反推此前密钥格式或账户效力。
- 修复沿用已有reader，不复制凭据解析或传入任意callback；仅增加显式文件来源配置与非秘密身份摘要，文件内容仍在批准、reserve、dispatch后读取。来源变更必须改变审批身份；旧v0.1/v0.2记录、故障账目与恢复输出保持不变。恢复不依赖凭据文件仍然存在。
- 缺配置先在CLI创建事务前静态失败。禁止自动加载父env、环境/文件间自动回退、裸key参数、提前读key的启动脚本或公开发送器。新增源码只涉及诊断CLI/事务、既有reader的身份描述及精确source scanner；分为身份描述与测试、事务/CLI/边界与矩阵、文档收口三个小批。
- 验收：缺配置不占claim或预算；文件准备零open/read；审批换来源失效；替换/删除/link/超限/错误key失败；读key晚于dispatch；旧记录恢复及预算逐字节不变；假发送器一次发送、无重试、无泄露；兼容与范围检查。内部矩阵不证明官方凭据有效或Luna已调用。
- Astra独占实现与整合；Sol仅只读审查来源绑定、旧记录兼容与测试缺口。回退只撤销本批增量，保留此前dirty工作和全部真实失败证据；真实接线材料或新的付费尝试仍须用户另行提供/批准。
- 新文件模式使用v0.3 plan绑定非秘密来源摘要，旧v0.1/v0.2持久结构保持原语义；CLI plan外层新增credentialConfiguration，不声称整个旧CLI输出字节不变。execute在创建claim前比较批准与本次pin，缺失配置不再消费名额，内容有效性仍只在批准后的延迟读取中检查。
- 补充恢复限制：本卡前述空root可恢复仅指旧无文件身份模式；v0.3空root尚未保存来源身份时保持失败关闭，不猜测取消、不重开claim。完整pending/plan可在删除凭据文件后零请求恢复。三个实际命中fs的故障窗口及新增源码门反例通过；不为可用性扩写事务存储。
- 当前聚焦15/15、完整接线/预算/事务/恢复225项中224通过、0失败、1项Windows symlink权限跳过。早期参数计数、output检查优先级与未命中fs捕获的测试问题及修复已记入参考审计；未调整生产断言或网络/预算边界。完整R22回归与最终范围检查另记。
- 用户确认的 `C:\tmp\openai key` 属性查询为不存在，专用env只查存在性为false，未读任何真实key。本批零真实请求；原官方manifest/budget/report哈希未变，已消费claim不重新开放。
- 最终完整verify:r22退出0：904项中903通过、0失败、1项Windows symlink权限跳过，含实际Godot 4.6.3导入/probe。初次工具路径缺失和随后测试动态import被boundary拒绝均保留为exit1日志；只修正本次工具配置及测试固定地址，未放宽断言/规则，原完整命令重跑通过。最终日志与六项源码SHA见参考审计。
- boundary1540/1528、round/固定base parent-scope118/95及diff通过；测试自有临时目录与Godot进程为0，暂存为空。完整31步verify、父client和最终快照extraction未在本修复后重跑，真实调用和人工R22.7继续阻断。只宣称本地接线修复通过，不宣称Luna已成功调用或整轮完成；没有commit/push/PR或状态切换。

### 当前本地批：一次追加授权，不重开旧诊断（2026-09-13）

- 用户批准同一账户追加一次独立名额；只允许衔接已完整终结且 `credential_unavailable / requestCount=0` 的旧 official v0.2/v0.3 事务，旧证据和10000 microusd保守扣账不删除、不冲销。更正凭据路径为 `C:\tmp\openai key.txt`；本批仅元数据检查，不读取内容。
- 新 v0.4 文件模式 plan 显式绑定旧 transaction、terminal、transport 身份及两名额硬上限。默认单名额模式不变；第二名额一经创建即消费，取消、零请求、崩溃均不得产生第三名额。新计划必须获得全新内容审批，不能重用旧 SHA 或 token。
- 创建、reserve前、派发前、发布与恢复均复验前驱记录和同账户预算；普通历史审计只接受唯一合法两记录链，不接受分叉、缺失前驱或两份无关记录。无完整plan的文件模式仍fail closed；恢复不读取凭据，也不重试网络。
- 本批范围：现有诊断事务、CLI、精确边界检查和transport矩阵及本任务记录，随后独立只读审查、局部/完整R22回归和证据记录。零真实请求、零真实密钥读取，不修改Provider payload、接受规则、价格、R19/R20/Godot/Creator或MVP/V2状态，不提交/push/PR。
- 最窄退出门：只有指定零请求前驱可追加；旧/错审批不消费名额；20并发最多一次假发送；旧证据与预算保留；第三次与跨账户拒绝；前驱换身、重签篡改、崩溃、恢复和预算耗尽均fail closed。补测后仅生成新的只读完整披露，真实执行须再取得当次批准。
- 威胁边界：同一次writer持有期间拒绝相同字节的inode换身；跨进程恢复依赖已批准的canonical身份，不保证inode永久不变。第三名额门针对受控CLI/未被擅删的持久历史，不宣称能对抗同权限者删掉诊断目录、篡改预算或整包重签；没有外部不可变计数器。已存在两目录时只能按新事务ID恢复，不能用旧事务恢复绕过新记录；所有恢复仍零请求。
- 本地结果：聚焦27/27，最后一个审批前整链重签反例纳入完整verify:r22后共931项、930通过、0失败、1 Windows symlink权限跳过；包含实际Godot4.6.3导入/probe而非实时游戏资格。初轮预算排序fixture错误已按identity修正并复跑，未放宽生产规则。日志、源码hash、独立审查和残余P2覆盖建议见参考审计。
- 原真实账户manifest/budget/report哈希不变。新plan-only披露为 `sha256:81bf0a4996827335682a52e3f326826a557f7779d15b7df073d1fb4d714be725`，仅绑定metadata，未读真实key、未新增名额/预留或请求。需要展示完整披露并取得新批准后才可执行；不沿用本次代码批准作为真实发送授权。
- 本批boundary/round/固定base parent-scope/diff通过、暂存为空；不提交/push/PR、不改资格状态。全模块verify、extraction及父client未重跑，不据此宣称R22.7完成。

### 批准后的单次官方观察（2026-09-13）

- 用户批准新披露 `sha256:81bf0a4996827335682a52e3f326826a557f7779d15b7df073d1fb4d714be725`；重新核对官方Standard价格、九项hash和pinned-file metadata，计划身份未变。仅execute一次，真实请求1、重试0，状态observed；指定key只在持久审批/扣账后的私有发送器中读取，未输出或保存原值。
- 捕获的 `tool_usage` 为非空object、8个固定计数均为0、未知字段0。当前普通Provider仅允许空object，已证实这一具体接受规则不兼容本次响应；不能泛化为所有历史失败同因，不改收费推断或安全门。下一步应先做最小离线兼容方案及正反矩阵，不再盲目调用。
- 新report SHA `2edbc021ad655ea2c5591c52630a647cd08e9451fc1288b62880ca0cdfe73db3`，observation SHA `7b4ac0dbb5a6099334bf8a0f34fd565e05cc27678f5a2aaf3738140daf002372`；目录及完整身份链见参考审计。一次零网络recover返回同一terminal；新保守记账10000 microusd、累计30000，原两项budget字节身份可重建且未变；不冒充实际账单。
- 两个诊断名额已消费，本次不自动重试或开第三次。`qualificationEligible:false`保持；未保存原始response/对白，未启动Godot，未更改代码、R19/R20/Creator/MVP/V2或执行commit/push/PR。此处仅完成响应形状诊断，不代表R22游戏真实资格或整轮验收通过。

### 当前修复批：已观察零计数的闭合兼容（2026-09-13开始，2026-09-19离线收口）

- 用户要求开始修复；固定base e927db55、HEAD 9adb5493及23 modified/12 untracked保留。本批零真实Provider请求、零凭据读取，不重开已消费的两个诊断名额、不改预算、请求内容、Schema、R19/R20/Godot/Creator或MVP/V2，不提交/push/PR。
- 唯一生产目标：普通Provider保留原absent/空record语义，只新增接受实际观察到的完整五个object节点、八个正零number叶子的固定闭合tool_usage结构。任何缺字段、未知字段、部分结构、负零、非零、类型错误和伪装零值仍拒绝，不把观测器或词表当作业务授权器。
- 本批最多五文件：Provider实现、现有conformance/Host矩阵及两份R22文档；若需额外路径先停报。Astra独占编辑与整合，Sol仅独立只读证伪。先记录最小失败复现，再实现最小检查，补齐反例、20次确定性及旧diagnostic/预算/Host回归，最后运行完整verify:r22和范围门。
- 独立审查提出的新接受路径计费回退已纳入范围：完整零结构若后续任一门失败，仍清空可定价usage并保留全額不确定费用；旧absent/空结构的失败语义不改，预算内核不改。只对完整新结构使用现有strict scanner的额外词法检查，拒绝非零mantissa下溢成零，不从字符串或指数数字误判。
- 数据安全回退只撤销本次接受分支及对应测试/说明，不清理或重算旧Receipt、诊断观察与预算。现有回复体不得保存为fixture；仅用捕获的数值结构加独立合成响应。真实工具计费语义未由官方资料或一次观察完整证明，不把本次兼容当作真实AI/Godot资格。
- 2026-09-19恢复后：确认上次核心修复保留、测试扩展尚未落地；正常补齐同批测试，未绕过编辑审批。原唯一`echo_tool_usage`红测已转绿；conformance 289/289、真实校验器到宿主预算/收据/重开恢复矩阵11/11均通过。新完整形状后续失败仍保守记满预留，旧absent/空record费用语义不变；全部只用fake fetch及测试专用假凭据。
- 完整`verify:r22`退出0：1121个测试条目，1120通过、0失败、1项既有Windows文件symlink权限跳过，Godot4.6.3实际import/probe通过。初次boundary识别合成annotation夹具的URL字面量；仅替换该测试字段，保留拒绝断言和失败日志，未放宽扫描。原conformance和boundary/round/parent范围门重跑通过，`git diff --check`通过；独立Sol只读审查未发现本批可操作缺陷。全部日志和hash见参考审计。
- 本次仅完成最小兼容修复的离线验证；未重新执行全模块`verify`、extraction或父client clean测试/build，未取得新真实Luna/Godot游戏资格。MVP/V2状态、旧预算与已消费批准保持不变；不提交/push/PR，R22.7及人工验收仍未通过。后续真实资格须重新披露完整payload并取得当次批准。

### 当前修正批：协议默认值、独立oracle与跨层回归（2026-09-19）

- 用户批准执行审计后的修正。范围限定Provider实现/类型、独立协议oracle、现有conformance/Host补测和两份R22文档；Astra负责实现整合，Sol分别负责不重叠的oracle与Host测试。其余脏改动保留，暂存为空，不提交/push/PR。
- 已核官方context三值，修正本地遗漏auto/all_turns；不改请求、不增加远程历史、不放开mode/effort、billing、工具、Schema或Action。诊断新增固定子规则和浅层类型，不留原始响应或动态键值。
- 新oracle必须先红后绿；旧字段覆盖仅证明本地规则完整，不能代替独立协议依据。跨层补测走真实Provider解析器+fake fetch、Host持久化及重开，确认单次派发、保守10000 microusd、零恢复请求和无AI Intent。固定R20降级不能算认知成功。
- 官方billing结构及费用语义仍未核清；真实资格继续阻断。本批不读取真实key/env、不发模型请求、不启动新人工预览、不消耗新调用批准、不修改旧Receipt/预算和MVP/V2声明。旧Godot/Host仅在用户单独批准后核对PID并正常关闭，以释放43122。
- 完整`verify:r22`退出0：1152项中1151通过、0失败、1项既有Windows文件symlink权限跳过；实际Godot4.6.3导入/probe通过。Provider587/587、Host88/88；新独立oracle12/12，Provider四文件聚焦组495/495。Host用例经过真实解析器+fake fetch及事务operation seam，未把它描述成真实网络或完整物理资格。
- 最初沙箱EPERM和旧预览占端口导致的587/587+87/88均已记录；授权后原命令重跑通过，未修改失败断言或端口。boundary1541/1528、round/固定base parent-scope119/96、diff通过。最后两个端口无监听、无对应R22残留进程；旧Receipt SHA不变、staged为空，保留全部旧dirty工作，仅新增独立oracle文件。
- 仓外验证摘要`C:\tmp\matrix-oasis-r22-context-protocol-fix-20260919-verify.log`的SHA为`31cc3e0af60d34c026691981566eadbe4b520463367dad31883d840160bcdc88`；它不是完整原始日志，包含分组结果、先前环境失败和五个实现/测试文件hash。详细范围和未验证边界见参考审计最新节。
- 本批真实请求、真实密钥读取、commit/push/PR均为0；全模块`verify`、extraction和父client未重跑。仅宣称已证实默认值缺口的本地修正与离线验证完成，不宣称billing兼容、真实Luna/AI Action或R22.7通过；回退只撤销本批七文件增量，不清理或重算旧证据。

已完成的协作交接事项（保留来源）：

- 当前已开始的实现与证伪批次继续由Sol收口，避免在事务宿主、资格证据和实时Godot启动链仍有并行改动时中途更换主模型。
- 本批次通过聚焦测试并形成干净交接点后，输出一份可直接交给Astra的完整接手提示词，列明基线、分支、worktree、提交、未提交Diff、已验证证据、未通过硬门、付费调用边界、文件所有权和下一步命令。
- 后续采用Astra主导：负责跨R20至R22的实时链整合、异常决策、针对性证伪、真实调用前payload审查与最终验收；Sol子智能体只承担边界清楚、结果可独立检查的实现、测试、证据整理和文档任务。
- 禁止两个智能体同时修改同一文件或操作同一Godot/浏览器会话；主智能体负责分配文件所有权、复核子任务结果并执行最终集成验证。

### 当前本地批：billing 假设矩阵，不猜测生产语义（2026-09-19）

- 用户说明官方工单未获回复，要求构建潜在可能矩阵。范围只限既有 `response-conformance.test.mjs`、本任务卡及 `R22_REFERENCE_AUDIT.md`；保留 23 modified/13 untracked 现场，HEAD 仍为 `9adb5493501a954360edf6525542ffface55653e`。不改 Provider、Host、预算、请求、合同或持久化格式。
- 将缺失/null、空对象、付款方对象、金额/币种对象、工具计费对象、未知嵌套元数据与错误类型作为显式合成假设，不将 `payer`、`amount` 等猜测字段称为官方 Schema 或实际捕获值。最近真实响应完整原文未留存，不能冒充真实重放。
- 对假设逐项交叉三个现有 tool_usage profile、缺失及三个已核实 reasoning context，并测试仅移除合成 billing 的对照。移除只在测试副本中发生，产品仍拒绝非 null billing；不新增自动剥离、回退接受或候选 Schema 轮询。
- 加入独立权限/费用反例、同类型不同内容的诊断不可区分性、20 次确定性、原文诱饵不泄漏及同审批零重发。固定本地 fake fetch 和合成凭据；真实环境变量、密钥、缓存、旧 Receipt/预算均不读取或改写。
- 验收先执行新增 billing hypothesis 用例，再执行 Provider/Host 回归、boundary/round/parent 范围及 diff 检查；没有生产代码变更，本批不把跨 R1–R22 全模块、Godot 实时资格、extraction 或父 client 旧结果冒充重跑证据。
- 回退只撤销这三个文件的本批增量。公开资料缺失不构成新增真实调用批准；任何新采样须另行固定采集词表、完整 payload、金额及持久化边界并取得当次授权。不重开已消费诊断名额、不提交/push/PR、不解除 R22.7。
- 本批结果：新增 341/341，Provider 928/928、Host 88/88；276 组合、55 交叉反例、13 种对象各 20 次及 6 个 wire 反例全部符合预期。不同对象产生相同诊断，已证实仅重复现有调用不能判别 billing 语义。范围门 boundary1541/1528、round/parent119/96、diff通过；暂存为空，五项生产/状态文件 SHA 与开工前相同。沙箱日志及 Host EPERM 阻断、重跑结果、日志/hash和下一批建议见参考审计；本批不产生真实模型调用或资格结论。

### 当前本地批：只读 billing 观测器（2026-09-19）

- 用户同意先实现观测器。本批只改五个文件：新增私有 observer 与专用测试、既有 conformance 测试及两份 R22 文档；保留全部既有未提交工作。Provider 入口、公开类型、Host、预算、请求、Godot、R19/R20/R21、MVP/V2 与旧证据均不改。
- 词表是本地诊断假设，不是官方 billing Schema：仅识别 `payer`、`amount`、`currency`、`service_tier`、`tool_costs`、`tool_costs.total` 的固定路径、JSON 类型及有限字符串类别。未知字段只计数，不输出名字、值或其 hash；任何金额数字均不保留或解释为费用。`0`、解析后下溢为零及非零金额不能据此区分免费/收费。
- observer 只接受已严格解析且有界的数据；额外限制深度、节点、键数、数组长度和累计文本字节，拒绝 Proxy、accessor、异常原型、非法 Unicode、循环及非 JSON 值。失败丢弃全部部分观察，输出静态状态；输入不变，结果深冻结，20 次 canonical 字节一致。
- 新 capture policy/profile/hash 与既有 tool_usage 采集身份分离；传入 hash 仅作关联，不证明已批准。所有输出固定 `semanticCoverage:observation_only`、`qualificationEligible:false`。本批不接入实时采集或持久化，旧审批不得复用。
- 先运行专用测试与观察前后生产判定/保守预算不变反例，再运行 Provider、Host 与范围门。无真实请求、无凭据读取，不启动 Godot，不提交/push/PR。回退只撤销本批五文件增量，不清理旧证据或重算费用；后续真实采集仍需单独接线、完整披露及新的逐次批准。
- 本批结果：专用 133/133、23 形状非干扰聚焦组 24/24、Provider 1085/1085、Host 88/88；boundary1543/1528、round/固定 base parent121/98、diff 通过。新测试曾混淆 Provider 的未知实际费用与 Host 的保守扣账，已依据实现修正断言并重跑；边界检查的测试关键词触发已移除重复检查，扫描器未放宽。完整证据、初次失败、hash 及回退边界见参考审计最新节。
- 七项既有生产/状态文件 SHA 保持不变，暂存为空；本批没有真实 Provider 请求或凭据读取，没有全量跨轮/Godot/extraction/父 client 新证据。当前完成的是隔离观测器核心，不是实时采集链；新 profile 不解锁旧审批、名额或 R22.7。

### 当前本地批：billing 诊断接线与事务证伪（2026-09-19）

- 用户要求补齐观测器之后的诊断接线和崩溃矩阵。保留 HEAD `9adb5493`、23 modified/15 untracked 与全部既有工作；不提交/push/PR，不读取真实 key/env，不调用模型，不变更旧账户、已消费的两个诊断名额、生产接受规则或 R22.7。
- 分为两个可验证子批：先增加固定 billing diagnostic plan、离线 Provider 采集缝、声明与非干扰测试；再让既有事务/CLI按明确 profile 选择采集器，并复用同一审批、预算、单次发送、持久化和恢复内核。旧默认 tool_usage profile 的请求、计划、格式与结果必须逐字节保留。
- 接线须同时覆盖 profile、Provider、公开类型、事务、CLI、测试与边界文档；这是同一采集身份的原子边界，不能只改解析器或只改审批。范围超过五文件的原因仅此，不解冻 Host、预算内核、Runtime、Godot、Creator 或 MVP/V2；不增加第三方依赖。
- billing 请求使用与现有诊断相同的公开合成 payload，仅改变本地 turn/capture/approval 身份。持久化只允许固定路径、类型和有限类别；未知字段、数值及原始响应不留存。观察不参与接受、Action、价格或退款决定，`qualificationEligible:false` 不变。
- 验收：独立 profile 交叉审批拒绝、20 次确定性、旧/新 profile 相同 fixture 回归、错误 response 与传输界限、并发重复、各持久化/崩溃/恢复窗口、重签污染证据拒绝、旧预算与官方双名额上限保留，最后 Provider/Host/完整 R22 及范围检查。真实采样仍需新的完整披露和当次批准；本批不会以新 profile 创建新付费额度。
- 回退只撤销本批新增 profile 及接线/测试/说明，不删除诊断历史、不重算费用、不清理其他脏改动。离线通过不表示 billing 官方语义、Luna 游戏资格或人工验收通过。
- 本批收口：新增采集聚焦211/211，事务/传输/预算原矩阵275/275；追加证伪发现并最小修复两个结构一致性缺口，16类攻击17/17通过。最终完整`verify:r22`为1764通过、0失败、1项既有Windows symlink权限跳过，Godot4.6.3实际import/probe通过；boundary/round/固定base parent-scope/类型/diff通过。两次测试夹具预期修正、先红证伪及首次Godot路径环境失败均保留证据，不把失败记录改写成通过。
- 完整最终日志 `C:\tmp\matrix-oasis-r22-billing-wiring-verify-20260919-b.log`，SHA `fab2c7548c26f0aad878886246c730f548dbff0f9ae99476c47f838b0e32019f`；实现、限制、各日志与source SHA见`R22_REFERENCE_AUDIT.md`本批节。Host、预算内核、旧采集器和MVP/V2字节未变；暂存为空、无commit/push/PR、真实请求及真实key读取均0。跨轮verify/extraction/父client和真实Luna资格未新增证据；下一次真实采样需要新增名额边界批准与新的完整披露审批，不能复用旧名额或审批。

### 单次 billing 追加名额（2026-09-19，本地实施；发送待新披露批准）

- 用户批准仅新增一个 billing 采样名额：最多1请求、10000 microusd、零重试。同一账户原两个诊断claim和30000 microusd历史保守记账全部保留；不换账户、不退款、不复活旧审批。本批不发送请求、不读取真实密钥内容、不提交或发布。
- HEAD仍为`9adb5493501a954360edf6525542ffface55653e`，23 modified/17 untracked，所有既有工作保留。范围为诊断事务、CLI、静态边界、transport矩阵和两份R22文档，共6文件；这是一项涉及持久化版本、审批和恢复的完整授权边界，不能只改变计数常量。
- 新增official-only v0.5授权，仅允许显式billing profile和pinned-file来源，并绑定已完成的v0.4工具观察及其v0.2/v0.3零请求凭据失败前驱。三个claim构成唯一链；旧入口保持原上限，不允许通用quota参数，第四次及旧审批一律拒绝。
- 新授权绑定前驱plan/terminal/transport/observation/预算entry哈希。取消、发送前崩溃、零请求失败和派发不确定均不重新开放名额；不完整记录继续fail closed。完整证据可无密钥离线恢复。
- 独立Sol审查发现并经先红反例确认：最初新链未要求继承v0.4的固定凭据来源。已仅在billing的plan前门与历史link加入元数据身份一致检查；不同文件、同文件变化、重签credentialSource均拒绝。旧v0.4凭据修复入口不变；此门只证明本地固定来源一致，不证明远端计费账户身份。
- 验收为新plan的20次字节一致、错误profile/前驱/审批拒绝、20并发单次本地拦截、历史及保守扣账不变、版本/哈希攻击、取消和崩溃窗口、第四名额拒绝、旧事务矩阵、完整verify:r22及scope/diff。实际网络始终由测试入口内存拦截；通过后才生成真实账户的metadata-only完整披露，等待当次发送批准。
- 回退仅撤销本批6文件增量。未来若新v0.5已实际持久化，旧代码会拒绝该新增历史，不能通过删除记录回退；须先结束诊断并保留可验证的新格式读取器，R16/R19/R20权威源始终不受修改。
- 最终离线收口：`verify:r22`1823通过/0失败/1项既有Windows文件symlink权限跳过，Godot4.6.3实际import/probe、boundary/round/固定base parent-scope/diff通过。独立Sol已复核凭据来源修复，无新增可操作发现；完整日志、先红后绿和最终source SHA见参考审计。
- metadata-only plan已生成新transaction `sha256:1ffa0caf03ad46144ba26c1787fede6c9c5e608d8198ac58c11882bb80d0c2ff`；旧账户和两份输出内容指纹前后相同，claim仍2个，真实密钥内容/Provider环境变量读取和请求均0。输出目录尚不存在，旧审批不得复用；等待完整披露后的当次发送批准。本批未提交/push/PR，R22.7及真实游戏资格继续阻断。

### billing 单次真实采样收口（2026-09-19）

- 用户已批准上述新披露，现有CLI执行一次并发布`observed`：realRequestCount=1、零重试，qualificationEligible=false。观察到的已知结构为`{"payer":"developer"}`，其他五个固定路径缺失、未知字段0；不保存原始响应，不宣称官方billing语义或实际账单已核实。
- 实际无凭据/无fetch恢复复验通过，旧账户前缀、旧预算项和两份旧输出不变；新增本地保守扣账10000 microusd，第三诊断claim已消费。已有合成规则对照277/277通过；这证明当前非null拒绝规则与真实观察形状冲突，不证明全部真实响应或游戏链已通过。
- 仅更新两份证据文档；生产代码、预算规则、Godot/Creator及MVP/V2不改，不提交/push/PR，不自动追加调用。下一批须先评估最小兼容及离线证伪，不能再次盲试；完整SHA、恢复和脱敏检查说明见参考审计新节。

### 当前本地批：已观察 billing 形状的最小兼容（2026-09-19）

- 用户批准最小兼容修复和离线证伪。本批仅在原Provider严格解析之后识别精确单键`{"payer":"developer"}`；缺失/null旧行为不变，其他payer、空对象、额外字段及金额/工具计费对象继续拒绝。来源是上一节脱敏实测，不是官方完整Schema或原始响应重放。
- 允许修改Provider `src/index.mjs`、conformance与billing-diagnostic测试、Host测试及两份R22文档，共6文件：诊断入口与普通入口共享解析器，必须同步证明同一接受/拒绝结果，不可把诊断测试遗留为过期绝对禁令。无需改变公开类型、请求、审批、诊断采集器、Host生产逻辑、预算存储或资格格式。
- 风险在于接受新字段后误放行能力或退还不确定费用。该形状只作丢弃的元数据，不驱动Action或费用；完整成功仍用原严格usage/Standard价格算法，任一其他门失败仍保守占满预留。旧缺失/null的失败计费和静态diagnostics保持；旧absence子规则对新形状标not_checked，不冒充其满足absence条件。
- 先新增回归并确认旧实现失败，再做最小实现，覆盖精确形状、非法嵌套/重复键/转义/大小深度、其他响应门、20次确定性、旧路径计费不变、真实Provider解析器接Host的持久化与恢复。执行Provider/Host、完整`verify:r22`及boundary/round/固定base parent/diff；所有Provider传输使用显式fake，不读取真实key/env，不启动人工预览。
- 保留HEAD `9adb5493501a954360edf6525542ffface55653e`与已有23 modified/17 untracked工作；真实账户、旧Receipt/预算/诊断claim只读且不重估。无真实模型请求、无commit/push/PR、无R22.7/MVP/V2状态变更。回退仅撤销本批6文件增量，不清理历史证据或其他未提交改动。
- 先红回归确认原billing策略拒绝精确实测形状后完成最小实现。Provider/conformance与billing-diagnostic最终848/848，实际Provider解析器接Host的持久化/恢复矩阵42/42；独立Sol复核新聚焦146/146且无新增可操作发现，分组重叠不累计。中途响应字节数、孤立代理项拒绝阶段和success Receipt实际写入点的fixture错误及修正均保留在参考审计，未以放宽生产校验消除失败。
- 新形状仍须通过全部usage、Standard、model、schema、工具、对白与choice门；任何独立失败扣满预留，成功只沿用原token费用计算。测试无真实请求/密钥读取，观察不能升级为资格；完整R22专项与最后scope检查另记，实时游戏资格仍待单独完成。
- 最终完整`verify:r22`退出0：2000通过、0失败/取消、1项既有Windows文件symlink权限跳过；含Godot4.6.3实际导入/probe。boundary、round、固定base parent-scope及diff通过；九项冻结源码/状态/真实证据hash不变，Godot与43120/43122监听均0。完整日志、各组数量及SHA见参考审计，不将重叠矩阵累计。
- 本批局部兼容与离线证伪完成；未重跑跨轮全模块verify、extraction或父client，未取得新的真实对白/Action/人工资格。现有三个诊断claim已消费，不因本地绿测重开；无真实调用、真实key读取、commit/push/PR或R22.7切换。后续真实请求必须另行完整披露并取得当次批准。

### 单次真实游戏链与局部人工确认（2026-09-19）

- 用户重新授权实测后，使用现有 `preview:r22 --provider-mode official-once` 启动独立新根 `C:\tmp\matrix-oasis-r22-luna-billing-compat-20260919-a`，保留全部旧资格源、失败证据及已消费诊断名额。固定 R20/R21 来源与 R16/R15 精确预览身份复验通过；初始请求数为 0，只有 `127.0.0.1:43122` 监听，43120 未监听。此后经游戏内内容绑定审批发出一次官方请求，零重试，不属于第四个诊断名额。
- 请求与返回模型均为 `gpt-5.6-luna`；Receipt 为 `finalized`，状态链包含 `validated → queued_for_r20 → adjudicated`，`fallbackReason=NPC_COGNITION_FALLBACK_NONE`，并有匹配的对白 display ACK。usage 为输入 527、输出 135、缓存读/写均 0、合计 662 tokens。沿用锁定价格计算并向上取整后的本地费用为 268 microusd（$0.000268），不是独立供应商账单审计；预留上限仍为 10000 microusd。
- R19 从初始 Session 完整重放本次 Ledger，得到 1 条 accepted、0 条 rejected，最终 snapshot 与 Receipt/Godot mirror 一致；choice 与当次候选、Intent、command 和 entry hash 交叉核对通过。真实 Godot 记录 outbound 39 ticks / 1900 mm，路径、floor、capsule、domain 均通过；随后 walked-home，归位误差 0 mm。300 帧中位 234.796 FPS，仅适用于本次锁定本机运行，不建立跨 GPU 保证。
- 不可变观察目录为 `C:\tmp\matrix-oasis-r22-physical-e4caf44b51bae9ccdd84cbe9f0032eba5c286f8240777ac4e4d927827933c1f2`。`observation-report.json` SHA-256 为 `e4caf44b51bae9ccdd84cbe9f0032eba5c286f8240777ac4e4d927827933c1f2`；physical observation 为 `f9061b97339f2a752cf8ff9e5732fd33263e1a65f2fcf3e083f8893acc5aa9ae`；Receipt 为 `ddbc09e0144a2d3f86383ecd5a920e9ddcd195ac96a8ae92c532d9b3ea0191ad`；Ledger 为 `74fb0c5d4ca416366c8ceb7b4b3bb619a0699fd02d859f7017929c9ee4473adb`。独立只读重放在内存中生成的 replay report SHA-256 为 `425428d9630184336fb472f0cdeb748c7e05eada96c6eeff345f27466625d652`，未冒充新增落盘文件。
- 用户随后明确确认：“我判定对白正常，NPC确实移动后返回”。本记录只将本次真实对白可理解与 NPC 移动/返回的人工观察记为通过。原观察报告的 `manualAcceptancePassed:false` 和 `qualificationStatus:unqualified-manual-observation` 保持原始字节，人工确认由本任务卡另行关联，不倒改历史报告或离线资格布尔值。
- 本次人工确认不等于全部 R22 人工项、整轮收口或 PR 批准。剩余门包括核对当前实现身份下中性案例、拒绝/降级、窄屏键盘与恢复等既定资格项；缺失项只按原边界补证，不能用本次成功覆盖。最新跨轮完整 `verify`、extraction、父 client 干净回归、两个剩余本地提交及最终验收文档仍待收尾；R22.7/V2 状态未切换。
- 本次确认记录仅修改本任务卡，无生产代码、合同、资产、旧证据或预算变更；未追加 Provider 请求、读取凭据、commit、push 或创建 PR。旧诊断账户预算 SHA-256 仍为 `bf10efed661c204888564cdb970e885306b7da2c7adc014d0b1e5ee1fcb0f8b3`。回退只撤销本节文档增量，不撤销已发生的调用或删除任何仓外历史。
- 文档补录后 `verify:r22-references` 4/4、`BOUNDARY_OK checked=1545 tracked=1528`、`ROUND_SCOPE_OK checked=123 changed=100`、固定基线 `PARENT_SCOPE_OK checked=123 changed=100`、V2 声明门及 `git diff --check` 通过；未把这些轻量检查写成完整回归或 extraction。

### 获准收尾与发布前证伪（2026-09-19）

- 用户随后授权“收尾后提交 PR”，并允许正常关闭本次已完成预览。Godot 正常退出、Host 完成清理，旧观察、Receipt、Ledger、预算和资格源全部保留；这不是新增模型调用授权。
- 收尾记录集中在 `docs/rounds/R22_FALSIFICATION_EVIDENCE.md`，区分最新真实样本、局部人工确认、离线证伪、测试隔离事故、父主线失败与仍待完成的门。不能用已有真实对白成功替代中性场景、降级或窄屏等缺失观察。
- v0.5 诊断新增真实子进程强杀/恢复补测。首次夹具错误可能造成最多两次合成假密钥的外发尝试，不能记为零外网；已告知用户并停止该实现。修正为先安装网络/凭据隔离再导入、显式保活与有界清理后，定向 3/3、完整传输 188/188 通过；生产事务未为补测改写，独立只读复核无新可操作缺陷。
- 最新主线 `61b6eea80152904eb8fb3cc0ebba5e7647594828` 干净父 client 的完整测试为 1033 通过、2 项价格相关失败；类型检查、构建及单独 header 测试通过。用户明确批准如实披露该基线例外、继续 R22 收尾和 PR、不修改父代码；这个例外不覆盖 R22 自身失败或人工门。
- 本地提交、clean-HEAD extraction 与最终发布只按实际执行结果补录。`V2_STATUS` 仍保持进行中且 `claimAllowed=false`；完整收口前不提前写整轮通过。

### 离线人工补验入口（2026-09-19）

- 用户明确说明尚未完整检查中性场景、拒绝/降级、ending/reset 与双尺寸键盘 UI，并批准“允许补离线故障验收入口，全部补验后收尾”。本批只提供限定验收入口，不新增生产能力、真实调用批准或资格结论。
- 新命令 `preview:r22:offline` 强制假 Provider，场景仅允许 `normal / timeout / refusal / invalid-response / injection`，尺寸仅允许 `960x540 / 640x540`。拒绝 Provider 切换、凭据文件、自定义 URL、响应文件及脚本；所有故障仍经过真实 Provider 解析器和既有 Host/R20/Godot 链。timeout 等待原 30 秒 AbortSignal，不以直接写入 fallback 冒充传输超时。
- 新生成的仓外 Godot project 只调整明确标为 `OFFLINE FAKE QA` 的窗口标题及四项窗口/逻辑 viewport 尺寸；不修改或复制改写冻结的 Godot 场景。审批和对白仍使用原纯文本 Control，结束后原正常入口不变。
- 手工验收 profile 绑定 session manifest 和 observation report 的独立 0.2.0 记录。普通入口保留 0.1.0 字段构造；恢复时场景或尺寸变化必须 fail closed，不能将旧根改成另一种故障。新入口及其可达实现进入 implementation hash；因此新离线实现身份不冒充此前真实 Luna 样本的实现身份，也不倒改该样本证据。
- 本批是一个闭合目标，允许超过五文件：入口/编排、限定脚本 allowlist、事务恢复测试、CLI/组合测试与本任务卡/证据说明必须同步。Astra负责恢复测试、边界接线和集成；两个Sol分别只编辑入口及其测试、composition及其测试；独立只读审查不得与实现者共享编辑范围。全部旧未提交工作保留。
- 新批前完整跨轮 `verify` 已退出0、31步全部通过，R22为2004通过/0失败/1项既有Windows symlink权限跳过，聚合测试941/941。日志 `C:\tmp\matrix-oasis-r22-closeout-verify-20260919-a.log`，SHA-256 `b031d903aaa362bf135faeb336541b9079a6e7571e177eb8da8005fb2c645f1c`；当时待提交tree为 `a35447c1690bbd4000107357a9b0107e261749c3`。该结果不覆盖随后新增入口，须另行补测；extraction与人工补验仍待执行。
- 退出前检查零官方派发/凭据读取、每Turn单次fake、精确profile恢复、独立新输出根和无残留进程。回退只撤销本批入口及接线增量，不清理旧真实Receipt、预算或资格源；不解除R22.7或R25声明门。
- 2026-09-20新入口完整 `verify:r22` 退出0：2017通过、0失败、1项既有Windows文件symlink权限跳过；实际Godot4.6.3导入/probe通过。日志SHA与分组结果见收尾证据。原场景和原live launcher源码hash不变；手工profile已绑定独立新实现身份。人工检查单六项仍待逐一观察，不能从专项绿测推定通过。

### 六项离线窗口补验完成（2026-09-20）

- 用户逐项完成中性 normal 双尺寸、末班地铁 injection/timeout/refusal/invalid-response；每项都重新校验真实 R19 commit 链、Receipt、Godot command/arrival/mirror、300 帧与 reset，不仅依赖用户回复或 READY marker。
- 拒绝审批为零请求，三个故障响应各为一次假请求、无重试、无映射 AI Action；降级后允许的固定策略动作单独标识。六个预览均正常退出，历史观察报告与资格布尔字段不倒改。完整运行路径、hash 与限制见 `docs/rounds/R22_FALSIFICATION_EVIDENCE.md`。
- 这完成六项窗口人工观察，不自动替代 head 漂移/跨进程恢复/隐私摘要的人工审阅，也不替代新入口后的全模块回归、clean-HEAD extraction 和最终文档。没有追加真实模型调用或读取凭据；R22.7、V2 状态与发布门尚未切换。
- 随后逐项披露上述三类证据的自动故障注入/真实子进程/限定扫描边界，用户明确回复“接受这些证据，继续收尾”。据此完成人工证据审阅，不冒充用户执行过故障操作；全量回归和 clean-HEAD extraction 仍需成功后才执行最终收口及已授权 PR。
