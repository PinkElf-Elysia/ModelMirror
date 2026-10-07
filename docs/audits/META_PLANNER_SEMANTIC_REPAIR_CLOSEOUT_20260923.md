# Meta Planner 生成与修复语义收口核验

日期：2026-09-23。本文记录本地实现、离线证据及随后失败的真实复测，不是 E3、真实生成稳定性或 PR 门禁通过证明。
前面各节的“本次”指 S1-S3 离线实施阶段；后续授权复测独立记录在文末，不改写此前证据。

## 范围与依据

- 用户要求针对整个链路的薄弱点收口，不继续为某个库存实例堆叠补丁。
- 工作树：`C:/tmp/modelmirror-meta-planner-cw10-recovery-e`。
- 分支：`codex/meta-planner-cw10-recovery-e`；基线 `09e8a7d644f626b3e1aa606fcf148f360fc9aadc`。
- 开工时已有 173 个修改/未跟踪路径；本次在既有产出上续作，不重置历史修改，不触及脏主工作区。
- 继续保持单模型、初始最多三次 completion、22 类能力及 Graph IR V3。显式模型修复仍须独立授权，
  每次最多一次调用；不能把它隐藏在初始生成预算中。
- 本次真实 Provider 调用、活表写入、真实 Proposal Apply/批准/发布、Commit、Push、PR、共享栈操作均为零。
  未更新或重启 `15489/16489` 预览，故该预览不代表本次源码。
- 先前真实失败见 [E-G1 复测记录](META_PLANNER_RECOVERY_E_G1_RETEST_20260923.md)，不修改历史调用或费用证据。

## 系统根因与处理

| 已核对的薄弱点 | 本次处理 | 没有做的事 |
| --- | --- | --- |
| 初始模型用 Recipe 决定语义，语义失败后却切换到更低层的 Graph Patch，模型要重新承担变量和逐边机械工作 | 新生成的唯一修复始终返回完整 Recipe；显式付费修复对有匹配 Recipe 的失败也使用同一语言，服务端降低并派生 Patch | 不增加模型、调用轮数、自动重试或业务模式库 |
| 输入字段、判空及 outcome 真值关系没有在生成和修复侧形成同一权威投影 | 从控制契约向两条提示链路投影相同语义，说明空 field、顶层字段、无隐式标量转换及分支真值 | 不依据节点标题猜条件，不自动补空值保护或改变阈值 |
| 前置输入域失败时，下游空诊断容易被理解为已经证明 | 结构、条件输入域、分支可达性、终点和数据可用性分别记录 passed/failed/blocked；已有独立反例仍标 failed | 不放宽路径校验，不把未知场景计为通过 |
| 降低后的 Intent 存在时原 Recipe 被丢弃，后续只能修低层图 | 同一私有产物配对保存 source_recipe、Intent 及独立 checksum，修复前重新降低并验证一致性 | 不新建 Store，不逆向猜测旧 Intent 的结构化 Recipe |
| 模型输出到 Patch 的机械转换也可能破坏输入顺序或扩大操作数 | 按语义 ref 派生，保留有序输入前缀、按边键去重断开，并验证 Patch 回放与目标语义一致；仍受 64 操作限制 | 不自动拆分多次 Apply，不扩大 Patch 上限 |
| 显式修复授权后，异步客户端准备期间 Proposal/目标/资源依据可能变化 | 发送前复核当前依据与正文/路由；返回和读取建议时再次校验，漂移建议不可载入 | 不声称跨 Store/Provider 原子事务；已派发后的漂移仍可能产生费用 |

模型仍负责业务条件、来源选择、控制路径和 Agent Prompt。服务端承担端口类型、变量、原生 Handle、
资源版本和 Patch 的机械派生。静态门禁只能证明契约及路径性质，不能证明模型忠实理解了用户业务意图。
现有证据不足以断定单模型能力已经到顶，也不足以据此提前进入 V4 或更换模型。

