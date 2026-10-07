# Recovery E：E-G1 真实生成复测

## 结论

本次真实生成未通过，不具备 E-G1 验收或 PR 提交依据。没有执行写节点、下游 Agent、
隔离评测、Proposal 批准、Xpert 发布、Commit、Push 或 PR。不能将待审批的兜底图视为模型生成成功。

## 固定范围

- 执行时间：2026-09-23 05:56:31 至 05:57:13 UTC（本地 2026-09-22 22:56:31 至 22:57:13）。
- 工作树：`C:\tmp\modelmirror-meta-planner-cw10-recovery-e`。
- 分支：`codex/meta-planner-cw10-recovery-e`。
- HEAD：`09e8a7d644f626b3e1aa606fcf148f360fc9aadc`，另有既有未提交 E 集成改动；不是纯 HEAD 验证。
- 独立正式应用预览：前端 `127.0.0.1:15489`，后端 `127.0.0.1:16489`。
- Provider / 模型：OpenRouter / `deepseek/deepseek-v4-flash-0731`，legacy Planner 路由。
  本次不是 Managed 能力认证证据。
- 合成表：`table_972e625145604c2f9419176b128d863b`，Schema v1；
  `sku:string`、`stock:integer`、`status:string`；前后均为零记录。
- Schema checksum：`86cc158d8d90861da3b88a29cad4d38a672110c30f35a83910deb981508249cf`。
- 授权：查询及 `update/status/最多 1 行`，仅生成，不运行；无其他资源、中间件、视觉授权。
- 目标：查询 DEMO-A；缺失停止；stock 小于 5 时仅将该条 status 改为待补货；否则不改，
  最终中文说明实际检查和修改结果。没有缩减分支目标换取通过。

## 请求及结果

通过预览器点击一次“生成候选智能体”，应用完成任务规划、能力编译和唯一一次定向修复。
三次均为真实请求，HTTP 200、`finish_reason=stop`，未发生不确定派发或自动额外请求。

| 阶段 | Provider 报告输入 Token | 输出 Token | 总 Token | 结果 |
| --- | ---: | ---: | ---: | --- |
| task_plan | 3080 | 481 | 3561 | 任务计划通过 |
| capability_compile | 9280 | 1568 | 10848 | Recipe 控制流展开失败 |
| generation_recipe_v1 修复 | 11255 | 1568 | 12823 | 同一错误，内容未改变 |

总计 27,232 Token，仅为 Provider usage，不是账单金额核销。
请求身份 checksum：`b3abd3e055ba5146b69fb236b53ec08d2dcea9c71c435619e638fdc8e5fe3f58`。
提案：`proposal_336c31a199e64c2a9fbc6bd7f277d751`，revision 1，pending，validation=false。

## 已确认的阻断

1. **Recipe 至 IR 边界拒绝控制流引用。** 首次与修复均停在 `intent_parse`，
   诊断为“控制流节点 conclude_agent 不存在或重复；不能通过重复引用生成循环。”
   `generation_recipe.py::_control_edges` 将“未声明 ref”和“重复 ref”合并为同一错误；
   当前保留材料不足以区分二者，不能直接断言是循环或互斥分支合流。
   资源授权、类型解析、编译和发布预检未完成，不能认为其他配置已正确。
2. **唯一修复没有改变结果。** 两次 Recipe 的内容 checksum 都是
   `d8bf44a574e2ffe5c409aeda86bb9c4bc9bfa5d16e568ab2e6f8a0518a90c1a6`。
   Provider、collector、validator 三处 checksum 一致；证据不支持传输截断或采集器篡改。
   两次 JSON Schema 检查均通过，阻断不是 JSON 语法失败。
3. **恢复层未覆盖 lowering 失败。** `_evaluate_payload` 在 lowering 成功后才取得 GraphIntent；
   失败捕获只保留 GraphIntent，两个 attempt 均为 `retention_status=unparsed`，无可恢复 Intent。
   Headless 返回 `recovery_intent_not_retained`，人工修复面板无法加载原图。
   `full_payload_replay=false`，不得凭摘要重构并声称是原始模型输出。
