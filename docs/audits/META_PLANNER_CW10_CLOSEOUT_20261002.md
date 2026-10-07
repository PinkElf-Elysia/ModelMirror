# CW10 剩余门禁核对

记录开始：2026-10-02 UTC（开始时本地 UTC-07:00 为 10 月 1 日），后续进展按章节追加。
状态：**固定 r2 隔离效果验收通过，整体门禁未通过，不提交 PR。** 本文保留历史取证与经授权的副本修订；当前 r2 不再修改图、Prompt、授权或评分断言。

最新集成状态见文末“最新主线最终集成核验”；前文各次基线、失败数量与“尚未集成”均为当时记录，不替代最新结果。

## 基线与隔离

- 原生成工作树：`modelmirror-meta-planner-cw10-acceptance-20260927`，HEAD `2bf50146`。
- 新集成工作树：`modelmirror-meta-planner-cw10-closeout-20261002`，分支
  `codex/meta-planner-cw10-closeout-20261002`，HEAD
  `0de311ec8b6c9e728612c76a8266269bfc1018bf`。
- 远端刷新增加 8 个提交、51 个路径；与本轮 199 个变更路径仅交叉 `server/main.py`。
  差异应用前通过 `git apply --check`。198 个路径与原工作树逐字节相同；`main.py`
  同时保留上游 Decisions 新模型逻辑与本轮实现，原本轮差异的反向 `--check` 通过。
- 干净对照：`modelmirror-meta-planner-cw10-baseline-20261002`，同一提交、detached HEAD。
  对照仅安装锁定测试依赖和运行临时脚本，版本控制状态保持干净。
- 不更新原预览服务、不复制连接或凭据、不修改原 Runtime Store。真实生成证据仍对应
  原基线；最新主线集成与离线验证不能冒充最新代码真实模型验收。

## 固定候选与效果

- Proposal：`proposal_62549c93dfb04e939bca8a0a2f2d40f0` r1，pending，未人工修改。
- Graph checksum：`78a5870f518f09edf53bcc44442e375a1f9f5072851a6981d8721e6a39c6427f`。
- Candidate checksum：`eb1ca17bee470ae76e62a6c0eb3c2d09a9d9ba239c440951846328769eb624d2`。
- 不变图在 8 种输入下各重复 3 次：无记录、stock=-1/0/2/4/5/8/2147483647。
  每项运行独立基线/候选实例，共 48 次真实 SQLite 隔离执行；文本 Agent 为 mock。
- 24 项效果验收通过：低库存仅修改目标 status，revision 1→2；库存足够不修改；
  无记录以固定错误终止且不调用 Agent。DEMO-B、DEMO-AA 保护记录不变，两个实例的
  记录 ID 不相交；重新打开 Backend 保留结果，完成项不重跑，权威表哨兵不变。
- 这证明固定图的确定性执行和隔离效果，不证明真实模型回答、跨任务生成率或普遍稳定。

## 汇总证据缺口

保持上述图和初始化不变，只将模拟 Agent 改为捕获实际模型输入，增加独立交付证据检查：

- 12 passed / 12 failed。stock=-1/0/2/4 的三次重复全部失败；其他场景不要求更新值。
- 每个失败均在数据库效果、revision、保护记录和恢复断言通过后发生。
  汇总输入不包含目标新 status“待补货”；它只有更新前记录及 `matched/affected` 回执。
- 原图 `summarize_agent.taskInput` 没有显式请求新值，也没有写后查询输入。
  `rolePrompt` 虽要求区分请求值、回执和查询时点状态，但没有提供缺失的业务值。
- 固定计划 `summarize_result.output_contract` 明确要求“若更新则说明请求值和执行回执”。
  因此这不是事后提高答案要求：计划声明了交付事实，但编译后 Agent 的实际输入没有承接它。
- 这是供给证据缺失的确定性反例，不是模型回答的实测失败。不能凭 `affected=1`
  证明 Agent 已获知更新后的具体字段值，也不能以模型可能猜中代替证据。
- 现有类型/路径/发布预检只证明候选可执行；它们没有证明业务交付所需证据完整。
  保留该缺口，不插入场景专用提示词、不自动加查询、不修改原候选、不删除红断言。

在此缺口明确后暂停真实汇总外发。本批实际外部模型调用为 0；没有复用已消耗的生成额度。

## 检查记录

所有测试原件位于本工作树忽略目录 `.tmp-recovery-e/closeout-20261002/`。

| 检查 | 状态与证据 |
| --- | --- |
| 写入、隔离、Headless、上游 Decisions 重点测试 | 129 passed，`focused-recheck.xml` |
| 固定候选隔离效果与恢复 | 24 passed，`fixed-candidate.xml` |
| 固定候选汇总输入反证 | 12 passed / 12 failed，`fixed-summary.xml` |
| 前端生产构建 | 通过，`frontend-build-recheck.log`，保留既有大 chunk 警告 |
| 前端全量 | 1181 passed / 3 failed，154 个测试文件，`frontend-full.log` |
| 前端干净主线对照 | 相同 3 个失败、26 passed，`frontend-baseline.log` 位于对照工作树 |
| 服务器头与代理 | 11 passed；因 Vitest 失败未进入原 `&&` 后命令，已独立执行 |
| 帮助截图 | 7 处失败，在干净同版主线原样复现 |
| 改动 Python 语法 | 135 个文件 AST 与 `py_compile` 均通过，字节码只写忽略目录 |
| 拟交付路径与已知凭据签名 | 含本审计记录共 200 个路径，无 Runtime/数据库/日志/构建产物，无已知凭据签名命中；不宣称此扫描覆盖所有敏感信息形式 |
| 后端全量 | 8299 passed / 136 failed / 69 skipped / 4 errors，3115.65 秒，`backend-full.xml` |
| 后端干净主线差分 | 138 个失败/错误测试身份在同版干净主线均可复现；不是全量通过 |
| MCP 时序单项复跑 | 两侧各 5 次，均为 4 passed / 1 failed，保留全部结果，不以重跑覆盖首轮失败 |