## 实现边界

### S1：控制语义与证明状态

主要文件：`control_flow.py`、`generation_recipe.py`、`generation_diagnostics.py` 和 `meta_planner_v2.py`。
共享控制契约在生成与修复提示中使用相同投影。首次修正字段后仍会暴露错误分支；修正条件后还要重新
验证分支专属数据，不能把此前 blocked 的检查沿用为通过。管理端显示当前证明状态及阻断前置项。

### S2：同语言修复与配对产物

主要文件：`meta_planner_v2.py`、`failed_artifacts.py`。已解析 Recipe 完整进入有界修复上下文，不静默
截取半份图；无法解析的原文仍走既有受限反馈。新生成的修复不能返回旧 GraphIntent 或 Graph Patch。

`recipe` 继续专指尚未降低的恢复形态，已降低的配对源使用 `source_recipe`，避免误触旧整包编辑判别。
单份 Recipe/Intent 各限 1 MiB，含所有尝试和诊断的私有产物限 2 MiB。旧 Intent-only 兼容入口仍保留
原 Graph Patch 修复；没有匹配 Recipe 时不猜测还原，不强制迁移旧提案。

### S3：显式修复的确定性桥接和发送守卫

主要文件：新增 `recipe_repair.py`，联动 `model_repair.py`、`model_repair_transport.py`、
`managed_gateway.py`、`graph_patch.py` 及两处既有管理端组件。

模型只返回语义 Recipe；服务端严格解析、降低、派生 Patch、回放核对，再运行原 Recovery Preview。
操作数超限返回独立的 `repair_patch_unrepresentable`，不误报为一定是模型 JSON 错误，也不自动扩大预算。
修复仍是建议，载入、预览和确认应用分离，不能自动变成可执行或已批准提案。

Managed 的末次守卫放在异步客户端进入后、派发标记与发送之前。它消除了测试复现的准备期漏检，
但不等于消除全部并发竞态：最后检查之后变化时，结果应标 stale；Provider 已接受的请求不能撤销扣费。
历史 suggested 回执保持原记录，当前读取投影若依据漂移则标 stale 并隐藏 Patch/Preview。

## 证伪与独立审查

| 攻击或反例 | 实际核验 |
| --- | --- |
| nullable 记录比较缺失 field、反向 is_null、共享 Agent 消费分支专属值叠加 | 不只修正一个表面错误；分阶段检查 blocked/failed，完整语义修正后才通过全部证明 |
| 换为质检/事件字段和不同谓词 | 固定离线跨域夹具可编译；这不是模型现场生成的泛化证据 |
| 配置改变导致输出 Shape 变化、Aggregator 输入换序 | 类型由 Adapter/资源重算，Patch 回放与降低目标一致 |
| 已保留无效图有重复输入边 | 先得到红测，再按完整边键断开一次；小规模排列及重复输入组合通过 |
| 50 输入中尾部换序 | 重用前缀，仅产生必要两项操作，不全量拆接；真正超 64 项仍阻断 |
| Recipe/Intent checksum、Schema、任务、原生字段或权限注入 | 拒绝，不调用第二次修复，不自动应用 |
| 派发前 Proposal revision 或 Snapshot 变化 | 真实 Transport 接口配合模拟请求的离线反例先失败；修正后记录未派发，拒绝发送 |
| Managed 异步客户端进入时改变修复依据 | 单独红测复现；修正后不进入模拟 HTTP 派发 |
| 响应后或读取建议前依据漂移 | 建议变 stale，隐藏可载入 Patch；不覆盖另一窗口修改 |
| 新生成修复仍返回旧 Patch、或夹带 value_schema | 明确作为协议降级/Schema 注入攻击保留，不靠兼容放行 |

独立 Sol 审查使用只读源码/测试快照，未接触 Runtime、日志、凭据或浏览器。主智能体复现审查发现，
随后实施修正并整合回归。既有产品 Planner 仍为单模型；开发子任务审查不构成多 Agent 产品能力。

