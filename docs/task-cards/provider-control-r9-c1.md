# R9C1 可信 Provider 调用上下文

## 99e50f99 发布前门禁

用户已授权在刷新上游、交叉审查和必要回归完成后 Commit、Push、PR。当前 HEAD 为 `99e50f99e341a013c4c826d623d2e75a26483088`，整合前 stash `de6c63652d14e3d9bf6e11e49480022fa35869c8` 保留，未发生合并冲突；主工作区和旧预览未变。

新冻结包 `C:/tmp/r9c1-acceptance-99e50-offline-20261015` 重新执行全部门禁。前端 1262 passed / 3 failed，三项精确目录断言已同 SHA、同依赖复现并分别获批本批非阻断；其他阶段、78 项交叉回归、36 项 Worker 补测通过。新独立 15169 预览及原样截图已归档。后端全量最终为 10668 passed / 37 skipped / 0 failed，另有 45 个子测试通过；独立 Workflow 合同 8 项通过。37 个跳过逐项核对，Shadow 已补测，其余 36 项仍为专用环境未验证。冻结证据核验仅保留前端三项失败，原报告不改写；本地提交门禁在明确豁免及边界下收口。

本次补齐 Jev PNG 格式修复，保留原件并更新引用，不放宽校验器、不修改模型数据。当前结果、指纹、命令与剩余边界以 [C1 验收记录](../audits/provider-control-r9-c1-closeout.md) 的 99e50f99 部分为准；下文旧基线结果仅为历史记录，不自动继承。尚未 Commit、Push 或创建 PR。

## 2026-10-15 ec79e120 历史验收归档

当前固定基线的测试、预览及关键路径审查已经完成，结论为包含一项获批基线豁免和明确专用环境限制的本地验收，不是全部全绿或生产验收。最终证据索引与待发布说明见 [C1 最终验收记录](../audits/provider-control-r9-c1-closeout.md)。正式提交前仍需获准并刷新上游、核对交叉变化。下文旧日期章节保留为历史记录，不覆盖本节结论。

`ec79e120` 的两次正常镜像构建分别在 pip 下载中遇到 DNS/TLS 与读取超时；原失败目录保留。用户随后批准只复用固定旧镜像中已安装依赖，重新装入当前源码并重跑门禁，不继承旧结果。

- 新证据目录：`C:/tmp/r9c1-acceptance-ec79-offline-20261015`。5397 个源码文件内容哈希 `fc77f97fe7c53ee66d932cdaaca5040873ddc1f40c2e3033c18407266af5afbd`；镜像 `sha256:efa9e5496a77fb91eb4e76d73e116aa1ea3b792fdd701859385185e7e558e094`。11 项依赖输入与固定旧镜像一致，Python 包清单哈希仍为 `7f8330684957bba86558d077da14127943b93dd9b584a250c8ac951405e6c386`。
- 构建变体为 `authorized-dependency-reuse-v1`，实际 Dockerfile、来源证明和镜像内证据分别归档。5397 个源码文件在镜像内逐项核验；Agency Worker 断网重新构建。旧源码不覆盖 `/validation`，没有改生产 Dockerfile 或添加依赖。
- `provider_acceptance.py run` 已完成 guard（47 passed）、workflow（8 passed）、worker、catalog、frontend_typecheck、frontend_build 和 backend。后端全量为 **10659 passed / 37 skipped / 0 failed**，另有 45 个子测试通过，耗时 2913.56 秒；容器正常退出 0、无 OOM。`backend.xml` SHA-256 为 `625935b783c2a3f664a642089562d60b3bb06d0e56bc7058afe962ad4c679c93`，已与阶段报告核对一致。
- 前端全量 1261 passed、1 failed。唯一断言 `models.oct10.test.ts:19` 要求 Ling Sante 为 live，但同基线主线目录明确列为 uncertain。精确 `ec79e120` 干净前端、同镜像依赖的窄测试复现 2 passed / 1 failed。用户仅批准该项为 C1 非阻断基线豁免，另案处理；原始失败不改写为 passed。被失败短路的 Node 检查另行补跑 17 passed / 0 failed。
- 一次基线探针误将 npm 复合脚本的参数附到 Node 命令而非 Vitest，因此跑到前端全量；仅导出 client 的探针缺少 RPG 源码，另外出现 6 项导入失败。它不是完整基线全量证据；目录问题的归因使用单独窄测试。探针输出保留，不据此增列豁免。
- 帮助资产阶段发现主线 JPEG 不符合 PNG 合同，同 SHA 干净基线亦复现。用户单独批准最小修复：保持 845×1634 尺寸，仅固定 256 色 PNG 编码，原 JPEG 移至证据目录原样保留，文章只改引用；未修改业务或校验器。宿主和同固定镜像的 `verify-help-images.mjs` 均通过。PNG 为 163489 bytes，详情见 `docs/help-center/openrouter-oct13-preview.md`。原冻结的 help_assets 失败报告仍保留，修复后的资产是明确的后续差异。

独立 15168 预览仅使用新合成卷，后端内网隔离且无 Provider Key。实际启动恢复：有可信来源的零模型 Workflow completed、previous_runs=1；缺来源任务失败于 `provider_call_context_origin_missing`、previous_runs=0。再次重启状态和计数不变；workload/context calls 均为 0。15166、15167 未改变。浏览器从帮助首页搜索“可信来源”得到唯一对应文章，处置指引可读；未将内存 Runtime 列表当作持久恢复证据。

新增收口证据：当前冻结源码的 Shadow/Worker 补测为 36 passed、0 skipped，JUnit 保存在验收目录 `shadow-recheck.xml`。使用真实 Node Worker、合成模型回调；不证明真实 Provider 调用。Worker 制品路径在 2df7efba 至 ec79e120 间无差异，测试 Python 源码来自本次冻结镜像。

用户提供的 15168 帮助截图已原样归档到 `docs/help-center/evidence/provider-context-r9c1-ec79e120/source-recovery-help.png`，SHA-256 为 `65e74d433451a7af12aefc93f7056dbdbf1895fd221b8b2344aa9c911b7affc0`。旧 15167 截图明确不计作新基线证据。仅帮助资产和引用的客户端增量镜像为 `sha256:7a663a3073ec1178f54fa50a45664bf4e0bab95fbdbff98da02c3ec2b92c15a7`；production build 和资产检查通过，浏览器确认 PNG 完整加载、845×1634。Server、合成卷和其他预览保持原状。

固定验收清单与当前工作树逐文件比对：仅获批 JPEG 保留/PNG 转码、文章引用及审计证据有差异，没有运行时代码或测试差异。源码哈希不冒充保持不变；原 help_assets 失败仍保留，由明确的增量复测补充。首次增量镜像构建错误地将裸 image ID 用作 FROM，未进入构建即失败；改为已核对 ID 的本地标签，以 `--pull=false --network=none` 重建通过，未放宽出口隔离。

37 项跳过已逐项复核：部署版 Shadow Worker 1 项、Windows 原生行为 16 项、Coding 专用语言服务器 5 项、Office/MCP 依赖和 sidecar 15 项。Shadow 项在上述当前补测中实际通过；其余 36 项仍是专用环境未验证，不计作通过，不继承历史豁免。跳过测试 ID 与上一冻结包相同，但分类结论来自本次 JUnit。C1 未修改这些专用模块实现。

最终关键路径审查未发现新的可确认阻断缺陷。归档前 96 个变更路径的有限秘密模式扫描无匹配，Core、newAPI、实际预览 Overlay 的 Compose 配置检查和 `git diff --check` 均通过；暂存为空，无未合并项。原冻结包 `verify` 仍退出 1，因为保留前端断言及修复前帮助资产的失败报告；人工豁免和后续补测作为独立证据，不覆盖原报告。

回滚只证明受控 B2 兼容 Repository 的 v18→v20→兼容读取→v20 循环及来源保留，不证明未经修改的旧 Server 或完整容器回滚。来源缺失或调用未决时继续阻断，不删除幂等记录、不恢复整库覆盖新事实。当前没有 Commit、Push、PR、真实 Provider 调用或生产启用。本节归档发生在冻结之后，不冒充原源码哈希不变。

## 获批整合 ec79e120（2026-10-14，当前状态）

用户明确批准整合新主线、仅登记 ScaleDown 为待迁移并保持 C1 原范围。已成功 Fetch 并快进到 `ec79e1203c0f26e9efa20e4e75e7e0bf9de2a795`。整合前完整源码冻结在 `C:/tmp/r9c1-before-ec79-20261014`，内容哈希 `9eb87ec2baab26ddcf9dd2da9b5bfae93776e035f9ddea6a04bdc461c80ed96e`；保留 stash `70872215f4d5b2839a939e8de2ee8de2b18e010f`，未删除。

生产入口与帮助索引自动合并；唯一冲突为出口清单。按结构逐项核对后保留主线的 God 工具、Office/Client Extension 分类及 C1 来源适配登记，不使用一侧整文件覆盖。八个相对先前 C1 漂移模块逐项复核：SpeechWorkspace、models、ScaleDownPage、speechAudio、main、audio_catalog、image_catalog、tts。

清单现在为 81 个分类入口、947 个静态候选。新增 `openrouter_scaledown` 明确为 `migration_pending`，没有添加部署开关、Policy、Managed shape、Binding 或资格。浏览器同源提交登记为 model_delegate，后端 AsyncClient 为 transport，实际 POST 为 model_dispatch；三者都不冒充已纳管。新增静态回归锁定这些边界。旧 80 个入口状态与 37 个 Workload 注册表不变。

本小批只修改清单、覆盖测试和两份审计文档。新基线交叉回归与完整冻结验收正在重新执行；下文 `2df7efba` 通过结果只保留为历史证据，不自动继承。现有 15166/15167 预览与数据不变。没有 Commit、Push、PR、真实模型调用或生产启用；待验收通过后再次核对提交门禁。

## 最终冻结验收与新主线阻塞（2026-10-14，优先于下文历史记录）

本节是 `2df7efbaa1e91bfc625cf6e9bb5a10e0162ab0cc` 加未提交 C1 修改的实际结果，不是最新主线或生产验收结论。2026-10-14 Fetch 后，`origin/main` 为 `ec79e1203c0f26e9efa20e4e75e7e0bf9de2a795`；尚未再次整合。当前提交门禁仍阻断，不能用本节绿测覆盖新上游。

### 冻结证据