4. **后续 modelId 报错属于兜底图。** `candidate_origin=server_synthesized_fallback`、
   `graph_ir_status=fallback_unapprovable`。`_fallback_candidate` 主动清空一个 Agent 的 modelId，
   以阻止审批。这不是本次失败应优先修补的模型配置问题。

## 预览环境问题与本次失败分开记账

- 独立预览出网护栏最初阻止设置页必需的模型目录检测；经用户单独授权一次 GET 后调整。
- 随后发现 `model_router` 与 `server.model_router` 双模块名导致辅助护栏未装到真实 API 实例。
  已先用实际 Router 离线复现 500，再改为绑定 API 使用的实例，并添加启动身份校验。
- 调用护栏 9 项测试、实际 Router 2 项回归通过；这些使用模拟响应，不计真实模型证据。
- 已授权 GET `/api/v1/models` 实际完成一次，HTTP 200。保存连接成功后前端自动发起第二次探测，
  被一次性护栏在出网前拒绝；刷新页面确认连接已保存，没有为此扩大配额或再次探测。
- 真实生成开始后没有更改生产代码、Prompt、验证规则、模型输出或候选内容。

## 后续处理边界

本报告只定位已证实的阶段，不据单次样例判定模型能力上限或要求提前引入多 Agent。
下一步应审查 Recipe 控制流表达、具体修复反馈和 lowering 前安全产物保留之间的契约衔接，
先建立可复现证据与失败恢复路径；不以补齐兜底 modelId、删除分支或增加自动调用次数收口。
真实新调用、效果评测及 PR 均仍需各自门禁和授权。

原始安全调用回执位于本工作树忽略目录 `.tmp-recovery-e/preview/calls.json`；
Runtime、连接密钥、配对码、完整 Prompt 和原始 Provider 响应不进入本报告或提交范围。

## 后续离线定位与修复

用户批准定位修复后，按以下已证实缺口收窄处理；本节不是新的真实复测，也没有重写上面的历史结果。

| 已证实缺口 | 最小处理 | 保持不变的边界 |
| --- | --- | --- |
| 未声明与重复 ref 共用错误，重复引用被误写成循环 | 独立错误码、JSON 位置、首次引用位置，单独记录 `recipe_lowering` 阶段 | 不猜拓扑、不自动去重、不删除分支 |
| 完整描述修复只拿到笼统文本，原样返回无法明确归因 | 给唯一修复提供结构化诊断和短检查清单；无效且 checksum 未变时记 `RECIPE_REPAIR_UNCHANGED` | 仍最多三次初始 completion，不自动追加 |
| 安全可解析 Recipe 在 lowering 失败后丢失，人工入口只接收 Intent | 复用原私有失败产物保存规范化 Recipe；管理侧仅可替换有界控制结构 | 不开放配置、任务、资源、授权或付费 Graph Patch 修复 |
| Recipe 占位的状态可能被可变报告标签掩盖 | 普通修改/批准共用独立恢复门禁；成功恢复回执仅由服务端绑定预览提交产生 | 回执不是业务运行或持续有效 Graph 的证明 |

新的 `recipe_control_flow_v1` 是尚未形成 Intent 的受限恢复协议，不替换普通 V3 Graph Patch。
恢复预览重新通过 lowering、授权、类型、路径、编译和发布预检；Apply 重算预览，并在 Proposal
锁内复核产物、目标 revision、原授权与当前快照后才写入 pending 提案一次。资源 Store 与 Proposal
Store 仍非跨库事务，审批/运行复核仍不可省略。原始失败产物不被修复后的内容覆盖。

独立只读审查促成了报告标签绕过护栏、真实协议收据与 Intent 优先读取的补强；最后复查未发现
当前 HTTP 可达绕过。该结论是源码审查，不代替测试或真实 Provider 证据。

### 验证记录

- 五文件重点回归先出现新诊断变量遮蔽问题，已修复并重跑；规范化空字段的测试比较改为完整
  `GenerationRecipeV1` 规范化值，没有放松语义断言。最终该组 120 passed。