## 验证记录

所有后端测试通过既有 `.tmp-recovery-e/run_offline.py` 清空 Provider 环境、禁用 dotenv、
使用隔离 Runtime 并拒绝非回环网络。模拟 completion/HTTP 不是真实 Provider 或费用证据。

| 检查 | 结果与解释 |
| --- | --- |
| S1 专项 | 60 passed |
| S2 协议及兼容 | 108 passed |
| S3、Transport 与 Managed 专项 | 82 passed；包含独立审查红测修复后的复核 |
| 首次受影响 69 文件回归 | 1420 passed / 5 failed；均为旧新生成修复夹具仍返回 Patch，与本次明确切换后的协议冲突 |
| 修复夹具后的两个完整文件 | 63 passed；迁移成功样例为 Recipe，新增两条旧 Patch 降级拒绝，保留 Schema 注入拒绝 |
| 最终同一受影响集合 | 69 文件、1427 passed / 0 failed / 0 errors / 0 skipped，534.86 秒；4 项既有 FastAPI 生命周期弃用警告 |
| 管理端四文件 | 最后一次直接 Vitest 复核 50 passed，12.24 秒，包含失败恢复、显式修复、候选及 Headless 转换 |
| 前端生产构建 | 通过；保留既有大 chunk 警告，不混入构建优化 |
| 最终语法 | 88 个 Meta Planner 源码及测试文件 AST 解析通过，不生成 bytecode |
| 最终 Diff、敏感信息及产物检查 | `git diff --check` 通过；本批 27 文件已知凭据签名 0 命中；177 个修改/未跟踪路径未出现 SQLite、Runtime、上传、构建或凭据文件，暂存为空 |

这些集合相互重叠，不能相加。首次回归的五项失败和中途新增测试夹具错误均保留为历史，
不能被最终绿测改写为“一次通过”。兼容字段 `recompile_executed` 表示进入共享重校验函数，
只有 `phase_results.compile` 才说明是否到达 Native 编译；解析失败的负例明确断言编译仍 blocked。

最终 JUnit：`.tmp-recovery-e/semantic-closure-final.xml`（忽略的本地验证产物，不提交）。
SHA-256：`af06769be2ca87bae167696b43e326594252db84c6a8c2761b06bc4924b0a6fb`。
JUnit 元数据独立核验为 1427 tests、0 failures、0 errors、0 skipped；与终端汇总一致。

补跑前端时曾使用 `npm.cmd run test:run -- <四个测试文件>`，但该仓库脚本包含 `&& node --test`，
文件参数被追加到后半段，导致 Vitest 开始全量。观察到 `ModelCard.test.ts` 和 `tokenPricing.test.ts`
中的两项 UTC 价格测试失败后立即中止，无完整汇总；名称与既有基线失败一致，本次未重新归因。
这是中止的错误选择集，不算一次完整全量验收。随后以下正确限定命令完整通过；未修改价格源码或断言。

最终受影响命令（PowerShell，工作树根目录）：

```powershell
$cases = @(rg --files server/tests | Where-Object { $_ -match '[\\/]test_meta_planner[^\\/]*\.py$' } | Sort-Object) + @(
  'server/tests/test_meta_agent.py', 'server/tests/test_workflow_node_contracts.py',
  'server/tests/test_xpert_runtime_authoring.py', 'server/tests/test_xpert_publish.py',
  'server/tests/test_xpert_evaluations.py', 'server/tests/test_xpert_structure_evolutions.py',
  'server/tests/test_xpert_app_api.py', 'server/tests/test_workflow_typed_values.py',
  'server/tests/test_workflow_typed_ai.py')
& 'C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe' -u -B .tmp-recovery-e/run_offline.py @cases --junitxml=.tmp-recovery-e/semantic-closure-final.xml
```