- 目录：`C:/tmp/r9c1-acceptance-2df7-final-20261013`。`source.json` 登记 5386 个文件；源码内容哈希 `04cbb4ef4bf17bca8371c490a0bfb0bf54519b9d117502f2285c547ecfb6983a`；镜像 `sha256:e78af1e0515a2335fcf2325b9cd44a2bb56ec51bf1413e3effaa5e624d0bfa13`；profile 哈希 `dabc7b8a065f40619ef29783f61a7337806bf2530a9fc5b1efeb680944088228`。
- `python -B scripts/provider_acceptance.py verify --bundle C:/tmp/r9c1-acceptance-2df7-final-20261013` 退出 0，九阶段通过：guard、workflow、backend、worker、frontend_tests、frontend_typecheck、frontend_build、catalog、help_assets。容器无网络、无宿主数据挂载、无发布端口；未发送模型请求。
- 后端全量：10614 passed、37 skipped、0 failed，34 subtests passed，2705.50s。`backend.xml` SHA-256 为 `a0d578e05c318bf27887100d0eaf4517a8ac1027b369e47ab7b540320d5ab183`。护栏 43 passed；工作流阶段 8 passed。
- 扩大 Windows 回归最终 1772 passed、2 skipped、0 failed，424.83s，报告 `C:/tmp/r9c1-integration-20261013/affected-final.xml`。两项符号链接/POSIX fork 场景在 Linux 全量中均实际通过。失败后的原集合已重跑，没有继承旧豁免。
- 37 项后端跳过逐项分类：1 项部署版 Shadow Worker；16 项 Windows 原生文件行为；5 项 Coding 专用语言执行镜像；15 项 Office/MCP 专用依赖和 sidecar。后三组 36 项不能算通过，也不能由通用容器结果证明其专用环境可用；本批不修改其实现。
- Shadow 部署版补测使用最终源码镜像加固定 Node 24 Worker 制品，`server/agent_upstream` 中被复制的 Worker 源码/锁在该制品基线到当前基线之间未变。`pytest server/tests/test_agent_upstream_port.py server/tests/test_provider_context_shadow.py server/tests/test_provider_context_worker_runtime.py -q`：36 passed、0 skipped，22.85s。镜像 `sha256:563090dbaa2cd6417ceef4e9ff3cedd2e7853515cc3fe226966b12bfe1373b7a`；归档 `C:/tmp/r9c1-preview-2df7-20261013/shadow-final.xml`，SHA-256 `70b353251bd40576549b4dee6f68f214ccf8e707f321b8c2bc896d4269f9f873`。模型回调为 Mock，不是 Provider 验收。

### 独立预览与回滚边界

- 新预览 `http://127.0.0.1:15167`，项目 `modelmirror-r9c1-preview-2df7-20261013`，独立合成卷与网络，后端仅在 internal 网络运行，未配置真实 Provider Key。镜像由最终冻结镜像构建，manifest `sha256:35f14c2fe6b4961ab4658bad8472d1d733716aeaee40d74218cdc8770f25c1fb`。15166 和其他预览保持原状。
- 实际启动恢复：有来源的合成 timer/input/output Workflow 完成，previous_runs=1；缺来源的任务失败于 `provider_call_context_origin_missing`，previous_runs=0。再次重启终态和计数不变；Router 的 workload calls/context calls 均为 0。这证明无模型节点的持久恢复边界，不证明真实模型调用。
- 2026-10-14 从新的帮助页进入，搜索“可信来源”得到唯一对应文章；进入“功能暂不可用时怎么办”，逐条核对保留旧运行、提供脱敏错误、管理员核对、显式新任务和可能重新计费的说明。页面截图可读。截图文件归档受工具写入权限限制，未声称已加入仓库资产；新主线整合后的同基线截图归档仍需补齐。
- Runtime 页面重启后显示空的内存运行列表，未把它当作持久恢复两例的 UI 证据；持久结果来自只读任务文件/SQLite 核对。管理配对、真实 Provider、付费恢复未验证。
- `python -B scripts/provider_qualification_rollback.py --verify-storage C:/tmp/r9c1-rollback-verify-20261014-final` 通过，Provider calls=0。只验证固定 B2 Repository 加受控兼容补丁的 v18→v20→兼容读取→v20 循环；不代表未经修改的旧 Server 可接受新 schema，也不是完整容器回滚。

### 尚需处置的发布前变化

新主线两个 PR 分别更新出口审计与模型/语音/ScaleDown。与 C1 直接交叉的是 `server/main.py`、`client/src/content/help-center/index.ts`、`docs/audits/provider-coverage.json`；另有 model_ids、TTS Profile/PCM 及目录变化，必须做关联回归。

`origin/main:server/main.py` 新增 `/api/scaledown`，使用全局 OpenRouter 凭据和 `httpx.AsyncClient` 直接 POST Chat Completions；该函数未建立 C1 来源或 Managed Receipt。最新 `provider-coverage.json` 未找到 ScaleDown 入口/函数登记。这是新发现的覆盖范围，不应仅刷新哈希或借用其他入口资格。建议先登记 `migration_pending` 并由用户决定后续独立纳管批次；本次没有改动或启用该新路径。

继续提交前需：确认新出口登记与后续范围；保留备份后整合新主线；重做覆盖/语音/Chat 交叉回归和冻结验证；完成同基线预览、截图归档和最终审查。2026-10-14 归档前 5386 个文件与冻结源码逐一一致；本次之后仅本任务卡和来源审查两份文档新增记录，不将文档变动伪装成冻结哈希不变。无暂存、Commit、Push、PR、生产部署或真实付费调用。

## 授权整合后的状态（2026-10-13，优先于下文旧基线记录）

用户授权整合和处置后，Fetch 成功，HEAD 快进至 `2df7efbaa1e91bfc625cf6e9bb5a10e0162ab0cc`。C1 未提交修改保留并重新应用；主工作区、15166 及其他预览和持久数据未改动。本次没有 Commit、Push、PR、部署或真实 Provider 调用授权。

整合前源码保存在 `C:/tmp/r9c1-integration-20261013/source.tar`，5163 文件，SHA-256 `17d3d7abb4f88bf93e53eb8af7ad07ea229420b3976d653d3afb8fe3a0a5f8b4`；同目录 `snapshot.json` 为逐文件哈希。保留 stash `b4586a82f8f4ba35a124c55195b4f06863a8b7e3`，未删除。此备份只用于比较和有针对性的恢复，不授权整树覆盖或删除后续修改。

四个文本冲突位于 `main.py`、`repository.py`、`workload_control.py`、`workflow_gateway.py`。合并同时保留主线的恢复租约、预算授权、旧调用键重放保护和 C1 持久来源；Handoff 只有一个位于派发保护内的运行器调用。没有绕过来源缺失，也没有删掉主线保护来使测试通过。

新基线已执行：

- 第一组来源/仓储/Workflow Gateway/Handoff 专项：82 passed，98.65s。
- 第二组 C1 Workflow 来源与运行时、新主线 Managed 预算/Handoff 预算/Handoff 结算：114 passed，149.31s。包含三个新增测试：客户端等待键保留来源、同键不能重占位、不能用新键空间绕过旧运行证据。
- 整合中复现的测试夹具问题：C1 恢复测试未取得主线新增的执行租约；新主线预算测试未建立 C1 要求的可信来源。仅补齐真实 Store 的 ready/claim 流程及 Gateway 的 bind_execution，保留生产校验和原断言。修复后重跑同组通过。
- 前端本地单文件命令因未安装 Vitest 而未执行；这属于环境缺项。主线已替换旧固定模型断言，但仍须在冻结镜像中运行，不能仅凭代码宣称通过。
- 出口清单新增九个主线 God-card 候选，逐项区分本地资源/代理、离线回调和可派发的真实验收工具；不将它们冒充只读探测或新增已验收模型。入口分类仍为 80 项，静态候选为 944 项，原 pending 分类不晋级。

下文 Linux 全量、前端、Worker 和预览证据均属于旧基线，不能替代本节新 SHA 的完整门禁。新基线全量、最终源快照核对和必要预览重放仍待完成；C1 尚未达到提交门禁。`js-yaml` 4.3.2 最小补丁与四项反例已保留，不能将旧预览认作已部署修复。

### 整合后的扩大回归及处置

新基线冻结包 `C:/tmp/r9c1-acceptance-2df7-20261013`，源码指纹 `783eb995f74d1f1367c5f0faeac480a161bcbd5b42cac75a3de84bbda93d7d96`，镜像 `sha256:92636fe42e488b9f4b08b95b4babb63603cc1dbdea6bc71489352e1d7d4ce00f`。通过仓库 `provider_acceptance.py` 在 network=none、无挂载/端口的容器运行：覆盖/验收护栏 43 passed；前端 159 文件、1253 项 Vitest 全过，附属 Node 测试 17 项全过；typecheck、production build、help-assets、catalog 阶段退出 0。旧固定模型断言已由新主线修正，无需豁免。Core、newAPI、旧预览 Compose 只读配置检查均通过；变更范围常见密钥/私钥模式无匹配（有限模式扫描不是完整秘密审计）。

扩大 Windows 回归（全部 C1 来源、`test_workflow_r8a*.py`、RPG/AI Research 桥接）初次为 1760 passed / 12 failed / 2 skipped，报告 `C:/tmp/r9c1-integration-20261013/affected.xml`。12 个失败不是已批准豁免：10 个新主线 Gateway 测试未绑定 C1 来源；1 个旧 NoEgress 替身没有来源校验接口；1 个 C1 HITL 重放断言未适配主线的租约先行拒绝。仅修改三个测试文件，分别用固定真实来源、真实离线 Repository/Gateway、准确的 completed 租约错误；保留一次派发、持久化、并发和重放断言。三文件原组重跑 **104 passed，66.86s**，报告 `cross-fix.xml`。一次并行单文件收集曾被共享测试任务目录的单写者锁拒绝，改用独立 `AGENT_TASK_STORAGE_DIR` 后复现并修复；未删除锁或放宽生产保护。

上述冻结包早于这三个测试适配，不可宣称其后端全量通过；最终后端验收须使用新的完整源码快照。前端内容、依赖及 production 源码在三文件适配中未变，前端阶段结果只绑定上述已记录快照。当前未运行新基线完整后端，未更新15166预览，未提交或发布。

## 全量门禁收口 2026 年 10 月 13 日

