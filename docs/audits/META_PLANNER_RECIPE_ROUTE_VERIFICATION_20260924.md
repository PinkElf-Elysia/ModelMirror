# CW10 Recipe 路由与修复闭环离线核验

## 结论与边界

2026-09-24，响应用户“开始验证 / 继续 / 恢复进程”。本次只做取证、离线测试和文档记录，
没有修改 `server/`、`client/`、生产 Prompt 或 Runtime。没有外部模型调用、活表写入、
Proposal Apply/批准、发布、共享栈操作、Commit、Push 或 PR。

结论：**当前拒绝错误图的安全边界有效，但生成与修复闭环尚不可靠，E3 和 PR 门禁仍不通过。**
不是单个缺失分支造成的全部问题；同一真实产物还存在独立的谓词语义和类型错误。
现有证据不能证明单模型架构已到上限，也不能证明更换模型或提前 V4 就能解决。
本次发现的是可复现的工程反馈缺口和已观察到的模型误用，两者应分别处理。

工作树为 `C:/tmp/modelmirror-meta-planner-cw10-recovery-e`，分支
`codex/meta-planner-cw10-recovery-e`，HEAD `09e8a7d644f626b3e1aa606fcf148f360fc9aadc`。
开始时有 178 个既有修改/未跟踪路径；本次没有刷新上游、搬迁工作树或覆盖既有产出。

## 1. 样本与请求绑定

只读核对 Proposal `proposal_655bec0a57614dc39b59294a5ee70b1f` 的私有保留产物，
revision 1，原始生成和唯一定向修复各一份 Recipe。它们不是诊断占位图，
也不代表保留了 Provider 原始字节或隐藏推理。

预览后端端口 16489 当前连接被拒绝，本次没有重启。改为只读读取已批准的合成表 Schema
以及内置 Todos 的安全目录元数据，使用现有 Registry 重建快照。第一次漏掉 Todos，
严格 checksum 校验阻断 13 项回放；补齐已验证的元数据后才继续，没有绕过断言。
未读取凭据，未给请求增加 Todos 授权，未读取业务记录正文。

| 绑定对象 | 核验结果 |
| --- | --- |
| Capability Snapshot | 与历史完全一致：`ce98f84a4f95f7fc109f805623b42178af45cb26f5ab09fc306ca393e2e24ca0` |
| 首次 Recipe checksum | `1ff05209bc0222e68d8b764fd413a4b6da87c13cb6d2c901766a0bde80ae5fcc` |
| 修复 Recipe checksum | `44d7201904d0169988efdcddd17e99e15ed24cf6ddfd24167a20cb478e4fbab2` |
| 首次 Recipe 请求 checksum | `f760109ce6a1476b7de1904153155bbda104a77731b72a54b326822f558b811b` |
| 唯一修复请求 checksum | `2714593b01d21288e69fae4c6e9ca859fd778371a3c280655b8b1c9a2636ab1f` |

请求重建经过现有 `collect_chat_completion_text` 的消息准备和 payload 构造，使用 fake sender，
同时禁止 `httpx.AsyncClient.send`。两个请求 checksum 均与当时观察值完全一致，
不是只对本次重建内容自行生成 hash。没有外发，没有保存完整 Prompt。
Proposal 文件、合成表 SQLite 文件和最近调用账本在测试前后 SHA-256 一致。

本次固定的生产源码 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `server/meta_agent/meta_planner_v2.py` | `ddd37139c5eede8920fa3af69052ddf59aa18f2fab2437a39fb11925a48fcbec` |
| `server/meta_agent/generation_recipe.py` | `0a2aa1af167780b84ed60b87ad656488d64804aa1d6af3fc10014641a4835129` |
| `server/meta_agent/generation_diagnostics.py` | `43855cccdc84703e9858b787662ade43ed94d181fa0906ab778dd2d3629cace4` |
| `server/meta_agent/control_flow.py` | `7ffa7e139c0496f71feebcb751d4af15027ffbff3371dae81e9fad80e038dff8` |

## 2. 已证实的问题

### 2.1 直接阻断正确，但没有修复进展

两份保留 Recipe 均通过 Pydantic 解析及当时生成 JSON Schema，再在同一 `recipe_lowering`
检查失败：`route_record` 应显式声明 `case_1 / case_2 / default`，实际 `branches=[]`，
后继使用 `parallel.paths`。这不能表达互斥 outcome，不能由服务端猜测路径归属。

两次 Recipe 整体 checksum 不同，但只改变了 `nodes`，`control_flow` 完全相同，
其 checksum 为 `1a41e95335af77997d23885f1fa825fd966139cf0090145070ef5d4d1292b9b4`。
当前按整份 Recipe 判断“不变”的观察不足以说明失败子结构是否改进。
应保留原样拒绝，不能据此增加自动重试或自动搬动业务分支。

授权、resolve、compile、publish preflight 仍为 blocked；本次回放没有让这些阶段伪装为通过。
页面占位候选的 `missing_workflow_agent_model` 不是此真实 Recipe 的根因。