管理端限定命令（`client` 目录）：

```powershell
.\node_modules\.bin\vitest.cmd run --configLoader runner --maxWorkers=1 --fileParallelism=false src/components/meta/FailedDraftRepair.test.tsx src/components/meta/ModelDraftRepair.test.tsx src/components/meta/MetaPlannerV2.test.tsx src/components/meta/metaAuthoring.test.ts
npm.cmd run build
```

凭据签名检查只覆盖本批文件中已知 OpenRouter、GitHub token 及私钥头模式，不等同于完整 DLP 审计。
无新生产依赖、数据库迁移或新 Store；新增源码、测试和审计文档保留未提交。

## 未完成门禁及回退

1. 本次未运行整个 `server/tests/`，未完成前端全量或 hosted CI；没有 fetch/集成后续上游。
   历史 E2 全量失败和纯上游归因见既有集成/Linux报告，不计为本次修复，也不自动豁免。
2. 尚无同一源码版本的真实预览器交互、帮助教程重放、真实 Planner 成功率或隔离写入效果验收。
   当前预览仍是旧进程，不能直接点击后将结果归因到本次修改。
3. 后续真实测试须分别绑定源码版本、外发安全内容、模型、调用上限及是否执行写入。
   不沿用已消耗额度，不把“额度恢复”当新调用授权，也不擅自批准提案。
4. 此次结论只到“已实现受限系统性修正并核验离线边界”，不宣布整个 CW10 稳定、E 完成或允许 PR。

回退先停用新生成与显式模型修复，恢复先前协议选择/投影接入；保留 Intent、Proposal、配对产物
和回执可读。新 `source_recipe` 不占用旧 `recipe` 判别字段。不删除数据，不自动撤销历史业务写入，
不通过关闭类型、路径、资源或授权校验获得通过。

## 随后授权的真实预览器复测：失败

用户随后明确授权一次复测。仅沿用 E-G1 的零记录合成表、安全 Schema 元数据、测试目标、
OpenRouter / `deepseek/deepseek-v4-flash-0731`，一次生成、最多三次 completion。
不执行候选、不写活表、不调用下游 Agent、不批准或发布，不使用额外显式模型修复。

### 固定环境和实际调用

- 保留前端 `15489`，只重启本任务后端 `16489` 以载入 S1-S3 源码；新服务 PID 为 `12316`。
  共享栈、其他预览器及历史 Runtime 不变；已有连接只由后端用于本次已授权调用。
- 新的独立调用账本位于忽略目录 `retest-semantic-closure`，没有重置历史账本。
  请求 checksum 与上一固定目标相同：`b3abd3e055ba5146b69fb236b53ec08d2dcea9c71c435619e638fdc8e5fe3f58`。
- 通过真实预览器的“生成候选智能体”按钮派发一次。未点击再次生成或模型修复。
- 请求开始时间：`2026-09-23T10:45:58.904995Z`。以下 usage 为 Provider 响应值，不是审计账单。

| completion | 状态 | finish_reason | prompt / completion / total tokens |
| --- | --- | --- | --- |
| 1，任务规划 | HTTP 200，完整返回 | stop | 3080 / 499 / 3579 |
| 2，生成 Recipe | HTTP 200，完整返回 | stop | 10763 / 1558 / 12321 |
| 3，唯一修复 | 未派发；构造提示时本地异常 | 不适用 | 不适用 |

合计两次请求，Provider 报告 15900 tokens；没有结果不确定的派发。单次生成已尝试，未自动消费
剩余第三次额度或另开实例。传输账本中的 `halted=false` 只表示没有传输阻断，不代表应用成功。

### 已确认的本地根因和影响

1. `_recipe_repair_prompt` 从真实类型校验器收到 `GraphInputTypeIssue` 对象列表，直接放进
   `semantic_feedback.input_type_issues`。末尾 `json.dumps` 因对象不可序列化而抛出
   `TypeError: Object of type GraphInputTypeIssue is not JSON serializable`。
   调用栈定位在 `meta_planner_v2.py` 的提示构造及 `generate` 调用处，不是 Provider 返回失败。
