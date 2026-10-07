# Recipe 局部预检与修复反馈收口

日期：2026-09-26。范围：CW10 Recovery E 的 U1-U3，不是新一轮能力开放。

## 结论与边界

本次修正的是已被历史样例和成对反例证实的反馈链路缺口：控制结构展开失败时，原有修复上下文
遗漏已经能够独立判定的输入类型和路由问题；只比较整份描述是否变化，也不能区分真实结构修复
与仅改标题。没有替模型选择业务谓词、补边或改写历史失败产物。

本批离线收口完成，最终受影响 81 文件矩阵为 1654 passed，无 failures/errors/skipped。
真实生成、泛化成功率、人工修复闭环与隔离写入效果未在本次复测；E2/E3 总门禁仍未通过，
不提交 PR。它不是整轮已稳定或生产就绪的结论。

- 工作树：`C:/tmp/modelmirror-meta-planner-cw10-recovery-e`。
- 分支：`codex/meta-planner-cw10-recovery-e`；HEAD：`09e8a7d644f626b3e1aa606fcf148f360fc9aadc`。
- 2026-09-26 只读检查的本地缓存 `origin/main` 为 `db508b8e7d7053895e70b234647aa95b63685d85`。
  本次没有 fetch、合并或声称这是远端即时状态。相对 HEAD 的 136 个上游变更路径与检查时
  181 个本地变更路径没有直接交集；这不能排除传递影响，提交前仍须整合并重验。
- 保持 Recipe V1、Graph IR V3、22 类能力、单模型、初次生成最多三次 completion，以及原审批语义。
- 不调用 Provider、不读取凭据、不读取或修改活表记录、不重启预览器/共享栈，不批准或发布。

## 收口内容

| 层次 | 本次变化 | 未改变的权威边界 |
| --- | --- | --- |
| Recipe 展开 | 分离原节点准备逻辑，准备后收集局部事实；新增分支缺失/重复/多余的结构化差异 | Adapter 配置、资源授权与版本解析；不构造替代图 |
| 局部检查 | 同一类型检查器与控制分区证明器；类型问题上限 64、路由证明上限 8 | 不决定全图可达性、终点或业务正确性 |
| 修复上下文 | 错误、局部事实、修复顺序前置；失败原描述与完整权威契约仍保留 | 不增加修复次数，不截断已解析描述，不扩大授权 |
| Schema 投影 | `$defs/allOf` 去重公共节点字段，保留 kind/config/resource/task 约束 | 不改变 Recipe V1 的接受语言和 Runtime 校验 |
| 进展证据 | 控制结构 checksum 与同一错误指纹比较 | 仅观测；仍须完整重校验，不能据此放行或自动重试 |

`recipe_preflight` 的 `completed` 只代表局部检查完成，结果可包含 failed 或 blocked。
未经节点准备不声称零问题；完整路径证明未执行时明确显示 blocked/not_executed。
字段比较对某些空值抛错、但合法值可达全部出口时，留给上游判空保护证明，不误拒合法分支图。
诊断不保留 Prompt、路由标签、业务取值、记录或字段正文；敏感形态 ref/port 改为 checksum。

生产文件限于：

- `server/meta_agent/generation_recipe.py`
- `server/meta_agent/generation_diagnostics.py`
- `server/meta_agent/recipe_preflight.py`
- `server/meta_agent/meta_planner_v2.py`

正式反例位于 `server/tests/test_meta_planner_recipe_preflight.py`。另将
`test_meta_planner_recipe_integration.py` 的旧布局断言改成模型输出与服务端解析的实际注入拒绝。
生成描述文档和分批任务记录同步更新；没有修改 Runtime、Store、授权策略、Provider 或前端实现。

## 历史原图回放

固定 pending r1 提案 `proposal_655bec0a57614dc39b59294a5ee70b1f` 的首次与唯一修复 Recipe，
按原授权合成表的安全 Schema 元数据重建快照。快照 hash 与历史记录严格相等：
`ce98f84a4f95f7fc109f805623b42178af45cb26f5ab09fc306ca393e2e24ca0`。

两份原图均得到以下结果，未创建新候选、提案或调用：

1. `route_record` 在 `control_flow[1].branches` 缺少 `case_1/case_2/default`，
   错误码为 `RECIPE_BRANCH_OUTCOMES_MISMATCH`，而非通用错误。
