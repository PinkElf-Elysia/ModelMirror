# Planner 输入契约收口

日期：2026-09-19。状态：本批重点回归通过，全量入口失败；未进行新的真实模型复测，未通过 PR 门禁。

## 范围与证据

- 工作树：`C:\tmp\modelmirror-meta-planner-controlled-writes-10-closeout`；沿用 `codex/meta-planner-controlled-writes-10-closeout`，不改脏主工作区。
- 最近真实复测为 `proposal_6087ff47a9e9426687261ec5d0b1d838`。十项 Patch 没有解除 `serialize_evidence` 的双输入；原子校验正确拒绝。完整模型图未保留，不声称逐字重放。
- 离线核对发现 JSON 端口的 required 元数据与 Adapter 不一致；七类辅助节点省略 inputs 时，生成 Schema 接受而 Adapter 拒绝。纯节点没有进入数据表专用的输入数量诊断。
- 本批只修复契约投影和无副作用诊断，不改变 Runtime、业务记录权限、原子 Patch 接受规则或三次模型调用上限。不调用 Provider、不重启预览或共享栈、不提交或发布。

## 两个可验证批次

1. 契约：NodeContract JSON 必需输入、Adapter 输入数量投影、生成 Schema 必填属性，以及针对遗漏/空/重复输入的测试。
2. Harness：修复清单与安全诊断复用相同端口事实，覆盖全部独立输入错误；测试配置空操作、部分修复及保留两份业务证据的完整 Patch。

每批控制在五个主要文件内；跨批文件共同完成同一个输入契约闭环。既有逐节点 Validator 和 Runner 不重写，不根据节点名写特例，不自动选择、删除或重接数据来源。

## 验证与回退

- 先运行新反例确认红测，再运行同组、Meta Planner/NodeContract/Headless/Runtime 相邻回归、后端语法、前端构建和全量后端测试。
- 使用既有 `run_offline_v2.py`，隔离 Runtime 路径、清除凭据环境并阻止外网。测试中的人工构造图和模拟 Provider 不是实际模型成功率证据。
- 本批回退只恢复输入元数据和提示/诊断投影，不回退其他 CW10 修改，不改变已有业务数据。旧 JSON Runtime 行为通过回归保护。
- 真实分支泛化、三路径效果、最新全量、人工验收及提交授权仍是独立门禁。

## 结果

### 已实施

- NodeContract 将 JSON Serialize 的 `value` 和 Deserialize 的 `json` 输入明确为必需。生成 JSON Schema 同时要求必需的 `inputs` 属性，拒绝省略、空输入及单值端口重复输入；不改变旧 JSON Runtime 行为。
- Adapter 从 NodeContract 和已存在的配置绑定规则投影 `minimum/maximum/actual/input_indices`。聚合器仍按 `output_fields` 数量计算，Agent 仍允许多个 `task` 输入，literal Insert 仍可没有输入。
- 模型修复清单和持久化安全诊断复用上述计数事实。纯节点不再只有实际输入数量而没有期望数量。动态资源事实仍须先通过既有授权解析；报告不回显未知端口、业务值或 Prompt。
- 修复清单只在输入形状不合法时展开数量细节，删除重复定位信息，保留完整图、授权、Schema 和全部已有错误。明确配置更新不会修改数据边，也不会自动删除、猜测或重接来源。
- 新增 36 项测试覆盖契约一致性和多错误组合。人工构造的两处独立错误必须在同一原子 Patch 中显式修正；仅改配置、空 Patch、部分修复仍失败。完整修复保留查询与写入回执两份证据，经授权和编译/反编译往返通过。

本批正式范围共八个文件，分两批实现：

| 文件 | 职责 |
| --- | --- |
| `server/workflow_native/node_contracts.py` | 两个 JSON 输入的 required 元数据 |
| `server/meta_agent/node_adapters.py` | 共用输入数量投影和只读状态观察 |
| `server/meta_agent/generation_contract.py` | 模型 JSON Schema 的必需输入约束 |
| `server/meta_agent/generation_diagnostics.py` | 全节点安全计数诊断 |
| `server/meta_agent/repair_context.py` | 有界、授权内的修复关联清单 |
| `server/tests/test_meta_planner_input_cardinality.py` | 新增 30 项契约及安全反例 |
| `server/tests/test_meta_planner_repair_context.py` | 新增 6 项多错误组合测试，保留既有语义和体积门禁 |
| 本文 | 范围、证据与未完成门禁 |

### 实际验证

所有后端测试使用既有隔离启动器 `.tmp-cw10-closeout/run_offline_v2.py`，Python 为 `C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe`。JUnit 与构建产物保留在忽略目录 `.tmp-cw10-port-contract-20260919/`，不纳入提交。