2. 提示构造发生在修复派发之前，异常未进入失败候选保存流程。管理 API 核对只有三个历史 Proposal，
   最新仍是 `proposal_7ae72802a2474d6d810590dc88fbe345`，创建于 `08:44:50Z`、revision 1；
   没有本次 `10:45:58Z` 之后的新 Proposal。页面仍保留上一轮候选，不可误认为此次生成产物。
3. 源码已有 `GraphInputTypeIssue.repair_detail()` 的受限安全投影，本次 Recipe 修复没有复用它。
   此缺陷与异常留存边界必须一起核验；不能靠 `default=str`、跳过类型错误或增加模型调用掩盖。
4. 未保存本次完整 Recipe，因此不能声称已还原其全部语义错误，也不能证明修复后必定编译成功。
   当前证据不足以归因于模型能力上限、截断、RAG 或业务写入执行。

### 离线证伪与安全复核

仅新增忽略目录内的诊断测试，不修改生产源码。测试由现有 `guarded_recipe` 合成夹具出发，
让对象结果直接连接 Agent 文本任务端口，由真实类型校验生成非空诊断，而不是 Mock 一个异常。

```powershell
& 'C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe' -B .tmp-recovery-e/run_offline.py .tmp-recovery-e/test_semantic_repair_serialization_repro.py --junitxml=.tmp-recovery-e/semantic-repair-serialization-repro.xml
```

- 实际结果：**1 passed / 1 failed**，10.13 秒，4 项既有 FastAPI warning。
  无类型问题的提示对照通过；包含真实类型问题的提示在相同 `json.dumps` 调用处失败。
  这是确认缺陷的红测，不是修复通过或新的全量回归。
- 实测后表 `table_972e625145604c2f9419176b128d863b` 仍为 0 条记录、published、Schema 1；
  Schema checksum 仍为 `86cc158d8d90861da3b88a29cad4d38a672110c30f35a83910deb981508249cf`。
- 生产文件和前端构建 hash 与派发前一致：Planner
  `4c5c9218429106a24c705f468a4260eddd43551d5bc81a5d9c6633f73f25e790`；Recipe
  `0a2aa1af167780b84ed60b87ad656488d64804aa1d6af3fc10014641a4835129`；Control Flow
  `7ffa7e139c0496f71feebcb751d4af15027ffbff3371dae81e9fad80e038dff8`；Recipe Repair
  `e0cbc3e4e5f778454ca549e2ea731bff847f9be1219885af28340ecb2aae8e9e`；前端 index
  `ecfc60a890a83c8ce152dfbd7f543ac7ea84cc78b74b918a9a672b5f78ba34f0`。
- 旧 `retest-resource-schema/calls.json` hash 未变：
  `51acb86860646e7d061832bdce78697af8559e880af0516af8e2eb2b62932e9d`。
  新账本 hash：`ef5948bfd6bfb83fa8c159acf6b50a91d5b761cee276e7a47feae66fdb4d5573`。
  红测 JUnit hash：`901bf510685ca4b8e15919919e8328b846ecdb0bddce1fadb02f38e0b52ea01d`。

本次停止在取证。下一修复应限定为“诊断安全投影与修复准备失败的可恢复留存”这一闭环，
补充非空类型诊断、前置异常、未派发计量和失败候选读取的真实链路测试；这是建议，尚未实施。
此前 1427 项离线通过不覆盖这个已证实反例，不得作为 E3 或提交门禁通过证据。
没有业务写入、自动批准、发布、Commit、Push 或 PR。

## 后续本地修复：准备、派发与留存边界

用户随后授权“开始下一步收口”。本批只修改两个生产文件，一个专项测试文件及两份证据文档；
没有追加 Provider 调用、业务写入、自动批准或提交授权。沿用同一工作树及基线，不重启预览服务。

