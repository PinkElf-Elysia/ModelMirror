# R8-B：Provider 可信身份与 B2 恢复集成

## 开工契约

- 2026-10-18，最新基线 `253ad98a`，分支 `codex/r8b-provider-origin-integration`，工作树 `C:\tmp\modelmirror-r8b-provider-origin-integration`。
- 原 B2 工作树与未提交内容完整保留。本树只移入已有 B0/B1/B2 生产差异、测试及证据；不复制 Runtime、凭据、前端构建或独立目录修复。移入超过五个文件是既有批次的完整依赖集合，不是新增功能范围。
- 本批新增变更限定：子调用可信来源衔接、针对性反例测试、必要测试夹具和本卡。Provider 审计清单另以语义核验小批收口，不机械刷新哈希。
- 高风险：跨 Store 的任务/运行身份与预算绑定。禁止将 budget task ID 当作 Provider execution ID，禁止用请求 metadata 重建可信 actor，禁止因过期接管自动重发未知外部请求。
- 不开放 Planner 节点，不增加依赖、业务路由或权限；不调用真实 Provider、不写业务表、不触碰共享栈、不提交或部署。

## 验收顺序

1. 将最新上游与原 B2 差异联合运行，先保留真实失败；区分夹具缺失可信来源与生产恢复漏洞。
2. 验证同一子调用的 Provider 来源、根预算、固定输入与派发身份；覆盖来源缺失/伪造、跨来源降权或扩权、父运行恢复、已完成复用与不确定请求不重发。
3. 运行 B0/B2、Provider context、Managed Workflow、R8-A 预算/恢复与旧子工作流回归；所有网络调用仅合成传输，运行数据只进临时目录。
4. 语法、Diff、敏感模式与 Provider 出口审计。完整后端、前端、同基线独立预览仍为后续门禁，未执行不得写为通过。

回退：原工作树不变；集成树可单独核对与放弃，不删除历史账本或原运行证据，不把移除可信来源校验作为兼容方案。

## 初始语义结论

- 上游 Provider origin 保存运行 `run_id`、逻辑 `task_id`、来源、actor 和 purpose；B2 预算委托保存父任务与发生标识，二者不同。
- Provider 来源登记不是授权或重放许可。调用预算与执行所有权继续由 B2 持久 Store 决定。
- 初始审计不作为联合验证通过的证据；以下为复现、最小集成及复跑记录。

## 复现与最小集成

- 基线固定为 `253ad98a537dd71e6c41761d89ff03d9e6e86d4e`。原 B2 工作树不变；本树从该主线移入既有三个生产文件的差异、新增只读准入模块、B0/B1/B2 测试及历史证据。旧清单、独立截图/目录修复和 Windows 临时文件断言修复没有覆盖上游文件。
- 首次只读挂载执行有 2 项收集错误：World 模块导入时创建默认目录。改为将 `server/` 复制到独立 tmpfs，源码挂载仍只读；未修改生产初始化逻辑或放宽测试。
- 初次旧夹具联合测试为 **21 failed / 14 passed**：测试重复使用合成子任务 ID，却未隔离新增的 Provider 来源仓库。上游重放保护按设计拒绝重复来源。改为每例真实 SQLiteRouterRepository，进程恢复复用同一测试仓库，不关闭来源校验。
- 隔离夹具后，新增 Provider 联合反例得到 **12 failed / 35 passed**：B2 子调用丢失父 Provider origin，将 classic 来源改成独立 deployment 服务身份；父来源缺失或被篡改也不能在派发前阻断。此为实际交集缺口，不是只改断言。
- 最小生产修改限定 `main.py` 子调用桥：由预算委托定位持久父执行，再以现有 Gateway 校验 `run_id / task_id / source_kind`；向原 Runner 传递可信父运行和原来源，派发及结算时重验所有权和来源。根预算委托和稳定子任务 ID 沿用 B2，不建立另一套 ID、授权或账本。
- 只允许已接入的 `workflow_classic / workflow_deployment` 来源继承；其他已声明来源拒绝，不改变运行类型冒充 Xpert。没有 origin 且 `source_kind=None` 的历史未接入运行保留原兼容边界，不将它描述为已认证的 R8-B Provider 委托。
- 同组修复后 **47 passed / 4 warnings**；随后增加不确定请求重载、派发准备/结算时父来源被移除的反例，再运行扩大回归。

## Provider 清单语义核对

扫描器、Workload Registry、入口授权状态不变，未按扫描输出批量刷新清单。

| 漂移 | 归因与证据 | 登记范围 |
| --- | --- | --- |
| `client/src/data/models.ts` | 上游 `99612f4e`（#420）更新目录；TypeScript AST 比较仅四个静态字面初始化发生变化：`rawCatalogModels`、`rawBatchServingVariants`、`DEEPSEEK_V4_1_FLASH_MODEL_ID`、`LATEST_REFRESH_MODEL_IDS`。无删除语句、新网络逻辑或凭据逻辑。 | 保留 `non_model_http / browser_backend_api`；更新该模块摘要和对应脚本候选，不改为模型派发。 |
| 主线 `server/main.py` | #420 只向 `OpenRouterDecisionRequest.model` 增加五个枚举值；先前版本 AST 摘要与清单原值精确相等。 | 独立记录主线摘要漂移，不归因 B2。 |
| B2 Runner 委托 | Python 3.12 AST 对照当前主线只有一个候选移除、一个候选新增：`run_deployed_workflow_trigger` 的 `_run_workflow_response` 移至受所有权保护的 `_run_deployed_workflow_trigger_body`，增加预算和可信父身份参数。 | 保留 `model_delegate / workflow_shared`，更新单个委托及主模块摘要；无新增网络出口或业务入口。 |