| 检查 | 结果 |
| --- | --- |
| 修改前输入契约反例 | 17 失败、11 通过；其中 9 项证明现有 Schema 接受遗漏/空输入，另外 8 项是待实现投影接口的预期红测 |
| 修改前纯节点诊断反例 | 2 失败、16 通过；缺失期望数量清单和纯节点数量诊断 |
| 最终定向回归 | 149 通过，57.89 秒；`focused-final.xml` |
| Meta Planner 及相邻回归 | 1085 通过，210.21 秒；`adjacent-final.xml` |
| 受控写入与隔离效果回归 | 192 通过，32.78 秒；`write-effects-final.xml` |
| 全量入口，首个失败停止 | 94 通过、1 失败，67.90 秒；后续未运行，`full-final.xml` |
| 七个本批 Python 文件 `py_compile` | 通过，pycache 写入本批临时目录 |
| 前端 `npm.cmd run build -- --outDir ../.tmp-cw10-port-contract-20260919/client-build` | 通过；保留既有大分块警告和 outDir 提示，没有覆盖预览器的 `client/dist` |
| `git diff --check` 与八文件空白/常见凭据形态扫描 | 通过；这不是完整安全审计 |

相邻回归与隔离效果回归经 JUnit case ID 去重，共 **1277 项通过**；149 项定向测试包含在其中，不重复相加。第一轮新 Harness 回归曾触发提示体积检查失败，已通过删除重复信息修正，没有放宽断言；最终同组测试通过。首次沙箱内测试尝试无测试结果，已中止，再于获准环境以同一隔离命令运行；无输出原因未证实，不把该次尝试计入测试证据。

核心命令（工作目录为本工作树，前端命令在 `client`）：

```powershell
$python = 'C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe'
$runner = '.tmp-cw10-closeout/run_offline_v2.py'
& $python -B $runner server/tests/test_meta_planner_input_cardinality.py server/tests/test_meta_planner_repair_context.py server/tests/test_meta_planner_generation_diagnostics.py server/tests/test_meta_planner_patch_change_evidence.py server/tests/test_meta_planner_generation_contract.py server/tests/test_meta_planner_prompt_projection.py server/tests/test_meta_planner_input_contract_alignment.py --junitxml=.tmp-cw10-port-contract-20260919/focused-final.xml
$tests = @(Get-ChildItem -LiteralPath server/tests -Filter 'test_meta_planner*.py' | Sort-Object Name | ForEach-Object { 'server/tests/' + $_.Name }) + @('server/tests/test_meta_agent.py', 'server/tests/test_workflow_node_contracts.py', 'server/tests/test_xpert_runtime_authoring.py', 'server/tests/test_xpert_publish.py', 'server/tests/test_xpert_evaluations.py', 'server/tests/test_xpert_structure_evolutions.py', 'server/tests/test_xpert_app_api.py', 'server/tests/test_workflow_typed_values.py', 'server/tests/test_workflow_typed_ai.py')
& $python -B $runner @tests --junitxml=.tmp-cw10-port-contract-20260919/adjacent-final.xml
$writes = @(Get-ChildItem -LiteralPath server/tests -Filter 'test_*.py' | Where-Object { $_.Name -match '^test_(agent_table|controlled_write|evaluation_write|evaluation_controlled_write|workflow_data_table_nodes|meta_agent_managed_gateway|workflow_capability_audit)' } | Sort-Object Name | ForEach-Object { 'server/tests/' + $_.Name })
& $python -B $runner @writes --junitxml=.tmp-cw10-port-contract-20260919/write-effects-final.xml
& $python -B $runner server/tests/ --maxfail=1 --junitxml=.tmp-cw10-port-contract-20260919/full-final.xml
& $python -X pycache_prefix=.tmp-cw10-port-contract-20260919/pycache -m py_compile server/meta_agent/node_adapters.py server/meta_agent/generation_contract.py server/meta_agent/generation_diagnostics.py server/meta_agent/repair_context.py server/workflow_native/node_contracts.py server/tests/test_meta_planner_input_cardinality.py server/tests/test_meta_planner_repair_context.py
```

### 门禁与归因边界

- 全量失败仍是 `test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted`：worker 断连后，取消清理中的 `stdin.drain()` 抛出 `ConnectionResetError`，遮蔽原 `EngineUnavailableError`。相关实现和测试与本地 HEAD 均无 Diff，前批已复现同一失败。本批不修改该模块，也不宣称已对执行时最新远端 main 完成独立归因。
- 这次收口消除了已证实的输入契约和诊断投影不一致，**不证明单模型能够稳定利用这些提示完成生成**。组合测试是结构化反例及可表达性验证，不是历史完整模型响应的逐字重放，也不是付费 Provider 成功率证据。
- 源码回执中 101 个文件经核对，仅上述六个既有正式文件出现本批预期变化；另新增本文和输入契约测试。最新真实调用账本 SHA-256 仍为 `4c6e8e70dfc57fea9fb363d5377d2b289b796280cac22463ca73dc01f997429e`。
- 基线仍为本地 `a7d99925584e818e7f2df84b1646256abfc08ed6` 加 CW10 未提交改动。本批没有刷新或集成远端，没有读取凭据、追加真实模型调用、操作活表、重启预览/共享栈、审批/发布、提交、推送或创建 PR。
- 下一道门禁仍是单独授权后的真实分支泛化生成及隔离三路径效果、全量失败归因与闭环、帮助中心同基线重放和最终人工验收。未经通过不进入下一轮、不以本批绿测替代提交门禁。
