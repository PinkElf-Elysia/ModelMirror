# R9B1：文件一次性 Vision / OCR 安全发送边界

## 当前检查点：R9B1 收口门禁通过，停在提交前

- 最终修复快照 bundle-04：基线 `e3ac455553c2d3065bcd8768d78b849c8477392a`，源码 `cb44e1cf81e27e92bfe137c42f97095aee5f80a4f24ff1d6b7338d6c52e43e47`，镜像 `sha256:478cd8198bb44067dc49a6f287db3f5dfed9214f8d92c1e9520a2908f2470a05`。本检查点是构建后的文档补记，不冒充镜像内文本；生产代码和测试未再次更改。
- Windows 窄回归 170 + 121 passed / 34 subtests passed；新镜像中 Linux 独立双启动进程 2 passed。最终收口再次执行新增出口及独立启动测试，28 passed。
- bundle-04 后端最终为 **8574 passed / 30 skipped / 0 failed / 8 warnings / 34 subtests passed**，耗时 2627.91 秒；Guard 43、Workflow 7、Worker、Catalog、前端 typecheck/tests/production build/help assets 均通过。九阶段 `provider_acceptance.py verify` exit 0，源码、镜像、配置与测试证据匹配，不沿用 bundle-01/02/03 代替。
- 30 项 skip 的边界再次逐项核对：Windows 原生合同 9；mcp-files Office 解析 10、专用 sidecar 2；固定语言服务器镜像 5；部署 Node worker 1；隔离 renderer 1；可选 docx/matplotlib 各 1。本批未添加 skip；它们不是通过记录，也不因下述 CI 豁免而获得验收。
- 修复前 bundle-02 后端全量已完成：8572 passed / 30 skipped / 0 failed，耗时约 47.5 分钟。这证明其对应快照测试状态，不能覆盖四处后续导入修复或新增双模式进程测试。
- 已重建且验证预览 `http://127.0.0.1:15160`，仅新建 `modelmirror-r9b1-preview-04-{server,client}`；后端只加入内部网络 `10.254.95.0/28`，前端另外加入新入口网络 `10.254.95.16/28`，端口仅绑定 `127.0.0.1`。无主机数据挂载、无真实凭据、所有清单控制开关关闭；原 preview-02 容器、网络及其他应用保留。
- Dockerfile 同款 `python -m uvicorn main:app` 正常启动；`/api/health` 和 `/models` 均 200。应用内浏览器实际完成模型筛选、进入 Chat、展开附件菜单与打开 Settings；视觉/OCR 显示未启用，Provider 显示尚未配置，Marble 仍在独立区域。此时预览入站 POST 计数为 0。
- 日志中现有 Chroma 遥测尝试被隔离网络阻断，不是 Provider 请求或本修复新增重试；没有为消除该告警而放开后端网络。页面成功不代表真实模型调用验收。
- 帮助教程改用当前精确型号 `Qwen3.8 Max (0902)`，清空筛选后重放七步通过，停在选择文件之前；新截图已保存，旧截图原样归档。详见 `docs/help-center/evidence/provider-file-analysis-r9b1-e3ac4555.md`。不更改模型目录、数量、价格或业务调度。
- 帮助补验 05 中，前端全量 1194 passed / 2 failed：帮助页旧断言仍要求两张截图及旧日期。修正为当前精确图片路径、替代文本、数量及日期，未删除测试；06 中帮助窄回归 **58 passed**、typecheck、完整前端 **154 文件 / 1196 tests + 17 Node tests**、production build、help assets 全部通过。05 失败证据保留，不覆盖。
- 06 补验镜像 `sha256:852a51cc9bdbe92304006e6366db1dc14486cbb2f2d783380f04120e928403ed` 从 bundle-04 派生，只覆盖帮助文章、目录元数据、两项帮助测试和新 PNG，并移出两张已归档的旧图。后端与依赖保持相同字节；补验脚本按 bundle-04 文件哈希断言不存在其他运行时代码漂移。帮助补验不冒充 bundle-04 原始全量报告。
- 同基线 AI Research CI `37625247280` 的 `c568ccf5 → e3ac4555` 审计在干净 detached 工作树只读复现：`functional candidate changed a protected path`；核对审计脚本与 base blob 相同。本批路径与工作流 11 个触发路径零交集。**2026-10-07 用户明确批准仅将这一失败列为 B1 非阻断基线豁免、保留证据并另案处理**；没有修改 AI Research 治理或自动豁免其他失败。
- 最新基线 Core、newAPI、实际 Overlay 三套 Compose `config --quiet` 通过；仅 Overlay 设置合成网关 URL，无凭据、无 Compose 部署。实际出口扫描 80 entries / 935 candidates；Diff 检查通过，14 个变更文本文件的凭据模式扫描无命中。补齐帮助页面测试后共 19 个改动路径（含两张图片原样归档的源/目标路径），与 AI Research 触发路径仍零交集。
- 最终静态产物安装到本批获批预览；前端旧 `dist` 保留为容器内 `dist-before-help-06`，后端、网络、数据和配置不变。新 `index.html` SHA-256 为 `58c012fed58e623b23fee6c99bdda8d7b9672afbec0f8380bcacb111526724e2`。帮助页实际显示 `2026-10-07`、精确 `(0902)`、七步教程和一张新图；滚入图片后确认 complete=true、1000×720。默认视口已恢复。
- 结论：**B1 限定范围可进入提交审批**，含用户明确批准的一项非阻断基线 CI 豁免。此结论不代表 D1 完整资格纳管、真实 Provider 或生产验收。未 Stage、Commit、Push、PR、生产部署或真实付费调用；不自动进入 B2。