前端 3 个红项为 `ModelCard.test.ts` 的时段价格展示、`tokenPricing.test.ts` 的时段价格选择、
`helpContent.test.ts` 的历史 RPG 文章章节要求。帮助截图红项为两个上游 PNG 资产的格式/尺寸
及三个历史未引用资产。均不在本轮修改范围，不以“基线同败”改写为通过。

后端全量在本机 Windows 的断网隔离运行器中执行。干净主线先复跑历史失败集合
（109 failed / 20 passed / 2 skipped / 5 errors），再对未覆盖项补跑
（29 failed / 2 passed）。仍未复现的 MCP 短时钟测试做了上述两侧原样重复，最终覆盖
全部 138 个失败/错误身份。136 次 failure 与 4 次 error 并非 140 个独立测试身份。

其中 113 个归一化诊断完全相同；其余保留原诊断，包含随机路径、临时文件名、回执 ID
等差别。该对照只支持“同一环境的干净主线也失败”，不能推导所有底层原因均已排除，
更不能替代目标平台的绿色 CI。没有为消除红项修改测试、延长时限或放宽安全边界。
MCP 单项在全量中为 `remaining() == 0` 得到约 3ms；其测试与实现文件在两侧 SHA-256 相同。

核心复核命令：

```text
python -B -u .tmp-recovery-e/run_offline.py server/tests/ --junitxml=.tmp-recovery-e/closeout-20261002/backend-full.xml
python -X pycache_prefix=<本任务忽略目录>/pycache -m py_compile <135 个变更 Python 文件>
npm.cmd run build
npm.cmd run test:run
node --test server-headers.node.mjs
npm.cmd run verify:help-images
git diff --check
```

Python 使用既有 `C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe`；离线运行器禁用
dotenv、移除 Provider/网关凭据环境、阻断非回环网络并隔离测试 Store，不使用业务存储。
前端命令在 `client/` 运行。所有失败原件和补跑结果均保留。

验证过程中的工具/环境失败原样保留：首次重点命令引用了不存在的
`test_openrouter_decisions_api.py`，未执行任何测试；校正为实际
`test_openrouter_decisions.py` 后完整重跑通过。第一次前端构建缺少仓库内
`card-replica` 的本地依赖；按该目录现有锁文件离线安装后原构建命令通过，没有改动锁文件。
后端 pytest 已完成并落盘后，两个 PowerShell 日志尾部读取进程因巨大参数化输出卡住；
确认没有测试子进程后仅停止这两个读取器。终态以完整 JUnit 与 pytest 汇总为准，
不把读取器被停止的退出状态记为测试取消。

## 门禁与回退

本批不修改生产逻辑。保留原工作树、生成回执和预览服务即可返回原验收环境；新集成副本
不替换正在运行的服务。没有 Commit、Push、PR、Merge、共享栈操作、活表写入、审批或发布。
最终核对固定夹具、Proposal Store 和活表数据库文件均与开始时 SHA-256 一致，数据库没有
遗留 WAL 文件；干净主线对照无版本控制改动，集成工作树暂存区为空，`git diff --check` 通过。

当前不可把“候选校验通过”“隔离写入通过”和“汇总质量及生成稳定性通过”合并为一个结论。
继续前应独立收口交付证据完整性，并保留真实汇总、跨任务泛化和正式修复入口的各自验收。
不得以重新生成直到碰巧通过代替这一门禁。

下一步边界：先定位计划交付事实到 Recipe/Graph Intent 的输入供给约束，区分写入请求值、
执行回执和写后状态证据；不扩大写回执、不自动追加查询，也不针对 DEMO-A 注入答案。
保留本候选作为失败夹具，并在不同字段和值的反例上验证修复，再进入另行授权的真实验收。

## 后续收口：编译器请求证据

本节为上述只读核对之后、用户要求“开始收口”授权的修复批次。上文测试与失败原件均保留，
不能用新测试覆盖旧候选的 12 个红断言；原始 Proposal、业务表及预览没有更新。

修改限定为编译证据派生、Adapter 读取、Headless 兼容、模型契约投影及针对性测试。
生产与测试共 6 个路径：`write_delivery.py`、`node_adapters.py`、`meta_planner_v2.py`、
`headless_authoring.py`、`generation_recipe.py`、`test_meta_planner_write_delivery_contract.py`。
超过默认 5 文件的原因是旧候选严格往返比较也必须同步兼容，不能让新编译器使旧候选无法加载。
文档与帮助更新单独为 3 文件同步批次。不改变业务表 Backend、Runner、权限、Schema、SSE 或调用预算。

### 收敛依据

- 先验证的 Recipe 阶段复制方案虽通过 201 项测试，但复审发现 Headless 改值会使复制内容过期；
  已撤去该方案，不以绿测作为它成立的证据。
- 最终在共同 Native 编译阶段从已解析 Graph Intent 的显式回执来源派生。原始模板不变，
  反编译移除可核验附录，后续修改写入值时重新生成。没有场景专用值或新增业务连线。
- 防注入测试保留 `{{user_input}}` 等固定业务字符串原值，不把它解释为 Runtime 模板。
  固定请求、执行回执和写后状态分别断言；不同字段的真实 SQLite 隔离执行不依赖模拟回答“猜中”。