2. 同时报告三处 Agent `task` 输入类型不符，不再因控制结构错误而显示空的类型问题列表。
3. 两条 `is_null` 规则在既有 Runtime 中含义相同，第二条出口不可达。
4. `route_stock` 对整条记录做数值比较，其整个权威输入域没有合法数值 outcome。
5. 全图授权/解析/编译/发布预检继续 blocked，没有把局部检查冒充完整证明。
6. 两份描述正文不同，但阻断控制结构相同；进展观测如实记录这一点。

读取仅限原合成失败产物及必要安全元数据；表记录和凭据未读取。Proposal 文件、表数据库、
调用账本及原核验报告文件的字节 hash 均未变化。调用账本 SHA-256：
`5b8440b810c4250f353fda0ebca19e97b3f2f6c33240fe0c3791cf9f0bc7a82e`。

新回放脚本和证据独立保存在忽略目录，不重跑会覆盖旧证据的原审计测试：
`.tmp-recovery-e/test_recipe_preflight_closeout_replay.py`、
`.tmp-recovery-e/recipe-preflight-closeout-replay-evidence.json`。

## 上下文计量

同一冻结请求/计划/快照，按真实生成与修复入口构造正文。单位全部是字符，不是 token：

| 部分 | 修改前 | 修改后 | 解释 |
| --- | ---: | ---: | --- |
| 模型所见 Recipe Schema | 21451 | 18923 | 重复结构减少约 11.785%，没有删除配置约束 |
| 初次生成正文 | 34583 | 32557 | 减少 2026 字符；仍完整保留授权和业务契约 |
| 修复正文 | 39964 | 40158 | 增加 194 字符；新增精确局部反馈，且移到前部 |

旧数值来自未改写的 2026-09-24 同快照取证，新值来自本次离线重建，未实际发送。
不宣称整个修复提示变短，也不将字符减少换算为 Provider token 或成功率。
本次没有测量真实模型对 `$ref/allOf` 的理解效果或服务延迟改善。

## 验证与失败记录

| 检查 | 实际结果 |
| --- | --- |
| U1 初始夹具 | 3 failed；夹具返回顺序使用错误，校正后才进行有效红测 |
| U1 校正后结构诊断红测 | 3 failed；期望结构化差异，实际为通用错误 |
| U1 实现后三文件 | 77 passed / 4 warnings，119.87 秒 |
| U2 反馈红测 | 12 passed / 3 failed，24.62 秒；类型问题被隐藏、缺少状态与进展观测 |
| U2 四文件核心矩阵 | 65 passed / 4 warnings，80.91 秒 |
| U2 扩展首次 | 58 passed / 12 failed；失败均在未经计划规定的 15% 压缩比例断言，接受/拒绝对照已通过 |
| U2 扩展最终 | 72 passed / 4 warnings，86.59 秒；改检验共享结构与实际缩短，不为比例修改生产契约 |
| 历史原图离线回放 | 4 passed / 4 warnings，17.26 秒；没有修图、派发或覆盖旧证据 |
| 受影响 81 文件首次完整矩阵 | 1653 passed / 1 failed / 4 warnings，845.82 秒 |
| 唯一失败精确复现 | `test_production_entry_requests_recipe_and_stores_only_existing_graph_ir` 的 `properties` KeyError，1 failed，13.26 秒 |
| 布局断言改为实际注入拒绝后精确复跑 | 1 passed / 4 warnings，14.52 秒；Prompt Schema 与服务端解析均拒绝伪造 outputs |
| 相同 81 文件最终矩阵 | 1654 passed / 4 warnings，921.19 秒；两次测试选择完全一致，无 errors/skipped |
| 前端四文件 | 50 passed，20.75 秒 |
| 前端生产构建 | 通过；保留既有大包体 warning，未调整阈值或依赖 |
| 后端 AST、Diff、敏感签名 | 六个 Python 文件 AST 通过；git diff --check 通过；九个本批文件的已知凭据模式及尾部空白均零命中 |

警告为既有 FastAPI `on_event` 弃用提示。各选择集互有重叠，不累加为独立用例总数。
原集成断言依赖旧内联布局；最终改用空及伪造 `outputs` 的真实拒绝检查，而不是放宽安全检查。
两业务领域的成对测试还保留了重要反例：谓词方向写反也可能通过编译，必须用独立业务 Gold
和实际效果断言验收；本次不会把合法编译等同于正确实现用户目标。

### 命令