### 最终验收命令与证据

```text
python -B scripts/provider_acceptance.py verify --bundle C:\tmp\modelmirror-r9b1-acceptance-04
python -B -m pytest server/tests/test_file_analysis_egress.py server/tests/test_meta_agent_import_modes.py -q -p no:cacheprovider --basetemp=C:\tmp\modelmirror-r9b1-final-gate-01
python -B scripts/check_provider_coverage.py
npm exec --prefix client -- vitest run --root client --configLoader runner src/content/help-center/helpContent.test.ts src/pages/HelpCenterPage.test.tsx --maxWorkers=1 --fileParallelism=false
npm run typecheck --prefix client
npm run test:run --prefix client
npm run build --prefix client
npm run verify:help-images --prefix client
git diff --check
```

前端五项最终命令在 `mm-r9b1-help-06-*` 的无网络、无挂载、无凭据容器执行，实际命令、退出码、镜像和输入文件哈希保存在 `C:\tmp\modelmirror-r9b1-help-06\verification.json`；原始日志只留本地。补验 05 的 Dockerfile 首次错误地把 config image ID 当作镜像名，未生成镜像；核对 Docker 官方文档后为既有精确镜像增加本地标签并检查 ID，未推送、拉取替代镜像或改变依赖。此工具问题不归为产品通过证据。

最终生产代码仍仅 B1 发送函数和四处已授权的基线导入兼容修复；其他改动为出口清单、回归测试、教程及证据。无 Schema、依赖、默认 Provider、Catalog 统计、Chat SSE 或模型派发次数变化。构建的既有大 chunk 告警与隔离预览的遥测 DNS 告警未通过放宽限制来掩盖。

## 2026-10-07 基线整合（重新验收中）

- 用户已授权保留 B1 工作、整合最新主线、重新验证并启动独立无付费预览；不含 Commit、Push、PR 或真实模型请求。
- 整合前五文件快照保留在 Git stash `a52dc6551c24fd5d7de8e2aea68c45b4fe89dcac`，不删除；原基线为 `2c82d8a53c52fdec507ec5f81ffec5ebc577c750`。
- 再次 Fetch 后实际最新主线为 `e3ac455553c2d3065bcd8768d78b849c8477392a`，比上一轮核对的 `c568ccf5` 多出模型目录更新 PR #404。分支已 fast-forward，再应用五文件改动，无冲突、无提交。
- 交叉审计：#402/#403 改动前端构建、Meta Planner、共享 main.py 和覆盖清单；#404 改动模型目录及 Decisions 请求校验。上游没有修改 file_assets、model_router 或本批帮助文章；B1 发送实现与整合前快照逐字一致。清单保留上游新增项，B1 相对新基线仍仅原有六处小范围调整。
- 下文旧基线测试保留为历史证据，不计作新基线通过；新镜像、全量结果与预览证据需要重新生成。其他工作树、已有应用容器与持久化数据未修改。
- 基线归因：`e3ac4555` 的 Quality run `37625247325` 在实际出口扫描失败，后端全量未运行；干净 detached 工作树 `C:\tmp\modelmirror-r9b1-baseline-e3ac4555` 同 SHA 复现相同三个诊断（models.ts 未登记/陈旧候选、源码指纹漂移）。#404 只更新目录常量和 Decisions 校验，未新增发送位置；复核后仅更新清单内 models.ts/main.py 指纹及对应候选记录，不修改检测器或放宽例外。
- 新基线窄回归 121 passed / 34 subtests passed；隔离 bundle `C:\tmp\modelmirror-r9b1-acceptance-02` 对应源码摘要 `5233e7153e59f7179e652c0796d2033be2fb758534cb899af0bd9764ec0bc322`、镜像 `sha256:34df785010c8123ede449536d89315710c78bda14e29541ba497adf2e118972d`。此快照在上述清单登记修正之前构建；不能声称其实际清单扫描通过。后续文档/清单变更须单独验证并说明与该快照的差异。
- 修正后实际扫描通过：80 entries / 935 registered candidates。清单修正当时生产代码、测试、帮助文章与 bundle-02 一致；后续又进行了下述已授权包启动修复，因此 bundle-02 不再覆盖当前全部代码。
- bundle-02 Guard（43）、Workflow（7）、Worker、Catalog、前端 typecheck/tests/build/help assets 八阶段通过；后端全量仍在运行，尚无终态报告，不宣称全量通过。主线 AI Research CI `37625247280` 另有失败，尚未完成责任归因或取得豁免。