- 旧候选兼容专项先复现 `headless_lossy_conversion`，然后只处理新编译器增加的附录投影。
  旧候选加载与预览不写入，显式 Apply 后才增加一次 revision；新附录篡改仍拒绝。
- 新接入初次测试的 34 个失败来自误用 V2 内部输入绑定读取 V3 来源字段，已改为解析后的
  Graph Intent 来源，原命令复跑通过。测试过程中没有放宽断言、忽略错误或调用外部模型。

### 当前证据边界

- 针对性与固定候选副本编译：77 passed，`delivery-compiler-focused-recheck.xml`。
- 六文件回归：207 passed，`delivery-compiler-regression.xml`。
- 旧候选及编辑专项：7 passed，`delivery-legacy-recheck.xml`。
- 最终受影响集成回归：1802 passed / 0 failed / 0 skipped，907.104 秒，
  `delivery-compiler-integration.xml`。覆盖全部 `test_meta_planner*.py` 和 Meta Agent、
  NodeContract、Authoring、Publish、Evaluator、Evolution、App、typed values/AI 共 86 个测试文件。
  其中包含本批旧候选兼容修订之后的全部交付证据和 Headless 测试，不仅是此前原型的绿测。
- 六个修订 Python 文件 `py_compile` 通过；前端生产构建通过，`delivery-frontend-build.log`。
- 前端受影响界面与帮助回归：105 passed / 1 failed，`delivery-frontend-tests.json`。
  红项仍为 `use-rpg-memory-palace` 缺少“适用对象”章节，其文章与测试文件均与同版干净主线
  SHA-256 一致，不在本批修复范围；不描述为全绿。
- 201 个待交付路径没有 Runtime、数据库、日志或构建产物，已知凭据签名扫描无命中。
  该有限模式扫描不是全部敏感信息审计；暂存区为空，`git diff --check` 通过。
- 再次核对固定夹具、原 Proposal Store、原活表数据库 SHA-256 均与开始时一致，未出现 WAL。
- 这些仅证明受限证据的机械送达、反编译/编辑一致性与隔离效果，不证明外部模型回答稳定性。
- 原预览仍为旧代码；本修订没有重启服务、修改原候选、调用 Provider、审批、发布或创建 PR。
- 修订后的完整后端全量尚未重跑；上文全量失败仍是独立门禁，基线同败不代表绿色 CI。

回退可停止新证据派生，但保留 `plannerWriteRequestContextV1` 的读取和 Headless 兼容；
已保存 Workflow 仍按其自身 Prompt 执行，不迁移数据，不自动撤销写入或重新派发调用。

本批结论：**编译器证据供给与编辑一致性修订通过受影响回归；整轮 PR 门禁仍未通过。**
下一步应在同版独立预览中检查固定候选的重新编译 Diff，明确确认 Apply 后固定新 revision，
再按单独授权进行真实汇总与浏览器验收。不要求重新生成业务图，不把原失败候选悄悄替换成
新样例；本修订的全量后端、绿色 CI、完整教程重放与真实模型证据仍各自保留门禁。

## 剩余门禁复核

本节记录用户要求“开始收口剩余门禁”之后的终态，不替代上文原始失败记录。
工作分支仍为 `codex/meta-planner-cw10-closeout-20261002`，HEAD 为
`0de311ec8b6c9e728612c76a8266269bfc1018bf`。只读查询远端 main 仍为同一提交，
没有新的上游提交需要集成。没有新增生产代码修订。

### 最终本地回归

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 修订后全部 `server/tests/` | 8317 passed、136 failed、4 errors、69 skipped | `backend-final.xml`，3169.229 秒 |
| 失败身份与干净主线对照 | 138/138 均已有同版基线失败记录 | `backend-final-comparison.json` |
| 前端全量 | 1181 passed、3 failed，154 个测试文件 | `frontend-final.log`，515.72 秒 |
| 前端服务响应头 | 11 passed | `frontend-headers-final.log` |
| 帮助图片检查 | 7 项失败，与前次一致 | `help-images-final.log` |
| 变更 Python 语法 | 136 个文件通过 | `final-static.json` |
| Diff、交付范围与有限凭据签名扫描 | 通过；201 个路径、暂存区为空 | `final-static.json` |

完整后端 JUnit SHA-256：
`77eaf288fb13f455130bdf8330d8af9650c9eaee21474955babc711841925315`。
最终全量比前次增加 18 个通过项。失败身份不是完全相同：两个 Coding 测试本次失败、
两个文件渲染/MCP 时序测试本次通过；前者也已有干净基线失败回执。没有未被基线覆盖的新失败，
但不能据此宣称所有失败根因都已排除，更不能把 Windows 断网测试结果称为全量通过。

前端三个失败仍是 `ModelCard` 的 UTC 分时价格、`tokenPricing` 的生效时间和
`use-rpg-memory-palace` 缺少“适用对象”章节。相关实现、测试和文章均与同版干净主线
SHA-256 一致。帮助图片失败仍是两个非 PNG 文件及其尺寸、三个未引用资产。
由于 `test:run` 使用 `&&`，Vitest 失败后未执行响应头测试，已单独原样补跑，未遗漏也未代替红项。

全量测试已正常终态并落盘；读取 46 MB 参数化日志末尾的 PowerShell 读取器随后卡住，
中断的仅为该读取器，不是 pytest。测试结论以完整 JUnit 和执行会话终态为准。

### 上游 CI 独立门禁

