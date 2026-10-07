# CW10 固定候选隔离效果验收

日期：2026-09-27。结论：**部分验收成立，尚不能判定整体稳定或进入 PR 前收尾。**
本次没有修改生产实现、候选、生成目标或评分断言。没有重新生成、定向模型修复、
写活表、批准 Proposal、发布 Xpert、提交、推送或创建 PR。

## 代码与证据边界

- 真实预览：`modelmirror-meta-planner-cw10-recovery-e`，基线 `09e8a7d6` 加冻结的 187 个变更路径。
- 最新主线集成：`modelmirror-meta-planner-cw10-acceptance-20260927`，基线
  `2bf501461261fb36ccd298b1d71fd57ac16e3e8b`，分支 `codex/meta-planner-cw10-acceptance-20260927`。
- 本次刷新较旧基线前进 20 个提交、141 个路径；直接交集仅 `server/main.py`。
  机械集成后 186 个路径逐字节一致；`main.py` 保留上游四个模型别名。
- 真实调用仍在原预览代码上发生；最新主线副本验证的是离线集成，不将两者合称为最新主线真实调用。
- 干净对照：`modelmirror-meta-planner-cw10-baseline-20260927`，同一主线提交，detached HEAD。
- 所有临时测试、数据库、账本和构建产物均在忽略目录；正式文档不包含夹具记录正文或凭据。

## 固定输入

- 候选 `proposal_346603e15e064b85bf0d0bc22f9b9954` r1，名称“库存检查与状态更新流程”。
- Graph checksum：`00453f0011b92d7dbf8240f3e4136a05e3f9a967f53219e6e7899da62746fe04`。
- Candidate checksum：`d4b9a22aa5bd360bea48425a4dac5e80b3f2a3bc650927746cb2b2780a78a4da`。
- 表 `table_972e625145604c2f9419176b128d863b`，Schema v1，仅授权查询和 `update/status/1`。
- Dataset `xeval_dataset_15f49c7c20d54c0c860b32df6db64b65` v1，6 条用例。
- Dataset checksum：`55b2d57b68e861fa4d6c289d8734da40826ff8f2dccb97c4ed7b795706f239c4`。
- 初始化夹具 checksum：`7f5ecfb0cb2f2a62c127c7d1d7d8c970f574e68b0ca9a26b82bf9ab4579b1472`。
- 图保留两个路由、三个符号场景、两个互斥成功来源和一个明确错误终点，未改成线性串行图。

用户单独批准三种场景各两次、最多四次汇总 completion；仅 OpenRouter 的
`deepseek/deepseek-v4-flash-0731`，无记录分支禁止调用模型。六个用例在真实调用前发布固定。
没有启用 Judge、模型覆盖、工具调用或自动重试。

## 真实执行

运行 `xeval_run_65f76c1cbbd643e283d2dbd61dab8ef4`，由预览器“安全预检并运行”启动。

| 场景 | 次数 | 数据库真实效果 | 模型调用 | 路径/资源/效果 | 文本 contains |
| --- | ---: | --- | ---: | --- | --- |
| 查不到目标 | 2 | 零写入，`RECORD_NOT_FOUND` 终止 | 0 | 全部 100% | 未配置答案指标 |
| stock=2 | 2 | 每次只更新目标 status；revision 由 1 到 2 | 2 | 全部 100% | 每次 50% |
| stock=8 | 2 | 零写入，原值和 revision 不变 | 2 | 全部 100% | 每次 100% |

- 6/6 执行完成，执行错误 0；不等于所有质量指标通过。报告综合分 95.8%，contains 聚合 75%。
- 四次真实 completion 均 HTTP 200、正常 `stop`，Provider 实际计量合计 2,057 tokens。
  报告的 238 个估算 Token 是另一口径，不代替 Provider usage。
- 不确定派发 0；调用账本分别固定六个用例 hash，零记录用例预算为 0，其余各为 1，总预算 4。
- 临时护栏首次拒绝了含未授权 Judge 的界面请求，外发为 0；在界面明确选择“不启用 Judge”后才启动。
  未放宽模型白名单或请求预算。
- 初始化只发生在六个私有 Evaluation Backend；没有复制或修改活表数据。

## 独立效果与恢复核验

除产品报告外，另以 SQLite `mode=ro` 直接读取这六个实例及对应操作账本：

- 六份实例记录 ID 集合两两不交；每例完整业务状态与预先固定预期一致。
- 两条保护记录在所有实例中业务值、revision 均未改变；仅两个低库存实例各有一次
  `controlled.update` 提交，字段仅 status，账本请求 hash 与回执一致。
