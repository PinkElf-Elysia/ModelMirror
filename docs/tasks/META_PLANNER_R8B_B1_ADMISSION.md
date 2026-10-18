# R8-B B1：固定目标与传递性只读准入

## 开工契约

- 基线：`ec79e1203c0f26e9efa20e4e75e7e0bf9de2a795`。
- 工作树：`C:\tmp\modelmirror-meta-planner-r8-b-readonly-subflows`；分支 `codex/meta-planner-r8-b-readonly-subflows`。
- 开工状态：仅 B0 测试与任务卡未跟踪，保留原样；无已跟踪改动。
- 本批五个文件：本任务卡、NodePolicyService 所在 `node_contracts.py`、`workflow_deployments.py`、新建 `readonly_subworkflow.py`、B1 测试。
- 单一目标：建立只供服务端调用的固定发布版本准入与重校验服务，不接入执行入口或 Planner。
- 禁止：修复 B0 预算/恢复问题、开放节点、改变 classic 发布/执行行为、调用外部模型、写业务数据、提交/部署。
- 无新依赖、业务路由或 Store；不修改持久化格式和 Capability V10/22 类。

## 契约边界

1. 显式目标授权为严格的 `project_id + version`，默认空；检查授权后才查询 Store，不能接受 latest、字符串版本或布尔版本。
2. 部署 Store 在锁内校验已发布 callable、活动版本和定义摘要，返回隔离副本。
3. NodePolicyService 根据配置和有效 NodeContract 决策；整个子图全部节点均须检查，包括绑定与断开的元数据，不能只看可达执行节点。
4. 首发拒绝网络、工具、插件、外部 Xpert、中间件、嵌套子调用、任何迭代、等待和写入；JSON 旧错误契约不自动升级。
5. 普通模型节点必须无工具和隐藏扩展；准入仅返回模型身份与后续预算要求，不授权模型外发。
6. 只读资源必须有服务端注入的只读权威校验器；没有校验器、未授权或无法验证均拒绝。校验器只查本地资源 Store，不执行检索、查询、Provider 或业务写入。
7. 安全快照只保存 ID、版本、摘要、资源身份和模型身份，不返回 Prompt、默认输入、凭据或工作流正文。重校验重新读取当前授权、目标及资源；旧快照不是执行许可。
8. 旧 classic 入口保持兼容。B2/B4 接入执行与 Authoring 前不得声称该服务已经保护所有运行。

## 验收与回退

- 新增离线测试覆盖空授权、版本注入、非 callable/非活动目标、定义篡改、隔离副本、配置级隐藏工具、绑定副作用、重试等待、嵌套调用、资源校验缺失/失败、快照漂移与脱敏。
- 运行 B1 测试、NodeContract/Registry/Validator、旧子流程/迭代及 B0 对照；B0 的四条恢复反例继续如实保留，不作为 B1 通过项。
- 本批无用户界面变化，不用静态测试冒充预览器或真实模型验收。
- 回退仅移除新增服务与内部策略分支；没有在途执行或发布数据需要迁移。

## 实测结果

本批在缓存镜像 `modelmirror-r9a1-linux-validation:4932e3b4` 的 Python 3.12 中离线验证；源工作树只读挂载，网络关闭，不加载凭据、不访问共享栈。

执行命令（容器内临时源码副本）：

```text
python -B -m pytest server/tests/test_workflow_r8b_admission.py server/tests/test_workflow_subworkflows.py server/tests/test_workflow_iteration_v2.py server/tests/test_workflow_r8a_inline_budget.py server/tests/test_workflow_node_contracts.py server/tests/test_xpert_runtime_workflow_node_registry.py server/tests/test_workflow_native_validate.py server/tests/test_workflow_deployments.py server/tests/test_workflow_deployment_api.py server/tests/test_xpert_publish.py -q -ra --tb=short -p no:cacheprovider
```

- 最终结果：**357 passed，4 warnings，29.97s**。警告为既有 FastAPI `on_event` 弃用警告。
- 首轮 B1 为 23 failed / 20 passed：定位到 `workflow_call_entry` 的既有契约不是确定性纯节点；修正为显式输入入口准入，不修改原契约或全局放宽。修正后 45 passed，再补充攻击覆盖并纳入上述最终回归。
- 扩大回归首次在复制源码时发生临时内存文件系统分配失败，尚未启动测试；改用容器匿名临时卷后重跑原套件通过。未清理或停止其他服务。
- B0 原命令重新执行：`python -B -m pytest server/tests/test_workflow_r8b_entry_evidence.py -q -ra --tb=short -p no:cacheprovider`，仍为 **4 failed / 7 passed，14.05s**。四个既有反例为子调用缺少持久根预算绑定、空上下文恢复绕过已耗尽预算、调用输入漂移复用旧结果，以及不确定派发被重放；未跳过、弱化或宣称修复。
- 四个 Python 文件的 UTF-8 / AST 语法检查通过。使用仓库 Provider 覆盖脚本的 Python 扫描器检查三个生产文件，新增调用候选均为 0；这不是全仓 Provider 审计或真实 Provider 验收，也没有刷新审计哈希。
- 新文件的有限密钥模式及 `skip/xfail` 扫描无匹配；有限模式扫描不等同于绝对无泄漏证明。

## 未覆盖边界

- 资源权威校验器仅定义服务端注入契约，尚未连接实际资源 Store；缺失校验器时拒绝资源节点，不默认放行。
- Runtime、Planner、Headless 尚未接入；本批快照不构成执行租约或外发授权，后续必须在执行边界重校验。
- B0 的四条红测留给预算和恢复批次；本批不提前修复。
- 未运行全量后端、前端生产构建、独立预览器或真实模型验收；不据此宣布 R8-B 或 PR 门禁完成。
- 未提交、推送、创建 PR、部署或写入业务数据；后续仍按小批次验证推进。