用户要求继续剩余门禁，并授权新的独立无付费预览。Fetch 成功，上游仍为 `6ff70de6`；工作树 HEAD 仍为 `7eaaa305`，未提交的 C1 改动全部保留。基线更新另行询问，未收到该项授权前不修改 Git 历史或移走工作树改动。上游直接交叉为 `server/main.py`（Decisions 枚举）和 `server/tests/test_rpg_bridge.py`；RPG Bridge 实现新增参数 Profile 和派发前失败 Receipt，属于发布前关联回归范围。

本次先运行 Linux 后端全量、前端 typecheck/全量/build/帮助资产及 Agency Worker 门禁，再归因和最小修复。所有容器断网，不挂载宿主数据；依赖安装只按既有锁文件，不升级生产依赖。准备使用独立端口、合成存储做用户已批准的预览；不触碰已有预览、不调用真实 Provider、不 Commit/Push/PR。

验证快照位于 `C:/tmp/r9c1-closeout-20261013`，含 5162 个 Git 登记的源码文件，排除 ignored 运行数据；`snapshot.json` 保存每文件 SHA-256，`source.tar` SHA-256 为 `60eef92ad9f1672bfa38312c5de86866c52a38d50065b647c24a1dba1afafac8`。测试镜像 `modelmirror-r9c1-validation:7eaaa305-20261013` 的 ID 为 `sha256:3f8e05a8b1f753e6c90461a3999c21b9695ddb9baf350a82f6ffe6bedc2bb02c`（manifest list）；它是验证快照，不冒充正式 Server 镜像。

测试基础镜像 `modelmirror-r9b3-baseline:debd7509-exact-deps` 的前端锁 SHA-256 `0769346386c572ab84f1be52a4882315d98896d76f7e77d21223c359c7dde476` 与当前一致，后端固定 requirements 逐项一致。Agency Worker 使用当前锁安装并构建成功。测试结果完成后记录；开始运行不等于门禁通过。

### 独立预览与阶段性门禁

- 前端：typecheck、production build、help-images 通过；全量为 1214 passed / 1 failed。唯一断言为 `ExpertTeamPage.test.ts:77` 固定模型 `qwen/qwen3-8b` 不在推荐列表。同 SHA 的 `git archive` 干净 client、同镜像依赖单文件复现 5 passed / 1 failed；未沿用旧批次豁免。首次 client-only 基线容器误跑全套，另有五个 suite 因缺少仓库其他夹具加载失败，不视为产品基线失败；精确单文件复现另存。
- Agency Worker `npm run test:all` 容器退出 0。另按正式 Dockerfile 的 Node 24.19.0、pnpm 11.18.0、frozen lock、build/deploy 步骤构建 Worker：`test_agent_upstream_port.py`、`test_provider_context_shadow.py`、`test_provider_context_worker_runtime.py` 共 36 passed / 0 skipped，18.27s。模型回调为 Mock，不产生 Provider 费用。
- 新预览为 `http://127.0.0.1:15166`，项目 `modelmirror-r9c1-preview-20261013`，只使用新建 `synthetic_state` 卷。后端仅连接 internal 网络 `10.254.166.0/28`，前端另有入口网络 `10.254.166.16/28`，只绑定本机端口。未触碰原有预览。默认子网池耗尽后明确分配未占用子网，没有清理任何现存网络。
- 先停本预览 Server，通过正常 Store 接口植入两个不含模型节点的定时恢复任务，再启动 Server。有来源的任务 `completed`、保留一次 prior run；缺来源的任务 `failed/provider_call_context_origin_missing`、prior run 为 0。第二次重启后状态保持，合成库的 `provider_call_contexts` 和 `provider_workload_calls` 均为 0。只使用合成数据，恢复由真实启动协调器执行；这是持久恢复证据，不是模型 Smoke。
- 新来源帮助段落已单独构建并在 15166 重放，页面显示三步处置、保留旧事实及不自动重派发的费用边界。帮助专项最终 64 passed，production build 与 help-images 通过。首轮新增四步使文章总步骤数超过八步，出现 1 failed；改为说明加三步后重跑同组通过，没有放宽测试。文章原有核对日期未冒充全篇重新验收，新段落证据记录在本节。此帮助变更晚于原 `source.tar`，不能将原镜像指纹视为最终完整交付快照。
- Core、独立 newAPI 及本次 preview Compose 的 `config --quiet` 均通过，不启动 Core/newAPI 栈。预览使用验证镜像，不冒充正式生产镜像构建。

Linux 后端全量完成：9249 passed / 30 skipped / 0 failed，另有 34 个 subtest passed，2675.52s；容器退出 0。报告归档为上述证据目录的 `backend.xml` 与 `backend.log`。其中一个部署版 Node 24 Worker 跳过已由独立部署镜像的 36 项测试覆盖；其他 29 项为 Windows 原生约束、Coding language-server 专用镜像及 Office/MCP sidecar 依赖场景，未把跳过算作通过。现有重复 OpenAPI operation ID 等警告保留在原日志。本次使用自定义隔离验证镜像，不宣称正式 acceptance harness 或发布镜像已通过。

覆盖检查通过：80 entries / 935 registered candidates（仅源码覆盖）。`git diff --check` 通过、暂存区为空。普通沙箱读 Git 返回非 worktree 错误，经授权只读访问元数据后确认路径和状态正常，未做修复或 Git 历史操作。

当前基线尚未更新，完整发布镜像、最新基线重放与全量最终 Diff 审查仍未完成；前端固定模型断言无本批豁免，C1 不满足 PR 门禁。基线整合仍待授权。既有 Agency 依赖补丁升级已获单独授权，按下节执行；不因依赖修复授权而推进 Git 发布。本次归档遵循 write-page 技能的证据和限制表达规范；未改变生产准入逻辑。

### 已授权的 Agency YAML 依赖补丁小批次

用户单独授权最小补丁升级及回归。范围限定四个仓库文件：`server/orchestration_worker/package.json`、npm 生成的 `package-lock.json`、新增 `test/yaml-safety.test.ts` 和本任务卡。不新增生产依赖，不修改上游 vendored 源码或 YAML 产品协议，不部署共享栈。

将既有 `js-yaml` 从 4.3.1 固定升级至 4.3.2。Context7 返回的主要文档为新版源码，因此实际断言以已安装 4.3.2 包的 loader 为准：空合并来源纳入累计预算，单个合并序列超过 100 个来源被拒绝。没有依据 v5 文档更换 v4 默认 schema。

四项回归只使用小型合成输入，不运行耗尽资源的攻击负载：显式小预算、单序列上限、跨序列累计默认预算、正常合并和明确字段覆盖兼容性。保留旧锁的独立断网 Linux 容器中为 3 failed / 1 passed；升级后 Windows 为 4 passed，Worker 构建通过，`npm run test:all` 退出 0（本地测试 79 passed，上游各组另计）。初版默认预算测试先命中补丁新增的序列上限，已拆分为两个明确断言并在旧版重新复现，不以泛化异常断言掩盖原因。

补丁验证镜像 `modelmirror-r9c1-yaml-validation:4.3.2-20261013` 的 manifest 为 `sha256:b96f6a0574f1f44694d05940329d1e80b12e720576f8b7ae3f2b4fab7844f7e7`。Linux `npm run test:all` 退出 0；Python 的 Agency Planner、Agency Execution、Expert Team context、Worker runtime context 共 93 passed，35.09s。证据目录保留 `yaml-before.log`、`yaml-linux.log`、`yaml-python.log`、`yaml-python.xml`。原 9249 项后端结果早于此依赖升级，不将其冒充最终补丁镜像全量结果。15166 仍保留旧验证镜像，无真实连接、后端断网；它也不代表补丁已部署。安全回退不得将已知存在缺陷的解析依赖重新用于不可信输入，应停用受影响入口或保留修复版本。

## 桥接与兼容分支收口（2026-10-13）

本次沿用 HEAD `7eaaa305` 的 C1 未提交工作树；Fetch 后上游仍为 `6ff70de6`。上游 RPG 增加参数 Profile 与派发前失败 Receipt，涉及桥接但不改变来源授权边界；本次不擅自 rebase 或混入该变更，发布前须重新集成验证。

范围为 RPG/AI Research 服务来源、缺失来源的可选兼容分支和静态覆盖登记。先增加反例，再收紧新派发；旧记录仍可只读查看，不自动补造身份。第一小批最多五文件：本任务卡、来源存储、来源存储测试、Workload 调用服务、桥接来源测试。后续测试夹具及覆盖登记分批复审，每批最多五文件。验收先运行来源专项和两个真实桥接路由的 Mock 测试，再运行受影响仓储/Adapter 回归、覆盖扫描、Diff 检查；完整门禁缺项单独列明。

不更改限定模型、协议、R5 样本统计、业务调用预算或 D 阶段覆盖范围；不部署、不调用真实 Provider、不提交。回退保留 v20 来源及幂等事实，不能恢复缺失来源的新派发权限。

### 本次最终核对（2026-10-13，优先于下文历史进度）

本次桥接来源、缺来源兼容分支及覆盖登记复审已完成本地修复与专项回归；**C1 整体提交门禁尚未通过**。详细矩阵见 [来源与覆盖复审](../audits/provider-control-r9-c1-context-review.md)。下文的“覆盖仍未通过”“桥接仍待适配”属于历史记录，由本节更新；其他未验证边界不能据此视为完成。

- 来源存储反例在修改前复现 19 failed / 31 passed：18 个原可选入口和保存/传入上下文均为空的路径未按预期阻断。这是本地来源层的缺口，不是已发生真实 Provider 调用的证据。现已移除绕过分支；37 个既有 Workload 来源集合与入口注册表有一致性测试。
- Workload PreparedCall 的上下文必填；仓储新 Call/dispatch 必须有可核对的持久来源。旧记录可只读查询，不能借可选参数重新派发。没有把来源缺失转为 legacy，也没有补造匿名主体。
- RPG 与 AI Research 的真实应用路由使用 MockTransport 核对：认证依赖、客户端伪造字段拒绝、派发前持久服务主体、Receipt 对应及仓储重开后的来源。AI Research 包含文献 default 和 scoped 路径；不修改原桥接策略/统计。D8 的独立 Policy 和文献样本隔离仍需按原计划处理。
- 覆盖清单逐项复核后更新 26 个模块指纹、6 个候选 ID，并保留此前 Chat 认证发送点登记。分类仍为 80 个入口、935 个静态候选；没有将 migration_pending 晋级或改变模型数量。
- 旧底层夹具补齐固定来源、当前策略 revision 和认证关联。异步认证测试改为正式 Adapter 所用的原子 pending-evidence、refresh claim/complete 路径，验证重启后的 uncertain/confirmed、原上游 ID、一次轮询及两次拒绝重派发。未改变生产恢复语义或放宽断言。