后端统一使用隔离 runner：清除 Provider/代理凭据环境，禁用 dotenv，所有 Store 定向新临时目录，
阻断非 loopback 网络，不读取默认 Runtime 数据。

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B .tmp-recovery-e/run_offline.py `
  server/tests/test_meta_planner_recipe_preflight.py server/tests/test_meta_planner_recipe_matrix.py `
  server/tests/test_meta_planner_recipe_model_repair.py server/tests/test_meta_planner_prompt_projection.py `
  --junitxml=.tmp-recovery-e/recipe-feedback-u2-expanded-final.xml

& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B .tmp-recovery-e/run_offline.py `
  .tmp-recovery-e/test_recipe_preflight_closeout_replay.py `
  --junitxml=.tmp-recovery-e/recipe-preflight-closeout-replay.xml
```

最终矩阵为所有 62 个 `test_meta_planner*.py`，另加：

```text
test_meta_agent / test_meta_agent_managed_endpoints / test_meta_agent_managed_gateway
test_workflow_node_contracts / test_xpert_runtime_authoring / test_xpert_publish
test_xpert_evaluations / test_xpert_structure_evolutions / test_xpert_app_api
test_workflow_typed_values / test_workflow_typed_ai / test_agent_table_controlled_writes
test_controlled_write_execution_integration / test_controlled_write_runtime / test_controlled_write_store_trust
test_evaluation_controlled_write_runner / test_evaluation_write_error_paths
test_evaluation_write_evidence / test_evaluation_write_isolation
```

各名均为 `server/tests/<name>.py`；同一 runner 的报告为
`.tmp-recovery-e/recipe-preflight-affected-regression-final.xml`。
runner 的 `TEST_FILES` 实为参数数，会将 JUnit 选项计入；真实选择数是 81 文件，不能记成 82。

前端在 `client` 执行现有依赖，无安装：

```powershell
& .\node_modules\.bin\vitest.cmd run --configLoader runner --maxWorkers=1 --fileParallelism=false `
  src/components/meta/MetaPlannerV2.test.tsx src/components/meta/FailedDraftRepair.test.tsx `
  src/components/meta/ModelDraftRepair.test.tsx src/components/meta/metaAuthoring.test.ts
npm.cmd run build
```

### 源码与证据绑定

四个生产文件和两个测试文件的 SHA-256：

```text
generation_diagnostics.py 8f0f2ce0afbabb218569f270f09154bb11fcbace5a2b00a178ad97d2066889f1
generation_recipe.py 61dc268202e5658947d185aa7fe43de35982cdfcb1c4c2c83da70836dafe349f
recipe_preflight.py 5981e9f422480096a82824308df952e127e5efbc2c009ad9e30589e6a4ee597b
meta_planner_v2.py ba1a2897e4e9e9fd8629af208b16e5f7a3e76ab02a4015da099009a925d47a4f
test_meta_planner_recipe_preflight.py d5f34bd5a4886ce0bf081ccb5e67f6900a3bdb636fc646c1bb9127ef3be156d4
test_meta_planner_recipe_integration.py ac39e7779987aa3df2c1960e8463e338d2b618afbd95bad74096b966321105ba
```

原图回放证据 JSON hash：
`90071672b9cfb50c28339a70f3ba5526810896922c12ca11c7272942a40235c1`。
该文件仅含安全诊断、计数和 checksum，不包含模型 Prompt 或业务记录。
最终 81 文件 JUnit hash：
`6078f232611ef3b271d25a8fb215fc856fcef7f96b5da06695b7b00607f862c6`。
源码 hash 在收尾时再次核对；测试进程全部结束，暂存区为空，182 个既有/本批变更路径中没有
Runtime 数据、SQLite、日志或构建产物。模式扫描不等于可以识别所有形式的秘密。

## 未完成的整轮门禁

- 未刷新/整合当前上游，未重跑全量 `server/tests/` 或完整 CI。历史 E2 的基线/环境失败记录仍须
  单列，不把本次受影响选择集替代全量，也不推断上游后续是否已经修复那些失败。
- 未操作真实预览器，未追加 Planner、定向模型修复或下游 Agent 调用，未做新的业务表写入。
- E-G1 最近真实结果仍为失败；E-G2、E-R1/E-R2 及隔离分支效果按原固定矩阵独立验收。
- 即使后续一次成功，也只证明该实例，不足以声称统计稳定、消除模型不确定性或已达到 PR 门禁。
- 本次没有 Commit、Push、PR、Merge、部署、审批或发布。

回退仅撤销本批局部诊断/反馈接入及 Schema 去重，保留既有 Recipe/Graph IR、失败工件和调用
账本；不撤销业务写入、不清理提案、不改变活动指针。下一步仍是既定 E2/E3 验收，不进入 V4。