### 独立预览启动阻塞（停止页面验收）

#### 已授权启动兼容修复小批次

- 用户授权修复；首个小批修改两个导入文件、独立进程回归测试和本任务卡（四文件），不改生成协议、Schema、Prompt、派发或模型调用次数。
- 新回归在未修复代码上得到 1 passed / 1 failed：`server.meta_agent` 成功，Docker 顶层 `meta_agent` 复现越过包根异常。测试采用 `python -I -B`，显式固定唯一源码根，避免 pytest 已加载模块或 PYTHONPATH 掩盖问题。
- 两个文件沿用仓库双路径 import 兼容模式；只有 `ModuleNotFoundError.name == "server"` 才使用顶层路径，其他缺失依赖继续原样失败。
- 首批回测 105 passed / 1 failed，顶层包继续暴露 `recipe_edits.py` 同类错误。扫描 meta_agent 全包确认余下两处为 recipe_edits.py 与 recipe_repair.py（后者为延迟使用路径）；第二个四文件小批修复这两处，并扩展同一独立进程测试与本记录。仍只修复导入，不修改业务实现。bundle-03 生成于第二小批之前，只是中间快照，不作为最终验收对象。
- 第二小批回测通过：双模式独立进程、Meta Agent、Meta Planner V2、生成合同、Recipe Adapter/预检/集成/编辑/修复共 170 passed；文件分析/Egress/Provider Chat/覆盖护栏另 121 passed / 34 subtests passed。四个文件除 import 兼容块外的业务 AST 与 HEAD 一致。未调用真实模型。
- 验收：双模式进程测试、Meta Planner/Meta Agent 与受影响生成合同回归、文件分析和覆盖检查；新隔离镜像验证 Dockerfile 同款 `main:app` 启动及无付费预览。失败时保留镜像、容器和证据，不恢复不安全文件发送路径。

- 获批启动的新对象：`modelmirror-r9b1-preview-02-{server,client}`、内部网络 `modelmirror-r9b1-preview-02`，回环端口 15160；无挂载、无真实凭据、全新容器层数据、全部清单开关关闭。默认 Docker 地址池耗尽后，只读核对现有网段并指定无冲突 `10.254.94.0/28`；没有删除旧网络。
- 按仓库 Dockerfile 的 `uvicorn main:app`（工作目录 server）启动失败：`meta_agent/generation_contract.py:13` 的 `from ..workflow_native.node_contracts import WorkflowValueSchema` 在顶层包模式抛出 `ImportError: attempted relative import beyond top-level package`。`generation_recipe.py:29` 也存在同类相对导入，需一并核对。
- 在干净 `e3ac4555` 工作树的 server 目录执行 `python -B -c "import meta_agent.generation_contract"`，复现完全相同异常。失败文件与 B1 工作树 SHA-256 均为 `58ae0a2f26653366f971950f78da887b20f9724211c5f88234e2c3d9f1cac5f2`，来源提交 `8f920852`（#403），不是 B1 修改。
- 后端已退出；仅停止本批新建客户端，容器、网络和数据保留。HTTP/页面验收未通过，未通过更换启动模式掩盖问题，未配置密钥或发出 Provider POST。其他预览器和应用未改变。
- 此阻塞的最小启动兼容修复已获授权并按上文完成窄回归；下一步重建新隔离预览验证 `main:app` 实际启动。B1 尚不能判定为 PR 就绪。