- 新增真实目标 `draft_revision` 漂移反例：1 passed，35 deselected，确认不是只改 Proposal revision。
- Headless/Authoring 四文件回归：120 passed，覆盖私有回执、普通编辑拒绝、API Apply 与重放。
- 前端四文件：44 passed；生产构建通过，仅保留既有大 chunk 提示。修改文件语法检查通过。
- 离线合成浏览器检查：定位重复 ref、仅编辑控制结构、预览未写回、模拟应用 r1 到 r2 后锁定；
  1270 像素桌面与 390 像素窄屏无横向溢出或文字重叠。只验证组件和模拟交互，不连接后端/Provider。
  临时 15499 页面、进程与视口覆盖已关闭/恢复，原 15489 预览和共享栈未重启。
- 新攻击文件最终复跑：36 passed，91.12 秒，覆盖未声明/重复/遗漏 ref、来源与协议注入、
  目标及授权漂移、审批绕过、恢复重放和原始产物保留。
- 66 文件跨模块首跑：1362 passed / 1 failed，730.62 秒。失败是新增收据字段误入旧 Graph Patch
  白名单；已将该字段限定于 Recipe 收据，未修改旧测试的安全白名单。包含原失败测试的
  Headless/Authoring 四文件重跑为 120 passed。
- 同一 66 文件矩阵最终复跑：1364 passed / 4 warnings，447.70 秒；多出的一项是补充的真实目标
  revision 漂移测试。四条 warning 均为既有 FastAPI `on_event` 弃用提示，不是新的运行错误。
- 后端语法、`git diff --check`、本次文件凭据特征扫描通过；暂存区为空。测试进程均已结束。

最终矩阵使用禁外网、清除凭据并隔离 Store 的离线 runner，实际命令如下；这不是全量后端测试：

```powershell
$tests = @(Get-ChildItem -LiteralPath server/tests -Filter 'test_meta_planner*.py' |
  Sort-Object Name | ForEach-Object { "server/tests/$($_.Name)" }) + @(
  'server/tests/test_meta_agent.py', 'server/tests/test_workflow_node_contracts.py',
  'server/tests/test_xpert_runtime_authoring.py', 'server/tests/test_xpert_publish.py',
  'server/tests/test_xpert_evaluations.py', 'server/tests/test_xpert_structure_evolutions.py',
  'server/tests/test_xpert_app_api.py', 'server/tests/test_workflow_typed_values.py',
  'server/tests/test_workflow_typed_ai.py')
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B `
  .tmp-recovery-e/run_offline.py @tests `
  --junitxml=.tmp-recovery-e/recipe-recovery-regression.xml
```

JUnit 证据保存在忽略目录 `.tmp-recovery-e/recipe-recovery-regression.xml` 与
`.tmp-recovery-e/recipe-recovery-attacks.xml`，不纳入提交。

### 未验证与回退

- 当前 E-G1 仍是失败的真实样例，未新增真实调用；既有提案没有 Recipe 正文，不能追溯恢复。
  新的合成反例覆盖重复、缺失、遗漏及失败恢复，不声称重放原始响应。
- 未执行实际写入效果、后续 Agent、提案批准、发布或新的全量 `server/tests/`；未刷新上游。
- 不据本次离线结果宣布生成成功率或泛化稳定。真实调用仍需新的精确授权。
- 回退关闭新的 Recipe UI/恢复协议及新生成入口，保留已有 Recipe 产物、私有回执的读取和批准门禁。
  不把含新增回执的快照直接交给不认识该字段的旧二进制，不删除历史产物、不撤销业务数据。
  普通提案不写入空的新增回执字段，无批量迁移、新 Store 或生产依赖。

## Recipe 恢复修复后的真实复测

本节记录用户再次授权后的新实测，不覆盖前文的首次失败和离线验证记录。
**结果仍为失败，不能判定 E-G1 或 PR 门禁通过。此次在更早的 Schema 解析阶段阻断，
没有执行到新修复的 Recipe lowering 诊断和恢复路径。**

### 固定输入与调用边界

- 执行时间：2026-09-23 07:19:26 至 07:21:11 UTC（本地 00:19:26 至 00:21:11，UTC-07:00）。
- 工作树、分支和 HEAD 与前文一致，运行包含前节离线修复的未提交源码。
- 使用相同合成表、目标、模型、温度、节点范围和 `update/status/最多 1 行` 授权。
- 生成请求 checksum 仍为 `b3abd3e055ba5146b69fb236b53ec08d2dcea9c71c435619e638fdc8e5fe3f58`。
- 在独立正式应用预览器点击一次生成；调用上限为 3，没有点击再次生成或人工模型修复。
- 本次使用独立安全账本 `.tmp-recovery-e/preview/retest-recipe-recovery/calls.json`，
  未覆盖首次实测账本。未读取或输出凭据原值，也没有复制其他预览的连接。