### 2.2 同一产物中的其他独立错误

直接调用现有 Runtime 比较函数和 Adapter 权威类型检查，得到：

| 原始问题 | 离线反例 | 结果 |
| --- | --- | --- |
| 两条 `is_null` 规则分别携带 false/true | 输入 null、库存 2 的记录、库存 8 的记录 | `is_null` 忽略比较值，两条规则真值完全相同，第二条永不选中；标签不能改变语义 |
| `route_stock` 将 Query 整条记录交给数值 `lt/gte` | stock 2、5、8 | 都拒绝为 `NUMERIC_COMPARISON_TYPE_MISMATCH`；作为正对照，Condition 选择 `stock` 字段后正确区分边界 |
| Agent 的 task 端口直接消费对象 | Query 到两个 Agent、Update 到一个 Agent，共 3 个输入 | 权威源类型为 object（Query 可空），目标为 string，三处均不兼容 |

这些是组件级语义与类型反例，不是已经执行了实际工作流或业务写入。
没有猜测并修好真实分支后声称整图可执行，也没有以人工修改替代模型成功证据。

### 2.3 修复反馈不是完全缺失，而是不完整、不够可操作

精确重建的提示包含完整分支、禁止并行替代互斥、对象进 Agent 前需序列化等规则。
`validation_issues` 也确实含有 `route_record` 和期望分支的文字错误，不能归因为“规则未发送”。

但 `recipe_lowering` 的这类错误只是普通 `ValueError`，安全诊断只返回
`CONTRACT_CHECK_FAILED + fingerprint`，缺少结构位置、node ref 和期望/实际 outcome 差异。
对 Condition、Multi Route、读取 `success/error` 三类结构构造反例后，
新增的“可定位结构化修复反馈”验收断言均失败。原有 unknown/repeated/omitted ref 的
结构化处理没有覆盖这类错误。

同一修复提示的 `semantic_feedback` 中，control path、dependency 和 input type issues 都为空，
`input_type_issue_count=0`。事实上本次已独立证实 3 处类型错误。
在图展开失败后不进行全图证明是正确的，但“未执行局部类型收集”不应呈现为“零问题”。
需要区分独立可核验事实与因结构缺失而 blocked 的场景证明，不应伪造一个补全图继续验证。

### 2.4 上下文负担已测量，注意力因果尚未证明

以下是重建提示 JSON 的字符数，不是 tokenizer 计数、收费 token 或模型注意力测量：

| 项目 | 首次生成 | 唯一修复 |
| --- | ---: | ---: |
| user prompt | 34583 | 39964 |
| required_schema | 21451 | 21451 |
| node_contracts | 7080 | 7080 |
| rules | 1239 | 1239 |
| validation_issues | 不适用 | 59 |
| invalid_generation | 不适用 | 3896 |

Schema 与节点契约约占首次提示的 82.5%、修复提示的 71.4%，修复沿用整套生成上下文。
这提供了压缩和重排的工程依据，但**不能单凭长度断言错误由上下文窗口、注意力或某个模型能力导致**。
本次没有跨模型对照、提示消融或新增真实生成，因此不报告成功率提升。

## 3. 反证与验证边界

两类合成目标（数值质检、文本事件）各自带/不带读取错误分支，共 4 组非线性图，
均通过相同 Recipe lowering 与真实编译器。把互斥分支改成并行的成对反例均被拒绝。
这证明当前编译结构并非只能表达线性工作流，不证明模型能稳定生成这些结构。

另外，两类图把合法业务谓词反转（`lt -> gte`、`equals -> not_equals`）后仍能编译，
Runtime 真值却违背固定目标。这不是权限验证器应猜自然语言目标的证据，
而是证明**编译成功不能替代需求一致性与效果验收**。
后续必须使用独立于生成图的输入/预期效果用例，不能从生成结果倒推正确答案。

先前 Sol 独立静态审查同样指出：现有绿测主要使用预先正确的控制结构及人工提供的修复结果，
缺少同一语义下“互斥 vs 并行”成对检验。该审查不访问 Runtime、凭据或浏览器，
没有独立执行测试；上述主智能体实跑补充了其中可运行的证据，不把两者重复计数。

## 4. 命令、结果与环境问题

使用现有 `.tmp-recovery-e/run_offline.py`：清除凭据环境变量、隔离 Runtime Store、封锁外网。
本批新脚本和安全摘要均在忽略目录，没有把真实保留产物复制入 `server/tests/`。

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B `
  .tmp-recovery-e/run_offline.py .tmp-recovery-e/test_recipe_route_audit.py `
  --junitxml=.tmp-recovery-e/recipe-route-audit-final.xml
