# R22候选与Provider二次核查

R22没有直接继承R18的推荐结论。本轮重新固定与“单次受限认知”直接相关的来源身份、顶层直接许可证和适用边界；没有执行候选代码，也没有证明传递许可证闭包。

## 只读 billing 观测器核心（2026-09-19；未接入实时采集）

用户同意先实现观测器。此次再次核对 OpenAI Docs 的[兼容性规则](https://developers.openai.com/api/reference/overview#backwards-compatibility)及 Context7 的 Responses 资料：新增响应属性可以兼容，但此次取得的资料仍未定义 billing 的具体结构或计费含义。因此实现的是私有、纯函数的诊断词表，不是新的供应商 Schema 或生产放行规则。

实现文件为 `packages/npc-cognition-provider-openai/src/billing-observer.mjs`，私有接口 `observeBilling(value, binding, { present })`。该文件没有从包入口导出，没有被 Provider、Host 或现有诊断传输导入，也没有写盘或网络能力。实时读取、披露、审批、预算及原子发布接线均未在本批完成；不得描述为已能采集真实 billing。

### 固定采集与隐私边界

| 固定路径 | 可以记录 | 不可以推断或保留 |
| --- | --- | --- |
| payer | JSON 类型；精确 developer/user 字符串的固定类别，其余字符串归 other_string | 真实账户、项目或付款责任；任何其他字符串原值 |
| currency | JSON 类型；精确 usd 类别，其余归 other_string | 账单币种保证或汇率 |
| service_tier | JSON 类型；精确 default 类别，其余归 other_string | 根级服务档位覆盖、实际费率或折扣 |
| amount、tool_costs.total | JSON 类型，不记录数字或字符串数值 | 免费/收费、正负、零/非零、金额大小；JSON 数字下溢或舍入前的原值 |
| tool_costs | JSON 类型；下级只识别固定 total 路径 | 工具调用或费用授权 |
| 其他任何键 | 全树节点/类型及未知字段数量 | 动态键名、值、原始对象或其内容 hash |

- capture profile 为 `matrix-oasis.r22-billing-observation/1`，policy SHA-256 为 `466b3aacdbdc9390eb3d0d6f47b975f315f57d8424679923bddfc627240f8789`。该身份与旧 tool_usage capture policy 不同；旧审批和已经消费的两个诊断名额不得复用。
- 每份观察或静态失败均标为 `semanticCoverage:observation_only`、`qualificationEligible:false`，没有 ok/approved/Action/费用结果。call-plan 与 diagnostic-approval hash 只用于关联，不证明有效批准，也不宣称匿名化；后续 Host 必须独立核验新的内容绑定批准。
- 深度上限 8（根为 0）、节点 128、每对象 32 键、数组 127 项、累计解码字符串与键名 UTF-8 65536 bytes、canonical 观察 8192 bytes。输入必须先经过完整响应的有界严格解析；观测器不接收 wire bytes，不替代重复键、数字词法或完整响应字节门。累计文本上限不等于 wire 大小上限。
- Proxy（包括 revoked）、accessor、异常原型、非枚举/符号键、非法 Unicode、非有限数字、非 JSON 值、稀疏数组及循环均静态失败。反射前检查 Proxy；不执行 getter、toJSON 或调用方 callback。缺失与 null、不可捕获后代与缺失后代分别记录；点号键、数组中的字段及未知父项不能冒充固定路径。
- 所有输入保持不变，输出深冻结；失败丢弃全部部分观察，仅保留固定状态及合法关联 hash。合法 JSON 中的数字全部 type-only；例如 0、非零、负数、超安全整数和解析后下溢为零产生同样的金额观察，绝不据此解除费用不确定。

### 执行证据与限制

- observer 专用测试 **133/133**：六路径 × 六类型、未知原文/键/hash 诱饵、大小边界、非法对象、零回调、假授权、输入不变及 20 次/键序变化 canonical 一致。跨真实 Provider 校验入口的 **23 种合成形状 + 1 父测试**观察前后结果逐字节一致；非 null billing 仍拒绝、无 Proposal，保守费用行为不变。
- 最终 Provider 全套 **1085/1085**、Host **88/88**，均无跳过；使用 fake transport 与合成凭据，没有真实请求或真实密钥读取。Host 的既有反例实际验证 Receipt 全额 10000 microusd 保守记账、无 AI Intent 与恢复零请求；它不是实际供应商账单。
- 新测试首次将 Provider `actualCostMicrousd` 误断言为 10000，聚焦组为 2 pass/22 fail。检查真实 Provider/Host 实现后纠正：Provider 实际费用为 null、costUncertain 为 true；Host 才扣满预留。未修改生产代码，原聚焦命令重跑 24/24。首次 boundary 被新测试正则中的禁用网络词触发；移除该重复词项，网络禁止仍由原 boundary 检查承担，未改扫描器或 allowlist，原命令重跑通过。
- 最终 Provider 日志 `C:\tmp\matrix-oasis-r22-billing-observer-provider-20260919-b.log`，SHA-256 `37d4d79fbb5ea466713dc21fc1a89822ed6c36a68b132f7e0f608694bd31b7a3`；Host 日志 `C:\tmp\matrix-oasis-r22-billing-observer-host-20260919-a.log`，SHA-256 `9dbd753ffb1e1e7d481a039e28f3fe87f5a4d8ba65473eed536788fe2b88354e`。补充类型矩阵前的 Provider 1048/1048 日志另留存为 `C:\tmp\matrix-oasis-r22-billing-observer-provider-20260919-a.log`，未覆盖。
- 实现 SHA-256 `50df857501f4572e7f50f455e4b44bed656d0cdd84ce61d742d7144e2d8b2c97`；专用测试 `8c38f02efaae585e2e684f570ab8d1926221039735a676d58bd6f611ddb23c46`；conformance `8250281e540fcf8cb1dca246e2bbc0eebf4c1c460c588b9a613f536887753e21`。
- boundary1543/1528、round121/98、固定 base parent121/98 及 diff 检查通过。Provider 入口、旧 tool_usage observer/profile、Host、call-store、MVP/V2 七项文件的 SHA 与本批开工前一致；暂存为空。本批恰为五文件增量（其中两个新文件），没有提交、push 或 PR。
- 本批未运行完整 `verify:r22`、跨轮 `verify`、extraction、父 client 或 Godot 实时/人工资格。不能用本地词表测试补齐真实上游语义、实际费用、对白、物理 Action 或 R22.7 证据。回退只撤销五文件本批增量，不删除旧观察或重算预算。

下一步只可单独实现诊断接线：在有界严格解析之后保留原响应不变地观察，独立验证新采集 policy/完整 payload/审批身份和事务预算，再验证崩溃及零重试。新实际采样仍需新的完整披露和当次批准；本批既未申请也未消耗新名额，不自动进入真实调用。

## billing 假设矩阵与下一步判别门（2026-09-19；只做离线）

用户说明上次官方工单未获回复。本批不再把等待回复当作唯一研究路径，但也不把本地猜测当作供应商协议。通过 OpenAI Docs 实际读取的[兼容性规则](https://developers.openai.com/api/reference/overview#backwards-compatibility)明确允许增加响应属性；Context7 的 Responses 示例没有给出 billing 定义。文档缺失既不能证明该字段非法，也不能证明它没有费用影响。

### 固定假设及证据等级

| 假设族 | 离线样例 | 当前处理 | 后续观测能回答什么 |
| --- | --- | --- | --- |
| B0 缺失/null | 两种旧路径 | 其余门通过才接受 | 是否仍适用旧 profile |
| B1 空对象 | `{}` | 拒绝、费用不确定 | 是否确为零字段，而不是被脱敏成空对象 |
| B2 付款方元数据 | 假设 payer 为 developer/user/null/其他字符串/嵌套对象 | 全部拒绝，不猜测付款责任 | 是否恰有固定键、值是否属于预先披露的类别；不证明无额外收费 |
| B3 金额/币种 | 假设 amount 为 0 或非零，currency 为 usd | 全部拒绝；零值不当作免费 | 有无货币/计费数据，是否必须转人工费用核对 |
| B4 工具费用/不同层级 | 假设 tool_costs 为零或非零、billing 内声明不同 tier | 全部拒绝，不接受嵌套元数据覆盖 root/usage | 是否存在工具或费率歧义，不能由纯语言 token 估价掩盖 |
| B5 类型错误/未知数据 | array/string/number/boolean、私有嵌套字段、伪造 Action 批准 | 全部拒绝、不泄漏值、不赋予权限 | 只归类结构，不解释未知键值或执行任何内容 |

除旧路径与此前已核的 reasoning context 外，上表 billing 键名和值均是**人工合成假设**，不是官方 Schema、已捕获响应或概率排序。没有把某个猜测写入生产 allowlist。

### 可执行验证与结果

- 测试位于 `packages/npc-cognition-provider-openai/tests/response-conformance.test.mjs`，复用现有独立合成请求及真实 Provider 校验入口，仅注入 fake fetch；不新增第二套生产校验器。测试请求只使用合成凭据，不读取真实 env/key。
- 23 种 billing 假设 × 3 种现有 tool_usage profile × 4 种 reasoning profile = **276 组合**。缺失/null 的 24 组合通过；其余 252 组合均只因 billing 被拒绝，同时 28 项核心检查通过。只在测试副本中移除 billing 后，这 252 组合恢复合成基线成功。该对照定位本地拒绝门，不构成产品自动剥离建议、真实服务端证据或计费安全证明。
- 11 类独立故障 × 5 个对象假设 = **55 交叉反例**：未知 root、工具 output、Proposal 扩权、choice 伪造、Schema/context/model/tier/usage 漂移、非零工具计数和未批准 reasoning mode。即使从测试副本删除 billing，也仍然拒绝；未知 billing 不覆盖原故障优先级，不降低保守费用。
- 13 种对象假设各重复 20 次，包含根内字段重排，诊断与拒绝结果 canonical bytes 完全相同。此结果同时证明**现有浅层诊断无法区分付款方、空对象、零费用和非零费用对象**；不能用重复原请求消除该证据缺口。
- 另有 6 个 wire 反例：重复/转义重复根键、重复嵌套键、非法数字、深度超限和响应字节超限。全部失败关闭。每次本地执行后重复同一审批均零重发；原始合成数据、未知字段和诱饵值不进入返回诊断。
- 新增聚焦用例 **341/341 通过**（包含 4 个父测试，不能把它当作 341 次真实调用）。完整 Provider 回归 **928/928**、既有 Host 回归 **88/88**，无跳过。Host 覆盖审批前零凭据/请求、全额保守预留、Receipt/恢复及零重试；本批未改变其生产实现。
- 完整 Provider 日志：`C:\tmp\matrix-oasis-r22-billing-hypotheses-20260919-a.log`，SHA-256 `21b3f1aec7a7e5bdec01067a75f4ceecbf79e770e36288ac71ceb55543ec0c85`。Host 日志：`C:\tmp\matrix-oasis-r22-billing-host-20260919-a.log`，SHA-256 `cedb911b1597dc475a1ca336c45de5f748d556ad4b703422f0be0d6b5a586af6`。测试文件 SHA-256 `0f55e961b354c4beebcbcf0a7d7dbe90461e3a48e6de2eb9b308e97a4a550cb0`。
- 环境失败如实保留：第一次日志重定向被沙箱拒绝，测试没有启动，PowerShell 最终 exit 0 不作为通过；Host 首次在沙箱内因 mkdtemp EPERM 为 3 pass/85 fail，属于夹具目录权限阻断。在正常权限审批后原命令完整重跑得到上述通过结果，未修改失败断言或改用更弱的测试。
- 范围验证：`BOUNDARY_OK checked=1541 tracked=1528`、`ROUND_SCOPE_OK checked=119 changed=96`、固定 R22 base 的 `PARENT_SCOPE_OK checked=119 changed=96` 及 `git diff --check` 通过；staged 为空。Provider、Host、call-store、MVP、V2 五项文件哈希与本批开工时相同。本批没有生产代码改动，没有网络 Provider 调用、真实凭据读取、提交或发布。

### 上批收口时的建议（保留时序，核心实现进展见本文首节）

1. 先实现并离线证伪一个只读 billing 结构观测器：固定词表、浅层类型、固定类别与未知项计数，不保存原始值、动态键名、账户/项目标识、完整响应或对白。不得从观察器返回 Provider 成功、Action 或价格结论。
2. 只有观测器经过隐私/预算/崩溃矩阵，完整外发 payload、采集词表、1 次请求/$0.01/零 retry 和费用不确定语义得到**新的一次性批准**后，才可考虑一个新样本。本批没有建立新诊断名额、读取凭据或复用已消费批准；旧两名额硬门不自动扩展。
3. 若观察为纯受限归属元数据，可再评估“窄兼容并保守记满预留”的方案；这仍是待批准的语义变更，不因矩阵通过而自动实施。出现金额/工具/费率/未知字段则保持阻断。重复形状也只证明样本一致，不证明完整协议或最终账单。
4. 真实对白、R20 移动、R19 Action 和 R22.7 人工资格必须继续独立验证；不能将固定策略 fallback 或离线对照成功算作 AI 成功。本批不修改 MVP/V2、Godot、Creator，不提交/push/PR，不重跑跨轮全模块 verify、extraction 或父 client。

## 生产取舍

生产实现固定为`internal-bounded-cognition-native-control`：内部有界上下文与事务适配器、Godot原生`Control`、R20移动链和R19裁决。新增第三方运行依赖为零。

| 候选 | 固定来源 | 顶层直接许可证 | R22结论 |
| --- | --- | --- | --- |
| Dialogue Manager | `v3.10.4 / 5487c524…`与`v4.0.3 / ffc0011a…` | MIT | 仅作为以后可信作者对白的表现层备选。v3适配Godot 4.6但表达式/场景成员访问面不能承接模型文本；v4面向Godot 4.7。 |
| LangGraph | `v1.0.5 / 84023451…` | MIT | 只参考持久化与人工中断。interrupt恢复会从节点开头重跑，不能包裹本轮付费单次调用。 |
| AutoGen | `python-v0.7.5 / 83afbf58…` | MIT（代码） | 拒绝作为R22生产依赖；多Agent、工具与状态服务面超出本轮，且官方已将项目置于maintenance mode。 |
| CAMEL | `v0.2.90`注释tag对象`a3a21ef…`，目标commit`deb286f…` | Apache-2.0 | 延后参考；工具、记忆、存储和多Agent表面没有为单轮认知提供净收益。 |

Dialogue Manager的安全问题、LangGraph interrupt语义、OpenAI模型/Structured Outputs与数据控制说明只作为公开文档证据，不作为可执行依赖。`store:false`不得描述为ZDR；官方默认abuse monitoring可能保留内容最多30天，且可能使用prompt caching。

## 重新评估条件

- 只有可信作者对白确需可视化脚本表现，且严格关闭表达式、mutation、动态资源和场景成员访问后，才重新资格Dialogue Manager。
- 只有R23之后出现跨多个本地纯函数节点的持久编排需求，并有付费outbox幂等证明，才重新评估LangGraph。
- AutoGen/CAMEL只有在产品明确批准多Agent或工具编排范围后才能重审，普通对白功能增长不能触发。

## 证据边界

`third-party/npc-cognition-references/reference.lock.json`锁定commit、tree和直接许可证字节。它不证明候选可运行、传递依赖许可证、安全配置或生产适用性；R22正确性只能由内部实现的离线证伪、loopback资格和一次另行批准的真实调用证明。

## 2026-09-19：协议默认值纠偏与独立证据门（本地修正）

此节为最新纠偏；下文2026-09-12的字段表及后续各批记录保留为历史证据，不将旧绿测改写为真实资格成功。

### 依据、已证实问题与未核清项

- 重新读取[官方 Responses create 返回定义](https://developers.openai.com/api/reference/python/resources/responses/methods/create)，并通过Context7核对。`Reasoning.context`允许`auto/current_turn/all_turns`；GPT-5.6系列省略该配置时默认`all_turns`，返回的是实际采用的模式。本地请求只配置`effort:none`，旧响应限制却只允许`current_turn/null`，是不一致的本地假设。
- 新独立协议oracle只经公开Provider接口，手写有来源依据的三值枚举，不读取生产白名单或其它矩阵预期。修正前`current_turn`通过、`auto/all_turns`均在reasoning规则失败；修正后同一夹具均通过。既有470项全绿只能证明旧profile自洽，不能作为供应商协议兼容证明。
- 最新一次真实记录为HTTP 200、原28条主检查全部通过、`fieldPolicyFailures:[billing,reasoning]`。原始响应没有留存，不能断言其reasoning失败恰好由`all_turns`造成，也不能重建billing的内部形状。Receipt SHA为`7f4e8c965e7d2bf94698b0c17c22d4a0ca30c4560340c30f5b8ace33ee601469`；一次派发、无AI Intent、10000 microusd保守扣账，不是实际供应商账单。
- 本次官方返回定义未找到billing字段；检索未找到不是字段非法、无费用或应忽略的证明。非null billing继续阻断，不能只修reasoning后就宣称新实测条件满足。

### 按职责区分的接受边界

| 类别 | 本批处理 | 不能据此放宽的门 |
| --- | --- | --- |
| 业务结果与权威 | 三键Proposal、context身份、opaque choice、R19/R20授权全部不变。 | 未知Action、工具、资源、跨actor/节点和过期输出仍拒绝。 |
| 费用、能力与会话 | model、Standard、usage整数、工具、未核清billing、conversation/previous_response_id仍独立检查。 | 不以普通元数据兼容为由推断费用为零、允许工具或接入远程会话。 |
| 有官方依据的返回配置 | 只把reasoning.context补齐为三种官方枚举；字段本身不进入业务结果，不写下一次请求。 | effort仍只允许none、mode仍只允许standard；其它枚举/未知键保守拒绝。当前profile不是完整API Schema。 |
| 未知协议扩展 | 保留现有fail-closed限制并显式记录证据缺口。 | 官方向后兼容允许扩展，不意味着任意新字段都可安全忽略；不能宣称本地闭合profile兼容所有未来响应。 |

请求payload、Schema、价格、审批身份计算、预算内核、原28规则顺序和失败优先级不变。`all_turns`只作为有效返回配置被丢弃；不保留reasoning内容、不增加历史输入，独立reasoning输出项仍不符合本轮输出硬门。

### 无正文的失败诊断

- `fieldPolicyChecks`只在billing/reasoning失败时附加七个固定规则，每项仅`rule/status/jsonType`。规则为billing缺省/null、reasoning闭合结构及effort/mode/context/summary/generate_summary；不会记录值、内部对象键、数量、路径、URL、response ID、玩家/模型原文或凭据。
- 诊断共用被审查的字段谓词，但不决定接受、定价、Receipt或发布；生成异常不得覆盖原拒绝。不合法父结构的子项为`not_checked/unavailable`，避免猜测具体子值。
- 既有`emptyRecord`与`descriptionString`是描述性谓词，不是接受结论；主接受结果仍看`checks`。新增静态细节不把未知billing变为可定价证据。
- 合成测试覆盖多字段同时失败、失败优先级、20次canonical一致、私密诱饵不泄漏及Host重开不重派发；fixture不冒充已丢弃的真实响应或费用证明。

### 本批离线验证结果

- `verify:r22`退出0：1152个测试条目，1151通过、0失败、1个既有Windows文件symlink权限跳过。分组为治理95、CLI/live/恢复/诊断332（331通过）、参考4、合同14、Runtime13、Provider587、Host88、Godot19；Godot4.6.3实际import/probe均通过。这不是实时游戏或真实模型资格。
- 新oracle为12/12，旧Provider+矩阵+conformance+oracle聚焦组495/495；实际Provider解析器+fake fetch到Host/store的新增6条测试通过。该Host测试使用既有operation seam，不声称单一用例贯穿官方live默认executor、物理移动及R19；live-provider另行42/43通过，1项同为Windows symlink权限跳过。
- 初次沙箱执行被仓外夹具写入EPERM阻断，授权提升后执行；其后一次Provider/Host运行是587及87/88，唯一Host失败为旧预览占用43122。用户单独批准关闭已核对的Host55508/Godot50292后，两者正常退出，原命令重跑为587及88/88。没有更改端口、断言或跳过失败测试。
- `check:boundary`为1541/1528、`check:round-scope`与固定base的`check:parent-scope`为119/96，`git diff --check`通过。收口时43120/43122监听数为0、对应R22预览/验证进程为0；上文旧失败Receipt的SHA保持不变。
- 仓外验证摘要（不是完整原始stdout；工具有一处截断）为`C:\tmp\matrix-oasis-r22-context-protocol-fix-20260919-verify.log`，SHA-256为`31cc3e0af60d34c026691981566eadbe4b520463367dad31883d840160bcdc88`，含五个实现/测试文件的实际SHA。两位Sol分别执行不重叠测试任务，Astra复核并执行集成；独立协议审查未发现本批额外放宽或诊断泄漏。
- 本批只改Provider实现/类型、conformance、独立oracle、Host测试及两份文档七个路径。HEAD仍为`9adb5493501a954360edf6525542ffface55653e`，暂存为空，既有23 modified/12 untracked均保留，仅新增oracle后为23/13。跨R1–R22完整`verify`、extraction和父client clean测试/build没有在本修正后重跑，不能以本轮结果替代收尾门。

### 仍关闭的真实资格门

本批零新增Provider请求、零真实凭据读取、不重开消费过的批准、不重估历史扣账、不修改R19/R20/Godot/Creator或MVP/V2。还须核清billing正式结构及费用语义，完成相应离线正反例，再展示新计划取得新的当次批准。R22.7、真实AI Action与人工验收继续未完成。回退只撤销本批枚举、诊断与测试/说明，不删除或改写旧证据。

## 2026-09-12：Responses 响应契约二次审计

### 依据与能确认的根因

- [官方向后兼容规则](https://developers.openai.com/api/reference/overview#backwards-compatibility)允许新增响应属性、改变属性顺序和不透明 ID 格式。整包白名单是 R22 的本地能力限制，不是完整的供应商协议；Structured Outputs 只约束指定的模型结果 Schema。
- [固定 OpenAPI 源](https://github.com/openai/openai-openapi/blob/38170fdddbb6a1813eae6c6587ee17cf2987185b/openapi.yaml)与 Context7 返回的公开定义交叉核查。`TextResponseFormatJsonSchema.description` 是可缺省的 string，官方定义没有证明 nullable。真实 c 的脱敏诊断只能证明它实际返回 null。因此本轮将 null 记为**已观察的服务归一化**，不改写为官方保证。
- `PromptCacheOptions` 返回定义要求 `mode`、`ttl`，可选 `comparison_response_id`；与请求 Param 类型分开核查。`Reasoning` 的各属性可缺省，官方没有 `effort=none` 时其它字段必须为空的条件。下表中对这些字段的限制属于更窄的本地 profile。
- 未找到可作为合同依据的 `tool_usage` 定义。真实 c 只保留“非空普通对象”，没有其内部键值；公开 issue 中的他人响应不是规范，也不能代表 c。不得臆测其中都是零计数。

已证实的直接失败位置是本地 `echo_text_format` 与 `echo_tool_usage`，不是 HTTP、模型身份、Standard 档位或 Proposal 其它检查。另有系统性问题：旧根字段清单对部分已知名字不校验其值，既可能误拒绝兼容元数据，也可能接受未披露能力。独立测试用 17 个反例重现后者；这是本地证据缺口，**不是已经观察到真实越权或额外计费**。

### 本地字段策略：matrix-oasis.responses-envelope/1

所有输入先经 64 KiB、UTF-8、深度 256、重复键和普通对象检查。下表覆盖全部允许的 root 字段；未知 root、未知嵌套业务字段继续拒绝。本轮没有新增接受的 root 名称。

| 字段 | 类别与本地处理 |
| --- | --- |
| `object/status/error/incomplete_details` | 完成硬门：response/completed/null/null；拒绝与不完整结果不能当对白。 |
| `model/usage/service_tier` | 身份和费用硬门：精确 Luna、独立整数用量与价格计算、Standard default；不能由元数据覆盖。 |
| `output` | 权限硬门：仅一条 completed assistant message、一段 output_text；拒绝 tool/refusal/其它输出。这是 R22 的限制，不是完整 API 输出规则。 |
| `background/store/truncation/max_output_tokens` | 若返回必须与已批准 false/false/disabled/512 一致。 |
| `instructions` | 若返回必须与完整已批准 instructions 一致，不作为新的指令执行。 |
| `previous_response_id/conversation/prompt` | 缺省或 null；不接受额外会话、模板或历史来源。 |
| `tools/tool_choice/parallel_tool_calls` | tools 必须空；choice 仅 auto/none；parallel 仅 boolean。仍须通过无工具输出门，不能仅凭 tools 空推断无收费。 |
| `tool_usage` | 仅缺省或空普通对象沿用旧接受范围；非空、null、数组、零样式嵌套值一律阻断。空对象也不是独立的“没有调用工具”证明。 |
| `frequency_penalty/presence_penalty` | 已观察扩展，仅缺省或正零；不推断其它值与本轮批准配置等价。 |
| `text` | format 的 type/name/strict/schema 与批准内容精确绑定；可选 verbosity 限 null/low/medium/high 后丢弃。 |
| `text.format.description` | 缺省、观察到的 null、既有安全 NFC 字符串（最多 2048 UTF-8 bytes）归一化时丢弃。请求侧 description 可指导生成，但返回侧此值不获得本地执行权限；不允许它替代 Schema 或结果校验。 |
| `metadata` | 缺省/null/空对象；不保留用户自定义数据。 |
| `id/created_at/completed_at` | 只读元数据，校验后丢弃：ID 安全文本 1–256 字符；时间为非负 safe integer，completed_at 可 null。不是厂商 ID 长度保证，不进入裁决/时钟逻辑。 |
| `agent/billing/context_management/moderation/prompt_cache_diagnostics` | 未核清或本轮未启用的能力/成本/安全元数据：只接受缺省/null；不再凭已知名字接受任意对象。这里的 null 仅作为字段未提供，不证明上游未发生任何行为。 |
| `max_tool_calls/prompt_cache_key/safety_identifier/user` | 缺省/null；没有批准非空额外能力或身份绑定。 |
| `reasoning` | 缺省/null 或闭合对象；effort 若有须 none，mode 若有须 standard；context 仅 null/current_turn；summary/generate_summary 仅 null/auto/concise/detailed。不执行 reasoning 内容；输出与价格仍单独审查。 |
| `prompt_cache_options` | 缺省或闭合 `{mode:implicit,ttl:30m,comparison_response_id?:null}`。不请求 explicit cache 或比较响应。TTL 是缓存断点最短生命周期，不冒充下行最大保留政策。 |
| `prompt_cache_retention` | 缺省/null/in_memory/24h；属于已披露的可能 caching，不意味着 ZDR。收费仍按严格 usage 分类计算。 |
| `temperature/top_p/top_logprobs` | 丢弃的生成配置元数据；各自限 null 或 0–2、0–1、正零。最终文本预算和 Proposal 权限不变。 |

message 的 id/role/type/status/content/可选 phase 与 output_text 的 text/annotations/可选 logprobs 沿用原硬门。最终只有 `contextSha256/dialogueText/actionChoiceId` 三键进入临时结果；Schema/enum、当前 context 与本地候选集均独立复验。response ID、额外配置、description、工具元数据不进入 Receipt、Ledger、R21 或日志。

新增失败详情只含固定 profile 名与最多 19 个固定字段名，记录在原脱敏观察通道。原 28 条规则先决定主要拒绝码/阶段；仅当它们均未拒绝时，新增字段失败才选 `envelope_profile`，避免抢占 refusal、usage 或 choice 等诊断。**新增字段失败时费用仍须保守占账**：哪怕已核清语言 token，未知 billing/能力字段也不能被误报为已精确计费，因此 usage/actualCost 置 null、costUncertain 为 true；不据此退回预留。没有新增字段失败的既有费用行为不变。

多个异常同时检查，不能让一个 echo 错误掩盖其它已可安全检查的错误。`descriptionString=failed` 只表示观测值不是 string；null 归一化是否接受以 `echo_text_format` 为准。`effort:none` 锁定的是请求与返回配置，不额外承诺服务端内部 reasoning token 必为零；现有 usage 仍将其计入 output token、总价及输出上限，任何独立 reasoning 输出项仍被输出硬门拒绝。本批没有凭推测增加零 token 断言。

### 实测重开门：仍关闭

三步整改不等于真实资格完成。`description:null` 的本地误拒绝已可定向消除，但 c 的非空 `tool_usage` 仍不能重建或解释。不得把合成 `{count:0}`、测试预期拒绝、正常固定策略降级或 Godot import 当作该问题解决。

再次付费前必须取得可核验的公开协议依据，或经单独批准、仅含必要结构且不含内容/凭据的既有响应材料；据此明确工具能力和费用语义并建立无需真实请求的正反夹具。若做不到，保持真实调用暂停，交由用户决定供应商兼容方向。本批不通过真实请求继续探测字段，不放宽工具/费用硬门，不改请求或重复使用旧审批。

之后还须由同一 Host 入口完成离线正反链、重新展示完整实际 payload、取得新的一次性审批，并独立取得真实 AI Action、物理链和人工验收证据。本批不会提前更新 R22.7 或产品声明。

### tool_usage 专项复核：证据缺口，不是已证明的额外工具调用

2026-09-12 在旧预览关闭后执行。没有新模型请求、凭据读取或响应内容留存；下列来源为公开只读查询，SDK 不进入生产依赖。

| 来源 | 本次核实结果 | 不能推出的结论 |
| --- | --- | --- |
| [OpenAPI `38170fdd…` 的 Response](https://github.com/openai/openai-openapi/blob/38170fdddbb6a1813eae6c6587ee17cf2987185b/openapi.yaml#L59690) | 固定全文 102141 行精确搜索没有 `tool_usage`，根对象未闭合禁止扩展。主智能体复核了固定原文及搜索结果。 | 未收录不等于不会返回，也不是非空对象一定有费用的证明。 |
| [同版 ResponseUsage](https://github.com/openai/openai-openapi/blob/38170fdddbb6a1813eae6c6587ee17cf2987185b/openapi.yaml#L63370) | 定义语言 token 及 cache/reasoning 明细，没有工具调用次数或工具专属费用维度。 | 不能用这些 token 验证另一未知字段或全部工具费用。 |
| [Node SDK `e69eb4e…`](https://github.com/openai/openai-node/blob/e69eb4e4a88e1322e82385d4c119cef30c298dff/src/resources/responses/responses.ts#L1042) 与 [Python SDK `e12b81d…`](https://github.com/openai/openai-python/blob/e12b81d3bbf644ec7045e152d69bc4b68d69cd48/src/openai/types/responses/response.py#L260) | 独立只读复核未在 Response / ResponseUsage 定义中找到该字段。这里只作为生成类型旁证，不作为完整服务协议。 | 不证明真实响应违反供应商合同，更不证明工具类别、必填项或零值语义。 |
| [官方定价的 Tools 部分](https://developers.openai.com/api/docs/pricing) | 工具既可能产生 token 费用，也有按调用、存储或容器计价的项目。没有在该页找到 `tool_usage` 与账单的字段映射。 | 不能将本地全预留占账写成实际账单，也不能仅凭标准 usage 推导额外费用为零。 |

OpenAI Docs 的 create reference 此次只返回生成页面指针；endpoint OpenAPI 片段只有指向 Response 的引用，未展开 components。两者都不足以证明未知字段语义。Context7 未命中精确字段定义也只是检索局限，不是规范性否定。

独立复核找到的第三方 issue 仅是他人的已脱敏、未完成 SSE 观察，缺少完整工具配置和完成态用量。没有复制其原始响应、采纳其工具类别为闭包或将其当作本次 c 的重建夹具。c 的内部键值已丢弃，不能由形态、hash、同模型他人响应或合成全零对象恢复。新增字段 profile 也无法对 c 的未保留原值追溯执行；“其余旧规则通过”不保证新 profile 对该旧响应全部通过。

本地直接拒绝原因已确认，但供应商为何返回该扩展及其费用语义仍未确认。**非空 `tool_usage` 继续 fail closed**；不忽略字段，不接受任意全零树，不更改 Request / Prompt / Schema / 价格 / 审批身份，也不新增 paid probe。

[ToolChoiceOptions](https://github.com/openai/openai-openapi/blob/38170fdddbb6a1813eae6c6587ee17cf2987185b/openapi.yaml#L66618) 明确说明显式 `none` 禁止工具。当前未提供工具定义并不证明已发生工具调用；未来若单独批准把禁用意图显式写入请求，它仍不能解释返回的扩展字段或替代费用证据，不能作为重开实测的捷径。本批不修改该请求约定。

无需新付费请求的下一步是取得供应商可核验说明，或经单独批准提供既有响应中仅 `tool_usage` 子树的脱敏材料，再结合正式费用依据建立正反夹具。仅取得一个例子仍不能代替类别穷尽或费用规则。供应商询问要点如下；这是本地草案，尚未对外发送：

1. 对 `POST /v1/responses` 的 completed、非流式纯文本响应，`tool_usage` 的正式 Schema、版本、必填项、可选项及类别扩展规则是什么？
2. 未配置工具时能否返回非空全零结构；null、空对象、缺省与各计数的语义分别是什么？
3. 它与 `usage`、输出中的工具项及实际账单如何对应；是否覆盖所有独立工具收费项目？
4. 能否提供官方无工具正例、发生工具用量反例及兼容性规则，而不要求重复购买诊断请求？

在上述证据足够前，保持供应商兼容阻塞和 R22.7 未完成；不以更多本地绿测或固定策略可玩替代真实认知资格。

### 最小脱敏诊断取证方案（离线观察层已实现；真实诊断未启用）

用户暂无既有材料，批准先完善本地、零调用方案。本节只冻结建议的数据范围、执行次序和待执行矩阵；**不是启用采集或付费请求的批准**，没有新增 CLI、Provider 开关、实际响应材料或已执行矩阵。现有 `echo_tool_usage`、费用和 Action 放行规则不变。

制定时的状态为方案获批、矩阵 NOT_RUN。随后用户授权“开始实现和矩阵”；当前已实现纯观察器、固定公开合成计划与共用 Provider 解析入口的离线 fixture，实际验证见下方“本地落实记录”。真实诊断发送、共享预算及原子发布未接通；模型请求 0，真实新证据无，业务接受规则未改变，R22.7 未完成。

#### 目标与非目标

- 目标：若以后另行批准一次独立诊断，能够辨别非空 `tool_usage` 是否落在披露的有限观测词表内，观察必要计数及遮蔽情况；不再仅留下“非空对象”这一条信息。
- 非目标：不恢复 c 的已丢弃原值，不证明完整官方 Schema、费用为零或工具类别穷尽；不让诊断结果决定对白、Intent、Ledger、R21 或 R22.7 的成功。
- 任何未知、遮蔽、超限或含义不明都必须保留为证据缺口。一次诊断可能仍不足以解决供应商兼容问题；不得以“这次必成功”为再次付费的依据。

#### 独立诊断请求的建议边界

后续实现只允许显式、默认关闭的诊断 profile，不能根据响应形状自动启用。产品预览、普通 Provider、原 Call Plan/Receipt 合同及全部 R16–R21 路径不变。诊断请求使用公开合成内容，动态 Action enum 只有 `null`；不读取真实游戏缓存、玩家输入、记忆或关系，不启动 Godot，不提供 R19 写入口。

仅诊断 profile 建议显式固定 `tools:[]`、`tool_choice:"none"`，继续固定官方 endpoint、Luna、Standard、非流式、`store:false`、输出/请求/响应预算及零重试。显式禁用工具有公开协议依据，但不能因此解释未知返回字段；这两个字段与合成输入是未来诊断的已披露请求差异，**本次没有修改产品请求**。若后续不能在保持普通入口逐字节行为不变的情况下隔离该 profile，停止实现，不借用宽松的第二套发送器绕过原安全检查。

当前 Provider 的 `PAYLOAD_KEYS` 会拒绝这两个额外字段，因此现有入口尚不能执行上述诊断。后续须先实现专用、闭合且只接受禁用值的受控 profile，并用差分测试证明普通入口仍拒绝额外字段；不能删掉原请求白名单或偷偷把诊断 payload 当成普通 Turn。

新的诊断批准必须同时绑定：完整实际 payload、model/endpoint/price/retention、现有 Call Plan hash、诊断 profile/version、固定观测词表及其 canonical SHA-256。观测范围变化与请求变化一样使批准失效；旧 Godot/真实 c 审批和普通 Turn 批准均不能启用诊断。预算 reserve/dispatch、一次请求和恢复语义继续复用已有内核，不能用新根目录绕过尚未核清的历史预算记录。

正式实现仍先用假 fetch 验证。后续若申请真实诊断，必须另行展示完整 payload 和保留策略，核清价格与禁用工具前提，并取得至多一次请求、最多 `$0.01` 的内容绑定批准；本方案没有消费或预先授予该批准。若费用前提无法成立则不发送，不能把本地占账上限宣称为供应商账单保证。

#### 允许保留的最小数据

建议诊断数据格式暂命名 `matrix-oasis.r22-tool-usage-diagnostic/1`，它是本地观察格式，**不是供应商 `tool_usage` Schema**。原响应仍只驻进程内存；原始字节、正文摘要及正文 hash 均不新增留存。

| 数据 | 允许保留 | 禁止或异常处理 |
| --- | --- | --- |
| 来源绑定 | 已批准 Call Plan/观测 profile 的 hash、受控模式、原有固定阶段和规则状态 | 不保存 response ID、请求 ID、header、时间戳、URL 回显、原始异常或凭据 |
| 根及固定路径形态 | absent/null/object/array/string/number/boolean 的固定枚举；父层不可安全解析时为 not-captured | 不从 shape 推断工具被调用或费用为零 |
| 固定完整路径下的计数 | 下述八个路径，且只能是非负、不是 `-0` 的 safe integer，值不超过 65536 | 负数、负零、浮点、过大数字、数字字符串等仅给静态类别，不输出原值；该上限是诊断最小化边界，不是官方计数上限 |
| 未知子树 | 有界节点类型计数、未知字段数和最大深度；标明 redacted | 不保存未知键名、键名 hash、字符串、字符串长度/hash、动态路径或未经批准的数值 |
| 完整性 | 捕获/遮蔽/限制/故障状态，观察范围版本 | 禁止输出 `toolsFree:true`、`schemaExplained:true` 或资格成功标识 |

八个计数路径只作为预先披露的观察词表，不作为来源锁、类别穷尽或必填条件：

```text
image_gen.input_tokens
image_gen.input_tokens_details.image_tokens
image_gen.input_tokens_details.text_tokens
image_gen.output_tokens
image_gen.output_tokens_details.image_tokens
image_gen.output_tokens_details.text_tokens
image_gen.total_tokens
web_search.num_requests
```

必须按完整路径匹配。未知父级下即使出现 `num_requests` 或 `input_tokens`，也只能进入遮蔽摘要，不能借同名提权。缺失字段保持 absent，不能补成 0。已识别数字仅是供应商返回的观察值，不参与费用计算，不声明其单位或计费权威。

形态和数值仍可能关联同一次请求，不宣称绝对匿名化。只有观测词表内、批准范围内的计量数值允许保留；所有业务文本及未知字符串都不保留。未知结构被遮蔽后可能仍无法解释该字段，这一隐私取舍不能隐瞒，也不能在看到响应后自动扩大采集范围。

字符串禁留指所有供应商派生文本，而非固定 profile/状态枚举；也不承诺内容从未进入解析器内存。已知路径全零、未知 `file_search` 子树含正计数是必须识别的反例。即使没有未知字段，词表仍不是官方闭包；所有未来观测固定 `semanticCoverage:observation_only`、`qualificationEligible:false`，不能生成 `toolUsageZero/noToolCharge` 结论。

#### 有界解析、隔离与发布

1. 先复用现有 64 KiB、UTF-8、重复键、深度 256 和安全普通对象门。语法或基础捕获失败时，仅保留固定错误状态，不提取原文片段；不得用 `JSON.parse` 后丢失重复键信息的副本冒充等价输入。
2. 只对 `tool_usage` 做独立只读观察：根深度为 0、子节点加 1，最大允许深度 8；容器和叶节点都计数，根计 1，最多 128 节点；每个对象 own-key 最多 32 个，键本身不另计节点。数组元素也计节点。循环、访问器、非普通数据对象拒绝，不能触发 getter/toJSON 等代码。最终完整观测 canonical JSON（含绑定元数据）UTF-8 字节数不超过 8 KiB；**8 KiB 是产物上限**，不是未实现的原始子树切片预算。原文输入继续沿用全响应 64 KiB 门，不为计算子树大小另造解析器。
3. 任一资源上限或内部故障使整份结构观察不可用；只允许固定 `failed_limit/failed_internal` 状态和既有安全身份，已累计的数值、类型计数、未知字段数和深度全部丢弃，不发布成功前缀。观察只能单向旁路读取，不能反馈修改原 `echo_tool_usage`、主拒绝码、Proposal、Receipt、费用保留或重试决策。
4. 观测输出按固定路径顺序和固定类型顺序 canonical 化；未知键名、值或输入键顺序改变时，不能因排序、动态路径、hash 或异常消息漏出被遮蔽内容。结果深冻结、输入不变。
5. 未来产物只进入单独、尚不存在的 `C:\tmp` 诊断目录，同父 staging、文件身份复验、单次 rename；不新建产品 current，不覆盖旧日志/Receipt，不写入 Ledger/R21/资格报告。诊断文件只能绑定已有 dispatch，不能独立伪造“已请求”。
6. 所有诊断结果恒为 evidence-only，模型可重现、账单已核清和产品资格均不得据此宣称。请求不明确、断电、超时或发布失败后不重新请求；dispatch 已落盘则保留全额预留，不能把没有观测文件当成零请求。

#### 冻结故障矩阵

下表是冻结的完整验收要求。制定时全部未运行；当前只完成下方落实记录中明确标为通过的离线子集，不以历史 633 项或普通 Host 回归冒充新诊断事务已通过。

| 组 | 正反用例 | 必须证明 |
| --- | --- | --- |
| 形态 | 缺省、null、空对象、固定非空全零、单个正计数、缺少某计数、已知全零加未知收费样子树 | 逐项区分；缺失不补零；任何观察都不推导零费用、放行未知工具或形成 Action |
| 数值 | 0/1/65536/65537、负数、负零、浮点、unsafe integer、数字字符串、布尔值 | 只有批准完整路径与合法范围的数值能保留；异常不强制转换 |
| 路径 | 同名字段放入未知父级；字段名中藏私人标记/路径/URL；未知类型深层嵌套 | 未知名称与原值均不泄漏，词表不能按叶名匹配，证据明确不完整 |
| 文本与对象 | 字符串值含文本/密钥诱饵、Unicode控制符、toJSON/getter、原型污染、循环 | 不执行回调，不输出原文/hash/长度，不把内部异常写入诊断 |
| 解析与资源 | 重复键正数后再写零、截断、非法UTF-8、64 KiB边界、深度8/9、节点128/129、字段32/33、8 KiB产物边界 | 资源有界，失败无成功前缀、无半成品；观察错误不覆盖业务拒绝 |
| 批准与隐私 | 普通批准、旧批准、profile/词表/hash/payload漂移、捕获未启用、诊断禁用工具字段进入普通请求 | 新 profile 必须专门批准；批准前零请求、零凭据读取；普通请求白名单与诊断字节不变 |
| 状态与费用 | 20个并发重复请求；reserve/dispatch/收包/校验/rename窗口崩溃；timeout/refusal/错误model/档位 | 每诊断最多一次假 fetch，恢复不重调；未知费用保留预留，旧预算不被重置 |
| 隔离与确定性 | 固定输入20次；键重排；诱饵字符串替换；无R19/Godot/current写能力 | 观测 canonical 字节一致、输入不变、没有内容泄漏或产品状态改变 |

除采集器单测外，必须经过真实 Provider 解析入口的假 fetch 注入、既有 one-shot/预算/恢复链和独立 Reviewer 的恶意反例。测试构造器不得导入采集器内部词表来生成唯一正例，避免生产与测试共同误设边界。最窄测试通过后再执行 Provider 全套、相关 live-provider/Host 回归、`verify:r22`、范围检查和 `git diff --check`；不运行真实网络来补红测。

#### 阶段退出门

1. 本次：完成数据范围、隐私取舍、故障矩阵与独立只读审查；状态仅为“方案已制定”。
2. 后续本地实现：落地独立观察器和假 Provider 矩阵、验证默认未启用及普通入口逐字节不变；此前不能说“取证工具已可用”。
3. 如申请真实诊断：先复核费用前提，展示完整新 payload、观测词表及版本/hash、存储目录和保留策略，再取得单次批准。若无法在现有硬门内构造诊断请求，停报，不借此修改产品安全规则。
4. 诊断后：区分“观察到具体形态”“正式语义已解释”“业务兼容已修复”“真实游戏资格通过”。后面三项仍需各自证据；诊断成功本身不是 R22.7，也不授权第二次请求或正常游戏实测。

[官方数据控制](https://developers.openai.com/api/docs/guides/your-data)仍适用：`store:false` 不等于 ZDR，默认 abuse monitoring 可能保留内容。[官方预算示例](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api)明确仅演示 token 费用且不包含所有工具收费，示例价格不能用于本轮价格锁。因此，本方案不借用该示例证明未知 `tool_usage` 免费。

#### 观察器阶段记录（2026-09-12；不是实际调用证据）

- `tool-usage-observer.mjs` 实现固定八路径、数值范围、未知结构遮蔽、深度/节点/键数/产物上限和失败清空；无 I/O 或业务写权限。捕获 policy canonical SHA 为 `sha256:64314133dc13fa0bce6e078e10dffc8059f01cc6f716d3bcb832bd7a4d2a07ae`，不是供应商 Schema hash。
- `createNpcCognitionToolUsageDiagnosticPlan()` 只生成公开固定 synthetic input、null-only Action、显式禁用工具的待审批材料及捕获身份。生成 hash 不等于用户批准，不读取当前游戏、历史预算或凭据，也不签发实际 dispatch。
- `evaluateNpcCognitionToolUsageDiagnosticFixture()` 只接收该固定计划及合成字节；内部只使用内存 Response 的假请求，复用原 64 KiB/严格 UTF-8/重复键/深度 256 解析与业务校验。没有可传入的 key、远程请求函数、目录、Runtime 或 Godot handle。结果明确 `fixtureOnly:true / realRequestCount:0 / qualificationEligible:false`。其中 `providerResult` 是用于差分断言的**临时原业务结果**，不是允许持久化的观察；它可能含原业务返回的对白及旧 root diagnostics。只有独立 `observation` 采用新脱敏规则。
- 真实发送接口没有增加，普通 Provider 的 payload 白名单、结果结构和使用方式不变，普通批准不能开启观察。新增 profile 是在离线 fixture 内部闭合分流，不能当成已接通的真实诊断入口。
- 独立反例抓到完整带点 key 冒充获准路径，已改为逐层键匹配；又抓到 TypeScript 错误子类型、失败明细的松散类型及 fixture Proxy 钩子，均已修复并补测。使用 `node:util` 的无属性检查、原生 typed-array getter/set 复制，拒绝 Proxy/SharedArrayBuffer；不声称整个 Node 同进程是沙箱或能抵御被提前替换的全局内建实现。

| 冻结矩阵组 | 本批状态与证据 | 仍未证明 |
| --- | --- | --- |
| 形态、数值、路径、文本与对象 | 通过：独立 observer 测试 49/49；含已知零加未知正数、点号 key、同名叶、缺失/父层不可捕获、私有名称/字符串/hash、getter/toJSON/Proxy/循环 | 上游字段语义、工具费用映射均未知 |
| 解析与资源 | 通过：严格 Provider fixture 中重复键及转义重复键、非法 UTF-8、64 KiB/65537 字节、深度和节点/键数失败清空；8 KiB 已有显式输出门且高节点/全词表最大计数产物通过 | 没有注入 8193 字节产物故障分支：固定八路径/有界统计产物自然远低于该值；不得冒充已覆盖此分支或磁盘半成品恢复 |
| 批准与隐私 | 通过：普通/旧 hash、profile、词表、payload、整包重签与额外字段均阻断；类型负例实际编译；Host 侧 tripwire 证明合成入口不取真实 key、不调用全局请求函数 | 持久审批时效、dispatch 前后身份复验尚未接入新诊断 lane |
| 状态与费用 | 普通 Host/one-shot/recovery 回归通过，原拒绝、未知费用保留和无替代 Action 行为不变 | **新诊断的** 20 并发一次派发、reserve/dispatch/收包/rename 崩溃矩阵未实现/未运行；不能借普通测试计数宣称完成 |
| 隔离与确定性 | 通过：20 次计划/fixture/观察字节一致、键重排与诱饵替换、输入不变、深冻结；无产品/磁盘入口 | 未执行真实诊断、Godot、R19、真实资格或发布 |

主智能体实际复跑：新增 observer/diagnostic 两套 **92/92**；整个 Provider **383/383**。`node --test tests/r22-host.test.mjs tests/r22-live-provider.test.mjs tests/r22-live-recovery.test.mjs` 共120项，**119通过、0失败、1项 Windows 文件 symlink 权限跳过**；其中包含新的零网络/零密钥 tripwire 和现有 TypeScript 编译器的正反类型检查。独立 Reviewer 复核三个修复后未再发现阻断项；其沙箱内旧凭据夹具写入 EPERM 不作通过证据，主智能体在授权的 C:\tmp 权限下重跑了同一完整检查。

日志保留为 `C:\tmp\matrix-oasis-r22-tool-observer-provider-20260912-b.log`（SHA-256 `85053cdfd592203228bce44de08251da470db5d5c00f6c23a7b6b57dea3fd788`）和 `C:\tmp\matrix-oasis-r22-tool-observer-host-20260912-b.log`（SHA-256 `d28013be9861e96522303e5d295db1bb179508997104e30e94bab670d856deec`）；早期 a 日志保留。边界规则未放宽：网络/环境 tripwire 与 URL 诱饵位于既有 Host 测试面，纯包仅使用普通数据及注入式内存响应；所有伪凭据明确为 placeholder。

观察器阶段结束时尚未解决事务接缝：当时 `r22-call-store` 新 root 会初始化预算，而 `r22-live-recovery` 只接受能对上普通 Turn 的预算条目。因此不能临时新建 root 清空账，也不能把诊断伪装成普通 qualification Turn。该历史阶段没有实施接缝或新增真实发送 CLI；后续进展见下节，不能以观察器单测代替事务门。

#### 共享账户事务阶段（2026-09-12；仅离线合成执行）

本阶段落实独立诊断记录、原账户共享预算、审批、恢复和原子观察发布。安全审查拒绝了尚未被持久事务强制封闭的公开直连诊断发送接口，相关补丁未落盘；没有改名或转用终端绕过该限制。当前新增实现只接受合成响应字节，不接受 key 或远程执行函数，明确拒绝 `official-once` 来源账户。它不是可用的真实诊断 CLI，也不是 R22.7 资格入口。

- `r22-call-store.mjs` 的诊断访问器只打开已存在的根、完整12字段 session manifest 和 host-budget；复验调用方固定 SHA、FileHandle/realpath/bigint 身份，使用与普通 Turn 相同的 writer lease、预算函数、五字段条目和排序。不创建第二账户，不将旧余额归零。诊断预算键由固定 purpose 与 transaction hash 分域生成，不能伪装普通 authority 身份。
- `r22-diagnostic-transaction.mjs` 固定 `executionKind:offline-fixture / realRequestCount:0 / qualificationEligible:false`。内部合成 dispatch 计数为1表示执行了一次假响应解析，**不表示发生外部请求或真实费用**；合成账户中的10000 microusd只验证保守记账。
- 持久计划绑定 source manifest/原 budget、实际固定 Call Plan、capture policy、output 位置 hash；完整 disclosure 的 hash 才能批准，旧产品/捕获批准不能复用。审批令牌与5分钟有效期驻内存，重启不复用；时间戳、响应字节、对白和原业务 ProviderResult 不进入产物。固定公开模型/端点是计划元数据，不保存供应商回显 URL 或动态字段名。
- 计划、批准、dispatch、观察和终态进入原 cognition 根的 `diagnostics/<transaction-hash>/`；另在指定新目录发布脱敏副本。预算先reserve，dispatch落盘后保留完整预留；恢复不重执行解析器或远程请求。无dispatch才可释放原reserve；有dispatch但无观察只能得到 `dispatch_uncertain`，不能生成资格通过。
- 普通恢复增加双向核对：诊断条目必须逐项对应完整内部记录及终态，不能靠目录名/hash前缀豁免；不允许同时对应普通Turn、孤立预算、删除dispatch或换身。没有diagnostics的旧账户走原检查且无新文件写入。
- 新测试已接入 `scripts/verify-r22.mjs`，不是只能手工运行的旁路测试；本阶段没有执行包含Godot的完整 `verify:r22`。范围仅新增三条精确允许路径，不放宽原扫描器、冻结规则、Provider业务接受条件或价格门。

独立证伪先后发现并修复四个 P1：创建端遗漏128目录上限、简化manifest错误冒充真实结构、首条plan持久后无ID恢复缺口，以及close瞬态失败造成writer无法释放。附加覆盖空root窗口、取消后late approval和close期间late enqueue；关闭开始后业务保持拒绝，但可对仍为本writer的清理操作单独重试，不能借清理失败重新开账。第129个创建在写入前拒绝；当时未绑定凭据文件身份的旧模式中，完整首plan/空root可从原配置且无该domain预算时只收口为cancelled，此空root结论不适用于后续v0.3文件模式。实际子进程在plan同步后被终止也可恢复，不只依赖throw注入。

| 当前证据范围 | 已实现并运行 | 明确保留的边界 |
| --- | --- | --- |
| 共享账户与生命周期 | 旧预算字节保留、普通/诊断 writer互斥、重签manifest攻击、迟到操作拒绝、close-only清理恢复、128/129容量门 | expected hash是调用方信任根，不宣称本地文件具有不可伪造签名；不支持官方账户诊断派发 |
| 批准与派发 | 内容/目录绑定、过期、20并发单次合成解析；reserve/dispatch/收包/观察/发布窗口中断 | 真正网络超时、单次真实fetch、官方key延迟读取仍未接入这条诊断事务链 |
| 恢复与发布 | 首计划无ID恢复、同步后杀进程、完整staging恢复rename、零重复执行、普通恢复严格对账 | 损坏或混杂的pending、部分output/budget staging保持fail closed，不自动修补/递归删除；需人工核查，不能声称任何I/O故障都自动可恢复 |
| 观察与费用 | 只发布闭合脱敏observation；错误model/tier、拒绝、非法字节均不降低合成预留 | 尚未解释真实c的tool_usage语义或供应商账单；任何观察都不能证明零工具费 |
| 外部副本 | 显式diagnostic recovery验证/恢复固定output副本 | ordinary recovery只核对内部transaction/terminal/budget，不证明外部output目前仍存在；输出路径仅存hash，不能从它恢复任意目录 |

本阶段未调用真实模型、未读取真实凭据、未启动Godot、未改R19/R20/Creator/MVP/V2状态，未commit/push/PR。下一步仍须单独设计并审查强制经过持久审批/预算/dispatch的真实发送接缝，公开固定payload和捕获范围后另获当次批准；本地矩阵通过不授予或代替该批准。完整R22、extraction、父client及真实资格未以本节自动补记为通过。

本阶段最终验证（主智能体实际执行；分组有重叠，不相加宣称独立用例总数）：

| 命令/分组 | 结果 |
| --- | --- |
| `node --test tests/r22-diagnostic-budget.test.mjs tests/r22-diagnostic-transaction.test.mjs tests/r22-live-recovery.test.mjs` | 105/105通过；预算54、事务40、普通恢复11。含真实子进程终止、20并发、20次同一已发布事务重放字节一致；不是20个随机新目录产生相同identity |
| `npm.cmd test --workspace @matrix-oasis/npc-cognition-provider-openai` | 383/383通过 |
| `node --test tests/r22-host.test.mjs tests/r22-live-provider.test.mjs` | 114项，113通过、0失败、1项Windows文件symlink权限跳过 |
| 原完整Host/live-provider/live-recovery命令 | 125项，124通过、0失败、1项同类权限跳过；位于最终close-only补测前，最终新恢复11项另随105组复跑 |
| live-composition/live-preview/live-recovery-process/recovered-behavior | 34/34通过；使用固定只读中性源、合成Provider和Godot替身，不是图形/物理资格 |
| round-scope/v2-claim/r22-reference治理 | 99/99通过 |
| 独立contracts/runtime/references | 14/14、13/13、4/4通过，参考锁5项、生产新增依赖0 |
| boundary/round-scope/固定base parent-scope/diff | 通过；boundary1538/1528，scope115/93，未放宽检测 |

失败也保留：首次旧Host命令在受限沙箱的 `C:\tmp` fixture创建处出现EPERM，随后在已授权的相同目录权限中复跑原完整命令通过。最终矩阵早期b/c两次均为104通过、1个120秒测试超时取消，不能记为绿测；串行仍超时排除了“仅本组并行”解释。定位为容量fixture循环创建128次、每次严格复审所有历史前缀造成的平方级准备I/O。只优化fixture准备：先真实创建/取消第一条，机械构造126条合法闭合历史，真实第128条创建复验此前127条，真实第129条拒绝且零写入，最后普通恢复复验全部128条。未改生产校验、容量上限、断言或120秒限时；最终该用例约7.4秒、整组约39.4秒。此覆盖不再包含128次连续真实create/cancel的耐久压力，独立Reviewer已确认128/129正确性证伪仍有效。

仓外日志及SHA-256：

- 最终矩阵：`C:\tmp\matrix-oasis-r22-diagnostic-transaction-matrix-20260912-d.log`，`4f6fb6ce188d861b2c4079b69c1a22728d673b6b4a2327e31a7bd855bf48a750`。
- 最终Host：`C:\tmp\matrix-oasis-r22-diagnostic-transaction-host-20260912-b.log`，`dbab60c7943067dacfb0df1c5b2516fe9e8bbcdf086ca708d9e278fcdacebebb`。
- Provider：`C:\tmp\matrix-oasis-r22-diagnostic-transaction-provider-20260912-a.log`，`83deaeef91aaf75afccefaf7b6b93c933c6fad1c68df803ae2e7657f8185aa82`。
- 原完整Host/恢复：`C:\tmp\matrix-oasis-r22-diagnostic-transaction-host-recovery-20260912-a.log`，`f3a60b3e234c1cb092d769824c6762645c5a66e77fdc89c921e7e203d999d2a4`。
- 组合/进程回归：`C:\tmp\matrix-oasis-r22-diagnostic-transaction-composition-20260912-a.log`，`aad8c5d6643e800606e1d3cc5babc8ea109805731703b8ed589b3d2616f6d949`。
- 治理：`C:\tmp\matrix-oasis-r22-diagnostic-transaction-governance-20260912-a.log`，`678b769b74c34d45d3e456fc6d4c26b729a8266e01dcd689dfec05aa6fae4cb9`。
- 超时b：`C:\tmp\matrix-oasis-r22-diagnostic-transaction-matrix-20260912-b.log`，`345bb974fec984764275106fcb8d03527d1b002d69d13e38d574b61f77e8e5e9`；超时c：`C:\tmp\matrix-oasis-r22-diagnostic-transaction-matrix-20260912-c.log`，`3278ff6ba081129c5a8857796ea84d43e6e01ef5a0562eaf2ff910b768c644f6`。两份失败日志未覆盖或删除。

最终冻结复核：Runtime、Host core、live-provider、live-preview、qualification-core、MVP/V2七项SHA与本批开工一致。HEAD仍 `9adb5493501a954360edf6525542ffface55653e`，暂存为空；开工所有修改/未跟踪文件保留。未把局部回归冒充完整 `verify:r22`、R1–R22、extraction、父client或R22.7通过。回退只移除本阶段三条新路径、事务/预算/恢复的增量和测试注册，保留旧脏改动、原缓存、真实证据与历史预算；不执行任何回退或目录删除。

### 封闭发送接缝与新审批门（2026-09-12，本地实现，不是付费执行）

本节接续上节尚未接通的真实transport。用户要求先完成剩余本地项，再等待其重新批准Luna；旧批准、旧普通Turn、旧诊断fixture均不能授权本节的新请求。实现仍不改变非空 `tool_usage` 的业务拒绝或费用语义，R22.7继续阻断。

- 保留v0.1 `offline-fixture` canonical记录；v0.2精确区分 `injected-transport/offline-fake` 与 `official-once/official-once`，模式及source provider都进入transaction和budget key。旧三字段budget key不得用于官方账户。测试仅接受复制后的有界数据场景，不接受调用方sender、key reader或完整请求回调。
- 官方工厂只接受既有账户、manifest/budget期望hash及新output配置。固定环境变量仅在该事务完成当次内容批准、同账户writer、原子reserve、原子dispatch和完整保守扣账后读取；发送前再次复验所有记录、预算与批准期限。请求使用模块加载时捕获的原生能力，只有固定endpoint/payload和一次发送，无备用地址、redirect、retry或动态请求参数。
- 官方账户保留已有预算条目；新批准明确绑定历史entry数量、已扣/预留金额以及额外最多1次/10000 microusd。`diagnostics/`第一次创建事务目录即永久消费该官方账户的诊断名额，取消、失败或重启不能重新创建；恢复/普通历史审计同样拒绝多个目录。这个额外诊断许可不重置普通单次资格或host总预算，也不是供应商账单证明。
- 私有transport有30秒总截止、64 KiB响应、4096 chunk上限、URL/状态/JSON类型/长度门，主动中止并丢弃迟到结果。收包后和写观察前复验原plan/approval/dispatch/transport链；批准到期只控制新派发，不把已经合法派发的响应因自然到期误报为新请求。失败只记录静态状态，不持久化底层异常、response ID或文本。
- v0.2 terminal分别记录调用接缝计数与真实模式计数。dispatch后缺少transport记录时请求数为 `null`、上界1，仍扣完整10000 microusd；有固定缺凭据证据可记录0，但不自动退款或重试。数据测试永远 `realRequestCount:0`；所有模式 `qualificationEligible:false`，观察成功不等于普通Provider接受，更不等于工具费用已解释。
- 新 `scripts/r22-tool-usage-diagnostic.mjs`提供 `plan/execute/recover`。plan只持有短时writer读取并展示完整公开合成payload及新disclosure hash，不创建诊断名额或预留。execute还要求精确 `--approve-disclosure`，在创建前和创建后两次核对。recover只核对/收口已有事务，绝不发网。无key/model/endpoint/payload/根目录覆盖参数，输出限于固定 `C:\tmp` 新子目录。
- source scanner仅精确放行该事务文件的一个固定credential读取与一个私有发送点，锁定全部公开函数名单及私有能力引用；普通脚本规则不放宽。测试故障注入仍属于可信同进程Harness，静态扫描和模块加载时能力捕获都不宣称能隔离恶意同进程代码。真实env所属账户不能由本地格式验证证明，调用前仍需用户提供正确官方凭据。

独立审查发现并修复：官方恢复的一次性目录遗漏、收包后记录复验缺口、legacy key跨模式及私有发送能力导出扫描反例。测试也纠正了一个构造错误：在同一事务先执行无批准请求会占用claimed，不能再期待错误审批走到另一个错误分支；现改用独立事务分别攻击，未放宽生产断言。最终测试和日志结果在本节后续记录，不挪用前一批绿测。

extraction边界另行核实：现有 `verify:extraction --allow-dirty`明确只拆分HEAD、忽略未提交内容，不能证明本批脏工作可拆分。正式source/split/archive证据须在批准的本地提交快照后生成；本轮不以该参数绕过快照真实性，也不为了拆分提前提交或宣称R22.7完成。

追加交叉窗口复核也修复两项问题：私有native能力可通过非export的全局赋值绕过仅计数的source门，现同时要求完整type guard并拒绝获准capture之外的global/globalThis；批准后的output/stage抢占必须在credential读取后、真正发送前被拒绝。收包后的复验不因外部output争用抹掉transport记录。63项矩阵覆盖这些反例；正常响应fixture采用与官方相同的30秒deadline，故障timeout用例仍显式1秒，不修改生产限时。

主智能体本地整合结果：

- 最终兼容组105/105通过（日志 `C:\tmp\matrix-oasis-r22-diagnostic-sender-compat-20260912-b.log`，SHA-256 `a4bd25aa506e81fd5624bd46d101fa826bcae0d4f8decc6ea493cd379357758d`）；它覆盖旧offline记录、共享预算及普通恢复，不是官方网络证据。
- 完整 `npm.cmd run verify:r22` 退出0：共890项，889通过、0失败、1项环境权限跳过。分组95治理、291实时/CLI/新事务矩阵、4参考、14合同、13 Runtime、383 Provider、71 Host、19 Godot；Godot 4.6.3真实import/probe通过。日志 `C:\tmp\matrix-oasis-r22-diagnostic-sender-verify-20260912-a.log`，SHA-256 `cfc25edc989dddd1cb1fef8696baa64ae32c72d45e5d0ff9079e3960a503560f`。分组重叠不另叠加105项为唯一总数。
- 针对现存官方c账户只执行新CLI的plan模式，未执行approve/execute：manifest hash `91665bd232d622f212a488134a2a075bdc4cfda0b5b57de570a8fb5d621fbb27`、budget hash `272f46a469bb1324f265312e3cedbc42ac2aa092746bd2a99853d53d9c3d9cf3`前后不变；diagnostics/output均不存在，短时writer已释放。历史1条、保守记账10000 microusd、无活动reserve；这不是核实已发生的实际账单金额。
- 待审批disclosure身份为 `sha256:17b70f36f28be91ace9957f65d0342b84656c755e51c66bf08f9f4c33ab183b3`，绑定原host账户、最多额外1请求/10000 microusd、公开合成1290字节请求、null-only候选、既有capture policy及新的仓外output。完整payload只在计划返回值中展示/驻内存，没有写入文档、账户、日志或产物。该hash是计划身份，不是已取得当次批准；真实调用前仍须重新核对内容、官方价格、来源hash并取得新批准。
- 独立复核在上述修复后未发现新增P0/P1；这是有界审查结论，不保证不存在其他缺陷。当前没有真实网络transport、真实凭据读取、R22.7资格、人工游戏验收、commit/push/PR；只读plan不能消费或重新激活原普通单次额度。全模块及父client验证另行记录最终结果。

父client干净验证（不修改冻结父代码）：

- 新目录 `C:\tmp\r22-client-clean-9adb5493-20260912-local`由当前HEAD的git archive创建；461个tracked blob在验证前后均与HEAD一致。当前HEAD及固定R22 base的client tree同为 `5aa5ef0ec024e8420239c0db68ccc3e666e5d9ff`，不存在本轮client源码漂移。归档SHA-256 `832dda0f3cd1b0562c9caa06e423b400cfbf7169396b5024364de17bb67b2528`。
- `npm ci --offline --ignore-scripts --no-audit --no-fund`安装384包，退出0；先检查生命周期，仅对Windows已安装的esbuild 0.27.7执行离线rebuild，退出0；平台可选fsevents未安装。npm的allowScripts提示原样保留，没有调整安全配置。
- 原 `npm run test:run`退出1：131个文件中130通过，916项中914通过、2失败。`src/data/models.refresh.test.ts`第110、1645行分别期望live=502和6条过期模型；实际live=500，过期列表额外含两条Nex AGI。`models.ts`第23689行读取当前时间，第24118–24120行按expiration_date切换状态；两条数据的到期epoch均1788825600，即2026-09-08T00:00:00Z，当前验证时已过期。这是完全相同冻结父tree中的日期驱动断言失效，不归因为R22，也不虚报父测试已通过；不改时间、数据或断言。
- 因原命令的 `&&`短路，server-headers未随它执行；另行运行得到1/1通过、退出0，不覆盖原exit1。`npm run build`退出0、3172模块；MainApplication较大chunk警告保留，不以放宽阈值消除。
- 原测试日志 `04-npm-test-run.log` SHA-256 `be60ad84311054f27b5d6f86762ebb1c93ec042ecc850ac32b5a41a0ec4a6f47`；build的 `05-npm-build.log` 为 `91d0223a79c74ba40ebba67df13e0c7f9e8a65e453eb963d2e463c2ce9030c97`；独立 `07-server-headers-node-test.log` 为 `6abb4a0669d2ac11dcdd5d4ccb06f92b78f346673c98ac20366c69c0eeb82708`。全部在上述新目录；无源码修改、外部网络、供应商调用、后端或Docker。父基线失败是发布前待处理项，不扩权为本批修改父项目的许可。

全模块回归收口（本地日期2026-09-12，等待新Luna批准）：

- 主智能体接续同一个完整 `npm.cmd run verify` 进程，实际退出0并输出 `VERIFY_OK steps=31`；未因旧解算器耗时而跳过、缩短或改写用例。包含R18至R22专项、旧Runtime/空间/资产链、Godot基础及实际40次空间分析一致性检查；综合 `npm test` 为941/941通过、0失败、0跳过。各专项与综合测试有重复覆盖，不将分组相加冒充唯一用例总数。
- Creator构建退出0，248个模块；启动检查输出 `CREATOR_SMOKE_OK status=200`及原R0/R2/R3标识。此处只证明既有Creator构建与本地启动回归，不是新R22游戏人工验收或真实AI资格。
- 完整日志 `C:\tmp\matrix-oasis-r22-diagnostic-sender-full-20260912-a.log`，SHA-256 `53438cb1b7d49c5129a5e06f207f0fed26590d052a78882b167bffa875f6b5fa`。完整R22专项的环境权限跳过仍按前文单独披露，不用综合测试的零跳过覆盖它。
- 整合期间四项关键源码保持不变：`r22-diagnostic-transaction.mjs` SHA-256 `a75d213c10ff01e534ce8a0762a31601285beb373647e8b0fa8a4edc44a6c7f8`；`r22-call-store.mjs` 为 `2d172eae9ead62f594b055ceb62a2419dbe5d9bbb887d6fc8f7fa96d4f26a835`；`boundary-core.mjs` 为 `b72c14350f6987c29569ff545044eed231394bd7558a0db4c942635ad44af4eb`；独立63项transport矩阵为 `0cec57f6f7d03ac109f3a2e2d37d477a741be21ab04aa71b894b4e4adc977233`。新矩阵自有 `r22dt-` 临时目录检查为0，没有删除旧资格缓存或历史日志。
- 本批零真实模型请求、零真实凭据读取；官方c账户manifest/budget仍与plan前相同，diagnostics及拟议output尚未创建。无commit/push/PR；固定base、HEAD及原未提交工作保留，V2仍为 `r22-bounded-cognition-in-progress / claimAllowed:false`。
- 当前交付只是“诊断本地修复及验证已完成”。发布前仍有冻结父client的两项日期断言失败、最终提交快照的extraction及真实/人工资格门。下一次若获新批准，仅运行公开合成、null-only的单次诊断（最多1请求/0.01美元），必须重新展示完整payload与捕获范围、核查官方价格和来源身份；旧批准不可复用，诊断结果不得转换成游戏资格成功。
- 文档补齐后最终复跑：`BOUNDARY_OK checked=1540 tracked=1528`、`ROUND_SCOPE_OK checked=118 changed=95`、固定base `PARENT_SCOPE_OK checked=118 changed=95`及 `git diff --check`均通过。43120/43122监听、Godot进程及新transport矩阵自有临时目录均为0；无新残留预览。最终仍为23 modified/12 untracked、暂存为空，全部位于模块内；本批不清理或覆盖这些既有/新增工作。

批准后的单次诊断执行记录（本地日期2026-09-12，UTC 2026-09-13）：

- 用户在完整披露后以“批准执行”授权上述 `sha256:17b70f36f28be91ace9957f65d0342b84656c755e51c66bf08f9f4c33ab183b3` 事务。执行前重新核查[官方价格](https://developers.openai.com/api/docs/pricing)：Luna Standard输入0.20、输出1.20美元/百万tokens，与已批准价格锁一致；四项关键源码hash与前述全量测试快照一致，未修改payload、模型、端点或凭据来源。
- 仅执行一次 `execute`，退出0表示失败证据已发布，不表示模型成功。最终状态为 `transport_failed / credential_unavailable`；`transportRequestCount:0`、`realRequestCount:0`、`providerReplayRequests:0`，无observation，`qualificationEligible:false`。`dispatchCount:1`只代表本地持久化派发标记，不能当作API请求数。
- 私有发送器在持久事务门后尝试读取固定环境变量 `MATRIX_OASIS_R22_OPENAI_API_KEY`，但未取得可用凭据；没有访问OpenAI。静态错误不能区分变量缺失、格式不符、读取失败或发送前中止，不能据此判断密钥、账户或模型本身无效。该入口不自动加载旧key文件或父 `.env`；本次未另读那些文件，也未显示任何凭据值。
- 沿用现有保守规则记录 `chargedMicrousd:10000`；这是本地预算扣账，并非供应商账单。本次真实请求为0，因此没有本次模型调用费用。原账户保留两条历史扣账、合计20000 microusd、无活动reserve；不清零、不退款、不换账户、不重新开放诊断名额。该官方账户的单claim已在事务创建时消费，零请求失败同样不允许自动重试。
- 输出为 `C:\tmp\matrix-oasis-r22-tool-usage-diagnostic-20260912-a`，只有plan、approval、dispatch、transport和terminal五份记录，无原始payload、玩家/模型文本、原始响应或observation。`diagnostic-report.json` SHA-256为 `93693a3632dcc5e1a72e49bfb4fd8f3b2ef029b49265d32e48a975af22850338`，`transport-record.json` 为 `4711f5f2649f2998f3e5add14b5bd38d4303ce23c1fcf4f1f9169835a5648afe`。
- 随后仅执行一次零网络 `recover`，退出0且terminal/report字节完全相同。原manifest仍为 `91665bd232d622f212a488134a2a075bdc4cfda0b5b57de570a8fb5d621fbb27`；扣账后及恢复后的budget SHA-256均为 `daca059f4722c2cbef08cf5d1f988190cf07f06b61d5e040930564339d00a7f9`，writer锁已释放。没有再次execute、补发、创建新事务或修改旧记录。
- 本次没有启动Godot、改动Runtime/Ledger、提交、push或创建PR；只追加本节与任务卡的执行事实。原tool_usage语义仍无新真实观察，R22.7、最终快照extraction、父client基线失败及人工游戏资格继续阻断。任何后续凭据接线、诊断名额政策或新请求都须先明确范围并另行取得所需批准，不能将本次批准沿用为重试许可。

### 诊断凭据接线修复（2026-09-13；本地验证，非再次实测）

用户授权修复，并确认拟用路径为 `tmp/openai key`。按照此前约定解析为 `C:\tmp\openai key` 后，仅查询文件属性得到不存在，同名前缀的文件名检查也为空；本进程专用环境变量的存在性检查为false。没有读取任何真实key值、父env或其他凭据文件，也不据此判断此前key格式、账户或模型无效。

- 根因范围：实时预览已支持显式文件来源，但封闭诊断CLI仅接固定环境变量，没有复用文件reader。本批补齐 `plan/execute --credential-file`，只允许已有reader支持的临时根直接子文件；不复制解析、不接受裸key或caller callback、不在文件与环境间自动回退。文件不存在时在创建claim前失败。
- metadata-only准备生成冻结的 `sourceBinding`；只绑定文件/root身份和路径的分域摘要，不保存原始路径、文件名、key或key hash。实际内容仍在批准、reserve、dispatch持久化和保守扣账后由同一reader读取，并执行既有FileHandle/realpath/bigint换身检查。
- 文件模式使用仅官方事务允许的v0.3，`credentialSource`进入plan及批准hash；CLI execute一次pin并在创建diagnostics目录前比较新plan与当次批准。文件变化、错误批准或旧环境模式批准均不能占claim。env模式只在创建claim前检查专用名称是否存在，不检查值；存在性与文件metadata通过均不代表key有效。
- v0.1离线/v0.2既有plan和record结构不变；CLI plan外层新增非持久化 `credentialConfiguration`，这是明确的增量输出变化，**不宣称整个旧CLI disclosure逐字节不变**。`recover`拒绝credential-file参数，完整持久记录可在文件被删除、env读取被禁止时离线恢复。
- 恢复边界收窄：前文空root可收口的结论属于不需文件身份的旧模式。v0.3若在目录创建后、plan首次保存前崩溃，来源身份尚未持久化，恢复会拒绝而不是猜测身份、制造取消或重开名额；预算不变，但该claim不可自动恢复。完整pending plan或完整plan则可无凭据恢复为cancelled。未扩大事务存储设计，也不提供删除claim或重新武装入口。
- 独立审查未发现当前发送链新增P0，但发现原静态门没有约束唯一reader调用；已补齐精确preparer/read调用数、禁止额外reader引用/直接调用和创建前配置guard，并增加源码变异反例，包括保持reader引用数不变的直接调用及call/apply/bind。静态门与执行矩阵互补，不声称能隔离任意同进程恶意代码。
- 聚焦15/15通过；完整接线/旧预算/事务/恢复矩阵225项中224通过、0失败、1项Windows symlink权限跳过。矩阵含20并发单次本地替身发送、20次恢复字节一致、来源换身、拒绝错误key、无env回退、取消及三个plan崩溃窗口。官方模式正路径由模块导入前截获的本地native替身执行，**不是真实API请求或账户有效性证据**。
- 早期红测如实记录：CLI可选flag未纳入现有精确参数计数，修改本批调用方式后通过，未放宽通用parser；已存在output先于已消费claim拒绝，测试改用新output检查原claim门，未改生产优先级；三个崩溃用例最初未命中模块加载时捕获的fs函数，改为mock后独立import使注入实际生效，未调整生产恢复规则或断言。
- 本矩阵日志 `C:\tmp\matrix-oasis-r22-credential-matrix-20260913-a.log`，SHA-256 `3e6238b7ae48c59ec0b3567540392ddb801ab77b06c37e534197719bd93988f4`。完整R22回归、最终范围和diff结果在实际结束后另行追加，不能用此矩阵替代。
- 全套回归先后保留两次exit1：a在缺少本进程Godot路径时停止于 `GODOT_4_6_3_NOT_AVAILABLE`；指定原已锁定4.6.3后，b的Godot导入/探测通过，但最终boundary拒绝新增测试中拼接的动态import地址。仅把测试的隔离模块地址改为固定字面量，保持mock-before-import和各故障断言，未增加扫描豁免；修正后原聚焦15/15和boundary通过，再重跑原完整命令。a/b日志分别为 `C:\tmp\matrix-oasis-r22-credential-verify-20260913-a.log`（`b073de5aec86a52e8e8c43b4609a427d39bcabdaf64688363b437fc19d8fd090`）和 `C:\tmp\matrix-oasis-r22-credential-verify-20260913-b.log`（`1009a402cf9cb8de98000ca664b6efe55b3c8a5d6bcaa406b20097f57c387ed3`）。失败日志未覆盖或删除。
- 原官方失败账户的manifest、budget及诊断report SHA分别仍为 `91665bd232d622f212a488134a2a075bdc4cfda0b5b57de570a8fb5d621fbb27`、`daca059f4722c2cbef08cf5d1f988190cf07f06b61d5e040930564339d00a7f9`、`93693a3632dcc5e1a72e49bfb4fd8f3b2ef029b49265d32e48a975af22850338`。本批不调用模型、不更改旧claim/预算/证据、不重开账户、不更新R22.7、不提交或发布。缺失凭据材料和已消费claim都不能靠新文件接线自动解除；任何后续新请求仍须另行明确授权。

本批最终本地验证：

- `npm.cmd run verify:r22` 在固定Godot路径下完整重跑退出0：904项中903通过、0失败、0取消、1项Windows symlink权限跳过。包含live/事务305项、Provider383项、Host71项、Godot19项和合同/Runtime/治理；分组有重叠，不与前述225项相加冒充唯一数量。实际Godot版本为 `4.6.3.stable.official.7d41c59c4`，导入和probe均成功，不等于实时游戏或真实Luna资格。
- 最终日志 `C:\tmp\matrix-oasis-r22-credential-verify-20260913-c.log`，SHA-256 `af79653319e4d3a64e3c8d8808664605478991d6041b67b0b1206bb3c160437d`。boundary为1540/1528、round-scope为118/95；固定base parent-scope为118/95，日志 `C:\tmp\matrix-oasis-r22-credential-scope-20260913-a.log` 的SHA为 `fd32e6837bbf9c698825f7eaba642baa4f9fe3fb2525b628cfee7c9b72250d63`。diff无空白错误、暂存为空。
- 本次验证的源码SHA-256：`r22-diagnostic-transaction.mjs` 为 `c4827b077173a9d2eda41e786293250690701ebfb43271db7ac159fc4dfa6d7e`；诊断CLI为 `8e35f63f191d9f5e4c7a08fddfbc38a309c8628a91265f69203c4fd0e7bd428a`；`r22-live-provider.mjs` 为 `dc78bf44b8af490cb1a0bab1e0a1cabd42165d709e2d13d98c8380293ce4aecd`；`boundary-core.mjs` 为 `d74d2cf3bc1a10ef8e0475217b6e917f0a66501b1507ac9a532685c8ffafb9d4`；transport矩阵为 `b54afbb0becc55fd695a61dd197a19c0a46cc95ee2bf4e7732001a2383d6db5e`；live-provider测试为 `b6150b307649f80663be68b861360eb7dbb03c811cb3ebbe0a39f3ea113c0638`。
- 独立Sol只读复核未发现剩余P0/P1；旧空root恢复表述已在原段落直接限定为旧无文件身份模式。最终新增transport测试自有目录与Godot进程均为0；未删除其他缓存或失败日志。对选定key路径使用包含隐藏文件的属性查询仍为不存在，未改读其他来源。
- 本轮只完成凭据接线的本地修复。完整31步全模块verify和父client没有在本修复后重跑；前一批成功/失败分别保留，不能作为当前完整快照新证据。最终提交快照extraction、父client两项既有日期断言、真实观察与R22.7人工资格继续待处理。HEAD仍9adb5493，R16 MVP状态不变，V2仍in-progress/claimAllowed=false；不提交/push/PR。

### 一次追加诊断授权（2026-09-13；仅本地实施与新披露）

- 用户批准最小修改与补测：同一账户追加一次独立授权，不复活旧claim、不退款、不换账户。新v0.4只接受显式指定的完整official v0.2/v0.3前驱，必须为 `transport_failed / credential_unavailable / realRequestCount=0` 且旧10000 microusd保守占额仍完整。新plan绑定前驱transaction、format、terminal、transport和旧budget entry哈希，仍需全新disclosure批准。
- 新 `--continue-from-transaction` 仅用于plan/execute，并强制pinned-file；recover拒绝此参数与credential-file。默认旧单名额路径不变；第二名额一经创建，不论取消、零请求或派发不确定，受控CLI都不提供第三名额。完整新plan或pending plan可以离线恢复；空目录无凭据身份可供重建时继续失败关闭。两个目录存在时只允许按新事务ID恢复，普通账户审计必须匹配唯一前驱/后继链。
- 创建前、reserve前、dispatch复验、凭据读取前后、收包、发布和终结均检查前驱与预算。旧批准的哈希通过terminal/transport→dispatch→approval链传递绑定，无需复制旧token字段。新审批不匹配在mkdir前拒绝；key元数据准备仍零内容读取，真实读取仅能发生在durable dispatch与保守扣账之后。
- 局部红测20项中16通过、4失败：新测试错误地把按身份排序的budget最后一项/数组前缀当作追加顺序，造成旧entry hash预期、替身内预算断言及退款目标错误。只把fixture改为按authority identity精确匹配，未改预算生产规则；同组20/20复跑通过。随后扩充v0.2、容量、缺前驱及第三目录，聚焦27/27，日志 `C:\tmp\matrix-oasis-r22-additional-authorization-focused-20260913-a.log`，SHA-256 `bb8254bb1369a86a8b084132bb43b280971f67aafc06d3494c2ec95ce51f34a2`。
- 最后补上describe后旧approval整链重签、新批准失配的反例，纳入完整 `npm.cmd run verify:r22`：931项中930通过、0失败、0取消、1项Windows symlink权限跳过，退出0。含20个并发create恰一胜者、20次execute恰一次本地拦截发送、20次无凭据恢复以及四个真实fs故障窗口。Provider383/383、Host71/71、Godot19/19及实际4.6.3 import/probe通过；不是Luna真实请求、物理Action或游戏人工验收。
- 完整日志 `C:\tmp\matrix-oasis-r22-additional-authorization-verify-20260913-a.log`，SHA-256 `cd36bdab89fd5c4bec2b9f7e6205105efa56edf6b99d344bf896db77ef8d30e6`。本批验证源码SHA：诊断事务 `ebd00eba027b885196da986e616f8cac06e6ada698817e308693843b325e7937`；CLI `abd4218b22a35d6315f932ad641ebff0cc2d4598f3160cb8e1997b04c9fac892`；boundary `b6f141a29aeacb1eff3af734f340daa456afec741dbdf1f8c867061a5bbcb86d`；transport矩阵 `81c3b69cd9dbbb9a05181e61833a42b4a334ab6fc914e6f7e89327f5877e3140`。
- 独立Sol只读复核当前代码未发现新增P0/P1；此前审查提出的旧budget entry绑定、合法双记录普通审计、reader前第三名额门、v0.2及真实并发争抢均已落实。残余P2测试建议如实保留：尚无专门的“旧approvalToken直接执行v0.4”用例（执行器精确token比较未改），也未新增完整loadR22LiveRecoveryHistory双目录端到端fixture；新链接审计与既有普通预算反向核对分别已测，不冒充其新端到端证据。
- 威胁模型没有扩张为恶意本机管理员防护：同一writer会拒绝字节相同的inode换身，跨进程凭canonical身份恢复；同权限者主动删除第二诊断目录、修改预算或整包重签，不能由纯本地文件保证永久防篡改。精确源码scanner也不是JavaScript沙箱。没有为追加名额引入外部计数器、数据库或第二套预算。
- 用户后续给出准确路径 `C:\tmp\openai key.txt`，当前metadata确认是164字节普通文件；此前无扩展名路径的不存在结论不适用于此文件。只做metadata-only plan，未读取真实key内容；真实账户manifest、budget与旧诊断report仍保持此前SHA，旧费用不被冒充为已退款或实际供应商收费。
- 在同一真实账户成功生成但未执行新v0.4披露：`sha256:81bf0a4996827335682a52e3f326826a557f7779d15b7df073d1fb4d714be725`；拟输出为 `C:\tmp\matrix-oasis-r22-tool-usage-diagnostic-20260913-b`。前驱是原 `sha256:17b70f36f28be91ace9957f65d0342b84656c755e51c66bf08f9f4c33ab183b3`，实际格式v0.2，旧预算entry SHA为 `1ea13a83a32fce4d7ebbe90a4de85d6fca337150ee7c2bbb98d105fcced8db2a`。固定公开synthetic/null-only payload仍1290字节、SHA `26c613a49a6ac370b6fc933dac4ef8957f7eacb9fa82ba2e2754fcbd81efae6e`；未新增诊断目录、预留或调用。
- 已复核官方Standard价格锁和数据保留说明，无payload、模型、端点、tier、Schema或采集词表变化。任何真实请求必须先向用户展示此新完整披露再批准，仍1次/$0.01/30秒/零retry；观察仅作诊断，不更新业务接受规则或R22.7资格。
- 最终boundary1540/1528、round-scope及固定base parent-scope118/95、diff通过；暂存为空，HEAD仍9adb5493。本批只修改诊断事务、CLI、边界、测试和两份R22文档，live-provider/Provider payload/R19/R20/Godot/Creator/MVP/V2未改。全模块31步verify、父client及最终提交快照extraction未在本批重跑；零真实请求/真实key读取、无commit/push/PR，R22.7仍阻断。

### 新披露批准后的官方观察（2026-09-13；不是游戏资格）

- 用户明确批准 `sha256:81bf0a4996827335682a52e3f326826a557f7779d15b7df073d1fb4d714be725` 后，复核九项源码/旧账户/旧报告hash及文件metadata；plan再次得到完全相同的披露身份。调用前再次读取[官方价格](https://developers.openai.com/api/docs/pricing)，Luna短上下文Standard输入/缓存读取/缓存写入/输出仍为0.20/0.02/0.25/1.20美元每百万tokens。固定请求1290字节、payload SHA及端点、模型、Standard、null-only候选和捕获词表均未改变。
- 只执行一次封闭 `execute`：授权后延迟读取指定 `C:\tmp\openai key.txt`，向官方Responses endpoint发送一次请求，收到通过固定HTTP 200/JSON/大小门的响应；terminal为 `observed`，`transportRequestCount:1`、`realRequestCount:1`、`providerReplayRequests:0`、`qualificationEligible:false`。未显示或另行读取密钥，未保存原始response、对白、玩家文本或远程response ID；不是模型对白/Action成功凭据。
- 真实观察：`tool_usage` 为object，根下 `image_gen` 与 `web_search` 的8个获准计数路径全部为number且值0；共有13个节点（5个object、8个number）、最大深度3、未知字段0。包括image_gen输入/输出各自的image/text细分及total_tokens，和web_search.num_requests。证据仅为这一次响应的固定词表观察，不宣称官方完整Schema、跨请求恒定结构或工具收费语义。
- 与当前源码交叉核对，`validateResponseRootEchoes` 对 `tool_usage` 使用严格空record检查，因此本次观察到的非空零计数对象不符合普通Provider接受规则。这证明一个具体兼容性缺口，不证明此前每次失败原因相同，也不证明其余响应、usage和费用门已通过。未据此修改接受规则、把缺失当零或开启工具；后续仅在单独的最小离线修复与反例矩阵后再评估新真实资格。
- 同账户新增10000 microusd保守记账，累计30000 microusd、活动预留0；这不是实际供应商账单金额，诊断未保留用于定价的完整usage，不能核定实收费用。按新dispatch的精确authority/call-plan身份移除唯一新增entry后，旧budget canonical SHA仍为 `daca059f4722c2cbef08cf5d1f988190cf07f06b61d5e040930564339d00a7f9`，证明原两项记账未改。当前budget SHA为 `8432e5b6c02d3bfcde4afed47887260fc691b399907668264e9352e588c90d93`；旧manifest及旧失败report均保留原hash。
- 新证据目录 `C:\tmp\matrix-oasis-r22-tool-usage-diagnostic-20260913-b`：report SHA `2edbc021ad655ea2c5591c52630a647cd08e9451fc1288b62880ca0cdfe73db3`，observation SHA `7b4ac0dbb5a6099334bf8a0f34fd565e05cc27678f5a2aaf3738140daf002372`，transport SHA `7b3cea9c01f1b2cebfcfba0487d56880bd6ee3872e83b808df743a3ea09cb6f2`，dispatch SHA `7ab663dbad6ce542e91b32020b04fac3f0bb07ed62dbf069fff62d82652976d0`，approval SHA `143dbf73dfd428f27f86755e298a1213b80c5c65f70952eb75a039325ccdf1fc`。之后仅执行一次不含credential参数的零网络recover，返回同一terminal；没有再次execute或新批准。
- 两个诊断名额均已消费，不自动申请第三次、不退款或重置历史。本次只更新两份R22证据文档；未改业务代码、R19/R20/Godot/Creator和MVP/V2，未启动游戏、未提交/push/PR。游戏内真实对白与物理Action、人工验收、最终快照extraction及父client基线失败继续独立待处理，不把本次观察计入R22.7通过。

### 已观察零计数兼容修复（2026-09-13开始，2026-09-19恢复；仅离线）

- 用户授权开始修复并恢复进程。本批只改Provider实现、既有conformance/Host测试与两份R22文档；所有其他未提交工作保留。上次写入扩展测试时本地编辑审批服务返回认证故障，补丁未写入；恢复后先核对文件及HEAD，再通过正常编辑工具完成，没有改用绕过审批的写入方式。
- 最小红测：独立合成响应仅增加已捕获的完整`tool_usage`形状，其余检查全部通过，唯一失败为`echo_tool_usage`。原日志 `C:\tmp\matrix-oasis-r22-zero-tool-usage-red-20260913-a.log`（SHA-256 `698549213ac34fa74d836497a1bf08c18bf6a701e2c6db1bff51e3b0125d2af4`）保留。它复现的是固定形状兼容缺口，不是已经丢弃的真实完整响应。
- 普通Provider现在只额外接受精确的5个object/8个正零number结构：root必须恰有`image_gen`和`web_search`，image_gen必须有input/output/total及各自image/text细分，web_search必须有num_requests。每层闭合、每项必填、每个数字用`Object.is(value, 0)`判定；不允许字符串转换、缺失补零、未知字段、部分结构、负零或非零。原absent/空record行为保留；未改变请求、Prompt、Schema、模型、Standard价格锁、审批身份或工具禁用设置。
- 为避免`1e-999`被JavaScript解析成零，只在完整新形状上复用现有严格JSON扫描器再次检查number token：非零mantissa下溢失败关闭；指数数字不当作mantissa，字符串里的`1e-999`不参与。这个保守检查覆盖整份响应，不只8个计数；例如新形状加`temperature:1e-999`也拒绝，旧absent/空record解析行为不变。深度/UTF-8/字节限制与转义重复键拒绝继续生效。
- 独立审查要求保留旧保守收费边界：新形状若后续output、refusal、usage、model、schema、context或choice等任一Provider门失败，返回`usage:null / actualCostMicrousd:null / costUncertain:true`，现有Host因此记满10000 microusd预留；不会因普通语言token有效而减少此类失败的保守占额。只有全部门通过才使用原有严格token价格算法；此值仍是本地估算，不宣称实际供应商账单。预算存储、恢复内核和历史记录未改，旧absent/空对象的失败计费语义保持。
- 2026-09-19重新用OpenAI Docs和Context7核查[工具请求配置](https://developers.openai.com/api/docs/guides/tools)及[Responses文档](https://developers.openai.com/api/reference/resources/responses/methods/create)。取得的官方材料说明工具通过请求配置、工具结果有独立output项；没有取得该完整`tool_usage`结构的正式Schema或完备计费定义。因此本变更严格称为“已观察的本地兼容结构”，而非官方通用Schema支持。新结构不允许工具项、额外能力或动态Schema；诊断观测器继续只读，不成为授权器。
- 聚焦conformance：289/289通过，含8叶子类型/非零/负零/下溢攻击、5个object缺失/多余字段、转义键/重复键、20次递归字段重排、30类其他响应门、旧路径计费兼容及诊断/普通执行结果一致。捕获结果仍`qualificationEligible:false`，普通结果不携带观测值。日志 `C:\tmp\matrix-oasis-r22-zero-tool-conformance-20260919-a.log`，SHA-256 `557c22bfcecdae4717fa86a3acfa20b4a3e91d90535d1617d28456089e38b522`。
- 宿主聚焦矩阵：11/11通过，使用真正Provider校验器和本地fake fetch，不是直接伪造Provider成功标志；覆盖三类形状成功、旧形状后续失败、新形状非法输出/refusal/工具项/未知计数、receipt rename后模拟中断，再close/open恢复。新失败全部保守结算、Ledger前后相同、无Intent、无重请求、持久文件不含原始正文或tool_usage。此为同进程重开文件存储及故障注入，不冒充操作系统真实崩溃；既有进程级恢复另由完整回归覆盖。日志 `C:\tmp\matrix-oasis-r22-zero-tool-host-20260919-a.log`，SHA-256 `434c0189a08da421d8a3a5cc2b73d533b422627e28bbfc73bddc29e27a7ceed2`。
- 当前生产源码SHA为 `e89ac5aee651a569cc0e020b0e4ec754645a7dcfea535e4609737c067070ccc8`；最终conformance为 `333c1ac010d5f907b4dd5abf109c6f490d252d9c7d9fd550b14fda6ce35d40c5`；Host测试为 `3202f3c8ada7099a7dcdf201995d8ea9594b056f3ac551dd23a695bb131ffd0e`。原诊断事务/CLI/live-provider源码hash均与上一批相同。
- 初次范围检查exit 1，原因是新增annotation拒绝夹具内的合成URL触发`module-network-forbidden`，不是发生了网络请求。仅将该字段改为无URL的`untrusted-reference`，保持非空annotation和原拒绝断言；未修改扫描器或增加豁免。失败日志 `C:\tmp\matrix-oasis-r22-zero-tool-scope-20260919-a.log`（SHA-256 `c9a6a48c20f59b632b134d04a5ad7cf092e3d950f0c1e71ede6622c248497b0e`）保留。修正后原conformance重跑289/289通过，日志 `C:\tmp\matrix-oasis-r22-zero-tool-conformance-20260919-b.log`，SHA-256 `26149c6fd48da142fda86b422a62d568cc0aa51f513ccb6af22696e80ce478e0`。
- 完整`npm.cmd run verify:r22`以0退出，1121个测试条目中1120通过、0失败、1项既有Windows文件symlink权限限制跳过；分组为scope 95、live 332、references 4、contracts 14、runtime 13、Provider 562、Host 82、Godot 19。上述fixture修正发生在本次Provider测试加载前，最终源码确实纳入整组回归。日志 `C:\tmp\matrix-oasis-r22-zero-tool-verify-20260919-a.log`，SHA-256 `bdbacbd2e05fa3e816058e243a57b79e5340aee6e7646b6245e2635089a04495`。实际Godot 4.6.3 import/probe通过，不等于实时游戏、导航Action或真实Luna资格。
- 修正后原范围门重跑通过：`BOUNDARY_OK checked=1540 tracked=1528`、`ROUND_SCOPE_OK checked=118 changed=95`、`PARENT_SCOPE_OK checked=118 changed=95`及`git diff --check`；parent基线仍为`e927db557f71db420e07a49818c0d4ae1e0d6ce3`。日志 `C:\tmp\matrix-oasis-r22-zero-tool-scope-20260919-b.log`，SHA-256 `e3cddf05eb85f4b189f4e3b8f12f31a23250442bdd5075a5e71c17dfca40cbe1`。独立Sol只读审查未发现本批可操作P0/P1/P2，另实际复跑conformance289/289并复核Host及完整R22日志；该有界审查不保证不存在其他缺陷。
- 原真实observation、diagnostic report及账户budget仍分别为 `7b4ac0dbb5a6099334bf8a0f34fd565e05cc27678f5a2aaf3738140daf002372`、`2edbc021ad655ea2c5591c52630a647cd08e9451fc1288b62880ca0cdfe73db3`、`8432e5b6c02d3bfcde4afed47887260fc691b399907668264e9352e588c90d93`；MVP/V2状态hash也未变，V2仍为`r22-bounded-cognition-in-progress / claimAllowed=false / blockingRound=R25`。检查结束无Godot进程、43120/43122监听或本批Host夹具残留。没有旧记录重估、退款、新诊断claim、真实key/env读取、Luna请求、commit/push/PR。
- 本批未重新运行跨R1–R22完整`verify`、extraction或父client clean测试/build；既有父client基线失败不因专项通过而消失。游戏内真实对白、物理Action、人工验收及最终快照收口仍待完成，R22.7未通过。下一次真实资格必须使用新计划和完整payload披露、取得当次批准；不得复用两个已消费的诊断批准或把本地合成响应视为真实响应重放。

### billing 观测器接线与持久化矩阵（2026-09-19，仅本地）

- 用户要求补齐诊断接线与崩溃矩阵。本批不发真实请求、不读取真实 key/env、不修改旧预算/证据、不启动人工预览，也不提交/push/PR。R22.7及真实游戏资格仍未通过。
- 二次文档检查使用 OpenAI Docs 和 Context7。官方[向后兼容说明](https://developers.openai.com/api/reference/overview#backwards-compatibility)允许响应新增属性，但不定义 `Responses.billing` 的结构或费用语义。Context7命中的是独立 Organization Costs API，不能用它冒充 Responses 字段的定义，也未调用该 API。六个路径继续只是本地观测假设；缺失、null和各假设对象均没有真实模型新样本证明。
- 新 `createNpcCognitionBillingDiagnosticPlan` 保持原公开合成 `providerRequestJson` 逐字节不变；改变本地 turn、call plan、capture policy和approval身份。`evaluateNpcCognitionBillingDiagnosticFixture`只接受固定计划和内存bytes，无凭据或任意发送callback；借助相同strict parser做单向采集，不改变原Provider接受/拒绝、usage/费用或Action。原始对白只在瞬态Provider结果中存在，事务只接收脱敏observation。
- 既有 `scripts/r22-tool-usage-diagnostic.mjs` 新增显式 `--capture-profile billing`，plan/execute/recover均须匹配；省略或显式 `tool-usage` 保持旧默认。CLI plan新增完整固定采集政策披露，其SHA与事务审批绑定；仍显示原完整合成payload、价格锁、预算摘要与保留政策。未知profile、旧approval、捕获政策hash或源身份不匹配在发送前拒绝。
- 为避免改写历史，复用旧 `matrix-oasis.r22-tool-usage-transaction` 信封及v0.1–v0.4事务版本，已绑定的 `capturePolicySha256` 只可选择两个固定profile之一。每份observation还精确绑定自身profile、call plan和diagnostic approval；读取历史不依赖用户自由metadata。新profile不增加账户、不改变官方两名额硬门或已有追加授权语义；旧两个名额均已消费，本批不会创建第三个。
- 持久化校验拒绝未知字段、原始数值、动态路径/类别、错位字面量、矛盾root/parent形状、计数越界和失败状态夹带部分结果。`amount`/`tool_costs.total`仅保留类型，不据零值、正负号或数量判断收费。旧批次observer核心SHA仍为 `50df857501f4572e7f50f455e4b44bed656d0cdd84ce61d742d7144e2d8b2c97`；采集政策SHA仍为 `466b3aacdbdc9390eb3d0d6f47b975f315f57d8424679923bddfc627240f8789`。
- 新profile/core聚焦211/211：含19形状各20次、交叉审批、7类非法wire、原输入不变、观察与普通业务判定/保守费用不干扰。公开类型另拒绝诊断计划进入普通Turn、跨profile调用、保存金额数值和错位payer类别。
- 事务首跑275项中271通过、4失败（3个子测试及父组）。原因在新增夹具：redirect的冻结分类为 `network_ambiguous`，且5 ms总deadline在前置文件复验中耗尽、尚未请求。改正预期并将网络/body超时夹具对齐既有1000 ms设置，未修改生产传输或预算规则；同命令重跑275/275。首跑日志 `C:\tmp\matrix-oasis-r22-billing-wiring-transactions-20260919-a.log`，SHA `79a356d9b4587b267a8d9b687043fefb39c66883363f020127d196b56eb252df`；重跑日志 `C:\tmp\matrix-oasis-r22-billing-wiring-transactions-20260919-b.log`，SHA `80cda26039031a78a558194b91b9b21ceb97e64a924e49e9294fbf925e812f6f`。
- 同一事务套件分别运行旧/新profile，包括并发、过期、取消、claim/plan/reserve/dispatch/observation/输出/terminal阶段故障、局部stage拒绝、文件换身、预算删除及真实测试子进程终止后的零重发恢复。传输另覆盖两profile的7个发送前后崩溃点，官方路径只以测试假密钥和原生入口内存拦截执行一次；其中fixture报告中的 `realRequestCount:1` 是被拦截模式字段，不是供应商请求证据。所有实际供应商请求、真实密钥读取均为0。
- 新旧混合history共用同一预算并可分别恢复；旧目录字节不变。13类重签污染证据均拒绝，但未留存原文，不能据此宣称可重放模型响应内容，或抵御同时控制全部本地证据/预算的恶意文件所有者。`observed`只是观察记录已发布，非模型接受或资格通过；全程 `qualificationEligible:false`。
- 初次boundary因合成API key占位字串不符合既有占位识别规则失败；改为明确 `offline-placeholder` 后通过，未放宽密钥扫描。类型检查、boundary1545/1528与`git diff --check`通过。完整R22回归与最终scope结果待本批末记录；跨R1–R22全模块verify、extraction和父client不在本次重跑范围。
- 追加先证伪后修复：两条新反例证实持久化校验遗漏了“多节点/已知嵌套路径的最低深度”和“对象根直接路径不能为not-captured”一致性，重签这些矛盾summary后原audit错误接受。最小修复仅位于新 `validateBillingObservation`，不改observer、Provider业务判定、旧tool_usage或预算；加上嵌套路径深度1反例后共16类攻击、17/17通过。日志 `C:\tmp\matrix-oasis-r22-billing-wiring-falsification-20260919-a.log`，SHA `3bf10159b2a8863c27d7b8d27992f235872e79243f2cae44b0fc7de30205e613`。此处加强的是内部结构自洽，不是丢失原文的重新证明。
- 首次完整R22：1762项中1760通过、0测试失败、2跳过，最终因 `GODOT_4_6_3_NOT_AVAILABLE` 退出1，不能宣称全量成功；其中Godot执行项因本验证shell未指定工具路径而跳过，另一项为既有Windows symlink权限限制。日志 `C:\tmp\matrix-oasis-r22-billing-wiring-verify-20260919-a.log`，SHA `44ee5d3ff40c0ca8a1051149a2fa4806cf58a68844730b4924cb2f0aa169b4a8`。原先范围阶段约三分钟无输出，经只读PID/CPU核查确认是测试仍在推进，未终止进程或改写冻结测试。
- 已验证原Godot工具文件存在；仅在重跑子进程设置 `GODOT_BIN` 指向既有4.6.3 console，不下载或修改产品配置。最终代码重新执行完整R22，不用专项绿测替代Godot门；最终结果在后续条目记录。
- 新纯本地plan：capture policy `sha256:466b3aacdbdc9390eb3d0d6f47b975f315f57d8424679923bddfc627240f8789`；diagnostic approval内容身份 `sha256:f1c12bab261b9c1c558e115a5abcc2dce13099aaec8bb9680b30c588950dcbc0`；call plan `sha256:24045b215d8ea3e144ed3e83d982a9932f73481ac1144dbaf90e0808b6ac4b86`；原payload `sha256:26c613a49a6ac370b6fc933dac4ef8957f7eacb9fa82ba2e2754fcbd81efae6e`，1290 bytes，request/retry/ceiling仍1/0/10000 microusd。以上hash都不是已取得当次人工批准的证明，不创建新事务、名额或外部请求。
- 最终代码的完整 `npm.cmd run verify:r22` 退出0：1765项中1764通过、0失败、1项既有Windows文件symlink权限跳过；分组为scope95、live412、references4、contracts14、runtime13、Provider1120、Host88、Godot19。实际Godot4.6.3 import/probe通过，非游戏内Luna/导航/300帧人工资格。最终日志 `C:\tmp\matrix-oasis-r22-billing-wiring-verify-20260919-b.log`，SHA `fab2c7548c26f0aad878886246c730f548dbff0f9ae99476c47f838b0e32019f`。
- 最终源码：billing profile `ea7f11ba9723d283033bd18dc7305e1d9738373866efd469c59f00b7c5479eb7`；Provider入口 `44f962edf6cdff79b8c30ae22ced929d4820b5272b0715e80a60917060b974f2`；事务 `dc20e9095f1cb5c8a91a56439e1b111024a9dfa37b86c8857c23ba35c84dec36`；CLI `eb4565bc6acfe593227576cd2bde9e1eb088fd20385fee34ac393327c6ba09bf`。事务测试 `1a812b7d8b6019dc1dd8e384cfd5e3a3551dba7dcdd95ab56c290a917ba12902`、传输测试 `86e134db22477fbcc7a77c15518c84b270011c889108ec0b020ebf094feb03d3` 已包含追加证伪修复。
- boundary1545/1528、round123/100、固定base parent-scope123/100、类型检查及diff通过；验证结束43120/43122无监听、无对应R22验证或Godot残留进程。Host、call-store、旧tool_usage observer/profile、MVP/V2六项SHA均与开工前相同。仍在原分支/HEAD，23 modified/17 untracked，暂存为空；本批新增的是两份profile/诊断测试文件，其余为保留工作上的最小接线增量。
- 当前交付仅为独立billing采集、审批与事务接线的离线完成。没有真实Provider请求、真实凭据读取、commit/push/PR，未重跑跨轮全模块verify、extraction或父client。旧官方两名额仍已消费；若要真实billing采样，须先单独批准新增名额/预算边界，再展示完整内容绑定计划并取得当次批准，不得以切换profile、换目录或本批测试通过来绕过。回退仅撤销本批13个源码/类型/测试/文档文件的增量，旧历史和保守记账不清理、不退款。

### 单次 billing 追加授权链（2026-09-19，本地实施，真实发送仍待批准）

- 用户批准在同一既有账户上增加一个独立 billing 诊断名额，最多1请求/10000 microusd、零重试；并未批准在新完整披露之前发送。本批不读取真实密钥、不请求模型、不启动人工 Godot 预览、不提交/push/PR。旧两个claim、历史30000 microusd保守扣账、原始source身份和所有旧证据保留，不建立替代账户。
- 复用原事务与发送内核；仅在显式 `--capture-profile billing --credential-file <file> --billing-after-transaction <v0.4 SHA>` 时生成 official-only v0.5 plan。新 `billingAuthorization` 绑定已终结v0.4工具观察的plan、terminal、transport、observation及原预算entry，并递归复验其v0.2/v0.3零请求凭据失败前驱。三目录必须构成唯一合法链；未知/额外目录、错profile、跨源、未闭合或退费前驱一律拒绝，不开放通用quota参数或第四名额。
- v0.1–v0.4字段、原默认与凭据修复入口不改；v0.5使用独立新disclosure身份，旧approval和捕获器身份不是新发送批准。create、approve、reserve前、dispatch复验、publish、recover和普通history audit均验证前驱链。新目录一经创建即消费该名额；取消、零请求失败、派发不确定和崩溃均不使其重新可用。恢复不要求密钥；已发布完整三链允许读取旧终态，不能重新派发旧请求。
- 继续使用同一封闭billing观察政策及公开合成payload，1290 bytes / SHA `26c613a49a6ac370b6fc933dac4ef8957f7eacb9fa82ba2e2754fcbd81efae6e`；价格、Schema、Standard、端点、Provider业务接受规则、预算内核及R19/R20/Godot/Creator均不改。2026-09-19通过OpenAI Docs重新读取[官方价格](https://developers.openai.com/api/docs/pricing)：Luna Standard输入/缓存读/缓存写/输出分别为每百万token $0.20/$0.02/$0.25/$1.20，与现有锁一致。金额类型观察不是实际账单、不是供应商billing Schema证明，始终`qualificationEligible:false`。
- 首个新入口测试先在旧代码中得到 `R22_CLI_ARGUMENT_INVALID`，本地实现后通过。新grant矩阵覆盖20次disclosure稳定、20并发仅一次被拦截的假发送、错/旧审批不占额、历史字节及扣账保留、零请求/取消/HTTP失败/网络歧义、合法满额账户、既有保留预算、前驱换身及整包重签、grant逐字段重哈希伪造、10个持久化中断点和旧接口回归。fake fetch与测试专用假凭据中出现的`realRequestCount:1`仅表示受测官方代码路径，不能作为真实供应商请求证据。
- 初轮27条目24通过、3失败（2子测试及父组）：重签夹具误选缺失counter，产生NaN而未进入攻击；改为明确已捕获`image_gen.total_tokens`后原反例通过。第二轮50条目48通过、2失败（1子测试及父组）：预算存储早已捕获其文件操作，注入并未命中其rename，不能据此说崩溃被测试。改为在下一次dispatch暂存文件创建前中断，同时读取自有fixture证明预算已reserved；10个窗口和精确边界反例重跑12/12。生产持久化/预算规则未为这些失败而修改，失败日志保留。
- 10窗口为同进程文件操作故障注入，不冒充新v0.5的操作系统强制杀进程证明；旧通用事务测试另有实际子进程终止覆盖。无完整身份的空目录和未完成transport暂存继续fail closed，不臆造零请求终态；dispatch已持久化但transport未知时保守扣满10000 microusd且不重发。整包hash只证明内部绑定，不抵抗能任意改写全部本地历史/预算的同权限攻击者；本批未引入外部可信计数器。
- 回退限本批6文件增量，不清理历史、不退款。未来若v0.5已发布，旧代码不能读取该新增历史；必须保留新格式读取器，不能删除证据来恢复旧代码可读性。跨轮全模块verify/extraction/父client及真实Luna游戏资格不在本次新增证明范围，R22.7/MVP/V2声明不变。完整回归、范围门和新plan-only披露结果在以下续记。
- 全部旧新事务/传输/预算三套件重跑333/333通过、0跳过：`C:\tmp\matrix-oasis-r22-billing-grant-transactions-20260919-a.log`，SHA `0839eba1a23f87bd6fbe03a2f37e5ad91bac3f6d82ab0034ed11fb3a6153e10e`。前两次新矩阵失败日志分别为`billing-grant-focused-20260919-a.log`（SHA `8e2d6a9ce40019ee6ac82808d8c6c421bea76929ca293f3a4b318c7b948d0781`）及`billing-grant-focused-20260919-b.log`（SHA `2e6089cec73679e5ca9b1f4aaefd9d821d16ffa2019023074c37607d42f1e9bf`），均在`C:\tmp\matrix-oasis-r22-`前缀下保留；窗口/边界重跑12/12日志`C:\tmp\matrix-oasis-r22-billing-grant-crash-20260919-c.log` SHA `7c0bb6d36e5af0e594a890ddd433a2ff8daac2c654181e72b1b2058daefa403a`。
- 此快照事务源码SHA `edad5fb682b7bb59fdefca1402673f58ab3c6f4fe6b6244a214778fbdfe6feb9`；CLI `49c3635c040f5f53016837fbb749666c3191e5a54cba0ed19d05b48a89ef769e`；精确边界 `59de291960150147c82596279e5ff1b1ec4a59b1c5ab7f760edad6f061707151`；transport测试 `7f21c2e0152bc7ece03fb1f335796aa6ff2d83aa036f538889a67703a796473c`。普通Provider仍为 `44f962edf6cdff79b8c30ae22ced929d4820b5272b0715e80a60917060b974f2`，call-store/Host/MVP/V2及真实账户manifest、budget、旧observation/report SHA与批前相同。
- 首次完整`verify:r22`被交互会话切换中断，日志`C:\tmp\matrix-oasis-r22-billing-grant-verify-20260919-a.log`仅81 bytes启动信息，无测试结果或成功退出；只读检查确认无对应Node验证进程后，以`-b.log`重跑同一命令，不计为通过。独立Sol审查亦须取得实际结论后才记录完成，不能沿用已中断会话的启动记录。
- 独立Sol静态审查发现P1：新grant虽绑定当前pinned-file，却未要求与成功v0.4前驱为同一来源；另一文件或原文件变化可以生成fresh disclosure，违背本次不换来源的追加边界。两条真实本地假凭据反例先得到`Missing expected rejection`（3个测试条目失败），日志`C:\tmp\matrix-oasis-r22-billing-grant-source-red-20260919-a.log`。不是绿色套件能排除的风险，未据此前333绿测放行采样。
- 最小修复仅两处生产条件：billing plan前门比较当前元数据身份，`validateBillingLink`比较两份完整credentialSource，覆盖create/dispatch/recovery/audit；对应两条静态必需字串及删除反例。没有改旧credential-repair、发送器、Provider、价格或预算；读取失败夹具改为身份不变但open失败，保持真实零请求故障语义。聚焦22/22通过，日志`C:\tmp\matrix-oasis-r22-billing-grant-source-green-20260919-a.log`。Sol随后只读复核确认通路封闭，未报告新增可操作P0/P1/P2；未将该静态结论称为真实账户或网络证据。
- 真实文件仅经metadata-only helper复验，且open拦截器禁止读取内容：与v0.4的固定来源身份同为`sha256:1b28f279d0a1712a514d04329fb156e37d7f5d8b5c8e0b4086fd43dc9c5ac079`，credentialReads=0。该相等性不能证明远端Provider账单账户，后者没有新证据。本地同权限全盘篡改及供应商账单语义仍不在此证明范围。
- `verify:r22 -b.log`开始后追加了这项先红修复，因此无论其中测试结果如何都不是最终代码快照的全量证明；以修复完成后的独立重跑和新日志为准，不把混合时点结果用于最终放行。
- 最终源码快照完整`npm.cmd run verify:r22`退出0：1824个测试条目，1823通过、0失败、1项既有Windows文件symlink权限跳过；分组通过数为95/470/4/14/13/1120/88/19，Godot4.6.3实际import/probe通过。日志`C:\tmp\matrix-oasis-r22-billing-grant-verify-20260919-c.log` SHA `4e4d6efe559621ab55130c28b2957b8fd462c9d54c9a2d749bb26455cf0066f2`。这是R22离线回归，不是跨轮完整verify、extraction、父client或新的游戏/模型资格。
- 最小来源修复的红测日志SHA `e02dc2cd3a6dd194ebc1f172a014d276347e05e045363da493c443f53ef2cd00`，22/22聚焦绿测日志SHA `9afc503014e012c9188f38cdb003646bb7259ed735eb56b551df059a8b497dc9`。最终事务源码SHA `30aa383dc1505e0b3096c362f46530e840b89f6317d944eb73c783a74e246e15`、CLI `49c3635c040f5f53016837fbb749666c3191e5a54cba0ed19d05b48a89ef769e`、boundary `3aec8609995bfa638de66596417619fbfe690a4f8981caf0912ace66065364d0`、transport测试 `096c8bfeb8cf21a5756397582aeae8b50d1d1c028fa95a3f28d161a419026f82`。
- 最终代码的boundary1545/1528、round123/100、固定base parent-scope123/100及`git diff --check`通过；日志`C:\tmp\matrix-oasis-r22-billing-grant-scope-20260919-b.log` SHA `6db0812d44298b80bb3de655f2cb8bfc39a8a7c5615bc26b8fac90f6bc4b4597`。预算内核、Host、业务Provider、MVP/V2五项SHA仍与本批开始前相同；未暂存、提交、push或创建PR。
- 最终plan-only披露已生成，但未批准发送。新transaction SHA为`sha256:1ffa0caf03ad46144ba26c1787fede6c9c5e608d8198ac58c11882bb80d0c2ff`，capture policy SHA `sha256:466b3aacdbdc9390eb3d0d6f47b975f315f57d8424679923bddfc627240f8789`，固定公开请求SHA `sha256:26c613a49a6ac370b6fc933dac4ef8957f7eacb9fa82ba2e2754fcbd81efae6e`，1290 bytes。计划输出为`C:\tmp\matrix-oasis-r22-billing-diagnostic-20260919-a`，该目录尚未创建。只在会话显示完整公开合成payload，文档不持久化payload或原始响应。
- plan使用读取拦截器禁止真实key内容及Provider环境变量读取、禁止fetch，并在前后比较旧账户和两份旧输出的完整目录内容指纹：真实credentialReads=0、environmentCredentialReads=0、providerRequests=0、diagnosticClaims仍为2、预算仍3条/保守charged30000/reserved0。第一次plan脚本因Windows路径在JavaScript中被错误转义而在绝对路径检查处失败；仅修正调用参数为规范化路径后重跑，未改产品代码、未创建第三claim或复用已消耗请求。
- 前后相同的目录内容指纹：账户24项为`sha256:8140bf54c18bc304cdb69874cf456cb7c2addc0ac9d983436d0ba05b8d3ae393`；首份输出5项为`sha256:1ba6f3ad46d6b6b161b1518f77efec65a428000cd9e392bb7a87a77ff313d662`；第二份输出6项为`sha256:5170b113a3187eaa80d0d830f40d7f3c74d809843086b3cbd34af9b5c667bcd1`。指纹对稳定排序的相对路径、目录项或文件长度/SHA数组计算；短暂读取锁已释放，不把目录mtime变化误写成历史文件变化。
- 新披露仍为官方Responses、Luna、显式Standard、一次/$0.01/零重试，只有脱敏billing结构观察，不改变`qualificationEligible:false`或生产非null billing拒绝规则。按当前官方数据控制补充：默认abuse monitoring最多30天，但法律或安全需要可更长；可能存在最长24小时的加密prompt cache；`store:false`不代表ZDR。本批没有新实际账单证据，也没有真实Luna/Godot游戏资格；待用户批准此新transaction后才可读取固定来源并派发一次。

### billing 单次真实观察（2026-09-19，已消费批准）

- 用户在完整1290-byte公开合成payload、新transaction `sha256:1ffa0caf03ad46144ba26c1787fede6c9c5e608d8198ac58c11882bb80d0c2ff`、一次/$0.01/零重试及保留/采集披露后明确批准。发送前复核HEAD、四项实现/测试SHA、真实manifest、budget、两个旧claim和新目录不存在；官方Standard价格仍为每百万token输入/缓存读/缓存写/输出`0.20/0.02/0.25/1.20`美元，未换模型、端点、来源、payload或价格锁。
- 使用现有`r22-tool-usage-diagnostic.mjs execute`一次，退出0；持久terminal为`observed`、realRequestCount=1、dispatchCount=1、transportRequestCount=1、providerReplayRequests=0，qualificationEligible=false。未手工发送第二次请求、未重试、未启动Godot，未修改业务Provider接受规则。新输出位于`C:\tmp\matrix-oasis-r22-billing-diagnostic-20260919-a`，仅6个闭合脱敏文件。
- 观察到billing根为object，nodeCount=2、maxDepth=1、object=1/string=1、unknownFieldCount=0。唯一存在的固定字段为`payer`，类别`literal_developer`；`amount/currency/service_tier/tool_costs/tool_costs.total`全部缺失。按这份脱敏结构可重构已知语义形状`{"payer":"developer"}`，不是保留的原始响应，也不是官方完整billing合同或实际计费证明。
- 现有生产拦截点为`packages/npc-cognition-provider-openai/src/index.mjs`中的`billing_absent_or_null`与非null中立字段检查；该规则必然拒绝本次观察到的形状。已有276-cell合成对照重跑277/277通过，日志`C:\tmp\matrix-oasis-r22-billing-observed-control-20260919-a.log` SHA `14a04c5e1401a4b0e574eaa1d0552fde698866d9485bacdbbeefd95e23b22fe7`。这是本地规则对照，不是完整真实响应重放；不能推断其他字段、返回模型、usage、对白或Action已经通过，亦不能证明唯一根因。
- terminal/report SHA `e0d45dfed70d3e914cd14c59ea04c560c8a02d22ff895e41e60e9cc8099607b4`；observation SHA `d0de2cbe85c3f5c17ccb9ca52d7b9d144957c384844d1fd63f6dd8450a70a3e7`；transport SHA `2e2a7737cddd3bc8a9b65b7ac54dba6788dc663c8e658f5d4ee2d3e2162387ab`。新budget SHA `bf10efed661c204888564cdb970e885306b7da2c7adc014d0b1e5ee1fcb0f8b3`，4条、保守charged40000、reserved0；其中新增10000 microusd不是已核验供应商账单，旧30000保守扣账未退款或覆盖。
- 已通过无凭据、无fetch的实际CLI恢复复验，前后全部已发布字节一致。仅在内存中排除本次新claim及新预算项后，旧账户内容指纹仍为`sha256:8140bf54c18bc304cdb69874cf456cb7c2addc0ac9d983436d0ba05b8d3ae393`；两个旧输出指纹仍为前述值。新账户31项指纹`sha256:d9ed395aecf197f0ebdfb3c876a1d9bfa7cedf1f1a2d82835aad38b0103c4491`，新输出6项指纹`sha256:110812a5c700d8bfb4ec9fbc851b65831f2c62e2f3b0d37880b392a7a72fedb5`。恢复日志`C:\tmp\matrix-oasis-r22-billing-recovery-20260919-a.log` SHA `ace200689c2067fad722cb08830f8195a299d12b91101aa5b6ac5a4ceede8ec0`，recoveryRequests/credentialReads/environmentReads均0。
- 初次粗粒度敏感标记摘要误报：`Authorization`子串匹配合法`billingAuthorization`字段，且PowerShell布尔集合被当作单个bool；随后按闭合JSON精确字段逐文件复验，6文件的禁止原文字段和凭据格式计数均0。未将模糊扫描摘要冒充真实泄露，也未据单一关键词扫描宣称绝对隐私保证；证据完整性由实际恢复校验共同证明。
- 本次仅消费已批准的第三个诊断claim，不产生第四名额；原文、密钥、response ID均未持久化。当前只确认一个具体协议兼容缺口，下一步应先评估该已观察形状的窄范围兼容及费用不确定性，再做离线矩阵；不得以观察成功放宽任意billing、退款、自动再调用或宣称R22资格完成。未修改生产实现、MVP/V2状态，未提交/push/PR。

### 已观察 billing 形状的最小兼容与证伪（2026-09-19，仅离线）

- 用户授权最小兼容修复和离线证伪；本批不读取真实密钥或供应商环境变量，不追加真实请求、诊断名额或预算，不重估旧Receipt。上节“现有生产拦截点”描述的是修复前状态，本节才是当前本地实现。HEAD保持`9adb5493501a954360edf6525542ffface55653e`，不提交/push/PR，不切换R22.7或MVP/V2声明。
- 二次核查使用OpenAI Docs与Context7。官方[向后兼容说明](https://developers.openai.com/api/reference/overview#backwards-compatibility)允许新增响应属性，却不定义`Responses.billing`。Responses参考和Context7返回内容未提供`payer`正式语义；不能用Organization Costs API或未知字段兼容原则替代本地安全判断。唯一新增依据仍是上节脱敏观察重构的精确`{"payer":"developer"}`，不是完整真实响应或供应商账单。
- 唯一生产改动在Provider `src/index.mjs`：经既有strict JSON/root校验后，用闭合`captureRecord`识别恰好一个`payer`字段及精确字符串`developer`，作为丢弃的元数据。旧缺失/null语义不变；空对象、其他payer、大小写/空白/Unicode伪装、额外字段、金额及工具计费信息全部拒绝。没有新增公开类型、Schema、请求、审批、观察词表、Host生产逻辑、持久格式或价格规则。
- 费用只沿用原严格usage和Standard价格算法，不从payer推断金额或权威。精确新形状也纳入现有失败保守路径：只要任何其他响应门失败，usage和actualCost均不再作为可信结算证据，完整预留不释放。完整合成成功的16 microusd仅是测试usage得到的本地计算，不是已核验真实账单。旧缺失/null的失败计费逐字节保持；`billing_absent_or_null`旧absence子规则在新形状下为`not_checked`，不谎称满足absence，也不把独立reasoning失败误归因为billing失败。
- 先红后修：新精确对象回归在旧代码中0通过/1失败，唯一业务拒绝为billing字段策略，其余28个检查通过。日志`C:\tmp\matrix-oasis-r22-billing-compat-red-20260919-a.log` SHA `3c477fc34ae5b7f438589ad1e02293ee4b782be95cbf105ec5705d1aa59f1980`。先定位现存兼容缺口，再实现窄例外；未用更多真实请求猜测。
- 新Provider矩阵覆盖19种payer类型/值、14类额外字段，三种tool_usage形状分别交叉36项独立门攻击，另含旧路径费用、20次排序/转义确定性及重复键、非法UTF-8、深度、大小、孤立代理项。旧276-cell假设矩阵仍保留全部单元：当前36成功、240拒绝，仅新实测形状改变预期；观察入口与普通入口共享结果，仍`qualificationEligible:false`，观察不驱动业务判定。
- 初次聚焦新组146项144通过/2失败：测试把添加/转义字段后的实际responseBytes误当作不变量；修正为实际UTF-8长度，proposal/费用/请求比较保留。随后完整聚焦848项847通过/1失败：JSON允许转义孤立代理项解析为字符串，该payer应在`envelope_profile`拒绝而非`json`；只改测试阶段预期，未放宽生产检查。失败日志`C:\tmp\matrix-oasis-r22-billing-compat-focused-20260919-a.log` SHA `9ab6c93fd67e6bae8f86c417563cf3344935ba9bdebf01f7d2859a69ebab822e`。
- 最终Provider/conformance与billing-diagnostic同命令重跑848/848、0失败/跳过。日志`C:\tmp\matrix-oasis-r22-billing-compat-focused-20260919-b.log` SHA `7fcc85cea72951ebbe1c2b468fc823a7b9f361abd9cb12baaf7f9c4465cf221a`。独立Sol只读复核并另跑新聚焦146/146，未报告新增可操作P0/P1/P2；其数量与主套件重叠，不重复累计。
- 实际Provider解析器接Host的41场景及父组共42项：原10场景保留，新增三种tool_usage与八种结果交叉、五种非法billing，以及success/fallback两个Receipt发布故障。证明审批前零假key读取/发送、批准后一次fake fetch、失败扣满10000、success按测试usage扣16，关闭/重开存储不再读key或请求，receipt/预算相同且Ledger不变。此组成功选择为null，不据此证明真实NPC移动或Action已执行。
- Host首跑42项40通过/2失败（一个子项及父组）：成功Turn的receipt写入发生在display ACK而非execute，原故障点未命中。将注入移到真实发布边界，生产代码不改。失败日志`C:\tmp\matrix-oasis-r22-billing-compat-host-20260919-a.log` SHA `fc21066df97d704531044c15349c3c94fa862970f07ebe4a98259c9f83e86150`；同命令重跑42/42日志`C:\tmp\matrix-oasis-r22-billing-compat-host-20260919-b.log` SHA `d655476eabbd226405ad83532f26ae3dab4fad0e254ef740d56a3d2d5d968c3f`。这是同进程rename故障加存储重开，不冒充新增OS强杀证明。
- 当前四项代码/测试SHA：Provider `925079b4598f4ad4b95aacb78791a263df7cfc7e46f95df8c7fe1256ab2cfc00`；conformance `42303ece9f38f2737886d3283d2673be40540545e116afaca669694d739fc549`；billing-diagnostic测试 `6637341c4a1fb7067656172e0f915372eb02cdbf363b68b9818e9fa825d81aa9`；Host测试 `3615f1d32e5e9dca6616e5161f57cb799b5ce6bf5d7404e7b53922243b40c64d`。本批共六文件含两份文档，诊断测试必须随共享解析器同步验证，不改诊断发送或事务逻辑。
- 当前证据只证明这个已观察形状的本地兼容及保守失败，不证明完整真实响应、官方payer语义、实际费用或R22游戏资格。旧三个诊断claim均已消费；没有自动补发权限。跨R1–R22全模块verify、extraction、父client clean验证和实时对白/物理Action/300帧人工验收不在本次新增证明范围，最终完整R22专项及范围结果在后续条目记录。回退只撤销本批六文件增量，保留所有历史、预算及其他未提交工作。
- 最终源码快照的完整`npm.cmd run verify:r22`退出0：2001个测试条目、2000通过、0失败/取消、1项既有Windows文件symbolic-link权限跳过；分组通过数95/470/4/14/13/1266/119/19。Godot4.6.3实际import/probe通过，不是Luna对白、物理Action或300帧人工游戏资格。日志`C:\tmp\matrix-oasis-r22-billing-compat-verify-20260919-a.log` SHA `b3b5c7c2d7dd6ed1c7d9d56bb7f9ba1fe706c42af1337efe398e109c20782449`。以上已包含聚焦测试，不再把848、42或146叠加计数。
- boundary1545/1528、round123/100、固定base parent-scope123/100及diff通过，暂存为空；日志`C:\tmp\matrix-oasis-r22-billing-compat-scope-20260919-a.log` SHA `6db0812d44298b80bb3de655f2cb8bfc39a8a7c5615bc26b8fac90f6bc4b4597`。实际结束时Godot进程与43120/43122监听均0，没有启动人工预览或操作其他共享服务。
- 结束时九项冻结/真实证据hash逐一与开工值比较相等：Host、call-store、诊断事务、诊断CLI、MVP/V2，以及真实observation/report/budget。真实budget仍为`bf10efed661c204888564cdb970e885306b7da2c7adc014d0b1e5ee1fcb0f8b3`；四条记录/本地保守charged40000/reserved0及三个已消费claim未改变，不能解释为40000 microusd实际账单。本批供应商请求、真实凭据读取、历史重估、commit/push/PR及资格状态切换均为0。
