# R8-B B2：子调用预算与恢复边界

本卡保留 B2 当批证据；后续主线快进、Provider 逐项审计、进程崩溃和全量验证以 `META_PLANNER_R8B_B2_CLOSEOUT.md` 为后续记录，不把下文历史未运行状态误作最新结论。

## 开工契约

- 基线 `ec79e1203c0f26e9efa20e4e75e7e0bf9de2a795`，工作树 `C:\tmp\modelmirror-meta-planner-r8-b-readonly-subflows`，分支 `codex/meta-planner-r8-b-readonly-subflows`。
- 保留 B0/B1 七项未提交文件；本批只修改 `server/main.py`、`server/workflow_deployments.py`，新增专项测试和本任务卡。部署 Store 与 B1 同文件，保留 B1 变更。
- 高风险运行路径：复用现有预算委托与持久 Store，不增加依赖、路由或 Planner 能力；不调用外部模型、业务写入、提交或部署。
- 原样重跑 B0：4 failed / 7 passed，15.02s。根因：子执行缺少预算父链、单调用未固定输入、未结算子执行被重新启动。

## 修复与验收范围

1. 固定单次及批内子调用的请求摘要，包含目标定义、输入及父子身份；旧快照只有在可核对原输入/定义时允许结果复用。
2. 派发前登记预算委托，Runner 创建本地预算并绑定持久父链；空 ContextVars 不重置根预算，既有租约所有权仍须有效。
3. 完成结果复用；已有未结算持久执行拒绝重发，不把 lease 到期当作未派发证据。
4. 覆盖首次派发前、结果返回前的取消/所有权变化，维持单实例限制。

离线测试命令：`python -B -m pytest server/tests/test_workflow_r8b_entry_evidence.py server/tests/test_workflow_r8b_call_recovery.py server/tests/test_workflow_subworkflows.py server/tests/test_workflow_iteration_v2.py server/tests/test_workflow_r8a_inline_budget.py -q -ra --tb=short -p no:cacheprovider`。

先复现再跑专项与预算/部署回归；全量、上游集成和预览验收不以窄测代替。回退保留可选请求摘要读取能力；不清除账本，不自动重发未知请求。无破坏性数据迁移。

## 已实现边界

- `WorkflowSubworkflowRelation.call_contract_checksum` 为可选新增字段，兼容旧快照读取；新调用同时固定单次输入和批内实际输入，校验根身份、深度和目标，重复调用不能借批摘要隐藏项输入变化。
- 有持久预算的父执行在派发前登记 `subworkflow:<execution_id>:<checksum>`，普通 workflow 子执行复用现有预算构造与父链绑定；不创建第二套预算账本。旧无预算 classic 调用没有被自动授权为 R8-B 合格目标。
- Store 重载后父任务为 `ready`：允许只读核对既有完成结果，不允许登记新项；继续执行必须重新认领。测试另外证明认领后仍命中真实根预算耗尽，不能靠“尚未认领”伪装预算验证。
- 调用桥与父调用入口均拒绝重新启动已有未结算 durable 子执行。未派发且没有 durable 子记录的原发生可在有效父所有权下继续；已完成 durable 结果在部署 checkpoint 缺失时可协调复用。
- 控制调用拒绝或失败不代表未知外部请求已失败；没有自动重试、换 ID 重发或退款承诺。已有 durable 记录但没有充分完成证据时保守阻断，未新增逐节点自动恢复能力。
- 子调用派发与结果返回检查父预算所有权及子部署租约；迟到结果不能推进已取消或已被接管的父任务。

## 验证记录

环境为缓存镜像 `modelmirror-r9a1-linux-validation:4932e3b4` / Python 3.12；容器 `--network none --read-only --cap-drop ALL --security-opt no-new-privileges`，源只读挂载，复制到匿名临时卷后测试，不加载 `.env` 或持久业务目录。