- 低库存实例的非目标记录前后 checksum 相同，目标修改前后状态与预期一致。
- 预览活表的记录数为 0、受控操作数为 0。
- 重启独立后端后再次审计，结果完全相同，六个完成项未重跑，四次调用账本未增加。
  前后安全回执文件 SHA-256 均为
  `e3fc45c413decdb07b3fe3749e6b384d1e03a913653b98a9e226a7c9c2c1b7e6`。
- Proposal 重启后仍 pending/r1、IR current、Graph 与候选 checksum 不变。

证据文件位于原预览工作树 `.tmp-recovery-e/acceptance-20260927/`：
`real-effects-before-restart.json`、`real-effects-after-restart.json`、
`verify_real_effects.py`。Provider 安全回执在
`.tmp-recovery-e/preview/effect-acceptance-20260927/calls.json`；不包含 Prompt 正文、记录正文或密钥。

## 汇总质量缺口

低库存两次均仅命中两个预定文本片段中的一个，不能在测试后删除断言将其改为通过。
公开报告不保留完整受控写入输出，也未记录逐片段失配详情；不能凭总分声称已确认缺失哪一个词。

已从固定候选和离线相同图执行确认一个上下文缺口：

- 更新后的汇总 Agent 只接收更新前查询记录与更新输出 `matched/affected`。
- 更新节点按既有契约返回影响数量，不返回更新后业务值；候选没有后置查询。
- 汇总 Prompt 要求说明是否修改，但未提供目标新 status，也未在模板中固定该业务含义。
- 对完全不改动的原图进行离线输入捕获，确认低库存输入包含目标标识和 `affected=1`，
  但不含新 status。该诊断在所有固定低库存边界重复成立。

因此，“写入已发生”和“汇总能够说明修改后的业务状态”不能混为一谈。当前证据支持
上下文供给缺口的解释，但不把它当作对四份未持久化完整模型回答的逐字复核。
下一步应先审阅交付契约：若要求回答修改后的具体状态，应由可信写入语义或后置只读查询提供证据，
而不是让 Agent 猜测。是否需要改候选/生成约束须另行确认；本次未修改。

## 离线验证

- 原固定候选：24 项通过，8 个库存边界各 3 次，每项均运行独立基线/候选，合计 48 次 SQLite 执行。
  文本 Agent 是 mock，只证明执行、隔离及状态，不证明模型质量。
- 最新主线重点选择集：193 项通过，包含上述 24 项，不重复相加。
- 临时外发护栏：11 项通过，覆盖每例一次、全局四次、零记录禁调用、未知结果停止与重启拒绝重发。
- 汇总输入诊断：24 项通过，含相同 48 次执行；这是缺口复现，不是答案质量通过。
- 最新前端全量补齐锁定的 `card-replica` 本地依赖后：1,138 passed / 4 failed，152 个文件。
  四个失败在干净相同主线复现（对照 25 passed / 4 failed），涉及两项价格和两项帮助内容断言。
  最初另四个 RPG suite 的缺失依赖错误已在同一完整命令复跑中消除，不计为产品修复。
- 前端生产构建通过，保留既有大 chunk 警告；服务器头/代理测试 11 项通过。
- 帮助图片检查失败，3 个历史未引用资产在干净主线复现，未删除无关资产。
- 125 个变更 Python 文件 AST 语法检查通过；187 个拟交付路径的敏感模式扫描无命中，
  没有 Runtime Store、SQLite、日志、上传件或构建产物；`git diff --check` 通过。
- 最新后端全量完成：8,088 passed / 112 failed / 69 skipped / 4 errors，47 分 51 秒。
  JUnit 共 8,271 项；setup/teardown error 与用例结果可能重叠，不从总数直接推算 passed。
  这是失败结果，不以重点测试通过或主线同败将其改写为全绿。

### 最新主线失败对照

从同一次全量的 114 条带 failure/error 的用例记录提取 108 个函数，在干净相同主线、
相同离线护栏和锁定依赖下运行。对照结果为 19 passed / 110 failed / 2 skipped / 5 errors；
参数化函数会带入其他用例，不能直接用两次 failed 总数相减判断回归。

- 按完整 testcase 身份比对，112 条在首轮主线对照也失败或错误。其中 109 条仅规范化
  临时目录、内存地址和时间数字后错误摘要完全相同；另外 3 条差异为随机临时文件、
  workspace/receipt ID 及执行计时，保留原始 XML，不声称每个底层根因均已查明。
- 两条初次未复现用例进行了原样、独立、有界对照，没有修改生产实现或断言。
  `test_isolated_renderer_has_one_absolute_deadline_and_no_retry` 在两侧均复现
  `manager.called == 0`，与测试 50 ms 绝对期限相关；不是本轮新引入的调用重试。
