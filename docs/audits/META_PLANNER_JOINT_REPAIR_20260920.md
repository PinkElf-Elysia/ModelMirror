# CW10 联合路径最小修复

## 范围与依据

依据 `META_PLANNER_JOINT_PATH_REPRODUCTION_20260920.md` 的三个已复现缺口，用户授权开始修复。
本次不再调用真实模型，不读取或写入业务记录，不重启预览器，不操作共享栈、审批或发布。

- 工作树：`C:/tmp/modelmirror-meta-planner-controlled-writes-10-closeout`。
- 分支：`codex/meta-planner-controlled-writes-10-closeout`。
- HEAD：`a7d99925584e818e7f2df84b1646256abfc08ed6`。
- 保留进入本次工作前已有的 131 项修改/未跟踪路径；只叠加下面的局部变更。
- 八个相关生产文件的修复前快照及 hash 固定于忽略目录 `.tmp-cw10-joint-repair-20260920/before/`。
- 旧复现文件、真实调用账本和报告不覆盖；真实失败图的完整原文仍未保存，不能宣称本次是逐字重放。

## 三个修复点

| 已证实缺口 | 本次处理 | 不做什么 |
| --- | --- | --- |
| 没有授权反序列化生产者，却默认向模型提供 input 写值模式 | `write_contract` 统一写值生产者事实；Graph/Patch Schema 与 Prompt 按相同可用 Adapter 集合投影，缺少生产者时只接受显式 literal | 不改变旧配置默认值，不自动提供业务值，不增加授权 |
| 已知来源端口错误被较早失败遮蔽 | 前置诊断与最终解析共用 ref/port/variable 身份检查；写值来源规则共用同一个检查；每项带目标位置和安全错误码 | 不用无效资源或模型声明猜测权威 Schema，不移除最终门禁 |
| 空值字段比较、旁路和双终点未合并成修复义务 | 投影已有符号场景中的路由类型、失败码、终点及控制根；与来源/数据依赖放入同一审查清单 | 不改场景算法、Scheduler、业务分支，不自动接边、不增加模型调用 |

生产修改局限于八个 `server/meta_agent/` 文件：`write_contract.py`、`generation_contract.py`、
`node_adapters.py`、`meta_planner_v2.py`、`graph_ir_v3.py`、`generation_diagnostics.py`、
`control_flow.py`、`repair_context.py`。没有改 NodeContract、Runner、Agent Table Backend、公共 IR 或审批协议。

## 验收设计

新增三个测试模块：

- `test_meta_planner_scoped_write_modes.py`：生成、完整修复和 Patch 共用 scope 约束；未授权/不可用生产者、遗漏模式、Patch 注入、旧默认及顺序确定性。
- `test_meta_planner_source_obligations.py`：未知来源、错误/歧义端口、变量冒用、非法写值生产者、资源拒绝后不猜类型、合法输入。
- `test_meta_planner_joint_path_repair.py`：联合反例、事实顺序与正文隔离、局部修复仍失败、四场景合法对照、唯一原子 Patch、三次调用和 pending 边界。

全部新增测试采用外网及 SQLite 业务访问拒绝守卫。生成集成测试使用 fake completion；其通过仅证明协议、诊断、校验和预算边界，不证明 DeepSeek 生成能力。

## 验证过程

- 模式投影红测：6 failed / 1 passed；修复后连同已有生成/表契约 56 passed。
- 来源义务红测：7 failed；修复后连同独立资源、诊断及修复上下文 81 passed。
- 联合路径红测：5 failed；修复后首次 86 passed / 1 failed，新说明超出既有上下文精简要求。保留事实并删除重复说明后，相同套件 87 passed；未降低断言。
- 联合生成集成首次 8 passed / 1 failed：新测试 Patch 忘记解除被删节点的入边。生产删除依赖门禁正确拒绝。仅补全合成 Patch 后 9 passed，完整/局部/空修复均保持三次 fake completion 和 pending Proposal。
- 扩大集成首轮：1156 passed / 1 failed。新增 `_ports` 必填参数影响了已有静态端口投影调用；恢复其内部兼容默认，真实生成入口继续显式传入授权集合。没有修改原测试。
- 兼容修复定向回归：70 passed。随后完整重跑相同集成套件：**1157 passed**，4 个已有 FastAPI 生命周期弃用 warning，309.10 秒，包含本次新增 23 项测试。
- 后端语法：`server/main.py`、八个改动模块和三个新测试共 12 文件，通过。
- 前端：`npm.cmd run build` 通过，保留大包体积 warning；未修改前端代码或重启服务。
- 全量后端入口使用 `server/tests/ --maxfail=1`：**94 passed / 1 failed，其余未运行**，47.00 秒。失败为 `test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted`，`port.py:541` 的 `stdin.drain()` 在 worker 断连处理期间抛出 `ConnectionResetError`。与修复前冻结记录的失败一致，两个文件相对 HEAD 无 Diff。本次不修复该无关问题，也不宣称全量通过。
- 13 个本次涉及源码、测试与文档文件 UTF-8 检查通过；密钥/私钥模式扫描零命中。模式扫描不等同于完整安全审计。
- `git diff --check` 通过，暂存区为空。工作区未跟踪内容中没有本次产生的构建、SQLite 或 Runtime 产物。
- 核验旧冻结清单 108 个文件，只有本次八个生产文件 hash 变化；旧复现文件和真实调用账本 hash 均不变。

所有 XML 位于 `.tmp-cw10-joint-repair-20260920/`，失败记录保留。各套件有重叠，不将结果相加当作独立用例总数。

最终扩大回归命令（PowerShell，工作目录为本工作树）：

```powershell
$tests = @(Get-ChildItem -LiteralPath 'server/tests' -Filter 'test_meta_planner*.py' |
  Sort-Object Name | ForEach-Object { 'server/tests/' + $_.Name })
$extra = @('test_meta_agent','test_workflow_node_contracts','test_xpert_runtime_authoring',
  'test_xpert_publish','test_xpert_evaluations','test_xpert_structure_evolutions',
  'test_xpert_app_api','test_workflow_typed_values','test_workflow_typed_ai',
  'test_agent_tables','test_workflow_data_table_nodes') |
  ForEach-Object { 'server/tests/' + $_ + '.py' }
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-cw10-closeout/run_offline_v2.py `
  @tests @extra --junitxml=.tmp-cw10-joint-repair-20260920/integration-regression-v2.xml
```

集成结果 XML SHA-256：`5fd55067194a4c0a8bbcd3d242570d70773de989222d115c404b883c0f808b67`。
历史真实调用账本仍为 `4ee134fe0578ee157c8827e6628bfa3afd2b41dc3051c8130c21b940ff5a2cc8`；没有新增 completion。

## 兼容、回退与剩余门禁

不增加第 23 类能力，不修改 V2 写入执行、不自动串行化、不运行真实业务节点。旧 Workflow/Proposal 和 public Schema 不迁移。

若需回退，只反向撤销本次冻结快照对应的局部 Diff，不回滚整条脏分支，不覆盖之前的受控写入工作。测试数据只在隔离临时目录中。

修复后的真实 Provider 复测尚未执行；历史额度授权不视为本次新调用权限。真实成功率、泛化稳定性、实际写入验收与 PR 门禁仍应单独确认，不能凭本次离线通过宣称本轮稳定或可提交。

本次无 Commit、Push、PR、Merge、批准或发布。下一次真实验收应继续使用冻结目标与授权边界，另记源码、模型、请求上限及结果，不覆盖本次离线证据。