### 实现与未扩大边界

- `meta_planner_v2.py` 复用 `GraphInputTypeIssue.repair_detail()`；不使用任意对象字符串化、
  `default=str` 或完整 dataclass 转储。类型反馈最多 64 条，另列实际总数和遗漏数，
  不把省略伪装成没有其他错误，也不复制字段 properties、默认值或业务记录。
- Recipe 和兼容 Graph Patch 的修复提示都先在独立准备边界完成，再调用 completion。
  准备失败记录固定中文说明及 `repair_preparation`，其中 `completion_started=false`；
  `repair_used=false`、`repair_protocol=none`，实际 completion 和产物尝试仍只有原来的数量。
- 准备错误通过原有 Proposal 流程保存首次失败的安全 Recipe/Intent，不制造第二次响应或新图。
  原始解析不成功时仍显示不可恢复，不能从 fallback 占位图反推原图。Headless、审批、资源授权
  和类型校验不变；恢复读取不代表允许执行，审批仍拒绝不合法候选。
- `generation_diagnostics.py` 仅增加 `repair_preparation` 阶段与固定错误码白名单。
  原始异常文本只进入诊断指纹，不进入报告正文。`CancelledError` 继续向上传递。
  已经进入 completion 后的异常不在此 catch 范围内，不改记为未派发、不自动重试。
- 本批没有接管 Provider 中断、跨 Store 故障或全部失败恢复分支；也没有修改 Runtime、Store、
  公共 IR/SSE、22 类能力、三次预算、模型或业务 Prompt 策略。

### 专项证伪记录

新增 `server/tests/test_meta_planner_repair_preparation.py`：真实类型校验的两种领域反例、
非空诊断驱动第三次模拟修复、准备异常的可恢复留存、原始文本未解析、Recipe 降低失败、
旧 Patch 准备失败、64 条反馈上限、无效提示返回、取消和派发后异常。

1. 首次红测 7 failed / 1 passed，其中两项断言误把独立类型诊断收集等同于返回错误列表，
   另一个派发后反例未重新序列化修改后的夹具；未把这些测试问题归因为产品缺陷。
2. 核对现有接口后重新运行同一 8 项：6 failed / 2 passed。六项均命中真实序列化或准备异常；
   取消和派发后异常两项保持原行为，构成不能被修复扩大 catch 的对照。
3. 实现后首次 6 passed / 2 failed：两项预期忽略了现有 Pydantic 默认字段规范化。
   改为与严格解析后的 Recipe 比较完整内容及 checksum，不改写实际输入、降低安全断言或修改生产逻辑。
4. 补充上限、Recipe-only、兼容入口与无效返回后：**14 passed**，24.53 秒，4 项既有 FastAPI warning。
   模拟 completion 不是付费 Provider 实测；这些结果不能宣称真实模型成功率改善。

```powershell
& 'C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe' -u -B .tmp-recovery-e/run_offline.py server/tests/test_meta_planner_repair_preparation.py --junitxml=.tmp-recovery-e/repair-preparation-focused.xml
```

此前真实复测失败和第三次未派发的账本保持原值；新代码不能恢复那次已丢失的候选，
不会偷偷复用剩余预算再发一次。

### 扩展核验

- 新专项的 14 项是同一受影响后端回归的一部分，不另加到总数。
- 前端四文件复跑 50 passed，11.40 秒；命令与上文限定 Vitest 命令相同。
- `npm.cmd run build` 通过 TypeScript 和 Vite 构建；Vite 15.23 秒，保留既有大 chunk warning。
  本批未修改前端源码，没有重启后端预览，不能将当前浏览器视为新后端实测。
