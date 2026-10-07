# CW10 全链路收口核对

日期：2026-09-19。工作树：`codex/meta-planner-controlled-writes-10-closeout`，
基线 `a7d99925` 加该工作树原有 CW10 未提交改动。本文只记录本次系统收口，
不覆盖历史失败、实际调用账本或先前验收结论。**尚未达到整轮 PR 门禁。**

## 已确认与未确认

确认的是可离线复现的工程缺陷，不是“DeepSeek 已到上限”或“必须提前多 Agent”：

| 环节 | 修复前反例 | 本次处理 |
| --- | --- | --- |
| 条件域 | 未保护的 nullable 对象可通过编译，执行时字段读取失败；合法空值保护反而被拒绝 | 按真实来源 Schema 建立有界输入等价类，复用 Runtime 比较函数，并在路径内检查可达错误 |
| 分支关联 | 同一来源的互补比较被独立枚举，产生不可能的同时到达终点 | 同一 `source_ref/source_port` 联合分析，保留不同来源之间的保守组合 |
| 资源类型 | 较早校验可能依据模型重复声明，较晚解析才看到固定表 Schema | 较早检查也使用已授权 Adapter/Store 的权威类型；拒绝从未授权资源及其依赖推导事实 |
| 语义身份 | 节点顺序改变场景编号，进而改变 Graph checksum 和往返产物 | 场景排序与编号确定化；验证节点/控制边重排和编译往返 |
| 诊断 | 前置错误遮蔽独立类型错误，未执行阶段容易被当作通过 | 保留原解析门禁，独立收集可证明的类型错误；明确 passed/failed/blocked |
| 失败产物 | 服务端占位图标为 current，看起来像模型生成的串行候选 | 增加来源和 fallback_unapprovable 标记，前端明确提示，不展示占位图的正向路径证据 |
| 单模型 Harness | Task Plan 承担图配置细节；Patch 输入同时要求“不是 Patch” | 分阶段分配信息，删除冲突和重复提示；保留原授权、端口、Schema 和最多三次 completion |

最后一次真实失败的完整 GraphIntent/Patch 未保存。本次反例不能被追认为其唯一原因。
已核对的合成表 `score` 为 required integer，不把那次具体错误归因于 nullable 字段。
没有新增真实模型样本，不能据离线结果宣布生成成功率、泛化稳定性或模型能力上限。

## 路径证明边界

`control_domains.py` 只支持现有受限 `WorkflowValueSchema` 与九类比较 DSL。
它不是表达式引擎、SAT 服务或新的 Workflow Runtime，不读取表记录、不外发数据。

- primitive、nullable、顶层 object 字段及 missing、array 成员、受限 union。
- 数值按比较边界划分；字符串使用有界状态机覆盖 equals/contains 的观测类；
  数组覆盖成员组合与精确相等；合法代表统一交给实际 Runtime 比较函数复核。
- 同一来源先联合分析，再组合独立来源；最多 8 个路由、256 个联合场景。
  不再先按独立路由的笛卡尔积错误淘汰共享来源的合法图。
- 每个输入域最多 256 个代表，字符串最多 4096 个状态；共享计算预算 2,000,000，
  Schema 深度最多 16、遍历节点最多 256。超过预算受控拒绝，不截断后放行。
- 当前 Schema 禁止 enum/minLength/minItems 等额外约束。增加 Schema 字段或子类时，
  证明器先拒绝，必须配套实现后再开放，避免过滤代表值时漏掉合法输入类。
- 证明可达路径不代表回答正确。不同来源的函数依赖、复杂约束和超限输入仍可能
  保守拒绝；未声称证明任意复杂图，也未用若干随机样例冒充完备证明。

独立 Sol 只读复核指出了潜在新 Schema 约束漏类和计算量风险。核实当前 Schema
确实不支持这些约束后，加入反向拒绝、未来扩展拒绝及工作预算回归。
字符串/数组用有限小域穷举与 Runtime 结果作差分检查；这仍不是形式化完备性证明。

## 诊断和失败产物

- `generation_diagnostics.version=3`；每阶段有 `phase_results`，未执行阶段写
  `blocked_by`，不伪造为通过。保留既有 `checks_executed` 读取兼容。
- 路由证据只保存可信 Schema/配置摘要与 checksum；字段名只保留 checksum，
  不保存比较常量、Prompt、表记录或资源正文。
- 独立类型诊断不改变原 `resolve` 门禁。循环内或依赖未授权来源的节点不提供
  猜测类型；控制错误之外仍可确认的合法来源类型可以反馈给唯一修复机会。
- `candidate_origin` 区分 `model_generated` 与 `server_synthesized_fallback`。
  fallback 的 `graph_ir_status=fallback_unapprovable`，保留已有无模型防误批准措施。
- `meta_planner_report.validation_scope=pre_authoring_proposal`，附候选 checksum；
  最终批准依据仍为 `proposal.validation`。不为对齐两份报告追加一次 Proposal 写入。
- 无自动批准、发布、活表执行、第二次修复或新的节点授权。Capability 仍为 22 类。

## Harness 证据

对同一冻结八类能力合成夹具测量 UTF-8 字节，不测 Token、账单或注意力：