## 范围、基线与证据

- Fetch 成功；A2 PR #401 已合并。基线 `2c82d8a53c52fdec507ec5f81ffec5ebc577c750`，与 A2 前基线相比仅有 A2 五文件，无其他业务交叉变更。
- 独立工作树 `C:\tmp\modelmirror-control-r9-b1`，分支 `codex/provider-control-r9-b1`；主工作区、其他预览器和数据保持不动。
- 已确认 `server/file_assets/analysis.py::_http_request` 使用独立 `AsyncClient.post`，未固定授权 IP，未禁用代理环境。既有 Egress 的通用 `request` 会在连接错误时轮换地址，不能直接作为本次付费 POST 实现。
- 本批只收紧实际发送边界，复用 Egress 与 Provider Chat 的单次发送能力。Vision 每页一次、OCR 所选 PDF 子集一次、解析、目标选择、费用确认和业务接口保持原样；完整资格、Policy 与 Receipt 仍属 D1，覆盖状态继续为 `migration_pending`。
- 最多五文件：analysis.py、新发送回归测试、覆盖清单、本任务卡、既有用户帮助文章。不新增生产依赖、Schema 或开关。

## 验收与实施顺序

1. 新测试先证明当前发送没有应用出口授权，再修复发送边界。
2. 每次 POST 重新授权 DNS，固定首个批准 IP，保留 Host/SNI；禁止代理环境、重定向、连接级重试及第二 IP。
3. 保留 150 秒 HTTP 超时及 10 秒连接超时、180 秒执行总时限。读取、解析、HTTP 失败和任务取消均关闭响应。
4. 测试 protected/mixed DNS、rebinding、精确内网白名单、无代理、3xx、401/429/5xx、超时、取消和逐页调用数。保留 OpenRouter OCR 错误信封内有效 annotations 的兼容行为。
5. 更新实际源码出口清单，不扩大例外、不冒充 D1 已纳管。运行文件分析与安全 Transport 回归，再运行全量和隔离构建门禁。

## 原基线验证状态（历史记录，以顶部最新检查点为准）

| 检查 | 状态 | 证据 |
| --- | --- | --- |
| 新增发送回归 | 通过 | 原发送实现上 14 failed / 8 passed；修复后新增 26 项均通过（包含下述 78 项） |
| 文件分析 / Egress / Provider Chat | 通过 | Windows 78 passed；原默认临时目录权限导致 7 errors，使用 B1 全新临时目录重跑原组通过 |
| R5—R8、后端全量 | 通过 | 本批隔离镜像：7018 passed / 30 skipped / 0 failed；Workflow 独立合同另 7 passed；跳过边界见下文 |
| 前端测试、类型、构建、Compose | 通过 | 149 文件 / 1109 测试 + 1 项 header 测试；typecheck、build、help assets、三套 Compose 均 exit 0 |
| 预览与用户帮助 | 未运行 | 未授权新预览部署；不得以 mock 代替 |
| Diff 与敏感信息检查 | 通过 | `git diff --check` 通过；五文件凭据格式扫描无命中；生产 Diff 无新增日志或持久化写入 |

## 用户影响、风险与回退

- 合法公网 HTTPS 或精确内网白名单连接仍按原业务流程使用。DNS 指向受保护地址、仅依赖代理或返回重定向的连接会失败关闭；这是有意收紧安全边界，不是新的模型资格。
- Help Center Impact：更新既有图片使用教程，区分“图片”与“视觉 / OCR”入口，说明文件分析失败时不得反复付费重试。发布前仍需同基线预览证据；本任务卡不宣称已完成。
- 立即停止门禁：重复 POST、切换 Provider、内容/凭据泄露、无法归因的回归、业务解析或调用次数非计划变化。
- 回退优先关闭受影响文件分析入口或前向修复；不得恢复未授权的 DNS/代理出口。不改数据库，无需整库恢复，不删文件或 Provider 数据。
- Commit、Push、PR、部署、启用和真实付费调用未授权，本批完成验证后停止。

## 可复现验收对象