- 三个本批 Python 文件 AST 解析通过，不导入应用、不生成 bytecode。
- Sol 在独立七文件只读源码快照中检查派发边界与产物留存，未发现可证实问题；没有运行测试，
  没有浏览器、Runtime 或凭据访问。快照未含完整审批实现和 `repair_detail()` 定义，
  这两处由主智能体源码核对及上述生产路径测试验证，不把独立静态审查说成独立实测。
- `git diff --check` 通过；五文件的已知 OpenRouter/GitHub token 和私钥头扫描 0 命中，
  不是任意秘密或数据泄露形式的完整审计。

最终受影响后端回归：**70 文件、1441 passed / 0 failed / 0 errors / 0 skipped**，552.69 秒，
4 项既有 FastAPI warning。使用上文相同的 `test_meta_planner*.py` 加九个领域文件选择规则，
本次新增专项文件自动纳入；JUnit 为 `.tmp-recovery-e/repair-preparation-regression.xml`。
逐项元数据核对与终端汇总一致，不能把 14 项专项和 1441 项重复相加。

| 冻结对象 | SHA-256 |
| --- | --- |
| 专项 JUnit | `85ed33b4e6f39787b13cba4397e84cfef28139e83f6fb3cd2225c5e24f56a2f1` |
| 受影响回归 JUnit | `3191f7e49260a58e58f1922957f75a785dec5f222691b10f13904e96b46ad44e` |
| `meta_planner_v2.py` | `ddd37139c5eede8920fa3af69052ddf59aa18f2fab2437a39fb11925a48fcbec` |
| `generation_diagnostics.py` | `43855cccdc84703e9858b787662ade43ed94d181fa0906ab778dd2d3629cace4` |
| 新专项测试 | `828ab3a424fb76dfde715e19bb11380cfadea33b919819ccabd4cc639e33533f` |

本批结束时 178 个修改/未跟踪路径，新增一份专项测试；暂存为空。未出现 SQLite、Runtime Store、
上传、构建或凭据文件进入待提交清单。两份历史真实调用账本 hash 与本批开始一致，
前端构建 index hash 也与此前相同；测试进程和构建进程均已结束。

没有重新运行整个 `server/tests/`、前端全量、帮助教程重放或 hosted CI，没有刷新或集成新上游。
结论仅为本批本地修复与受影响回归通过；E2/E3 和 PR 总门禁仍未通过。新的真实复测需要单独授权，
且必须先让独立预览器载入本次后端源码，不得对当前旧进程直接重试并归因于新修复。

## 修复准备边界后的授权真实复测

用户随后单独授权复测。本次仅重启本任务独立预览后端，保留前端与 Runtime 数据；
在 `127.0.0.1:15489/agents/meta-agent` 通过真实页面点击一次生成。
源码仍为上一节冻结的 Planner/Diagnostics hash，没有继续修改生产逻辑。

### 精确范围与调用回执

- OpenRouter / `deepseek/deepseek-v4-flash-0731`，一次生成、最多三次 completion。
- 仅发送 E-G1 零记录合成库存表的已批准安全元数据、目标和受限契约。
  查询范围为 `table_972e625145604c2f9419176b128d863b`，写入候选授权为
  `update / status / max_affected_rows=1`；不执行写节点或下游 Agent。
- 新回执独立保存在忽略目录 `retest-repair-preparation`；两份历史账本没有重置或覆盖。
  本轮没有调用显式模型修复入口，没有重新生成第二个实例。

| 调用 | 用途 | 开始时间（UTC-07:00，2026-09-23） | 结束时间 | 结果 | Provider tokens |
| --- | --- | --- | --- | --- | --- |
| 1 | 任务规划 | 05:32:29.554641 | 05:32:48.841024 | HTTP 200 / stop / completed | 3587 |
| 2 | 首次 Recipe 生成 | 05:32:49.496459 | 05:34:14.835258 | HTTP 200 / stop / completed | 12526 |
| 3 | 唯一定向 Recipe 修复 | 05:34:15.902495 | 05:35:41.771607 | HTTP 200 / stop / completed | 14448 |