- `test_writer_fails_closed_for_ineligible_target` 的 `writeback_branch_required`
  参数在集成侧复现 `git_repository_dirty`；干净主线单独三次为两次相同失败、一次通过。
  用例及所用 Coding 实现逐字节一致，属于已复现的基线不稳定性，不修改用户 Git 配置。
- 至此，114 条带失败/错误的记录均有干净主线同败证据；未发现本轮独有的全量红项。
  这不是基线豁免，也不证明测试通过或系统普遍稳定。干净主线没有执行第二次完整后端套件。
- 对照工作树 Git 状态仍干净；所有本次测试命令已结束。恢复后再次以只读方式复核
  六个隔离实例、活表哨兵和四次调用账本，结果未变；原预览 187 个冻结源码路径无漂移。
- 收尾加入本验收文档后，集成工作树共 188 个待提交路径、暂存为空；再次扫描全部路径，
  已知凭据模式和 Runtime/数据库/日志/构建产物路径均无命中，`git diff --check` 通过。

可重放证据均在各工作树的 `.tmp-recovery-e/acceptance-20260927/`，不提交日志：

| 工作树 / 文件 | SHA-256 |
| --- | --- |
| 最新集成 / `full-upstream.xml` | `5c72cdc52766f4f0f08fc744e59c9e6f6bc6d69b1eef1ae5f60fdc3a5a0690a5` |
| 干净主线 / `backend-baseline-selected.xml` | `463d081556c6e3e5733377d7fce002188d8cf4034a94b1b4a5acd2b0de82b081` |
| 干净主线 / `backend-differential-recheck.xml` | `14364d91d838799028a1744992ba3a45d935c0522f9785a18b2ea2bd4bb9a137` |
| 干净主线 / `backend-branch-baseline-1.xml` | `0dcf06a0aa850365d530cee070e981e773b0a9ecdb1e4ffd74bcdf332afee63d` |
| 干净主线 / `backend-branch-baseline-2.xml` | `9ffa0a68e5b9a3d251d920c1b79704788681f551c8e54ef9b3402a8d38e8f642` |
| 干净主线 / `backend-branch-baseline-3.xml` | `30b8c89934102244516e5e66277c31b2cbe72bba2d893a2bd098fb1412049bb7` |

可重放入口如下；运行结果以对应原始日志和 XML 为准。选择集参数另保存在
`baseline-selected.json`，只包含测试路径：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py server/tests/ --junitxml=.tmp-recovery-e/acceptance-20260927/full-upstream.xml
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py server/tests/test_coding_project_writer.py::test_writer_fails_closed_for_ineligible_target server/tests/test_file_output_renderer.py::test_isolated_renderer_has_one_absolute_deadline_and_no_retry --junitxml=.tmp-recovery-e/acceptance-20260927/backend-differential-recheck.xml
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/acceptance-20260927/verify_real_effects.py
```

最后一条在原预览工作树运行，只读 SQLite 和本地 GET，不新增外部请求；本次恢复核验未刷新
远端 main，也未执行 Docker、共享栈或 CI。因此最新基线一词仅指本次已获取并固定的 `2bf50146`。

## 门禁与后续

| 门禁 | 本次状态 |
| --- | --- |
| E-G1 固定分支执行、隔离写入、重启不重复 | 通过；仅限固定图和本次数据矩阵 |
| E-G1 汇总答案断言 | 失败；contains 聚合 75% |
| 最新主线重点回归与前端构建 | 通过 |
| 最新全量与基线差异核对 | 全量失败；差异核对完成，红项均有基线复现证据 |
| E-G2 多路不同任务的真实生成与效果 | 未执行；不得沿用本次汇总授权 |
| E-R1 / E-R2 正式修复入口验收 | 尚未形成完整本轮验收证据 |
| 提交、推送、PR | 不执行 |

本次证明的是固定图和固定数据边界下的可重复效果，不是跨任务生成成功率或统计意义上的普遍稳定。
四次授权额度已经用完；任何新生成或外部模型复测均须独立授权。

建议下一批只解决“写入后汇总能消费可信业务状态”的证据供给契约，先用不含本次字段名/状态词的
离线反例验证，再分别验收 E-G1 答案与 E-G2 泛化。不得把本次答案片段硬编码进通用 Prompt，
不得删断言、扩大自动重试或以人工修过的单图冒充生成稳定性；基线红项应单列处理或由用户明确裁决，
不在 CW10 夹带无关修复。以上是后续建议，不是本次已实施变更或新增调用授权。