### 真实结果

| 阶段 | Provider 报告输入 Token | 输出 Token | 总 Token | 结果 |
| --- | ---: | ---: | ---: | --- |
| task_plan | 3080 | 219 | 3299 | 缺少必填 `tasks`，任务计划不合法 |
| task_plan_v1 修复 | 3314 | 488 | 3802 | 任务计划通过，消耗唯一修复机会 |
| capability_compile | 9352 | 2138 | 11490 | `resources[0].kind` 和 `resources[1].kind` 枚举校验失败 |

3 次均完整完成，HTTP 200、`finish_reason=stop`，不确定派发为 0。
合计 **18,591 Token**，仅为 Provider usage，不是账单金额核销；本次授权额度已用完。

提案 `proposal_833c3e67d5f04b9eae8ad2852f3ed939`：revision 1、pending、validation=false，
`repair_used=true`、`repair_protocol=task_plan_v1`。没有生成可批准候选。

### 证据定位与边界

1. 编译响应仅完成 `intent_parse`，诊断为 `SCHEMA_VALIDATION_FAILED / literal_error`，
   位置为上述两个 `resources[].kind`。授权、类型解析、编译和发布预检均被阻断。
   该资源集合只允许四类 Agent 绑定资源；现有安全摘要未保留模型实际填写的非法值，
   不得猜测其具体类型，也不能据此证明后续节点和分支已经正确。
2. 每次请求的实际契约与期望契约一致。三次响应在 Provider、collector、validator 处
   的内容 checksum 分别一致，结构对比均为 `same_structure`；本次证据不支持
   传输截断、采集器改写或校验器替换响应。第三次内容 checksum 为
   `6dc9bc4cacebf0cb01cdd8b6ff293d9b78ebec790874b767229ea667f529151c`。
3. 失败产物为 `diagnostic_subject=unparsed_response`、`retention_status=unparsed`、
   `recoverable=false`。Headless 返回 `recovery_intent_not_retained`，
   `can_author=false`、`can_approve=false`；预览器仍提示原始图未能安全保留。
   当前新增恢复只覆盖 Schema 合法而 lowering 失败的 Recipe，本次不在此覆盖范围内。
4. `full_payload_replay=false`，本次不能作为完整原始响应重放夹具。
   不能用兜底图或安全摘要补造原图，也不应通过放松资源枚举、补齐兜底 modelId、
   删除目标分支或增加自动调用次数把失败改写成成功。
5. 表的读取核验仍为零记录，活动 Schema 仍为 v1。没有执行任何写节点、下游 Agent、
   Evaluation、Proposal 批准、Xpert 发布、Commit、Push 或 PR。

### 环境与下一步判断

本次只重启独立 E 后端，前端及共享栈未动。首个启动使用了底层 Python 而非既有 venv，
因缺少 `dotenv` 导入失败；随后改用已有 `modelmirror-mcp-test-venv`，健康检查通过。
该环境故障发生在生成前，没有模型派发，也没有安装新依赖。真实复测期间未修改生产源码。

下一步应先核对模型可见 Recipe 契约、资源目录与任务计划提示的表达是否一致，并单独界定
Schema 不合法响应的安全诊断与人工恢复边界；这是待审查方向，不是已经证实的根因或实施。
不能由本次结果直接判定 DeepSeek 能力上限、多 Agent 必要性或 lowering 修复无效。
新的模型调用与后续修改不在本次复测中执行；既有离线测试结果不能替代本次失败的真实证据。

## 资源契约收口与受限 Schema 草稿恢复

本节对应其后用户“重新核对 / 开始收口”的本地修复授权。没有新增真实模型调用、活表写入、
下游 Agent、Evaluation、批准、发布或 PR；不是把上一节真实失败改为成功。

### 重新核对结论