#### 最终通过集合（有交叠，不相加）

全部使用合成 SQLite、MockTransport 和独立 `MODEL_ROUTER_STORAGE_DIR`。Python 为 `C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe`；共同参数为 `-B -m pytest -q --tb=short -p no:cacheprovider --basetemp <下表目录>`。表中的文件均在 `server/tests/`。

| 集合 | 最终结果 | 临时目录 |
| --- | --- | --- |
| `rg --files server/tests` 筛选 `test_provider_(call_context\|context_).*\.py` | 312 passed，143.62s | `C:/tmp/c1srcfinal` |
| provider_chat_stable_service、provider_chat_auto_service、rpg_bridge、rpg_control_plane、provider_call_context_chat、provider_workload_control、provider_chat_canary_repository（均为 `test_*.py`） | 146 passed / 1 skipped，148.10s | `C:/tmp/c1broad2` |
| agent_upstream_managed_gateway、expert_team_managed_gateway、managed_rerank、managed_openrouter_batch、fusion_managed_gateway/api、meta_agent_managed_gateway/endpoints、provider_multimodal_r8e/r8b、route_team_managed_gateway/api、workflow_managed_runtime/gateway（均为 `test_*.py`） | 227 passed，201.15s | `C:/tmp/c1adapters` |
| provider_readiness_admission、provider_coverage、provider_chat_control_repository、provider_chat_stable_chat、provider_chat_canary_chat、rag_managed_generation、rag_managed_embedding（均为 `test_*.py`） | 119 passed，107.88s | `C:/tmp/c1gate3` |
| provider_chat_control_service、provider_multimodal_control、provider_qualification_events（均为 `test_*.py`） | 120 passed，23.20s | `C:/tmp/c1lowlevel6` |