| 检查 | 状态 | 结果 |
| --- | --- | --- |
| 修改前原样 B0 | 失败 | 4 failed / 7 passed，15.02s；保留原断言 |
| 首次修复后 B0 与旧调用/迭代 | 失败 | 4 failed / 30 passed，19.57s；定位到重载父任务 `ready` 与完成结果只读复用的区别，保留认领约束后修正调用入口 |
| B0/B2 与旧路径初次扩大 | 失败 | 7 failed / 75 passed，28.62s；六项为新增测试遗漏 Store 所有权异常类型，一项故障注入修改了读取副本；按真实 Store 行为修正夹具，不修改 B0 或放宽生产所有权 |
| 全部 R8 专项与部署/契约回归 | 通过 | 1536 passed，149.26s |
| 增补并发攻击与执行预算回归 | 通过 | 1554 passed，142.72s；4 条既有 FastAPI `on_event` 警告 |
| 最终强化断言后 B0/B2 复跑 | 通过 | 35 passed，17.09s；要求明确命中 model-call budget，且取消/接管钩子确实执行、父状态与租约确实改变；生产代码未再修改 |
| UTF-8 / Python 3.12 AST | 通过 | 本批三个 Python 文件通过 |
| 已跟踪 Diff / 新测试空白 / 有限敏感模式 | 通过 | 无空白诊断；新增文件未命中有限密钥或 skip/xfail 模式，不是完整无泄漏证明 |
| Provider 全仓源码审计 | 失败 | 947 个候选；存在基线与本批两类漂移，详见下节；未刷新清单放行 |
| 全量后端、前端构建、进程级崩溃、独立预览及真实模型 | 未运行 | 本批不以离线合成证据代替后续全轮门禁 |

最终扩大套件的可复现选择：

```text
python -B -m pytest server/tests/test_workflow_r8*.py server/tests/test_workflow_subworkflows.py server/tests/test_workflow_iteration_v2.py server/tests/test_workflow_deployments.py server/tests/test_workflow_deployment_api.py server/tests/test_workflow_node_contracts.py server/tests/test_xpert_runtime_workflow_node_registry.py server/tests/test_xpert_execution_budget.py -q -ra --tb=short -p no:cacheprovider
```

实际容器通过 Python `Path.glob('test_workflow_r8*.py')` 排序展开列表，避免依赖宿主 PowerShell 的通配行为。

## 审计与未关闭门禁

- Provider 扫描器直接从 Linux 容器读取 Windows 工作树的 Git 元数据失败。随后用宿主 `git ls-files --cached --others --exclude-standard` 清单驱动原 Python 3.12 扫描与校验函数，未更改扫描规则或审计文件。
- 基线事实：`git show HEAD:server/main.py` 的 AST 已有 `create_scaledown_result` 下 `httpx.AsyncClient` / `client.post` 未登记。未改动的 SpeechWorkspace、models、ScaleDownPage、speechAudio 也触发清单漂移，不能归因于 B2。
- B2 事实：对 HEAD 与当前 main.py 做调用 AST 对比，仅替换一项审计身份：`run_deployed_workflow_trigger -> _run_deployed_workflow_trigger_body` 中既有 `_run_workflow_response` 委托；没有增加模型/HTTP 出口。委托前增加所有权与未知执行拒绝，委托仍经过原 Runner。模块摘要必然变化，仍需正式逐项收口清单，不能表述审计通过。
- 本地已知 `origin/main` 比当前基线多两个提交，包含 PR #418（模型目录及漂移收口）；本批未 fetch、merge/rebase 或声称已完成最新上游集成。
- B1 资源权威接入、B3 顺序项恢复、Planner/Headless 开放均未提前实施。Help Center 的恢复说明及同基线预览留在 PR 前验收，不因本批无 UI 改动而省略用户行为边界。
- 本批没有提交、推送、PR、部署、真实 Provider 调用或业务写入。回退不得删除固定请求字段或账本后重新派发；须保留新字段读取及未知结果阻断。