- 本地 bundle：`C:\tmp\modelmirror-r9b1-acceptance-01`，源码摘要 `e1a7aeac9f4ddfb0918a2067d8d2352f31d1db1b6ea71d6ff996c50bac4d4609`。
- 镜像 `sha256:b0d7f56ea80f045ad6d8de71503072cb5ce84dde6343c2a9f0fc6541517e8b90`；Profile `dabc7b8a065f40619ef29783f61a7337806bf2530a9fc5b1efeb680944088228`。
- 五文件改动经精确 `--include` 纳入快照。测试无网、无挂载、无端口、无真实凭据；不启动业务预览。任务卡的结果附录在测试后更新，不冒充原镜像内文档。
- 精确基线 [2c82d8a5 Quality #37148351387](https://github.com/PinkElf-Elysia/ModelMirror/actions/runs/37148351387) 三个 job 均成功：后端 6990 passed / 32 skipped，Workflow 7 passed；前端 149 文件 / 1109 测试；Windows 专项 88 + 5 passed。这只是当前基线证据，不代替 B1 本地验证。
- 现有前端依赖安装报告 16 项漏洞（2 low / 5 moderate / 9 high），本批未新增、升级依赖，也未宣称依赖安全清零。
- HTTPX 0.28.1；通过 Context7 核对手动流式读取必须关闭响应、禁用环境代理及重定向的契约。只复用既有 Transport，不新增执行器。
- AST 比较确认生产文件中仅 `_http_request` 函数改变，另有两个模块导入（兼容两种包启动方式）；目标选择、payload、解析器和逐页执行器完全不变。
- 覆盖清单 80 entries / 930 candidates；主机 Guard 43 passed / 34 subtests passed。不把出口安全修复等同于资格纳管。

### 已执行命令

```text
python -B -m pytest server/tests/test_file_analysis_egress.py server/tests/test_file_analysis_engine.py server/tests/test_file_analysis_api.py server/tests/test_file_analysis_repository.py server/tests/test_model_router_egress.py server/tests/test_provider_chat_contract.py -q -p no:cacheprovider --basetemp=C:\tmp\modelmirror-r9b1-pytest-01
python -B -m pytest server/tests/test_provider_coverage.py server/tests/test_provider_acceptance.py -q -p no:cacheprovider --basetemp=C:\tmp\modelmirror-r9b1-guards-01
python -B scripts/check_provider_coverage.py
git diff --check
```

Core 与 newAPI 的 `docker compose --env-file .env.example ... config --quiet` 通过；Overlay 首次缺少必需的 `LLM_GATEWAY_URL` 被拒绝，设置合成 `http://new-api:3000/v1` 后同一校验通过。不使用实际密钥、不创建网络或服务。

隔离镜像内 Guard 43 passed、Workflow 7 passed、Worker、Catalog、前端 typecheck/tests/build/help assets 共八阶段通过。后端全量完成前执行 `verify` 返回 exit 1，未把缺失报告计作成功。

## 原基线自动验收与待完成项（历史记录）

- 后端全量 7018 passed / 30 skipped / 8 warnings / 34 subtests passed，耗时 1599.03 秒；26 项新增发送测试全部包含在该全量报告中。无新增测试 skip、无放宽断言。
- 30 项跳过逐项核对：Windows 原生合同 9；mcp-files Office 解析 10、专用 sidecar 2；固定语言服务器镜像 5；部署 Node worker 1；隔离 renderer 1；可选 docx/matplotlib 各 1。这些是环境覆盖边界，不是通过记录，也未被本批自动豁免。当前基线 Windows CI 仅能补充其对应平台合同。
- 九阶段 `provider_acceptance.py verify --bundle C:\tmp\modelmirror-r9b1-acceptance-01` 最终 exit 0；同一源码摘要、实际镜像和有效隔离配置关联完整。没有用另一 SHA 的通过结果覆盖本批测试。
- 独立无付费预览及帮助教程重放尚未执行，已单独请求授权。现有截图没有被冒充为 B1 预览证据；因此自动门禁通过不等于全部 PR 门禁通过。
- 未执行 Commit、Push、PR、应用部署或真实 Provider 请求，未进入 B2/D1。测试镜像、测试容器与本地证据保留；现有应用、数据和主工作区未修改。
- 下一步：获批后建立本批独立预览，核对入口、未启用状态与帮助内容，保留同基线截图和实操证据；不得使用真实凭据或付费请求替代本次无付费验收范围。