- 干净基线反向核对仍报模型目录候选陈旧及模块漂移，证明上游既有问题与 B2 可分离。
- 保留上游 `openrouter_scaledown / migration_pending` 和浏览器 `model_delegate` 分类，没有重新引入旧树的 `scaledown_text` 重复条目。
- 两个已审查模块当前摘要：`models.ts = 27b092df978f9aa4c901b388f8531bfc6b68286e297b98c0902c84bdd88adfcb`；`main.py = a85923e9e6bec67b5929dbeea0ad8b415ed354adb606574c47822de96e36a529`。后者基于 Python 3.12 AST，不以其他解释器的摘要替代。
- 新增离线反例核对委托分类及四项身份/预算参数；再次改名或删除任一参数时必须产生未审查候选、陈旧候选及模块漂移错误。

## 当前验证证据

2026-10-18，使用既有 `modelmirror-r9a1-linux-validation:4932e3b4` 的 Python 3.12；容器 `--network none --read-only --cap-drop ALL --security-opt no-new-privileges`，源码只读挂载，运行数据只在 tmpfs。模型传输为 `httpx.MockTransport`，没有真实 Provider 调用。

- 前一扩大测试会话在续接时已失效，最终结果无法取回；容器也不再运行，**未把 85% 进度当作通过**。本次从头执行同一 52 文件选择，结果 **1599 passed / 4 warnings，181.31s，退出码 0**。
- 选择为全部 `server/tests/test_workflow_r8*.py`，加 `test_provider_context_workflow.py`、`test_workflow_managed_gateway.py`、`test_workflow_managed_runtime.py`、`test_workflow_subworkflows.py`、`test_workflow_iteration_v2.py`、`test_workflow_deployments.py`、`test_workflow_deployment_api.py`、`test_xpert_execution_budget.py`。命令使用 `python -B -m pytest <上述文件> -q -ra --tb=short -p no:cacheprovider --basetemp /tmp/pytest`；运行前复制当前 `server/` 到 tmpfs，并排除 `storage/__pycache__/node_modules`。
- Provider 清单及变异反例：`python -B -m pytest server/tests/test_provider_coverage.py -q -ra --tb=short -p no:cacheprovider --basetemp /tmp/pytest`，**26 passed / 16 subtests passed，10.41s**。
- 节点契约与能力审计：同参数执行 `test_workflow_node_contracts.py test_workflow_capability_audit.py`，**29 passed，12.80s**。确认精确 22 类 Planner 能力；`invoke_workflow / iteration` 仍未开放。
- 完整 Provider 源码扫描：以宿主 Git NUL 清单（含非忽略未跟踪源码）传入只读容器，由原扫描器筛选并扫描；只替换容器无法解析的宿主 `.git` 路径枚举，不改变检测或验证规则。`validate` **零错误，81 entries / 947 candidates**。这是源码覆盖，不是 Provider 运行资格。
- 四个生产文件经 Python 3.12 AST 语法检查通过。4 条扩大回归警告均为既有 FastAPI `on_event` 弃用，没有屏蔽警告。

### 三项关键边界

1. **身份继承**：两类来源均核对父 origin、actor、purpose；伪造请求 metadata 无效；来源缺失、任务不匹配及来源替换在外发前拒绝。准备/结算阶段失去父来源不能报告成功。
2. **根预算**：脱离调用 Context 后仍从持久委托计入根预算；完成结果复用不再派发；父运行恢复为新 run ID 后，下一子项不能绕过已经耗尽的根预算。
3. **不确定恢复**：模拟 HTTP 超时后回执为 `dispatched + uncertain`；Provider、执行及部署 Store 全部重开并重新认领父任务后，仍只有一次合成 POST、原回执及一次根预算计数。独立进程崩溃、输入漂移、旧 worker 与取消竞态也在扩大回归内通过。

## 收口与剩余门禁

> 以下为本次扩大回归结束时的阶段状态。之后同基线全量、Windows 专项与三条纯计算预览已完成；请按 [本地交付材料](META_PLANNER_R8B_DELIVERY_PACKET.md) 查看最新状态，不把下文历史“尚未重跑”当作当前阻塞，也不把当前通过项扩大为真实 Provider 或完整 R8-B 验收。

- 本批结论：**Provider 可信来源与 B2 身份/预算/恢复交集的离线门禁通过**；不扩大为整个 R8-B 就绪或真实模型稳定性结论。
- 最新集成基线的全量 `server/tests/`、前端锁定依赖/构建/测试、同基线独立预览和帮助验收仍未重跑；旧 `10455 passed` 等历史记录不能替代本基线结果。
- 原树独立 Windows 测试修正、截图归档/去重等遗留项尚须逐项核验；平台与 sidecar 条件跳过不得用本次 Linux 通过数覆盖。最新 worker 锁文件变化也须在完整环境门禁中处理。
- B1 实际资源权威接入及后续顺序迭代属于后续范围，不在本批提前开通；未增加 Planner Adapter、Headless 能力、公共 App 或 Goal/Handoff 权限。
- 本批未提交、推送、创建 PR、部署或调用真实模型；原工作树、历史账本和共享栈保持不变。
- 最终 `git ls-remote origin refs/heads/main` 仍为 `253ad98a537dd71e6c41761d89ff03d9e6e86d4e`；`git diff --check` 通过，暂存区为空。对 16 个差异/非忽略新增文件的有限密钥模式与禁止产物路径扫描均零命中；这不是绝对无敏感信息证明。