| 阶段 | 修改前 | 修改后 | 降幅 |
| --- | ---: | ---: | ---: |
| Task Plan | 12,261 | 7,798 | 36.4% |
| Graph 编译 | 44,239 | 41,427 | 6.4% |
| Patch 修复 | 57,341 | 56,774 | 1.0% |

主要收益是职责划分与冲突清理，不能将修复提示只缩短 1.0% 说成显著压缩。
新增的受限诊断也占用上下文。操作和图 Schema、权限、原图、动态资源目录完整保留；
仅压缩 Schema 的展示 title，不删除字段约束，也不误删名为 title 的业务字段。
下一次真实复测必须固定目标、模型、温度、授权和调用上限，分别记录协议、编译、
Patch、实际路径与写效果，不能只检查“返回 Proposal”。本批未消耗该额度。

## 验证状态

- 新规范反例修复前 28 failed / 4 passed；修复后与控制流和写入组合 76 passed。
- 早期扩大回归 1,011 passed / 8 failed。类型收集误改失败阶段属于本批回归，已纠正；
  未授权依赖和循环事实也恢复拒绝。原失败四文件与新证据测试原样复跑 100 passed。
- 计算域及路径证明 46 passed；字符串、数组、联合类型和预算证伪保留。
- 正式三路径集成 3 passed：缺失安全终止、低分真实更新、高分不写入。编译器、
  classic Runner、隔离 SQLite、路径与效果评分均真实执行；**仅 Agent 文本为替身**。
  业务哨兵及非目标记录保持不变，不是预览器或真实模型效果验收。
- 最终相邻后端回归覆盖 55 个文件，1,206 passed / 0 failed，耗时 332.42 秒；
  保留四条既有 FastAPI 弃用 warning。此前一轮为 1,204 passed / 1 failed，
  唯一失败是旧测试把共享输入的六个路由误计为 729 个独立场景。现在同时保留
  共享来源的 13 场景正例和六个独立来源超 256 上限的拒绝例；对应组合 49 passed。
- 前端三文件 30 passed，包含 fallback 实际 DOM 提示与正向路径证据隐藏；
  最新 TypeScript + Vite 生产构建通过，产物在忽略目录，
  未覆盖运行中预览器。大 bundle 与输出目录 warning 保留。
- 1,010 个后端 Python 文件完成 AST 解析与内存编译；本批 19 个源码、测试和文档
  文件的常见凭据模式扫描无命中。`git diff --check` 通过，暂存区为空，
  SQLite/Runtime/构建产物未进入本次可提交文件清单；这不是完整秘密检测审计。
- 写契约、Adapter、Runtime 比较函数和写执行模块相对本批开始时的 hash 均不变；
  最后一次真实调用账本 hash 不变，本批没有派发新的真实调用。

本机离线复核入口如下。测试启动器清除 Provider 配置、阻止非回环网络并使用临时
Store；忽略目录中的证据和构建产物不纳入提交，也不是新增生产依赖：

```powershell
& C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B .tmp-cw10-proof-closeout-20260919/run_affected.py
# client 目录
.\node_modules\.bin\vitest.cmd run --configLoader runner --maxWorkers=1 --fileParallelism=false src/components/meta/MetaPlannerV2.test.tsx src/components/meta/metaAuthoring.test.ts src/components/meta/DataTableWriteGrants.test.tsx
npm.cmd run build -- --outDir ../.tmp-cw10-proof-closeout-20260919/client-build
# 工作树根目录
& C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B .tmp-cw10-proof-closeout-20260919/verify_source.py
git diff --check
```

最终相邻 JUnit 为 `.tmp-cw10-proof-closeout-20260919/affected-final-v2.xml`，
先前失败结果保留在同目录 `affected-final.xml`；不覆盖失败记录。

### 全量阻断

本次全量首次在约 6% 时中止：环境缺少 Agency Worker 构建产物，尚有未分类失败。
按原 lockfile 从离线缓存恢复 11 个包并构建，不新增依赖或改动 lockfile；
Worker 原文件复跑 28 passed。然后从全量入口执行 `--maxfail=1` 定位：
**94 passed / 1 failed**，不是完整全量结果。

失败为 `test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted`：
Worker 断连后发送取消帧触发 `ConnectionResetError`，覆盖预期的 `EngineUnavailableError`。
该模块/测试相对 HEAD 无差异，历史审计有同症状；本批没有重跑干净基线，
不把其余未执行项称为通过，也不把历史 Linux 全量当作本批源码证据。
未忽略测试或顺手修复该独立模块。整轮门禁需要在合适的独立环境补齐完整回归。

## 交付与回退

没有数据迁移，Runner、SSE、实际比较函数和业务写事务未改。旧已发布 Workflow
执行不受本证明器改变；重新生成/编译时以前漏检的空值风险现在会被拒绝。
不修改历史失败提案、不清理 Runtime，不将其直接标成已修复或重新生成。

回退仅撤销本批路径证明、诊断及提示/展示投影，恢复前一冻结源码；保留写入权限、
记录收据、revision 与审批门禁。不得通过回退清除账本或撤销已完成的业务写入。

当前未重启预览器，未操作共享栈，未调用真实 Provider，未写活表，未批准/发布，
未提交/推送/创建 PR。后续仍需完整回归、独立预览器中同配置真实生成、三路径实际效果、
帮助中心操作复核与用户最终验收；不提前进入下一轮。