同一 main 提交的 [Quality run 36964920455](https://github.com/PinkElf-Elysia/ModelMirror/actions/runs/36964920455)：

- Backend quality：failure，失败步骤 `Run remaining backend test suite`。
- Frontend quality：failure，失败步骤 `Type-check frontend`。
- Windows Project Host：success。
- AI Research 的 `Candidate routing (not trust authority)` 另为 failure，其后续范围检查 skipped。

此处只核验了检查状态与失败步骤；CLI 未返回可用失败日志正文，没有据此认定远端失败根因
与本地完全相同。尚无本工作分支的 CI，不触发远端任务，不以主线检查代替本分支验收。

### 同版独立预览

新预览 `http://127.0.0.1:15509`，后端 `16509`，使用当前集成代码及已验证生产构建。
原 `15489/16489` 服务保持原样。新 Runtime 从空目录初始化，仅使用已冻结合成夹具；
没有读取、复制旧连接凭据，也没有复用旧 Runtime。外网、运行、写记录、审批、发布和
未授权 Apply 均关闭。

- 副本 Proposal：`proposal_05b916557b914a9fa46d4fdd7312d6a4`，源为
  `proposal_62549c93dfb04e939bca8a0a2f2d40f0` r1。
- 源候选 checksum：`eb1ca17bee470ae76e62a6c0eb3c2d09a9d9ba239c440951846328769eb624d2`。
- 浏览器实际载入候选、显示两个路由/三个场景/两个成功来源，打开“预览元数据变更”。
- 仅把副本名称加上“隔离验收副本”标识。预览正常返回可应用，尚未点击确认。
- 两次真实后端预览得到同一 preview checksum：
  `e2f4329b75f14d0ffec65889dc586454d04a7a13c659d9a47cb53fcc5b5ed561`。
- 编译后候选 checksum：`da2ccb3b97c5e0a1047a2ccd302d05080adc327cf21a27eb790096ce16c3c02c`。
  节点身份及全部连线不变，唯一节点配置差异是 `summarize_agent` 的 `taskInput` 和
  `plannerWriteRequestContextV1`。附录准确对应固定请求 `status=待补货`，没有修改原模板。
- 预览前后副本均为 r1，持久化候选 checksum 未变；合成表仍为 Schema v1、零记录。
- 帮助首页搜索“分支”能够到达实际教程，同版页面正确保留“待独立预览验收”和请求值
  不等于写后状态的限制。只完成加载与预览步骤，不能宣称 Apply、评测和完整教程重放通过。
- 浏览器截图已在工具回显中检查；本会话截图写文件受权限限制失败，未伪称有持久化截图。

安全摘要保存在忽略目录 `closeout-preview/seed-receipt.json` 和
`closeout-preview/preview-verification.json`。这些不是生产 Store 或交付文件。
再次核对原 Proposal Store、原 Agent Table 数据库和冻结夹具哈希与本批开始一致。

**结论仍为未达到 PR 提交门禁。** 已完成本修订最终全量的执行与失败归属对照，完成同版
候选加载和无副作用编译预览。剩余：绿色质量检查、明确授权后对副本 Apply、固定新 revision
的真实隔离汇总、完整教程与人工验收。真实汇总拟为无记录/stock=2/stock=8 各重复两次，
最多四次汇总调用；该追加授权尚未收到，本批真实模型调用为零。

没有 Commit、Push、PR、Merge、共享栈操作、原候选修改、业务写入、审批或发布。
回退仍为保留原预览并停止新隔离预览；无需迁移或撤销任何业务数据。

### 续核：上游失败正文与强制类型检查

用户再次要求收尾后，本批只补充只读归因和本地验证，没有修改生产实现。

- `npm.cmd run typecheck -- --force` 实际退出 0，日志为
  `frontend-typecheck-force.log`。这是当前工作树强制类型检查通过，不依赖此前增量缓存；
  当前机器已具备此前按锁文件安装的 `card-replica` 本地依赖，不能等同于干净 CI 环境。
- 通过 GitHub jobs 日志接口取得了前次 CLI 未返回的失败正文。上游
  [Frontend quality](https://github.com/PinkElf-Elysia/ModelMirror/actions/runs/36964920455/job/110706464162)
  报告 `experiments/ai-rpg-engine/card-replica/src/` 无法解析 `react`、
  `react-markdown`、`react-dom/client`、`react/jsx-runtime` 及后续类型错误。
  同版 Quality 配置只在 `client/` 执行依赖安装，没有安装该实验目录依赖。
  这定位了依赖解析缺口，但本批未修改 CI，也未宣称全部后续类型错误均由此解释。
- 上游
  [Backend quality](https://github.com/PinkElf-Elysia/ModelMirror/actions/runs/36964920455/job/110706464218)
  的实际终态为 `6 failed, 6930 passed, 32 skipped`，880.23 秒。
  两个 `test_multimodal_chat_foundation.py` 失败明确为目录版本断言：预期
  `modelmirror-audio-contracts-2026-09-25-transcribe`，实际
  `modelmirror-audio-contracts-2026-10-01-mai21`。
  另外四个是 `test_provider_chat_structured_output.py` 中
  `test_actual_rpg05_compiler_schema_is_accepted` 的 legacy/keyed 与 action/query
  组合，均在外部 Node 编译子进程非零退出；尚未取得更深层 stderr，不能推定具体修复。
- 上述失败测试、实验代码及 Quality 配置不在 CW10 待交付变更范围内。本批不扩大范围
  修复 RPG、音频目录或 CI，也不把主线失败豁免成成功。远端 Ubuntu 六项失败与本地
  Windows 隔离运行的 138 个失败身份不是同一统计，必须分开保留。
- 日志补取曾出现一次网络 EOF；随后只读重试取得版本差异。没有保存或复述临时签名下载地址，
  没有触发工作流、推送、重新派发测试或修改 GitHub 状态。
- 浏览器再次核对副本仍为 r1，停在“确认类型化变更”预览；尚未执行 Apply。
  新预览没有可调用 Provider，不能复用原预览的连接或生成额度。副本 Apply 与最多四次
  隔离汇总已再次列为精确范围等待用户答复，本批真实模型调用仍为零。

剩余门禁不因上述定位而自动关闭。下一步按两条独立路径推进：取得明确授权后完成
固定候选副本的 Apply、真实隔离汇总和教程重放；上游质量失败由对应范围单独处理，
随后再核验更新后的基线和本分支。没有为取得绿色结果删除测试或放宽断言。

### 授权后的副本 Apply 与恢复

用户明确“授权推进”后，在真实预览器对已展示 Diff 的副本执行一次“确认应用”。

- `proposal_05b916557b914a9fa46d4fdd7312d6a4` 从 r1 增至 r2，保持 `pending`，
  `ir_state=current`，`receipt_count=1`；未批准 Proposal、未创建 Xpert 草稿或发布版本。
- 应用后的 candidate checksum 与预览一致：
  `da2ccb3b97c5e0a1047a2ccd302d05080adc327cf21a27eb790096ce16c3c02c`。
  Graph checksum 为 `5894f9932b6f83815d3257afb4a281f46eb2c3d5c0b9864d76d48cba9c7ffaeb`。
- 节点 ID 与连线未变，唯一节点配置差异仍是 `summarize_agent` 的校验后请求附录。
  完成后关闭副本 Apply 临时入口，不再次修改图或 Prompt。
- 后续核对发现新预览 15509/16509 已无监听，只恢复这两个任务端口，未停止其他服务。
  恢复后 r2、candidate checksum、`pending/current` 与单条 receipt 均保持一致。
- 已通过 `verify_applied.py` 固定 `applied-fixed-candidate.json` 和安全
  `apply-verification.json`。原候选 Store、原活表、旧冻结夹具哈希全部保持一致，
  新预览合成权威表仍为零记录。
- 用户已授权最多四次 OpenRouter / `deepseek/deepseek-v4-flash-0731` 隔离汇总，
  无记录、stock=2、stock=8 各两例，无记录不得调用模型；不得重新生成、自动重试或写活表。
  截至本节记录，真实模型调用仍为零。

新预览的凭据不从旧 Runtime 复制。仅为用户配置开放管理员配对和单个 OpenRouter
连接保存，继续保留原认证、CSRF 和 SSRF 校验；配对信息只在新 Runtime 的忽略目录内，
未读取或输出旧配对密钥和 Provider 凭据。

核查设置页实现发现：必须先测试连接才可保存，保存后还会自动复测。因此“只保存、
无需测试”的先前说明不适用于当前 UI，已向用户更正，并单独请求最多两次
`GET https://openrouter.ai/api/v1/models` 的精确许可。未伪造测试成功、未绕过保存前置条件；
该补充许可收到前不执行目录查询，也不进行四次汇总。

本节只完成副本 Apply 与持久化恢复门禁，不能据此宣布真实汇总、完整教程或 PR 门禁通过。

用户随后明确批准上述两次连接目录查询。仅在任务预览的忽略目录增加设置请求计量护栏：
保存前和保存后各最多一次，只能访问现有 SSRF 校验生成的 OpenRouter HTTPS 固定地址，
不允许重定向、completion、目录刷新或第三次请求。派发前持久化计数；结果不确定即停，
重启不得重发。八项离线护栏检查通过，未访问 Provider。

仅重启已核验的 16509 任务后端加载该授权，15509 配置页与配对入口可用。用户需亲自输入
新预览的配对信息与 OpenRouter Key；没有从旧运行目录搬运凭据。截至护栏就绪时，目录
查询 0/2、汇总 0/4，仍未派发真实模型调用。授权已经确认，不再以“未授权”作为后续阻塞；
当前待用户完成新连接配置。

### 连接配置临时入口误拦修复

用户明确本次“当前仅开放无副作用预览”是页面错误提示，原授权不变。
取证确认新预览的管理员配对曾成功，连接目录回执仍为 `calls=[]`，并非额度耗尽。

- 根因为任务临时 `server.py` 的两个独立条件重叠：保存前测试路径
  `/api/router/connections/test` 先获得许可，随后又匹配已保存连接的
  `/api/router/connections/{id}/test` 分支，覆盖为拒绝。
- 离线抽取实际 middleware 函数执行，不加载主应用或 Provider，精确复现
  保存前测试返回 403。将后一个条件改为 `elif`，不改变认证、CSRF、SSRF、
  两次目录调用上限或其他操作许可。
- 15 项离线入口用例通过，含正确的新连接/已保存连接测试和未授权生成、评测、
  数据写入、重复创建连接、错误连接 ID、目录刷新与再次 Apply 拒绝。
  原八项离线计量、重试拒绝及不确定恢复检查亦通过。离线测试没有外部调用。
- 仅重启经端口、PID、父进程和命令行核验的任务后端 16509。经前端 15509
  发送无凭据空测试请求，实际返回 `401 admin_session_required`，证明临时
  误拦已解除且生产管理员认证仍生效；健康检查为 `ok`。
- 重启后内存管理员会话失效，用户需自行重新配对；没有读取或显示配对信息及 Key，
  没有代替用户提交凭据。没有刷新用户当前表单，以免丢失未保存输入。
- 本次改动仅为忽略目录的临时脚本、其离线测试和本文；没有修改产品实现。
  `git diff --check` 通过。当前目录查询仍为 0/2、隔离汇总 0/4；尚未完成
  真实连接配置或隔离汇总，不据此宣布剩余验收门禁通过。

### 固定 r2 的真实隔离效果验收

用户完成新预览配对和连接配置后，实际目录探测为两次 200，分别为保存前与保存后；
两次授权均已消耗，没有刷新目录或追加探测。随后按已批准范围，在 15509 的正式
Evaluator 页面选择固定副本 r2、不可变数据集 v1、无基线、快照模型、不启用 Judge，
重复次数 1、并发 1、超时 300 秒、每例模型上限 1、工具上限 0，点击一次运行。

- Proposal：`proposal_05b916557b914a9fa46d4fdd7312d6a4` r2，候选 checksum
  仍为 `da2ccb3b97c5e0a1047a2ccd302d05080adc327cf21a27eb790096ce16c3c02c`。
- Dataset：`xeval_dataset_c1e9c180e9614ba4a8e20f7828b8ebf5` v1，
  六例已包含三场景各两次，因此不再设置运行重复倍数。
- Run：`xeval_run_05f3a338f95845ffa2097bd41d9cd94d`，6 完成、0 失败；
  `workflow_effect_match` 与 `workflow_path_match` 均为 100%。
- OpenRouter / `deepseek/deepseek-v4-flash-0731` 真实 completion 4 次，均为
  HTTP 200、正常 stop 和完整流结束；Provider usage 合计 4,228 Token。
  没有不确定请求或自动重试。页面的估算 Token 566 是另一统计，不能冒充实际计量。

| 固定场景 | 次数 | 模型调用 | 独立数据库核对 |
| --- | ---: | ---: | --- |
| 无 DEMO-A | 2 | 0 | 按 RECORD_NOT_FOUND 终止；无写入 |
| DEMO-A stock=2 | 2 | 2 | 每例只更新 status 为待补货；revision 1 到 2 |
| DEMO-A stock=8 | 2 | 2 | 不更新，status 正常、revision 1 保持不变 |

六个私有 Backend 中共 16 个互不重叠的记录 ID。各例 DEMO-B 与 DEMO-AA 的完整
业务字段及 revision 均未变化。只读 SQLite 核对确认：每个实例恰有一条
`evaluation.initialize` 账本，只有两个 stock=2 实例各有一条 `controlled.update`；
对应回执 affected=1、replayed=false。新预览权威表仍为零记录，原预览 Proposal Store
与 Agent Table 文件哈希保持不变，副本仍为 pending/current、r2、单条 Patch receipt。

任务临时护栏只允许固定六例和四次汇总，派发前持久化计数；缺失记录例为零预算，
模型、HTTPS 地址、请求方法、目标 revision 和隔离身份均被约束。完成后仅重启
16509 任务后端。前后验收摘要完全相同：运行 checksum
`f2a1c9ef56e499981e9469956e4d392a747000ba3ec4d0304955866b30d771c1`，
调用账本 checksum `a2222d83f3a7f51b02c4a481464f65589aac0a65421de6a8e33c1ba652f3d0de`。
没有新增运行、重复调用或重复写入。

验收准备中修正了两个临时脚本假设，没有修改产品代码、候选、Prompt 或断言：
候选比较改用系统既有的候选投影，排除 Proposal 审计元数据；独立数据库核对采用
源码定义的 `controlled.update` 账本名称，而非旧 `update`。安全结果保存在忽略目录
`closeout-preview/effect-acceptance/` 的 before/after-restart 摘要及调用账本。
浏览器报告和更新分支回执已实际查看；截图已捕获，但文件保存被环境权限拒绝，
不将其记为已保存的截图附件。

**本次验收的边界与剩余项：**

- 证明的是这个固定候选的三种分支、隔离写入效果、计量与已完成项重启不重放；
  不是重新生成成功率、多目标泛化、Insert/Delete 全覆盖或中断请求恢复的真实证据。
- 六例未配置 resource_reads 断言，报告诚实标记资源证据 missing 6；不能将其
  写成资源指标通过。独立数据库核对不替代该指标。
- 写入评测按设计隐藏模型正文，本批没有添加答案文本指标，故四次正常 completion
  不等于汇总文案正确性或答案质量通过。
- 未重跑或修复前述全量、上游质量失败，也未完成完整教程验收。因此仍不能判定
  整体 PR 提交门禁已通过。没有 Commit、Push、PR、审批、发布或共享栈操作。

### 本批文档与只读教程收口

用户要求“开始收口”后，本批不再调用 Provider、改候选、Apply、运行评测或修改产品代码。
在同版 15509 正式页面按以下顺序重放：帮助中心搜索“分支”并打开指南；进入 AI 工作流
生成器；确认固定 r2 的“控制流静态证据”包含 2 个路由、3 个场景和 2 个成功来源；点击
“评测候选”；选择“运行报告”及既有六例运行；分别查看无记录、库存 2、库存 8 的路径和
效果回执；在“我的评测集”展开“逐例表初始化与写入效果”中的库存 2 用例。

真实控件为“预览元数据变更”“控制流静态证据”“写入效果证据”；初次加载候选期间的
旧保存按钮在 Headless 状态加载完成后被预览入口替换，未将瞬态页面误报为保存协议回退。
展开用例核对了三条合成记录、Schema v1、update_status、applied、影响 1 行及变更前后字段，
没有点击应用记录、保存草稿、发布版本或安全预检并运行。草稿 r3 与固定 Dataset v1 是
不同身份，文档明确不使用当前草稿代替历史版本证据。

本批修订仅限帮助文章、本任务卡和本审计三份文档。帮助文章新增已有报告查看步骤及
资源 missing、隐藏正文、估算 Token 和请求值附录的边界；纠正固定场景仍“待验收”的
过期文字，同时保留其他修复入口尚未同版完整重放的事实。未将整篇教程核验日期提升，
未声称已有持久化截图或真实生成稳定性证据。

### 最新上游与未关闭门禁

本批仅执行 fetch，不 merge/rebase。`origin/main` 已从 `0de311ec` 前进到
`8c2a0120226be26f05c875f81c122589b216525d`，为 #398 的 Ling 模型目录与音频适配变更。
14 个新增或修改路径与本任务 201 个待交付路径没有直接交集；这不是无回归证明，最新
基线集成及集成后测试仍待完成，已验收预览继续固定原源码。

- 最新主线 [Backend quality](https://github.com/PinkElf-Elysia/ModelMirror/actions/runs/37095495956/job/111124451206)
  为 6 failed、6,931 passed、32 skipped，884.14 秒。两项音频契约版本断言预期
  `modelmirror-audio-contracts-2026-09-25-transcribe`，实际为
  `modelmirror-audio-contracts-2026-10-02-deepgram-flux`；四项 RPG05 Schema 用例仍在
  Node 编译子进程非零退出，本批没有取得足以证明更深根因的 stderr。
- 最新主线 [Frontend quality](https://github.com/PinkElf-Elysia/ModelMirror/actions/runs/37095495956/job/111124451271)
  仍在 card-replica 出现 React、react-markdown、react-dom/client、react/jsx-runtime
  解析失败及后续类型错误。不推定所有后续错误只有这一个原因。
- 该 CI 的 Windows Project Host 与 readiness 检查成功，不能代替上述失败门禁。
  未触发 CI、修改 Quality 配置或扩大范围修复音频/RPG；也未将上游失败豁免为通过。
- 同源完整回归的现有失败、帮助图片缺口、最新基线集成、完整修复教程与截图证据仍
  未关闭。固定 Update 分支验收不替代 Insert/Delete、更广目标生成或汇总答案质量。

结论仍是“局部验收成立、整体提交门禁未通过”。下一步应按范围分别处理上游质量问题，
再固定新基线完成集成验证；不得为得到绿色结果在 CW10 中追加无关补丁或重复付费试运。

### 本批验证回执

- 运行 `node node_modules/vitest/vitest.mjs run --configLoader runner --maxWorkers=1
  --fileParallelism=false src/content/help-center/helpContent.test.ts
  src/pages/HelpCenterPage.test.tsx src/components/meta/MetaPlannerV2.test.tsx
  src/components/evaluations/EvaluationWriteCases.test.tsx`：74 passed、1 failed。
  失败为既有 `use-rpg-memory-palace` 文章缺少“适用对象”，不在本批修改范围。
- 核对同一结构规则时发现新增报告说明会超过编号步骤上限，已改为报告阅读要点。
  本篇单独检查为 8 个必需章节、8 个编号步骤、6 个证据边界通过；随后完整复跑上述
  四文件，仍为 74 passed、1 failed，19.36 秒。没有删测试或修改断言以遮盖失败。
- `npm.cmd run build` 通过，保留现有大 chunk 警告。新构建在 15509 的正式帮助页
  已显示报告查看要点及最新局部验收边界，未更新整篇指南的旧全流程核验日期。
- `node scripts/verify-help-images.mjs` 仍失败 7 项：模型指南两张非 PNG 图片各自
  另有宽度问题，以及三张未引用的旧资产。本批没有改这些文章或删除资产。
- `verify_effect_result.py after-restart.json` 以只读 SQLite 和本地 GET 复核通过：
  六实例、16 个不同记录身份、两次隔离更新、保护记录不变、权威表零记录、Proposal
  pending/current r2、调用账本与冻结摘要一致。新增调用与运行均为零。
  首次在只读沙箱中打开 SQLite 失败；取得受限提权后用完全相同脚本复核成功，数据库
  连接仍为 `mode=ro`。未据沙箱错误推断数据库损坏，也未改业务数据或绕过断言。
- 本批只有文档变更，不重跑已有约 53 分钟的后端全量，不将之前全量结果改记为通过。
  无 Commit、Push、PR、审批、发布、共享栈操作或新增外部调用。回退本批仅撤回三份
  文档的本次增量，不改变候选或持久化业务数据。

## 最新主线最终集成核验（2026-10-02 至 10-03）

### 集成范围与原证据保护

- 重新 fetch 后固定 `origin/main@8c2a0120226be26f05c875f81c122589b216525d`。
  创建独立工作树 `C:\tmp\modelmirror-meta-planner-cw10-final-20261002`，分支
  `codex/meta-planner-cw10-final-20261002`，没有合并进脏主工作区或创建提交。
- 从 `codex/meta-planner-cw10-closeout-20261002@0de311ec` 集成 201 个待交付路径，
  写入前确认与新增 14 个上游路径无交集，写入后逐文件 SHA-256 一致。
  测试前再次核对 201 个文件均未漂移；本批新增持久化修改仅为本文和任务卡。
- 新树没有复制连接、凭据、运行 Store 或数据库。集成不替换旧树源码或 15509/16509
  预览数据；10 月 3 日原样恢复旧服务的记录见下。
  只读复跑旧树 `verify_effect_result.py after-restart.json`，固定六例、16 个记录身份、
  两次隔离更新、保护记录、零记录权威表、pending/current r2 与冻结回执全部一致。
- 上述真实验收仍绑定旧预览源码。新主线集成、离线测试及字节一致核对不能冒充新代码
  再次真实生成或调用证据。本批新增 Provider 请求、评测运行、业务写入与审批均为零。

### 本地依赖与最新主线对照

只使用现有 lockfile，在新树 `client`、`server/orchestration_worker`、
`experiments/ai-rpg-engine` 和 `experiments/ai-rpg-engine/card-replica` 分别执行
`npm.cmd ci --offline --ignore-scripts --no-audit --no-fund`。未增加依赖或改锁文件。

- 四项 RPG05 Schema 用例初次失败的本地 stderr 为 `ERR_MODULE_NOT_FOUND: ajv`。
  安装 RPG 根目录锁定依赖后，同组四项通过；没有修改源码或测试断言。
- 前端初次 `npm.cmd run typecheck` 在 card-replica 缺少 React 等依赖并出现后续类型
  错误。安装该目录锁定依赖后，同一命令通过，生产构建亦通过。
- 本地 Node 为 24.18.0，现有 Quality CI 使用 Node 22 且未安装这两处 RPG 依赖。
  这里只证明本地失败可由准备既有依赖解除，不宣称已修复 CI 或确认 CI 只有这一根因。
- 两项音频目录断言仍预期 `modelmirror-audio-contracts-2026-09-25-transcribe`，
  实际为 `modelmirror-audio-contracts-2026-10-02-deepgram-flux`。
  在最新干净主线独立对照树复跑，仍为 2 failed、0 errors。
- 前端全量三项失败在最新干净主线原样复现，针对三文件为 26 passed、3 failed。
  帮助图片在两树均有相同九项问题：两张非 PNG、对应宽度错误及五张未引用旧资产。
  未删除资产、改文章结构、改价格逻辑或放宽断言以取得绿色结果。
- 对照树为 `C:\tmp\modelmirror-meta-planner-cw10-final-baseline-20261002`，
  detached HEAD `8c2a0120`；只安装锁定依赖并使用忽略目录测试脚本，不修改主线源码。

### 验证回执与剩余门禁

当前新树离线回执保存在忽略目录 `.tmp-final-20261002/`。后端测试启动器清除继承的
凭据类环境变量、禁止 dotenv 加载，全部 Store 与 pytest 临时目录独立，Python socket
只允许回环连接；模拟测试不是 Provider 实测。沿用 CI 将旧运行契约分进程执行。

| 命令或检查 | 本批结果 |
| --- | --- |
| 15 个受控写入、隔离、Headless、NodeContract 与工作流文件 | 321 passed，`focused.json` |
| `test_workflow_run_contract.py` 独立进程 | 7 passed，`legacy-final.json` |
| `server/tests/ --ignore=server/tests/test_workflow_run_contract.py` | 未完成：约 70% 中断，无终态 XML/JSON；不引用旧树结果替代 |
| 六项已知后端失败原样复跑 | 4 passed、2 failed；干净主线两项音频失败已复现 |
| Worker `npm.cmd run build` 与 `npm.cmd run test:all` | 通过；本地 75 项及 vendor 八组 132 项通过，均为离线测试 |
| 前端 `npm.cmd run typecheck` 与 `npm.cmd run build` | 通过，保留既有大 chunk 警告 |
| 前端 `npm.cmd run test:run` | 1,182 passed、3 failed，481.20 秒；失败见上述干净主线对照 |
| `node --test server-headers.node.mjs` | 单独执行，11 passed；未将原 `&&` 后未运行命令冒充通过 |
| `node scripts/verify-help-images.mjs` | 9 项失败；最新干净主线同样失败 |
| `py_compile` | 136 个待交付 Python 文件通过，缓存仅在忽略目录 |
| 交付范围、已知凭据签名与 Diff | 201 个路径，禁止产物路径 0、已知签名命中 0、staged 0，`git diff --check` 通过 |

10 月 3 日恢复核验时，原测试会话已失效，限定命令行的进程查询未发现对应 Python 进程。
`full-final.log` 只有进度和中途红项，最后约 70%，没有 `full-final.xml` 或 `full-final.json`。
因此不能汇总通过数量、认定全部失败来源或标记本次全量完成；预备的逐测试身份对照脚本
也未执行。没有用零散重点测试拼成一次完整回归，没有重复派发这套长测试。

同次恢复发现旧预览后端 16509 拒绝连接，不能将之前的在线核验结果描述为当前服务健康。
在确认 15509/16509 均无监听后，审阅现有 `server.py`、`acceptance_runtime.py` 与
`frontend.mjs`，原样启动旧树任务服务，使用独立日志及隐藏窗口，没有操作共享栈。
现有护栏仍读取已完成的调用账本并拒绝重复创建运行；没有复制或显示凭据、重置额度或
改动任何启动脚本。恢复后健康接口为 `ok`、原报告地址返回 200 HTML（不冒充新的浏览器
视觉验收）。同一 `verify_effect_result.py after-restart.json` 再次通过，运行与调用账本
checksum、六实例结果、保护记录、零记录权威表、原 Store 和 pending/current r2 全部
保持一致。新增调用、评测及业务写入均为零。这是旧服务恢复，不是新集成代码部署。

此时仍不是提交门禁全通过。完整人工/模型定向修复教程及持久化截图未补验；固定 Update
验收不扩大为 Insert/Delete 全覆盖、生成泛化或汇总答案质量证据。本次后端全量门禁
保持未完成；后续先由各范围负责人处理已复现的主线质量阻塞，再固定基线执行
不中断的全量回归；不得在 CW10 中混入无关修复或重复付费试运。

本批没有 Commit、Push、PR、Merge、新代码部署、自动批准或发布。回退本批只需继续使用未改动的
旧预览与原工作树，不需要迁移或撤销任何业务数据；新集成树先保留用于核验，不自动清理。