最后三文件集合使用 `-x`，完整跑到 100%。中途夹具修订出现过来源缺失、参数/策略版本不匹配，以及孤立 Session 与真实认证恢复流程不一致；最终重跑的是同一组文件，不以跳过或替换测试取得通过。桥接集合的 1 skipped 为 Windows 符号链接权限不可用，不能算验收通过。

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1lowlevel6R'
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_chat_control_service.py server/tests/test_provider_multimodal_control.py server/tests/test_provider_qualification_events.py -q --tb=short -x -p no:cacheprovider --basetemp C:/tmp/c1lowlevel6
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B scripts/check_provider_coverage.py
git diff --check
git diff --cached --name-only
```

覆盖检查通过，输出 `80 entries, 935 registered candidates; source coverage only`；Diff 空白检查通过，暂存区为空。新增 Diff 和未跟踪源码的四类常见秘密模式匹配均为 0，仅为有限模式检查，不是完整泄漏审计。源码覆盖与 Mock 证据不等于真实 Provider、浏览器或部署验收。

#### 提交门禁仍缺

1. 当前工作树尚未集成 `origin/main@6ff70de6`；RPG 参数 Profile/派发前 Receipt 等上游交叉需发布前复核并重跑。
2. 全后端、全前端、typecheck、production build、Core/newAPI/Overlay Compose，以及完整 C1 预览/帮助与部署镜像证据尚未完成。本次没有修改用户界面，也没有启动预览。
3. 同 SHA Windows 的两项 R8C 超短时限失败已有干净基线复现，尚无本批豁免；不能假定 Linux 通过。部署版 Node 24 Shadow Worker、Windows 符号链接场景及此前 Worker 依赖告警仍待处理，未扩权升级依赖。
4. 本轮矩阵不能证明所有 legacy/外部执行域已纳管，不能替代 D8 或后续覆盖批次。生产启用、真实调用、Commit、Push、PR 均未执行。

## 开工契约

- 基线：`origin/main@7eaaa305cface49ca3eedba6f47a7315f641e584`，2026-10-11 Fetch 成功；B4 PR #411 已合并。
- 工作树：`C:/tmp/modelmirror-control-r9-c1`；分支 `codex/provider-control-r9-c1`；开工干净。
- 相对 B4 已测基线仅增加 B4 自身提交，无额外交叉；当前 AGENTS.md 未变化。
- 本 PR 只实施 R9C1。C2 会话持久化、C3 策略发布、D 入口迁移、E UsageEvent 不并入。
- 主工作区、B4/其他预览、数据库和用户资料保持不动。无真实调用、部署、Commit、Push、PR 预授权。

## 已确认的来源缺口

- `server/main.py::_trusted_workflow_execution_source_kind` 在缺少 Request 和恢复来源时返回 None；`ManagedWorkflowGateway.routing_mode` 对未映射来源返回 legacy。
- 受控子 Xpert 通过 `_PROVIDER_WORKLOAD_SOURCE_METADATA_KEY` 字符串传递两类来源，尚无统一且不可变的 actor/purpose/attempt 契约。
- `ProviderWorkloadCallService` 已有持久运行、逻辑键、策略指纹及派发保护；C1 必须复用，不另建重试机制或把上下文校验当作幂等锁。
- RPG 和 AI Research 桥有各自服务认证、限定模型和专有契约；不得归为普通交互 Xpert 或 R5 Gate 样本。

## 实施小批次

每个小批次最多五个文件，顺序推进，跨批次集成为同一 C1 PR。

1. 上下文纯契约、专项测试和本任务卡。固定 local，区分匿名本地主体、本地管理员和明确服务主体；不接管运行路径。
2. R5 与 Workload 的调用边界接入：服务端建立来源，关联已有运行、逻辑调用、尝试与当前策略 revision；派发前再次核对。逐 Adapter 检查，不通过可选默认值悄悄跳过。
3. Workflow/Xpert/后台来源及恢复：从受信入口、执行 Store 与父调用传递；禁止从请求正文/工具参数重建信任。来源缺失必须有明确处理，不能一概标为 Xpert。
4. R7/R8、认证及桥接逐一适配；原协议、鉴权、调用次数和费用授权规则不变。恢复需要持久来源证据，纯内存对象不算完成。
5. 覆盖矩阵、受影响全量回归、证伪、帮助及必要独立预览；新的部署/付费调用另行授权。

允许改动为相关 Provider/业务入口、对应测试及必要任务/架构/帮助文档。新增存储必须先核对现有 Schema 与恢复程序，采用计划允许的加法迁移，不预占版本号。不得扩展 R9D 的纳管状态或默认开关。

## 契约与验收

- `ProviderCallContext` 是内部值对象，不是 HTTP 请求模型、账户、授权凭据或 Provider 连接选择器。类型正确不能替代入口鉴权和服务端事实。
- 必须携带租户、主体、入口、用途、父运行、逻辑调用、attempt 与 policy revision；客户端不能选择这些内部授权字段。
- 子调用保留主体与用途，但选择自身精确入口及当前策略，不继承另一入口批准。
- 模型、连接、凭据、Prompt、正文和媒体不进入本上下文；公共 Receipt 不直接序列化它。
- 对伪造字典、缺失来源、跨入口/运行/attempt、策略漂移及后台恢复分别增加测试；真实准入仍由既有资格、Egress 与派发保护负责。
- 运行前后 Provider POST 数相同；不自动补发 uncertain，不因为上下文缺失回退 legacy。
- 验证先纯契约测试，再 Adapter/入口与 R5—R8 回归、覆盖检查、前后端全量及构建。既有失败必须在本 SHA 重新归因，不沿用 B4 豁免。

## 当前进度与回退

小批次 1 已完成契约及离线测试。最终执行：

```powershell
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_call_context.py server/tests/test_provider_readiness_admission.py server/tests/test_provider_workload_control.py -q -p no:cacheprovider --basetemp C:/tmp/r9c1-context-regression-20261011-b
```

结果：152 passed（44.46 秒），其中上下文契约 84 项。额外覆盖同一组调用 ID 下主体或用途被替换的拒绝检查；上下文不能自行证明调用者身份。

### Chat 调用边界的已实现部分

- `chat_stable.py` 的真实派发守卫现在验证上下文的主体、入口、用途、运行、attempt 和当前策略 revision。公开 Receipt 不输出内部上下文，既有资格及原子派发守卫保留。
- `ai_research_bridge.py` 的服务端依赖明确指定桥接来源，普通文献路由不再在上下文中冒充匿名 Chat。RPG 和 Research 使用各自服务主体；用户运行不因 S2S 调用方式被自动归为后台辅助。此步不改变桥接鉴权、模型、Transport 或既有 Gate 统计。
- `chat_auto.py` 为每个原有尝试建立独立 attempt 上下文，拒绝跨运行或复制的 attempt。原有显式 Auto 多次尝试策略保留，不新增 Provider 调用或隐式重试。
- 新增 `test_provider_call_context_chat.py` 与 `test_provider_call_context_auto.py`。桥接父运行、恢复及用途细分仍待后续适配，不将这些测试写成全链路归属完成。

首轮 Chat、RPG、Research 回归 136 passed / 1 skipped；Auto 专项及既有测试 17 passed。扩大回归发现本轮策略漂移校验抢先改变原公开错误码，已修复检查顺序，原失败测试未放宽，定向复测 15 passed。

修复后的受影响回归命令：

```powershell
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_call_context.py server/tests/test_provider_call_context_chat.py server/tests/test_provider_call_context_auto.py server/tests/test_provider_chat_stable_service.py server/tests/test_provider_chat_stable_chat.py server/tests/test_provider_chat_auto_service.py server/tests/test_provider_chat_canary_repository.py server/tests/test_provider_chat_canary_chat.py server/tests/test_provider_chat_canary_api.py server/tests/test_rpg_control_plane.py server/tests/test_rpg_bridge.py server/tests/test_ai_research_bridge.py server/tests/test_provider_coverage.py -q -p no:cacheprovider --basetemp C:/tmp/r9c1-chat-affected-20261011-c
```

结果：342 passed / 1 skipped（166.18 秒）。随后将 Research 用户运行用途从后台辅助修正为 `user_run`，`test_provider_call_context_chat.py` 再次 12 passed（18.66 秒，临时目录 `C:/tmp/r9c1-bridge-purpose-20261011-a`）。跳过项是 Windows 无法创建符号链接的既有测试，不代表该场景已验收。测试全部为合成数据及 Mock，不证明真实 Provider 就绪。

### 覆盖清单的基线阻塞

从固定 `7eaaa305` 的 Git archive 在内存读取源码，按原检查器的 UTF-8 和通用换行规则复现：935 个候选站点中，4 个前端模块的站点哈希过期，合计 10 个模块的来源哈希与清单不符。第一次 archive 核验未归一化 CRLF，产生了额外假漂移；纠正后才记录本结论。

10 个基线漂移模块：

- `client/src/components/SpeechWorkspace.tsx`
- `client/src/components/settings/NewApiChatCertification.tsx`
- `client/src/components/settings/ProviderWorkloadControlSettings.tsx`
- `client/src/data/models.ts`
- `server/main.py`
- `server/model_router/chat_certification.py`
- `server/model_router/workload_control.py`
- `server/multimodal/audio_catalog.py`
- `server/multimodal/stt.py`
- `server/multimodal/tts.py`

上述 Chat 小批次只改变一个被扫描模块：`ai_research_bridge.py`；其三个已登记网络候选未变化，只增加服务端上下文来源参数。清单仅更新这一模块的来源哈希，不重分类站点、不改变覆盖状态。后续持久来源批次又修改 `main.py` 和 `workload_control.py`，仍须逐项审查后更新对应哈希。其他基线漂移未经豁免或重新批准；覆盖检查仍未通过。

### 2026 年 10 月 13 日来源校验收口

Fetch 成功后，`origin/main` 已前进到 `262f7cafcfc77ce19263c86e59d0cb1dd7836365`，当前工作树仍在 `7eaaa305`，未执行 rebase。上游新增 RPG God Profile 及长输出预算；控制面交叉文件为 `rpg_bridge.py`，新增 Profile 绑定与明确未派发时的 409 Receipt，未改变 Stable Service 的调用接口。该差异已审阅，但本节测试运行在当前 C1 工作树，不代表新基线集成验收。

离线证伪复现并修复以下边界问题：

- Stable Chat 同时替换 gateway 与上下文，或通过另一服务实例重新标注已有 attempt，原校验均未拒绝。现在与服务端创建时保存的来源事实比较；不能从待校验对象反推合法来源。
- Auto 将 attempt 挂入另一 run 并同时替换上下文，原校验未拒绝。现在额外核对创建时的运行、位置及策略 revision。
- Auto 替换当前 attempt 并清除其内存 dispatched 标记，原校验允许再次标记派发。现在独立保留一次性派发占位；占位先于数据库标记写入，写入确认丢失也不释放占位或自动重试。

上述攻击只调用本地合成数据库的派发守卫，没有发送 Provider POST；派发标记被重复接受不等于已经发生真实重复付费。红测分别为 2 项来源重标注、1 项 Auto 归属替换、1 项 Auto 副本重放失败，均为未抛出预期错误。新增数据库写入后确认丢失测试，验证第二次尝试不再调用写入方法。

本小批次仅修改两个 Chat 服务、两个对应测试文件及本任务卡，共五个文件。原子资格检查、公开错误码和 Auto 原有明确计划的多次尝试保持不变。来源注册及派发占位是当前服务实例内的保护，不是持久恢复机制，不能替代后续数据库来源及幂等保护。

最终代码的回归命令及结果：

```powershell
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_call_context.py server/tests/test_provider_call_context_chat.py server/tests/test_provider_call_context_auto.py server/tests/test_provider_chat_auto_service.py server/tests/test_provider_chat_stable_service.py server/tests/test_provider_chat_stable_chat.py server/tests/test_rpg_bridge.py server/tests/test_rpg_control_plane.py server/tests/test_ai_research_bridge.py -q -p no:cacheprovider --basetemp C:/tmp/r9c1-final-origin-20261013-a
```

305 passed / 1 skipped，253.51 秒。跳过仍为 Windows 无法创建符号链接的 `broken_symlink` 场景；没有以跳过替代该场景验收。警告包括既有 lifespan 弃用和 Dify 重复 Operation ID，本小批次未修改相关代码。

```powershell
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_chat_canary_repository.py server/tests/test_provider_chat_canary_chat.py server/tests/test_provider_chat_canary_api.py server/tests/test_provider_coverage.py -q -p no:cacheprovider --basetemp C:/tmp/r9c1-canary-coverage-20261013-a
```

42 passed，23.70 秒。两组无重复测试，共 347 passed / 1 skipped；覆盖检查器单元通过不解除上文清单哈希漂移。`git diff --check` 通过，本批七个 Python 源码及测试文件未命中常见 OpenAI/GitHub token 或私钥头模式；模式扫描不替代最终人工秘密审查。没有真实 Provider 调用、部署、Commit、Push 或 PR。

### 2026 年 10 月 13 日持久来源与运行恢复

本次 Fetch 后上游为 `13e034702068af534161451b0bff8a7b6fe8e9d6`，工作树仍基于 `7eaaa305`，未 rebase。相对先前审阅的 `262f7caf`，新增十个 Agent 目录、公告、帮助及对应测试文件，无新的控制面、存储或 Workflow 交叉；先前 RPG 交叉仍需在集成时验证。

新增 v20 加法表 `provider_execution_origins`、`provider_workload_run_origins`、`provider_call_contexts`。主键及访问带 `tenant_id`；只保存受限来源字段和不透明引用，不保存 Prompt、工具参数、模型正文或凭据。沿用 Router 单写者、事务和迁移前 Backup API，旧记录不补造来源。

- Stable/Auto 的上下文与 attempt 同事务保存，派发前核对持久记录和当前策略。已有服务实例内的防重放保护保留。
- Workflow 交互、部署、Published Xpert 与 Xpert App 在受信内部入口建立运行来源。部署主体为 `service/workflow-deployment`，当前本地交互主体如实标记匿名，不假装是账户。
- 子 Xpert 和 Handoff 继承保存的父来源；公共 Handoff API 移除内部来源字段。子任务引用由父任务及 Handoff 或工具轮次生成，父运行重启换 ID 不改变其逻辑身份；重复进入不能换随机 ID 再次派发。
- 恢复先比较持久来源、任务、入口与执行引用，再建立新运行别名；来源缺失、降级成未知入口或引用冲突均失败关闭。已派发的 uncertain 调用不得重放。新调用仍重新走当前 Policy、资格及出口检查。
- 原 SSE 和公开 Receipt 结构不变，不公开内部 actor/context。没有扩展后台入口为 Xpert，没有启用新 Feature Flag。

兼容边界：升级前未保存可信来源的 Workflow/Xpert 快照不能被猜测为合格恢复来源，当前会返回来源缺失错误。独立尚未适配入口保持登记中的待适配状态，不代表已完成 C1；其他 Adapter 的可选上下文仅用于分批迁移兼容，不能用于宣称全面可信归属。

#### 已执行的定向验证

- 持久来源、执行引用、Workflow Gateway/Runtime：57 passed。
- 稳定子任务引用、存储、实际 Runtime 及子调用回归：39 passed。
- 来源删除失败关闭、资格迁移边界、单写者生命周期及 Workload：95 passed / 1 skipped。跳过为 Windows 无 `os.fork` 的 POSIX 所有权测试，不能算该场景通过。
- 首次较广 Runtime 回归为 153 passed / 5 failed：恢复租约测试缺少新增来源夹具；四项 Xpert 文件记忆测试在过长 Windows 临时路径失败。补齐来源夹具、缩短临时目录后，同四项业务代码不变，相关集合含磁盘恢复测试共 29 passed。未通过放宽断言或豁免取得通过。

最终 Runtime 集合重新执行为 166 passed（97.62 秒）：包含四类入口新运行及重新打开 Router/Execution Store 后的恢复、来源缺失/更换/降级阻断、重复任务阻断、Handoff、审批、部署及 Xpert 文件上下文。磁盘恢复使用合成等待快照，不等同真实用户 HITL 或整服重启验收。

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1rt2'
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_context_runtime.py server/tests/test_workflow_managed_gateway.py server/tests/test_workflow_managed_runtime.py server/tests/test_workflow_retry_xpert_cancel.py server/tests/test_workflow_retry_coordinator.py server/tests/test_workflow_handoff_coordinator.py server/tests/test_xpert_runtime_approvals.py server/tests/test_xpert_context.py server/tests/test_xpert_app_api.py server/tests/test_workflow_deployment_api.py server/tests/test_xpert_handoff_executor.py server/tests/test_workflow_resource_nodes.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/c1rt3
```

```powershell
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_context_workflow.py server/tests/test_provider_context_storage.py server/tests/test_provider_qualification_migration_boundary.py server/tests/test_provider_storage_lifecycle.py server/tests/test_provider_workload_control.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/r9c1-provenance-storage-final-20261013-a
```

第二条为上述 95 passed / 1 skipped（64.81 秒）。再次执行时须使用新的隔离临时目录；命令不指向真实 Router 数据。

Chat/Auto/RPG/Research/Canary 扩大回归为 327 passed / 1 skipped（238.10 秒；`C:/tmp/c1ch3`），跳过为既有 Windows 符号链接场景。该集合运行后，最终审查另外以两项合成红测复现持久来源冲突的异常转换缺口：原先阻止了派发，但没有转换为 `RouterServiceError`。已最小修复 Stable/Auto 的转换与 Stable 未派发失败落库；Auto 的一次性派发占位不释放。修复后受影响集合重新运行：107 passed / 1 skipped（168.90 秒），包括这两项红测；跳过仍为 Windows 符号链接场景。

```powershell
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_call_context_chat.py server/tests/test_provider_call_context_auto.py server/tests/test_provider_chat_auto_service.py server/tests/test_provider_chat_stable_service.py server/tests/test_provider_chat_stable_chat.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/c1green1
```

最终 `git diff --check` 通过，变更文件未命中常见 Provider/GitHub token 或私钥头模式；模式扫描不等于完整秘密审计。暂存区为空。上述分组有先后修复与重跑关系，不将重复测试累加为全量门禁通过。

固定旧版本 Repository 回退循环：

```powershell
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B scripts/provider_qualification_rollback.py --verify-storage C:/tmp/r9c1-rollback-provenance-20261013-b
```