三次预算已用完，总计 Provider 报告 **30561 tokens**（输入 26377，输出 4184），
没有结果不确定的派发。这是响应 usage，不是已审计账单或金额。
服务内请求契约均与意图契约一致；Provider 到 collector、collector 到 validator 的
受限结构观察均为 `same_structure`。这只能排除已观察结构范围内的漂移，不能证明完整语义正确。

### 结果与根因层级

新 Proposal：`proposal_655bec0a57614dc39b59294a5ee70b1f`，revision 1，`pending`，
`validation.valid=false`、`applied_resource_id=null`、`repair_used=true`。

1. 首次生成和修复响应都通过结构解析，但同样停在 `recipe_lowering`，诊断均为
   `CONTRACT_CHECK_FAILED`，指纹同为
   `efcbe209e6b0ec1d048f8a6a0de273d20c7e60872f54446305936011fdf6e1e9`。
   当前原图复核的明确错误是：`route_record` 必须且只能显式声明
   `case_1 / case_2 / default` 三个语义分支。
2. 修复后的真实保留描述中，`route_record` 被写为 `branches=[]` 的普通节点，
   随后用 `parallel.paths` 列出停止和继续路径；`route_stock` 也采用同类结构。
   并行结构不能替代互斥路由的 outcome 映射。本次没有猜测映射或放松校验。
3. 授权、资源与类型解析、编译和发布预检均被前置展开失败阻断，不能记录为已经通过。
   当前可见配置还包含无字段的对象数值比较等待后续核对项，但本轮没有执行隔离反例来
   完整证明这些项；不将它们混入已确认的直接阻断原因。
4. 页面附带的 `missing_workflow_agent_model` 报错属于
   `candidate_origin=server_synthesized_fallback` 诊断占位图，不是原始 Recipe 的新模型配置根因。
   占位图展示的零路由/单成功路径也不是本次生成图的控制流证明。
5. 上一轮“修复准备异常导致第三次未派发”的现象没有出现：第三次真实请求已完成，
   新失败产物成功留存。但本次更早停在 Recipe 展开，未覆盖上次非空
   `GraphInputTypeIssue` 的同一真实路径；该序列化边界的直接证据仍是上一节离线专项。

Headless 只读状态复核返回 `mode=recovery`、`source_format=recipe_v1`、
`status=editable`、`can_edit=true`、`can_approve=false`、`executable=false`。
浏览器显示“失败生成描述修复”、两次诊断和原始节点，并禁用批准。
未修改控制 JSON、未点击预览/应用、未验证人工修复成功或另行模型修复成功。

### 安全复核与门禁

- 表仍是 published、Schema 1、**0 条记录**，Schema checksum 不变：
  `86cc158d8d90861da3b88a29cad4d38a672110c30f35a83910deb981508249cf`。
- 新回执 hash：`5b8440b810c4250f353fda0ebca19e97b3f2f6c33240fe0c3791cf9f0bc7a82e`。
  两份历史回执 hash 与上一节一致；Planner/Diagnostics hash 也未变。
- 本次只修改忽略的预览预算目录配置、新增精确授权文件并同步两份证据文档。
  没有新的测试或构建结果，不重复计入 1441 项离线结果。
- 未执行业务写入、隔离写入评测、下游 Agent、Proposal 应用/批准、发布、共享栈操作、
  Commit、Push 或 PR。没有重新运行全量测试或集成最新上游。

**结论：本次生成仍失败，E3 及 PR 总门禁仍未通过。** 已验证准备/派发和失败留存链路可达，
未验证成功生成、分支泛化或写入效果。下一步应离线核对 Recipe 路由描述、语义反馈及
唯一修复之间的闭环，用同一类路由/并行混淆反例验证，而不是修补占位图 modelId 或放宽门禁。
上述是后续建议，本轮没有实施，也没有追加付费请求。