- 实际任务计划 Schema 包含必填 `tasks`，不能归因于服务端漏发该字段。
- 生产入口使用 Recipe；旧兼容 GraphIntent 提示已有节点自有资源与 Agent 绑定形态提示，
  新 Recipe 缺少相同投影。未授权任何 Agent 绑定时，旧私有 Schema 仍允许非空 `resources`。
- 严格 Recipe Schema 一旦失败，原捕获只留下摘要，即使失败只在可定位的资源 kind 枚举。
  部分提示测试仅覆盖旧兼容入口，不能证明当前实际派发的提示覆盖该边界。
- 这些是已核对的链路缺口，不足以证明它们是某次模型响应错误的唯一原因；未保留的原响应
  不能重放，不能断言模型实际用了 `data_table`。下面该值只用于独立合成反例。

### 本地变更

1. `resource_generation_contract.py` 统一 Recipe 与兼容提示的安全目录、形态说明和 Schema。
   节点自有引用与四类 Agent 绑定分开；ID/kind 必须来自授权与目录交集。绑定目录为空时数组
   只能为空；Query 与逐操作写授权独立。知识检索过滤无活动索引资源，旧 Agent 绑定行为不变。
2. 保留草稿只放宽 `resources[i].kind` 枚举，标记 `recipe_resource_draft_v1`。其他 Schema、
   Adapter 配置、安全扫描和大小/深度限制不变；草稿中的资源 ID 必须来自授权目录，防止用
   未识别字符串夹带正文。不自动修改该值，不将草稿送入 lowering 或普通 Graph Patch。
3. 人工入口只允许单次 `replace_recipe_resources`，必须使用严格 Agent 绑定 Schema，不能编辑
   节点、任务、控制结构或授权。预览先恢复严格 Recipe，再跑完整语义、编译和发布门禁；
   Apply 绑定产物、候选、revision、授权和资源快照，仍只更新 pending Proposal 一次。
4. 保留原始产物与私有恢复回执；普通整包编辑、审批和付费 Graph Patch 不能绕过。UI 展示
   精确错误位置、只读节点与分离的预览/应用。原未保留的真实提案仍然不可恢复。

两次独立 Sol 只读复核分别提出无索引资源过滤和资源 ID 夹带风险，已补针对性反例及守卫。
跨 Store 最终检查与持久化之间仍为乐观一致性，不声称实现全局事务；Apply 的历史 validation
不是执行授权，后续审批和运行必须重查。本次不混入跨 Store 锁或 Runtime 改造。

### 验证记录

- 资源投影首组先为 70 passed / 2 failed：旧合法夹具未授权所引用资源；补明确合成授权后
  相同命令为 72 passed。后来过滤无索引知识库又暴露两处无活动索引的合法夹具，补足固定
  合成 index 后复跑；未放宽“未授权或无索引必须拒绝”的断言。
- 恢复首组为 83 passed / 2 failed：新测试遗漏规范化默认值、误用 dataclass 的 `model_copy`。
  修正测试后，与资源投影等七文件联合测试为 162 passed / 4 warnings，141.04 秒。
- 补充草稿资源 ID 白名单后，五文件最终重点测试为 109 passed / 4 warnings，107.90 秒。
  包含三次预算在计划修复耗尽、Schema 错误保留、秘密/越权 ID 拒绝、ASGI Preview/Apply、
  重启、重放、旧恢复兼容和付费入口预先阻断。completion 为模拟，记录 CRUD 设置拒绝入口。
- 跨模块首跑为 1394 passed / 4 warnings，474.96 秒；该进程启动后才补完资源 ID 白名单，
  不将它当作最终代码完整回归。最终代码同一 67 文件矩阵复跑为 **1396 passed / 4 warnings**，
  491.16 秒；新增两项为不透明资源 ID 与节点自有 ID 的保留攻击。所有测试进程均已结束。
  四条 warning 均为既有 FastAPI `on_event` 弃用提示，无新运行错误。
- 前端全部 Meta 组件：7 文件、65 passed，20.36 秒；其中失败与模型修复组件 23 passed。
- 帮助文案同步后再次执行前端生产构建通过，Vite 14.71 秒，仅保留既有大 chunk 提示。
- 离线浏览器使用真实组件与内存模拟请求：显示 `resources.0.kind`，只编辑绑定，预览未应用，
  显式模拟应用 r1 到 r2 后锁定，共两个模拟请求。1280x900 与 390x844 检查无文字重叠；
  窄屏 clientWidth/scrollWidth 都为 380。临时 15499 页面、进程已关闭，视口已恢复。