结果为 `repository_cycle=passed`、`provider_calls=0`：B2 `114461d3` 的受保护兼容回退保留旧认证、v20 来源和上下文，返回新版本后记录一致。未做整服或容器回退验收；原旧代码会拒绝未来 Schema，不能直接运行未带兼容防护的旧镜像。回退仍要求显式关闭相关开关和策略、无未决操作、无待投递 outbox，不允许删除记录来绕过条件。

### 2026 年 10 月 13 日 Canary 与交互 Adapter 来源补齐

本次在同一 C1 工作树继续实现，未 rebase、部署或调用真实 Provider。实现按不超过五个文件的小批次推进：Canary 版本与来源、请求来源事务基础、Route/Team/Fusion 接入、Meta Agent 接入及受影响测试收口。没有进入 C2/C3 或扩大 R9D 业务范围。

- Canary 新增租户化 `provider_canary_policy_revisions` 表，归入尚未发布的 v20 加法迁移。管理员保存策略时，在同一事务内递增版本并保存策略快照摘要；运行上下文绑定实际版本，不使用固定占位版本。
- Canary 创建运行时原子保存上下文，派发时核对当前版本、连接、匿名主体、用途、会话哈希及逻辑调用引用。服务内一次性占位与持久派发守卫共同阻止重放；写入已成功但确认丢失也不释放占位。
- 旧 Canary 策略不补造历史版本。缺少版本证据或旧写入者改动策略后，状态查询报告版本缺失，不能创建新的 Canary 派发；管理员显式重新保存后才取得当前版本。旧认证、运行和门禁证据不重写，R5 已有的前置回退规则不扩展。
- Route Agent、Team Chat、Fusion 原生及应用层路径从各自内部 Gateway 显式建立来源。来源、Workload Run 和版本关联在同一事务提交；候选、成员、裁判及汇总各有独立调用上下文。计划调用数、协议及既有失败边界不变。
- Meta Agent 两个生成入口及共用 Gateway 的显式修复调用使用独立 `meta_agent_request` 来源，保留原父运行引用。当前主体为 `local_anonymous/local-anonymous`、用途为 `user_run`，不冒充 Xpert、管理员或后台服务。
- 新来源保留在控制面内部，公开 Receipt 不新增主体信息；不保存请求正文或输出。注册来源只能使用已列明的内部 Adapter，不能由缺少 Request 对象推断身份。

#### 定向验证

适配前对照：Canary 三个既有测试文件 22 passed；Route/Team/Fusion 四个文件 27 passed；Meta Agent Gateway、接口及显式修复三个文件 18 passed。

适配后：

- Route/Team/Fusion 新来源与既有回归：35 passed，48.42 秒。覆盖 Route 单次 POST、Team 三次计划 POST、应用层 Fusion 三次及原生 Fusion 一次 POST、来源删除阻断与磁盘重载。首次为 34 passed / 1 failed，失败由新增重启测试漏传 Repository 必填参数导致；修正为通过真实服务执行恢复守卫后重跑，未放宽阻断条件。
- Meta Agent 与请求来源存储、完成契约和生成边界：124 passed，80.73 秒。覆盖两次规划调用及一次显式修复的上下文与原调用次数；测试集合有重叠，不累加为全量覆盖数。
- Canary、资格事件、来源存储、Workflow、迁移边界、单写者及 Workload 合并回归：233 passed / 1 skipped，70.91 秒。跳过为 Windows 不支持 `os.fork` 的 POSIX 所有权检查，不视为通过。
- 最后补跑 Stable/Auto 来源与服务、稳定 Chat 及 Canary（含写入确认丢失新增用例）：119 passed / 1 skipped，105.65 秒。此组跳过为 Windows 符号链接夹具限制，与上一组的 POSIX 跳过不同；不把重叠集合相加。
- 重新执行受控 B2 Repository 往返验证通过，`provider_calls=0`，目录为 `C:/tmp/c1rollback-newadapters`。仅证明 Repository 集成，不代表整服或容器回滚。

主要复现命令：

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1-adapter-check-router'
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_context_request_adapters.py server/tests/test_route_team_managed_gateway.py server/tests/test_route_team_managed_api.py server/tests/test_fusion_managed_gateway.py server/tests/test_fusion_managed_api.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/c1-adapter-check
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_meta_agent_managed_gateway.py server/tests/test_meta_agent_managed_endpoints.py server/tests/test_meta_planner_model_repair_transport.py server/tests/test_meta_planner_completion_contract.py server/tests/test_meta_planner_generation_boundary_audit.py server/tests/test_provider_context_storage.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/c1-meta-check
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_context_canary.py server/tests/test_provider_chat_canary_repository.py server/tests/test_provider_chat_canary_chat.py server/tests/test_provider_chat_canary_api.py server/tests/test_provider_context_storage.py server/tests/test_provider_context_workflow.py server/tests/test_provider_qualification_events.py server/tests/test_provider_qualification_migration_boundary.py server/tests/test_provider_storage_lifecycle.py server/tests/test_provider_workload_control.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/c1-storage-check
```

上述为合成数据与 MockTransport 证据，不是实际 Provider 或用户验收。未变更预览、生产配置或默认路由。

最后一组执行命令：

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1chatregnew'
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_call_context_chat.py server/tests/test_provider_call_context_auto.py server/tests/test_provider_chat_auto_service.py server/tests/test_provider_chat_stable_service.py server/tests/test_provider_chat_stable_chat.py server/tests/test_provider_context_canary.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/c1chnew
```

本次实现文件为 `call_context_storage.py`、`repository.py`、`workload_control.py`、`chat_canary.py`、`route_team_gateway.py`、`fusion_gateway.py` 及 `meta_agent/managed_gateway.py`，另有对应测试与本任务卡。已审查 Diff；`git diff --check` 通过，暂存区为空，变更 Python/Markdown/JSON 文件未命中常见密钥与私钥头模式。模式扫描不替代最终人工秘密审查。当前分支 `codex/provider-control-r9-c1`，HEAD 仍为 `7eaaa305cface49ca3eedba6f47a7315f641e584`。

### 后续必做与提交门禁

C1 尚未达到提交门禁。已有持久来源、Workflow/Xpert 继承与恢复，以及 Canary、Route/Team/Fusion、Meta Agent、Shadow、Expert Team Planner/DAG 和 RAG/Embedding/Rerank 的本地来源适配。仍须逐项完成 Batch、多模态及认证（含异步恢复）的上下文适配；桥接父运行与用途归属、全入口伪造输入及调用计数也未全部验收。不得把其他 Adapter 仍可缺省的上下文视为完整覆盖。C2 会话、C3 发布和 R9D 新入口不并入。

本轮测试均使用合成存储及 Mock；尚未运行前后端全量、构建、Compose、新预览或整服迁移回滚。覆盖清单漂移和当前上游集成亦未解除。现有预览、真实数据库及 Provider 未改动；无 Commit、Push、PR 或部署。

回退必须保留 v20 来源及调用记录，不降低单写者、出口或派发约束，不用整库旧快照覆盖新增事实。发生真实重复 POST、未授权 fallback、来源或租户伪造、内容泄露、不可恢复迁移时停止门禁并报告。

### 2026 年 10 月 13 日 Shadow 与 Expert Team 可信来源

本小轮只补 C1 已纳管 Worker 入口的可信来源、继承与恢复。按来源与 Shadow、Expert Gateway、宿主协调器、验证与文档分为不超过五个文件的小批次；未新增入口、生产依赖或 Schema 版本。HEAD 仍为 `7eaaa305`。Fetch 成功后，`origin/main` 为 `6ff70de6`；相对上次 `13e03470` 的交叉文件 `server/main.py` 仅增加 Decisions 模型枚举，未改变本次 Worker 链路。目录、音频 Catalog 和帮助改动保留为后续集成检查，本次未 rebase。

- Shadow 用 EngineShadowStore 生成的宿主 run ID 注册 `agent_shadow` 来源，再原子关联稳定 Workload Run。各 Worker request ID 派生独立调用上下文；重复宿主运行和同轮请求不能重新 POST。宿主回调固定运行 ID 和 Managed 模式，映射丢失不能落入 legacy。
- Expert Planner 使用宿主 RunRegistry 引用注册 `expert_team_planner` 来源。DAG 由协调器生成 task ID，主进程工厂注册 `expert_team_agency`；子调用继续使用 Worker request ID。Planner 与 DAG 仍为独立用户动作，不根据客户端返回的计划内容伪造共同父运行。
- HITL 恢复复用已保存 task 来源，并使用既有审批 ID/revision 区分计划内新分段。显式失败续跑与返工继承服务端保存的父 task，重新检查当前策略和资格；历史来源缺失时失败关闭。父运行或调用仍为 running/uncertain 时不允许以新分段、新子任务绕过未决状态。
- 来源链核验拒绝缺失父记录、跨来源及循环；内部 actor、purpose、连接和来源关联不发给 Worker 或公开 Receipt。用户触发的 Worker 运行保持 `local_anonymous/local-anonymous/user_run`，不冒充后台服务或真实账户。
- Shadow 重启仍中断旧 Worker并保留 Workspace，不新增自动恢复执行。Expert Team 的三次规划、十次执行与并发二限制不变；来源继承不恢复调用预算。

实现文件为 `server/model_router/call_context_storage.py`、`server/agent_upstream/managed_gateway.py`、`server/agent_upstream/service.py`、`server/model_router/expert_team_gateway.py`、`server/expert_team_agency_runtime.py` 和 `server/main.py`。新增三个 `test_provider_context_shadow/expert_team/worker_runtime.py` 文件，并适配两个既有 Expert Team 测试文件。

#### 验证和门禁边界

修改前六文件回归为 85 passed / 3 failed：三项失败均指向缺少 Agency Worker 构建产物。按现有锁文件执行 `npm.cmd ci --prefix server/orchestration_worker` 和 `npm.cmd run build --prefix server/orchestration_worker` 后，Expert Team 原三文件完整重跑为 72 passed；没有跳过失败或继承豁免。依赖定义与锁文件无改动。

新增测试初跑的四项失败为夹具错误：Shadow 的第二逻辑轮次重复使用调用序号、错误分支缺少模型对象；Expert Planner 夹具误申请不支持的 JSON 形态，以及 Worker 响应断言遗漏既有 finish_reason。修正夹具后原集合重跑，不放宽正式协议、资格或派发断言。

