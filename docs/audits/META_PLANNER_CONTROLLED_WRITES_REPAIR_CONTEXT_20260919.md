# 受控写入唯一修复上下文收敛

日期：2026-09-19。状态：本批定向离线验证通过，全量门禁失败；未进行新的真实模型复测，尚未达到 PR 提交门禁。

## 范围与证据

- 工作树：`C:\tmp\modelmirror-meta-planner-controlled-writes-10-closeout`。
- 分支：`codex/meta-planner-controlled-writes-10-closeout`；本批开始时 HEAD 为 `a7d99925584e818e7f2df84b1646256abfc08ed6`。
- 保留全部既有未提交变更。本批只改修复提示投影、对应测试和本文，不改授权、Adapter、Graph Patch 执行器、Validator、Runner、业务 Store 或前端。
- 前置证据见 [离线复现](META_PLANNER_CONTROLLED_WRITES_REPRODUCTION_20260919.md) 和 [真实复测](META_PLANNER_CONTROLLED_WRITES_SYSTEM_RETEST_20260919.md)。原始模型图没有完整留存；重建案例仅证明相同结构的失败，不冒充原始响应逐字重放。
- 已证实：旧提示已经包含配置与连线联动规则。只解除多余输入后，独立的数据可达性和控制错误仍在；把筛选条件从 literal 改为 input 而未接入新端口，会在整批 Patch 校验时失败。
- 合理推断：重复审计信息和分散的关联事实会增加唯一修复的注意力负担。但现有证据不能把失败单独归因于上下文长度，也不能证明单模型或特定模型已达到能力上限。

## 本批处理

1. 在唯一 Patch 修复提示中增加只读 `dependency_review`，优先列出问题节点及其直接数据生产者，将当前必需输入、实际绑定和控制前驱放在一起。它不生成修改方案、不猜测来源、不证明路径可达。
2. 明确“最小修复”是最小语义改动，不是最少操作数；最终配置与显式连线须在同一原子 Patch 中成立，不能通过修改无关业务条件消除端口错误。
3. 模型上下文去掉重复的全图绑定审计、指纹和审计 checksum；所有错误、遗漏计数和后续阶段阻断状态仍保留。持久化报告继续保留完整审计信息。
4. 完整原图、权限、资源目录、任务计划、操作 Schema 和 Graph 契约保持原值。三个生成阶段继续使用完全一致的权威契约；首次生成和严格整图修复不经过本投影。

没有增加模型调用或修复轮次；仍最多三次 completion。没有新增自动补边、自动改筛选条件、自动审批、发布、评测或业务写入。

开发中曾尝试只在 Patch 提示中省略类型 Schema 的默认字段。既有跨阶段契约一致性测试发现两处不一致后，已撤回该部分，保留原测试和完整 Schema。这一失败属于本批试改引入，不能归为环境或基线问题。

## 定向证伪

新增 `server/tests/test_meta_planner_repair_context.py`。使用合成资源目录、脚本化 completion 和临时 Proposal Store；HTTP、网络及业务记录操作有禁止钩子。

- 两操作反例：解除多余输入后修改配置，仍因缺少新输入失败；原图不被部分修改。
- 局部修复反例：只解除多余输入，独立 `DATA_UNREACHABLE` 仍阻止编译。
- 完整显式修复：Update/Delete 均通过校验及编译、反编译、再编译；配置与接边的合法先后顺序均被支持。
- 保持配置的替代修复：不修改业务条件，仅修正多余输入和控制顺序，也可通过对应合成案例。
- 未授权资源不产生新的动态资源 Schema；未知或无法定位的错误进入全局待处理项，不被静默丢弃。
- 嵌套类型、业务配置、权限、Graph 和操作 Schema 不变；投影不修改传入对象。
- 脚本化完整生成验证三次调用上限、失败阶段阻断、完整审计落盘；结果仍为 pending Proposal，不创建 Xpert 草稿。

这些测试证明工程契约及拒绝边界，不证明真实 Provider 泛化成功率。

## 上下文测量

离线生成同一合成图的修复请求，比较启用投影前后 UTF-8 字节数，不换算 Token：

| 范围 | 处理前 | 处理后 | 减少 |
| --- | ---: | ---: | ---: |
| 与失败记录相同的授权种类，合成结构重建 | 65,024 | 62,004 | 3,020（4.64%） |
| 全部非视觉能力投影 | 84,378 | 81,358 | 3,020（3.58%） |

两组均保留四项独立错误。诊断主体从 7,462 减至 1,004 字节，新增关联清单为 3,417 字节；主要收益是信息组织，并非大幅缩短总上下文。不据此声称真实调用成本或成功率改善。

## 验证记录

本批证据目录：`.tmp-cw10-repair-context-20260919/`，不纳入提交。

- 新测试初次：3 失败、2 通过，定位缺少紧凑诊断与关联清单。
- 扩展首跑：174 通过、2 失败，为上文的契约投影不一致；已撤回对应压缩。
- 同一扩展集复跑：176 通过。
- 最终源码的受影响回归：1218 通过，包含上述测试以及 Meta Planner、Graph IR、Headless、NodeContract、Authoring、Publish、Evaluator、App、Evolution 和隔离写入路径；两组数量有重叠，不相加。
- `python -m py_compile server/main.py server/meta_agent/meta_planner_v2.py server/meta_agent/repair_context.py server/tests/test_meta_planner_repair_context.py`：通过；pycache 写入本批临时目录。
- 前端 `npm.cmd run build -- --outDir ../.tmp-cw10-repair-context-20260919/client-build`：通过。Vite 报出既有大分块警告；输出到全新临时目录，没有覆盖正在使用的 `client/dist`。
- 全量入口 `run_offline_v2.py server/tests/ --maxfail=1`：94 通过、1 失败后停止，后续测试未运行。失败为 `test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted`：断连后的取消清理中 `stdin.drain()` 抛出 `ConnectionResetError`，遮蔽原始 `EngineUnavailableError`。
- 该失败与前次全量尝试相同。`server/agent_upstream/port.py` 及对应测试与本地 HEAD 均无 Diff，本批也未修改；没有重新建立干净基线的全量证据，因此不把此事实扩大表述为“最新 main 全量已验证失败”，也不绕过此门禁。
- `git diff --check`：通过；本批四个文件的凭据形态扫描无命中。源码哈希对比确认，原有 116 个改动文件中本批仅改 `meta_planner_v2.py`，另新增投影模块、测试和本文三个文件；旧真实调用账本 hash 未变。

离线测试通过已有隔离启动器执行：`.tmp-cw10-closeout/run_offline_v2.py`。启动器清除进程凭据、禁止读取 dotenv，将全部 Runtime Store 指向独立临时目录，并拦截外部网络。

可独立重跑的最小检查为 `python -m pytest server/tests/test_meta_planner_repair_context.py -q -p no:cacheprovider`，共 12 项；完整证据分别为 `focused-02.xml`、`affected.xml`、`full.xml` 和 `prompt-measurements.json`。

## 边界与回退

本批没有新真实 Provider 请求、活表写入、预览器或共享栈操作，没有提交、推送、PR、批准或发布。真实模型成功率仍需后续独立授权、冻结输入的复测检验，不能用本批脚本化 completion 替代。

回退只撤销 `meta_planner_v2.py` 中本批系统提示措辞、投影导入与调用，以及新增的 `repair_context.py`；其余既有变更保持不动。没有数据迁移或业务数据回滚。