```

- 首次默认权限启动无输出；核对精确命令行后只结束本任务两个父子测试进程。
  使用允许创建隔离临时目录的权限后正常启动。无输出过程不计测试失败或通过。
- 第一次有效执行：8 passed、3 failed、13 setup errors，36.59 秒。
  13 errors 为本次审计夹具漏掉 Todos 导致的快照 mismatch，不归因于生产功能。
- 补齐该元数据，同一测试选择集重跑：**21 passed、3 failed、0 errors、0 skipped**，28.04 秒。
  三个 failed 是新提出的可定位诊断验收缺口，保留红测，不改断言、不加 xfail。
  21 个 passed 包含坏图正确拒绝与限制反例，不能概括为“生成通过 21 次”。
- 4 项 warning 为既有 FastAPI `on_event` 弃用提示。

| 本地证据 | SHA-256 |
| --- | --- |
| 忽略目录审计脚本 | `d10bb997353f66472b32002e03ff00d0e005bf5b68805ed86f8c339f7ba49734` |
| 最终 JUnit | `c120f258cece25268b3c815328d94baf4400bb23c0e9d7d1aa68570409b14581` |
| 安全摘要 JSON | `1ddccb67b1a7a86173516244008405fd851a24eeebceb70d367240dfdc014cdc` |

相关现有回归命令：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B `
  .tmp-recovery-e/run_offline.py `
  server/tests/test_meta_planner_generation_recipe.py `
  server/tests/test_meta_planner_recipe_matrix.py `
  server/tests/test_meta_planner_recipe_lowering_recovery.py `
  server/tests/test_meta_planner_semantic_repair_contract.py `
  server/tests/test_meta_planner_repair_preparation.py `
  --junitxml=.tmp-recovery-e/recipe-route-existing-regression.xml
```

五文件相关回归结果：**100 passed、0 failed、0 errors、0 skipped**，146.13 秒，
4 项既有 FastAPI warning。JUnit SHA-256 为
`b0cbb8b88204be1c7190733f5cd410d719f0bcc8ff7e1121671e05042767e117`。
该命令首次权限审核超时，进程未启动；按工具允许重试一次后完成，不计为测试重试失败。
其中隔离 Backend 效果测试使用独立合成状态及模拟 Agent，不是实际业务表或真实模型成功证据。

本次未执行整个 `server/tests/`、前端构建、真实预览器操作、最新上游集成或 hosted CI。
未把过去的全量、费用或 UI 结果升级为当前证据。

收尾检查：`git diff --check` 通过；两份文档和审计脚本的尾随空白、已知凭据签名扫描均为
0 命中，不宣称覆盖任意秘密形式。四个取证生产文件 hash 与开工一致，最新真实调用账本 hash
仍为 `5b8440b810c4250f353fda0ebca19e97b3f2f6c33240fe0c3791cf9f0bc7a82e`。
JUnit 条目逐项核对为 24 项（3 failed）和 100 项（全部 passed）。所有本次测试会话已结束。
审计脚本、JUnit 和安全摘要经 `git check-ignore` 确认被忽略；待提交路径 179 项（原有 178 项
加本报告），暂存为空。未提交 SQLite、Runtime、上传或凭据。

## 5. 建议收口范围（尚未实施）

不建议直接再改一条提示后付费重试，也不建议据此提前多 Agent。保留 22 类能力、
三次初始 completion 上限、NodeContract/Adapter/Graph IR 单一权威和人工审批边界。

1. **同一 Recipe 预检分离独立事实与全图证明。** 从授权与解析成功的节点派生端口类型、
   谓词输入域及期望 outcomes；结构检查返回稳定 ref、位置、实际/期望差异。
   即使控制结构失败，也报告已可独立证明的本地类型问题；路径与可达性仍明确 blocked。
   不构造假边继续跑证明，不按标题或目标猜字段与分支。
2. **收敛模型的决策界面，而非继续叠规则。** 新生成契约把判空的无操作数语义、
   整值路由与字段比较的差别表达清楚；机械 outcome 清单、输出类型和变量继续由服务端派生。
   现有解析语言先保持兼容，不另造业务表达式；若需改变生成协议，应另列边界并审阅。
   精简重复 Schema/说明，以紧凑、权威派生的约束及一个通用分支范式引导，不能针对库存案例硬编码。
3. **唯一修复面向已证实差异。** 将当前 failed 项、可定位差异、局部事实和 blocked 项前置，
   明确要求模型改动失败结构；比较失败子结构与问题集合，不能因说明文字变化就认定修复有进展。
   无进展时保留失败并交人工，不新增自动调用、不放宽授权、不推断业务真值。
4. **验收使用成对与效果证据。** 除历史原样回放，至少保留两个不同字段/谓词领域、
   null/边界值/命中/不命中/读取错误、合法互斥与错误并行对照，以及结构合法但业务谓词写反的反例。
   新真实生成必须另获授权；以独立定义的效果用例和人工审核验证目标，不能只看 `valid=true`。

以上是基于本次证据的收口建议，不是已完成实现，也不放开窄 Recipe 恢复入口的节点编辑权限。
本次没有验证失败图经人工/额外模型修复成功、隔离业务效果或真实泛化成功。
回退本次审计仅涉及新增文档与忽略目录测试资料，无生产回退或业务数据撤销操作。