最终 Shadow/Expert Team 集合为 129 passed / 2 skipped（50.39 秒），包括并发重复 Worker request ID 只产生一个 POST 的证伪用例。两项跳过分别为部署版 Node 24 Shadow Worker 不可用，以及 Windows 符号链接权限不可用，不视为通过；Agency 本地 Worker 集成测试没有跳过。

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1wlast_r'
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_context_shadow.py server/tests/test_provider_context_expert_team.py server/tests/test_provider_context_worker_runtime.py server/tests/test_agent_upstream_managed_gateway.py server/tests/test_agent_upstream_service.py server/tests/test_agent_upstream_api.py server/tests/test_agent_upstream_port.py server/tests/test_agent_upstream_tools.py server/tests/test_expert_team_managed_gateway.py server/tests/test_expert_team_agency_execution.py server/tests/test_expert_team_agency_planner.py -q -rs --tb=short -p no:cacheprovider --basetemp C:/tmp/c1wlast
```

共享来源与已完成适配回归为 137 passed（79.01 秒）：

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1cfinr'
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_context_storage.py server/tests/test_provider_context_workflow.py server/tests/test_provider_context_runtime.py server/tests/test_provider_context_request_adapters.py server/tests/test_provider_context_canary.py server/tests/test_workflow_managed_gateway.py server/tests/test_workflow_managed_runtime.py server/tests/test_provider_workload_control.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/c1cfin
```

`npm.cmd run test:all --prefix server/orchestration_worker` 返回 0；使用 mock 模型，不是付费验收。受保护 Repository 回退循环在 `C:/tmp/c1rollback-workers` 通过，`provider_calls=0`；不代表整服或容器回退通过。

本轮已知独立风险：`npm audit --prefix server/orchestration_worker --json` 报告锁定的 `js-yaml 4.3.1` 存在 high 级 `GHSA-2883-xcg3-v3hh`，涉及空 merge source 的 CPU 约束。尚未做本项目可达性评估或修复，没有自动豁免，也没有执行 `audit fix` 或升级依赖；提交门禁需给出处置结论。

本次未执行真实 Provider、浏览器或容器验收；未部署、提交、推送或创建 PR。两个 Worker 入口的本地实现不等于 C1 全入口收口，前述剩余适配、全量门禁、覆盖清单更新、上游集成与预览仍须完成。回退保留 v20 来源、最小幂等记录、Receipt 和业务 Workspace；旧 Managed 记录没有可信来源时不能补造身份后恢复。

最终 `git diff --check` 通过，暂存区为空；本轮选定 Python 与任务卡未命中常见密钥/私钥头模式。模式扫描不替代完整秘密审查。测试命令中的临时目录仅为本次合成验收位置，重跑请使用新目录。

### 2026 年 10 月 13 日 RAG Embedding Rerank 可信来源

本次仅适配既有 R7 入口的 C1 来源，不改变检索、分块、Embedding 空间、词法降级、Skill 晋级或调用预算。按来源结构、三个 Adapter、Job/API、评测与工具继承拆为不超过五个文件的小批次。没有新增生产依赖、Schema 版本或真实调用。Fetch 后 `origin/main=6ff70de6`；HEAD 仍为 `7eaaa305`，相关 RAG/Skill/Router 文件无上游交叉修改。`main.py` 既有 RPG/模型枚举交叉变化仍待发布前集成，不在本次 rebase。

- RAG 与 Skill API 由服务端建立本地匿名用户来源；请求中的 tenant、actor、连接或来源字段不能覆盖它。内部服务调用明确记录 `rag-runtime` 或 `skill-rerank` 服务来源，不冒充 HTTP 用户，不使用 `request=None` 进入 legacy。
- RAG 查询的 Embedding、Rerank 和答案生成继承同一来源，各自保持独立入口、Binding、调用与 Receipt。当前逻辑阶段用稳定 Workload Run 占位；同一来源同一阶段的再次启动不能用新随机引用重放。
- 索引 Job 创建时先在 Router 保存来源再落业务 Job；来源不写入客户端可提交的 `Job.origin`。Processor 各文档及 Embedding 各批次继承 Job 来源。领取、恢复扫描和实际阶段执行均复核来源；缺失历史来源或 running/uncertain 调用阻断，不补造身份。未决任务仍需显式新 Job，不自动重放。
- RAG Evaluation、Strategy Tuner 与 Skill Rerank Evaluation 使用独立服务主体和 `evaluation` 用途。逻辑引用由宿主 run、目标、候选、用例及重复序号生成；重启不为同一槽位产生新的付费尝试。调优排名、阈值、候选预算与 Promotion 语义未改。
- Workflow/Xpert 知识节点、知识工具及 Skill Router 只从宿主保存的执行来源继承。部署来源保持服务主体；缺失父来源失败关闭，不自动归为已纳管 Xpert。多知识库和工具轮次以节点、工具调用和资源 ID 区分，不保存问题、文档或工具参数。Expert Team 的独立资料库预览记录本地用户动作，不伪造 Planner/DAG 父子关联。

新增 `server/model_router/rag_call_context.py` 和两个 `test_provider_context_rag*.py`。实现涉及 `call_context_storage.py`、三个 RAG/Rerank Gateway、RAG API/Service/Evaluation/Tuner、Skill API/Service/Governance、Workflow Knowledge/Knowledge Toolset/Sandbox Toolset，以及 `main.py` 的宿主调用参数。公开业务 DTO、SSE、模型正文和 Provider 密钥传递方式不变。

#### 本地验证结果

修改前三个 Managed RAG 测试文件为 **34 passed**；Adapter 接入后同集合 **34 passed**。新增测试初跑四项失败均为测试错误码遗漏既有 `logical` 单词；生产返回正确的 `provider_workload_logical_run_replay_blocked`，修正断言后原集合重跑通过，未放松派发约束。

受影响十九文件集合 **295 passed**（292.16 秒）；共享 C1 九文件集合 **145 passed**（93.42 秒）。这些是中间检查，不相加为独立测试总数。恢复检查前移及 Skill 工具继承后，以下十一文件集合重新执行为 **166 passed / 1 skipped**（200.57 秒）：

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1rgfinr'
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_context_rag.py server/tests/test_provider_context_rag_runtime.py server/tests/test_rag_managed_generation.py server/tests/test_rag_managed_embedding.py server/tests/test_managed_rerank.py server/tests/test_rag_pipeline_execute.py server/tests/test_rag_pipeline.py server/tests/test_rag_strategy_tuner.py server/tests/test_skill_semantic_rerank_runtime.py server/tests/test_skill_rerank_governance.py server/tests/test_xpert_runtime_sandbox.py -q -rs --tb=short -p no:cacheprovider --basetemp C:/tmp/c1rgfin
```

跳过项为 `test_xpert_runtime_sandbox.py:149`，当前 Windows 无符号链接创建权限；不视为通过。最终入口关联篡改检查补齐后，以下八文件集合 **65 passed**（48.82 秒）：

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1rglastr'
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_context_rag.py server/tests/test_provider_context_rag_runtime.py server/tests/test_provider_context_storage.py server/tests/test_workflow_knowledge_retrieval_v2.py server/tests/test_xpert_runtime_knowledge_toolset.py server/tests/test_provider_context_workflow.py server/tests/test_provider_context_shadow.py server/tests/test_provider_context_expert_team.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/c1rglast
```

证伪覆盖 HTTP 伪造字段不生效、来源丢失、主体/入口关联篡改、并发上下文隔离、重启后未知结果阻断、重复运行零新增 POST、部署来源继承及控制面无正文。实际 Provider 请求均为 MockTransport；不能替代真实 Provider 或发布镜像验收。

`python -B scripts/provider_qualification_rollback.py --verify-storage C:/tmp/c1rollback-rag` 返回 `repository_cycle=passed`、`provider_calls=0`；仍仅证明受保护 Repository 的兼容循环，不代表整服或容器回滚。最终 Diff 格式通过、暂存区为空，选定源码未命中常见密钥/私钥头模式。

#### 剩余提交门禁

本小轮完成本地来源适配和受影响回归，不是 C1 完成。Batch、多模态、认证及其恢复、桥接来源、全入口覆盖证伪、覆盖清单更新、当前上游集成、前后端全量/构建/Compose、独立预览与帮助、整服回滚仍未完成。前述 Worker `js-yaml` high 告警仍须处置，没有继承豁免。本次未 Commit、Push、PR、部署或调用真实 Provider，也未改现有预览和真实数据。

回退只撤销获批入口接管或代码，保留 v20 来源、调用、最小幂等记录与所有 RAG 索引版本；不能通过清除来源或恢复旧整库来重新派发未知结果。

### Batch 多模态与认证来源补齐

继续 C1，先补 Batch 提交来源与只读恢复，再补多模态直接入口、继承入口及认证恢复。每个小批最多五文件，公开协议、功能开关、付费预算和业务输出不变。当前 HEAD 为 `7eaaa305`，Fetch 后上游 `6ff70de6`；上游 main.py 交叉改动仅 Decisions 模型枚举，不与本轮适配重叠。不得把恢复任务重新归为匿名请求，也不得猜测旧记录来源。

首批限定来源存储、Batch Gateway、专项测试和本任务卡。验收包含单次 POST、重启 GET-only、来源删除/篡改时零网络、幂等重投以及正文不进入来源表。随后按协议逐批验证，不以 Batch 通过代表所有多模态完成。测试使用独立临时目录及 MockTransport，无部署、真实调用或提交授权。回退保留来源与最小幂等事实，不清库或重派发。

#### 2026-10-13 实现及恢复核对结果

本次按最多五文件的小批次补齐以下 C1 来源适配；这不是新增 R9D 入口、生产启用或整轮提交结论。

- Batch 提交由宿主建立 `batch_request`，关联稳定 Workload Run 和 Call。GET 恢复核对原来源、运行、精确模型、形态、连接及配置指纹；缺失或错指向时零网络，不建立新运行、不重发 POST。
- Chat 图片/PDF/音视频、独立图片/STT/TTS/音频生成/视频分析/视频生成及 Realtime 创建使用明确 `multimodal_request` 来源。来源在派发前持久化，重复请求不能为旧运行补造已缺失来源。
- Workflow 交互/部署、Xpert/App Vision 从已持久宿主执行继承主体及用途，作用域执行后复位；RAG Vision 纳入现有 RAG 来源与 Pipeline Job 检查，包含仅启用 Vision 的任务。没有来源时失败关闭，不把评测或未知后台任务冒充交互 Xpert。
- 视频已有上游 ID 的恢复只执行原任务 GET；原来源、Run/Call 或连接缺失不能进入 legacy 解析。音频未知结果仍按既有任务机制终止，不新增恢复 POST。Realtime 保留原 Hangup/启动清理，来源缺失阻止确认资格，但不阻止结束原会话。
- Chat/Workload 认证在原 claim 事务中保存本地管理员来源，新增 `provider_certification_call_contexts` 属于尚未发布的 C1 v20 加法 Schema。认证不是运行策略，`policy_revision=null`，不伪造 revision；精确配置、模型、形态、Adapter、Profile 与幂等哈希参与原记录校验。恢复、异步刷新与确认校验同一认证来源及关联 Session/Batch；旧认证不回填主体，不自动重试或续期。