- 13 个 Python 文件语法检查通过；20 个本次路径的已知凭据模式扫描 0 命中，不能保证识别任意秘密。
  `git diff --check` 通过，暂存区为空；临时页面/JUnit/构建输出均在忽略目录。
  工作树共 173 个既有及本次修改/未跟踪路径，禁止提交的 Runtime/SQLite/日志/构建路径扫描为 0。
- 一次回归命令误将 JUnit 参数作为唯一 runner 参数，发现未限定文件后在测试输出前停止，
  不计入任何通过结果；随后用明确文件矩阵运行。没有修改测试门槛。

重点命令：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B .tmp-recovery-e/run_offline.py `
  server/tests/test_meta_planner_recipe_schema_recovery.py `
  server/tests/test_meta_planner_recipe_integration.py `
  server/tests/test_meta_planner_recipe_lowering_recovery.py `
  server/tests/test_meta_planner_generation_contract.py `
  server/tests/test_meta_planner_prompt_projection.py `
  --junitxml=.tmp-recovery-e/resource-schema-final.xml
npm.cmd run test -- run --maxWorkers=1 --fileParallelism=false src/components/meta
npm.cmd run build
```

完整跨模块命令仍用前文显式 `test_meta_planner*.py` 加九个领域测试文件矩阵；本次 67 个文件，
最终 JUnit 为 `.tmp-recovery-e/resource-schema-final-regression.xml`，不是全量 `server/tests/`。

### 当前停止点

- 真实账本仍为 3 次，额度已耗尽；管理接口复核旧提案仍为 `recovery_intent_not_retained`、
  `can_author=false/can_approve=false`。不删除旧提案、不重置账本，不把合成例子冒充原始响应。
- 本次未重启原 15489/16489 正式预览后端；下一次真实验收前须定向加载最终源码，保留原 Runtime
  与调用账本，并按新批准的精确范围建立新的调用预算。未改 Provider 凭据或共享栈。
- 本次未重跑全量后端/全量前端、未刷新上游；既有独立基线失败与实际写入/泛化门禁仍未闭环。
  不以离线通过宣布 DeepSeek 生成率提升、稳定或已达 PR 门禁；下一真实调用需新授权。
- 回退时关闭新草稿恢复入口与生成投影接入，保留原失败工件、私有回执和审批阻断；不删除
  历史证据，不迁移公开 IR/Runtime，不撤销业务记录。没有 Commit、Push、PR 或自动合并。

## 资源契约收口后的真实复测

本节对应用户随后“开始复测”。2026-09-23 08:44:08 至 08:44:50 UTC
（本地 01:44:08 至 01:44:50），通过 15489 独立预览器点击一次“生成候选智能体”。
仍为 OpenRouter / `deepseek/deepseek-v4-flash-0731`，一次生成、最多三次 completion；
只发送既定零记录合成表的安全元数据、测试目标和受限 Planner 契约，不执行候选。

### 固定范围与加载核验

- 请求 checksum 仍为 `b3abd3e055ba5146b69fb236b53ec08d2dcea9c71c435619e638fdc8e5fe3f58`。
  表、目标、模型、温度、Agent 上限以及 query/update/status/最多一行授权均未改变。
- 只重启任务自己的 16489 后端，使用既有 venv，前端刷新加载当前生产构建；Capability
  V10 / IR V3 / 22 类节点，合成表 published、Schema v1、零记录。没有读取或复制凭据，
  仅在后端内存使用用户已保存的同一 OpenRouter 连接。
- 使用独立 `retest-resource-schema` 账本，旧账本未重置。旧账本 SHA-256 前后相同：
  `0948ca62405f930a11e02e06162ca92cf6cb92a2fed7649a7ecd690420a285e4`。
- 实测源码 SHA-256：`resource_generation_contract.py` 为
  `0f7201cde9b0737c762dc2938205d674fb3c299aa8c443221157c32eedac93c5`；
  `generation_recipe.py` 为 `5262f568303c8e3fe533dcf5502b25daf7bcbdba965e2e55b86af77f76fd60d0`；
  `meta_planner_v2.py` 为 `fc3500697be9233a1549f500176ffcdb20b3397ea247b4a401a70e61df876c16`。
  本次未修改生产源码、重跑全量或刷新上游。

### 真实结果

| 阶段 | 输入 Token | 输出 Token | 总 Token | 结果 |
| --- | ---: | ---: | ---: | --- |
| task_plan | 3080 | 530 | 3610 | 首次任务计划 Schema 通过 |
| capability_compile | 9671 | 1555 | 11226 | Recipe Schema 与 lowering 通过，语义检查失败，6 项问题 |
| graph_patch_v1 | 21117 | 864 | 21981 | 13 项 Patch 操作解析、应用通过，剩余 4 项控制流问题 |

三次均 HTTP 200、`finish_reason=stop`、完整完成；不确定派发为 0。合计 **36,817 Token**
仅是 Provider usage，不是账单核销。请求实际契约与期望契约一致；三次响应在 Provider、
collector、validator 的正文 checksum 一致，结构均为 `same_structure`。这不支持传输截断
或采集器改写假说，也不能推导模型已具备稳定生成能力。

提案 `proposal_7ae72802a2474d6d810590dc88fbe345` 为 r1 / pending / validation=false，
`repair_protocol=graph_patch_v1`。**本次复测仍未通过，不满足 PR 门禁。**

### 原图核对与尚未验证的边界

1. 首发与修复后的安全 Intent 都保留了两个确定的语义问题：
   - `check_record_exists` 使用 `is_null`，但 `matched` 去库存比较，`unmatched` 去未找到终止。
     即“空记录”继续比较，“存在记录”反而停止，方向与目标相反。
   - `check_stock_low` 输入是完整 Query 记录，配置为 `lt / number / 5`，未提供 `field=stock`。
     这不是单纯需要放松 nullable 校验；空值与整条对象都不是数值。
2. 使用生产 `evaluate_typed_condition` 做三个不联网探针：空记录、stock=2、stock=8。
   `is_null` 分别返回 matched/unmatched/unmatched；缺少 field 的数值比较全部返回
   `NUMERIC_COMPARISON_TYPE_MISMATCH`。没有执行表查询、写入、工作流或模型。
3. 唯一一次 Patch 添加两个 JSON Serialize，消除了 Agent 输入的两个类型错误，但未改变
   两个 Condition 的配置或原有分支方向。剩余问题为数值比较不可证明、场景零终点、
   matched/unmatched 不可证明及下游不可达。尚不能确认修正条件后其他路径和数据依赖均合法；
   汇总 Agent 仍引用只在更新分支产生的结果，需要后续独立验证，不能假定整个图只剩两处问题。
4. 两个尝试都为 `retention_status=retained`，选择 attempt 2，来源 `graph_intent_v3`。
   Headless 为 `mode=recovery / status=editable / can_author=true / can_approve=false`，
   `executable=false`。预览器已实际展示原图节点配置、连接修复、诊断和人工预览入口。
   本次未编辑、Preview 或 Apply，不能据此声称该真实失败图已经人工修复通过。
5. 本次没有触发资源枚举错误，所以新 `recipe_resource_draft_v1` 分支仍只有离线证据；
   不能把本次 GraphIntent 留存当作该分支的真实命中验收。原始 Recipe 全文未保存，
   `full_payload_replay=false`；保留的安全 Intent 可用于后续语义重放，但不等于 Provider 原文。
6. UI 中兜底候选的 modelId 报错与“0 路由/1 场景”不是原始失败图的编译成功证据。
   本次原图的 resolve、compile、publish_preflight 均被前置语义失败阻断。

本次授权额度已用完，停止继续真实调用。建议后续先以这两份固定安全 Intent 做离线证伪，
核对条件字段选择、空值分支方向、分支专属数据消费与修复诊断优先级，再决定最小变更范围。
这是后续建议，不是本次已实施修复；不得靠放宽门禁、兜底图或继续追加调用宣布通过。

最后核验合成表仍为零记录、Schema v1；没有真实写入、下游 Agent、Evaluation、人工 Apply、
Proposal 批准、发布、Commit、Push 或 PR，共享栈未动。