主要文件为 `call_context.py`、`call_context_storage.py`、`certification_call_context.py`、`multimodal_call_context.py`、Repository、Batch/Multimodal Gateway、Chat/Workload 认证、Realtime、Video Jobs、Vision Service、RAG Service 和 main.py 的 Vision 宿主包装；新增五个 Batch/多模态/认证/视频恢复专项测试及补充既有 RAG/Workload 测试。没有修改前端、公开 DTO、媒体/SSE 协议、默认开关或生产依赖。

本地证伪曾复现：缺少来源的 Batch 可进入尚未适配分支；已修复为本轮入口在 claim/dispatch 前拒绝。视频缺少连接引用的 legacy 解析分支同步收紧。回归夹具增加显式来源，不放宽事务回滚、派发数或资格断言。

#### 本次测试证据（集合存在交叠，不相加）

统一 Python 为 `C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe`，所有 Provider 为 Mock，所有 SQLite 为新建临时目录。命令均使用 `-B -m pytest -q --tb=short -p no:cacheprovider --basetemp <独立目录>`，并设置独立 `MODEL_ROUTER_STORAGE_DIR`。

| 集合 | 结果 | 临时目录 |
| --- | --- | --- |
| `test_provider_call_context.py`、context_storage/multimodal/certification/batch、managed_openrouter_batch、provider_workload_control | 185 passed，30.68s | `C:/tmp/c1ctxf2` |
| context_certification、provider_chat_certification、provider_workload_control、multimodal_realtime、provider_multimodal_r8b/r8e | 224 passed，160.69s | `C:/tmp/c1crlast` |
| context_certification/batch/multimodal/video_recovery、managed_openrouter_batch 最终反例 | 58 passed，37.70s | `C:/tmp/c1srcend` |
| 全部 `test_provider_(call_context\|context_).*\.py` 来源专项 | 271 passed，132.94s | `C:/tmp/c1allcontext` |
| provider_multimodal_r8b/r8c/r8d/r8d_chat/r8e、multimodal_realtime 最终协议集合 | 533 passed / 2 failed，236.31s | `C:/tmp/c1mmfinal` |

来源全量的可复现命令：

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1allcontextR'
$contextTests = @(rg --files server/tests | Where-Object { $_ -match 'test_provider_(call_context|context_).*\.py$' })
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest @contextTests -q --tb=short -p no:cacheprovider --basetemp C:/tmp/c1allcontext
```

两项未通过是 `test_provider_multimodal_r8c.py` 的 `test_r8c_runtime_generation_metadata_attempt_timeout_can_recover` 和 `test_r8c_runtime_generation_metadata_total_deadline_fails_closed`。已在独立干净 `C:/tmp/modelmirror-control-r9-c1-baseline-7eaaa305`、同 SHA/Windows Python 串行复现 2 failed（7.21s，`C:/tmp/c1timingbase`）。前者使用 10ms attempt/100ms total，后者有外层 0.5s 截止；不据此认定 Linux 通过，不沿用其他批次豁免，也未更改阈值。

`python -B scripts/provider_qualification_rollback.py --verify-storage C:/tmp/c1oprollback` 通过：合成仓储 v20 来源保留、旧仓储回退再打开成功，Provider calls=0。该证据仅覆盖仓储，不代表完整 Server/镜像回滚。

`scripts/check_provider_coverage.py` 尚未通过。本轮 Chat 认证从 stream 改为先授权、核对来源再 send 的发送点已逐项复审并登记。另有此前 C1 两个 Xpert 委托点、整个 C1 模块指纹待统一复审；SpeechWorkspace、NewApiChatCertification、ProviderWorkloadControlSettings、models 四个前端登记漂移在同 SHA 干净基线复现。未批量刷新指纹掩盖审查缺口，未豁免。

`git diff --check` 通过；新增 Diff/未跟踪代码的有限秘密模式扫描匹配数为 0（不是完整泄漏审计）。工作树原有 C1 未提交改动保留。

#### 当前边界

本次 Batch、多模态、认证来源及恢复适配已实现并取得上述 Mock 证据；整体 C1 仍须核对桥接来源、所有剩余可选兼容分支、覆盖矩阵、上游集成、全量测试、帮助/浏览器及部署证据，处理已复现基线失败和先前 Worker 依赖/运行环境问题。未运行前后端完整门禁、未修改任何真实资格/连接、未启动预览、未 Commit/Push/PR。下一步是 C1 全域缺项审查与门禁收口，而非默认启用或直接提交。

回退保留 C1 v20 来源表、既有 Receipt、Batch/媒体/会话任务及幂等记录。不能通过删除来源、整库回滚或重派发未知任务恢复服务；来源不可验证时保持阻断，Realtime 必要清理仍可执行。

### Shadow 与 Expert Team 恢复边界复核

本次继续 C1 已有 Worker 适配，不新增业务入口、Schema、依赖或 Provider 调用。Fetch 后 `origin/main` 仍为 `6ff70de608b7ef7972a4ed023f38ce7f44110635`，工作树 HEAD 为 `7eaaa305cface49ca3eedba6f47a7315f641e584`；已有未提交改动全部保留，未 rebase。

待证伪的两个边界：Shadow 的宿主映射是否可误指向另一条合法运行；legacy DAG 在 HITL 等待期间启用 Managed 后，恢复是否复核当前策略而非仅按旧模式调用。风险为模型派发与持久来源关联，不涉及实际凭据或外网。实施拆为两个最多五文件的小批次：Shadow Gateway/Service/专项测试/本任务卡；Expert Team 协调器/宿主恢复测试/本任务卡。公开 DTO、Worker 消息与调用预算保持不变。

先执行三个 `test_provider_context_shadow/expert_team/worker_runtime.py` 基线，再增加反例并确认失败，最小修复后重跑原集合及 Agent Upstream、Agency Planner/DAG、共享上下文回归。没有真实模型、部署、提交或 PR 授权。回退保留现有来源、Workload Receipt 和 Workspace，不删除未知结果或幂等事实；未完成完整提交门禁前不得宣称 C1 完成。

#### 复核结论与最小修复

既有三文件基线为 30 passed。新增两个反例均复现失败：Shadow 将宿主映射误指向另一合法 Workload Run 时产生一次模拟 POST；旧 legacy HITL 检查点在当前 Managed 策略下未阻断恢复。两项是本地证伪结果，不是真实 Provider 事故，不以既有绿测掩盖。

- Shadow Gateway 新增必填内部 `origin_execution_id`，由主进程闭包固定的 run ID 传入，并与 SQLite 的运行来源关联精确比对。None、空值、其他来源、跨运行映射在 prepare/POST 前阻断。Worker payload 和公开请求没有新增身份字段。
- Expert Team HITL 恢复调用主进程 Factory 复核当前策略。旧 legacy 在当前 legacy 下仍按原协议继续；当前 Managed 激活或 degraded 时不得凭旧检查点绕过。缺少历史来源不补造，曾为 Managed 的任务也不能因 Factory 返回 None 静默降级。
- 既有 Planner/DAG 独立入口、Host 生成的父子任务、审批分段、显式续跑/返工、uncertain 阻断、三次规划/十次执行/并发二、Shadow Workspace 保留和 Worker 凭据隔离保持原范围。未增加重试、自动重启或模型调用。

第一小批涉及 `server/agent_upstream/managed_gateway.py`、`service.py`、`server/tests/test_provider_context_shadow.py`、`test_agent_upstream_managed_gateway.py` 和本任务卡；第二小批涉及 `server/expert_team_agency_runtime.py`、`server/tests/test_provider_context_worker_runtime.py` 和本任务卡。没有新增生产依赖或数据迁移。

#### 本次验证

Shadow 首轮 22 passed；Expert Team 首轮 70 passed。补充空来源与当前策略三态测试后，最终受影响十一文件集合 **136 passed / 2 skipped**（55.60 秒）：

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1w2allr'
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_context_shadow.py server/tests/test_provider_context_expert_team.py server/tests/test_provider_context_worker_runtime.py server/tests/test_agent_upstream_managed_gateway.py server/tests/test_agent_upstream_service.py server/tests/test_agent_upstream_api.py server/tests/test_agent_upstream_port.py server/tests/test_agent_upstream_tools.py server/tests/test_expert_team_managed_gateway.py server/tests/test_expert_team_agency_execution.py server/tests/test_expert_team_agency_planner.py -q -rs --tb=short -p no:cacheprovider --basetemp C:/tmp/c1w2all
```

跳过项：`test_agent_upstream_port.py:254` 的部署版 Node 24 Shadow Worker 不可用；`test_agent_upstream_tools.py:41` 的 Windows 符号链接权限不可用。均未豁免或计为通过。真实 Node Agency Worker 集成测试通过；Provider 均由 Mock 替代。

共享上下文、Workflow/Xpert、RAG 与 Workload 六文件集合 **90 passed**（46.69 秒）：

```powershell
$env:MODEL_ROUTER_STORAGE_DIR='C:/tmp/c1w2sharer'
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B -m pytest server/tests/test_provider_context_storage.py server/tests/test_provider_context_workflow.py server/tests/test_provider_context_runtime.py server/tests/test_provider_context_rag.py server/tests/test_provider_context_rag_runtime.py server/tests/test_provider_workload_control.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/c1w2share
```

本次没有重跑前后端全量、前端构建、Compose、浏览器、真实 Provider 或部署镜像验收。上一节中的 Batch/多模态/认证/桥接缺项、上游集成、帮助与发布证据、Worker 依赖告警仍未完成。Shadow 和 Expert Team 的本地 C1 来源适配已通过上述回归，但 C1 整体仍不能提交；未 Commit、Push、PR 或部署。
