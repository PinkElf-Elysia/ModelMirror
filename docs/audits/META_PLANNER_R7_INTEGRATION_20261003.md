# R7 最新基线独立集成核验

记录起始日期：2026-10-03；当前状态更新于 2026-10-07。前置 PR #402 已由用户合并，最新 main 为 `cff3f28d`，与已测基点 `89b72c67` 的文件树完全相同。R7 冻结代码、定向修复预览、隔离 Insert/Delete 及帮助截图门禁已核验，进入最终暂存审阅与 PR 提交。Windows 基线失败及生成泛化等未验证边界不变；不自动合并、不进入 R8。以下历史批次保留各自来源，最新依赖核验见文末。

## 范围与来源

- 用户确认先收尾 R7，再进入 R8。本次不开放任何 R8 节点或长运行能力。
- 首批冻结基线为 `origin/main@4932e3b4ae470ebb30c7f775d53e30961a5a07fd`。
  PR #399 解决的是主线质量问题；该提交的 Planner 仍为 V9 / 19 类能力，不能代替 R7 功能集成。
- 独立分支为 `codex/meta-planner-cw10-integration-20261003`，工作树为
  `C:\tmp\modelmirror-meta-planner-cw10-integration-20261003`。
- R7 来源为 `codex/meta-planner-cw10-final-20261002@8c2a0120` 的 201 个未提交路径。
  上游新增 27 个路径与交付路径无交集；逐文件复制前后 SHA-256 一致。
- 原脏工作区、原 R7 工作树、15509/16509 预览及其数据未修改，未复制连接或运行 Store。
  首批新增持久文档仅为本记录和本批任务卡，既有生产代码没有额外修补。
- 验证期间 `origin/main` 前进到 `f34aca06`（PR #400）。新增五个 Provider coverage / CI 路径
  与本次 R7 交付路径无交集，但本次全量依然严格绑定 `4932e3b4`，没有边测试边修改基线。
  该批旧回执不改标。后续明确冻结到 `f34aca06` 的结果另列下文。

## 首批冻结与环境

临时证据位于独立工作树的 `.tmp-r7-closeout-20261003/`，不纳入交付。

- 冻结清单包含 201 个 R7 路径和测试前任务卡，共 202 个路径。
  `source-manifest.json` SHA-256 为
  `2d91a6ff7b57c50134941ee4fd28d88b7be6db6d4791832a1f286685b051bdf2`。
- `git archive` 基线归档 SHA-256 为
  `6aa7cd5938ed283e620f0d0d989d4a5fdb245c72488a027e7407f3b614519578`；
  202 路径 overlay 归档为
  `7a30a2c696dab14683261d2fd5360c50ef45cf5a2469d23a5fa6b64dee5073e8`。
- Windows 使用现有 Python 3.12.14 环境和 Node 24.18.0；22 项后端直接依赖均与
  `server/requirements.txt` 固定版本一致。四处 Node 依赖均按现有锁文件离线安装，
  不增加依赖、不修改锁文件。
- Windows 测试启动器清除凭据类环境变量、禁用 dotenv、使用独立 Store 与临时目录，
  Python socket 只允许回环。旧运行契约与剩余全量按 CI 分进程执行。
- Linux 为本任务专用一次性测试容器：无网络、无端口、UID 1000、只读根目录、
  `cap-drop ALL`、`no-new-privileges`，限制 6 GiB / 4 CPU / 1024 PID。
  仅挂载冻结源码、已核验缓存依赖、测试启动器及本任务输出目录；没有凭据、共享卷或 Docker socket。
- Linux 使用相同 Python 3.12.14、Node 24.18.0 和 22 项固定依赖。仅为语言版本对齐下载
  官方 Node 镜像，测试本身禁网。测试镜像来源为缓存测试环境加官方 Node 二进制，
  不是应用部署或共享栈重建。
- Linux 解包后、准备依赖前的 5,060 个源码文件集合 SHA-256 为
  `709806ab202ba0898dbff59459b1a834d658bbb951b2c33d0c3b09a2b0b9d322`。
  环境、输入、启动器和源码摘要均写入 `environment.json`。

## 已完成验证

| 命令或检查 | 结果与边界 |
| --- | --- |
| 15 个受控写入、可信来源、隔离、Adapter、Headless、Store 与 NodeContract 文件 | 328 passed，42.23 秒；`r7oct03-focused.xml/json` |
| Windows `test_workflow_run_contract.py` 独立进程 | 7 passed，8.58 秒；`r7oct03-legacy.xml/json` |
| Linux 同一旧运行契约 | 7 passed，7.15 秒；`linux-legacy/results.xml` |
| Linux 修正验证环境后的旧运行契约 | 7 passed，7.78 秒；`linux-legacy-v2/results.xml` |
| Linux V2 剩余完整 `server/tests/` | 8,496 passed / 32 skipped / 0 failed / 0 errors，退出码 0；pytest 1,726.79 秒；`linux-full-v2/results.xml` |
| 前端 `npm.cmd run typecheck` | 通过 |
| 前端 `npm.cmd run test:run` | 154 文件、1,194 项通过；随后代理测试 11 项通过；没有跳过 `&&` 后命令 |
| 前端 `npm.cmd run build` | 通过，保留超过 4,500 kB 的既有 chunk warning |
| Agency worker `npm.cmd run build` / `test:all` | 通过；本地 75 项与 vendor 各组测试均为零失败，不将嵌套输出重复累计 |
| Quality CI 所列四个 Node 目录保护测试文件 | 21 passed |
| 前端实际基底 Node 22 的代理运行测试 | 11 passed；五个受限文件挂载、禁网、无主机端口；不是完整镜像构建通过 |
| `node scripts/verify-help-images.mjs` | 23 篇文章的图片格式、尺寸、引用检查通过；不是帮助教程实操通过 |
| `py_compile` | 136 个变更 Python 文件通过；缓存只在忽略目录 |
| Compose `config --quiet` | 通过；禁用默认 dotenv、使用空数据根，不启动服务 |
| Diff 与初步敏感扫描 | `git diff --check` 通过；202 路径禁止产物和已知凭据签名均零命中，不等于全面安全审计 |

## 全量与失败归因

首轮 Windows 与 Linux 剩余 `server/tests/` 全量均已结束：

- Windows：8,294 passed / 163 failed / 4 errors / 69 skipped，退出码 1，3,324.42 秒。
  证据为 `r7oct03-full.xml/json`。去重后涉及 165 个失败身份、137 个原测试函数。
- Linux V1：8,493 passed / 3 failed / 0 errors / 32 skipped，退出码 1，1,770.20 秒。
  证据为 `linux-full/results.xml`。三个失败均在未含 R7 的同提交基线原样复现，
  `linux-baseline-failures/summary.json` 为 0 passed / 3 failed。

三项 Linux 失败已定位为验证启动器覆盖默认行为，而非 R7 产品修复目标：

1. `test_office_warnings_are_bounded_persisted_and_returned` 使用模拟 PPTX 字节，启动器
   强制 `FILE_ASSET_STORE_MODE=native` 额外触发真实格式校验，产生注册失败 warning；
   生产默认为 `legacy`，原用例没有要求切换这一模式。
2. `test_handoff_executor_status_and_requeue_api` 与
   `test_published_manager_waits_for_specialist_xpert` 要求已有 Handoff Runtime 的默认启用
   行为，启动器强制 `HANDOFF_EXECUTOR_ENABLED=false` 与断言相反。

V2 启动器仅移除上述两个环境覆盖，未改测试、断言、生产代码或 Planner Handoff 权限。
容器继续禁网、无凭据、只读根目录、独立 Store。相同三项在基线和集成树分别 3/3 通过。
旧 V1 脚本保存在 `run_linux_v1.py`；V2 SHA-256 为
`c1d3718229775680363d4848f539917f8c6af9a3cf12f42187b6986c92e9144e`。
V2 完整后端回归已经结束：8,496 passed / 32 skipped / 0 failed / 0 errors，退出码 0。
逐身份比较 V1 三项失败，V2 三项全部通过，无缺失或跳过。两个全量使用同一冻结源码、
语言版本与依赖；只移除了已经定位的两项验证环境覆盖。没有删除测试或放松产品校验。

Windows 原始回执不覆盖、不改记为通过，也不统一归因为操作系统问题。

干净对照树 `C:\tmp\modelmirror-r7-baseline-20261003` 固定同一 `4932e3b4`。
对照只安装既有 Worker/RPG 测试依赖，并复用同一离线启动器。
已按失败测试身份选取原测试函数进行同环境复跑，保留参数化展开；没有找不到源函数的
不可对照项。该进程在输出部分失败后持续约半小时没有新的日志或测试目录活动，最终停止，
确认对应进程全部退出。它未生成完整 JUnit，不据零散进度字符统计通过数或宣称归因完成。
`r7oct03-base-selection.json` 和原始日志继续保留；该次停滞不计入后续有界对照的结果。

后续按同一 137 个原函数、46 个模块串行复跑，保留参数化展开并逐阶段落盘安全测试身份。
每项有 90 秒故障栈 watchdog，未更改测试或原启动器。46 个模块全部产出完整 JUnit，
没有超时或未完成模块：21 passed / 163 failed / 4 errors / 2 skipped，累计 966.53 秒。
这 21 个通过和 2 个跳过来自所选函数的其他参数化分支，不是原失败身份转绿。

`windows-baseline-bounded-comparison.json` 逐身份核对确认：原 165 个失败身份全部在
干净同基线再次失败，0 个转绿、0 个跳过、0 个缺失。对照结论是**同环境基线也失败**，
不是 Windows 全量通过，也不能排除被前置基线失败遮挡的后续行为。
已抽查 Coding 模块的 `os.fchmod` 不存在；不将这一原因推广到所有失败。
对照证据保存在基线树的 `.tmp-r7-closeout-20261003/r7oct03-bounded/`，原回执没有覆盖。

Windows 原全量的 165 个去重失败身份在 Linux V2 全量中全部通过，没有缺失或跳过，
见 `windows-linux-failure-comparison.json`。这仅证明当前失败依赖执行环境或测试组合，
不能据此统一断言为 Windows 操作系统缺陷，也不能把 Windows 原全量改记为通过。

## 独立应用打包阻塞

`docker build -f client/Dockerfile ... client` 在集成树与干净 `4932e3b4` 均失败，退出码 1。
各自 12 条唯一 TypeScript `TS2307` 诊断完全一致，证据为两树各自的
`client-image-build.log` 及集成树 `client-image-comparison.json`。

`docker-compose.yml` 将前端构建上下文限定为 `./client`，`client/Dockerfile` 只复制该
上下文；`tsconfig.app.json` 又包含 `src` 中的 RPG 测试，这些测试引用上下文之外的
`experiments/ai-rpg-engine/card-replica/src/*`。本地完整仓库构建具有这些文件，故其通过
不能证明该容器打包路径通过。失败发生在 TypeScript 编译阶段，尚未生成最终应用镜像。

此问题在纯主线存在，未通过删除测试、缩减断言或修改构建边界来混入 R7。本次 Node 22
代理测试已通过，但只证明 R7 代理模块的该 Runtime 路径，不替代应用镜像构建门禁。

用户随后明确批准独立修复。在最新 `f34aca06` 的
`codex/client-build-boundary-20261003` / `C:\tmp\modelmirror-client-build-boundary-20261003`
中，仅将生产构建配置与完整测试类型检查分开，保留严格选项、Vite 配置检查和原 CI typecheck。
该独立四文件补丁已通过 6 条边界反例、完整类型检查、149 文件 / 1,109 项前端测试及随后
7 项 Node 检查、生产构建、实际前端镜像构建和无网络容器静态冒烟。
证据详见该工作树 `docs/tasks/CLIENT_BUILD_BOUNDARY_20261003.md`。
没有 Commit、Push、PR 或部署，也没有自动复制补丁到 R7。后续仅在临时构建快照验证两者组合，
证据见下文；它不能替代独立修复的正式集成。

## 最新基线增量收口

再次只读核对远端后，仅将本任务分支 fast-forward 到
`f34aca06e3e494d42c40ff6ebff22f24bd637bfc`，没有创建提交或执行远程合并。
Windows 基线对照树和原 R7 树保持原版本。201 个原 R7 文件 SHA-256 全部不变。

### Provider 覆盖兼容

新增上游门禁在 R7 源码上最初报告 20 项问题：12 个待审阅调用点、7 个过时登记和
1 项模块 hash 漂移。没有将“无文件冲突”当作契约兼容。

逐项核对后，只更新 `docs/audits/provider-coverage.json`：

- 五个前端模块仅访问同源业务 API，不持有 Provider 凭据；定向修复的预检、明确授权、
  调用和载入仍是不同操作。浏览器 API 分类不等于其服务端业务无需权限。
- 共享 completion 传输增加被动时序/字节统计和派发前 request guard，实际网关、请求和
  调用方的许可仍沿用已有实现；保留 legacy 分类，不宣称整体 Managed 验收。
- 显式修复的 legacy 分支登记到现有 `meta_agent` 入口。其路由/body 确认与单次调用限制
  已核对；源码登记和模拟测试不替代该入口的真实模型验收。
- Evaluation 调用增加私有表 Backend 与稳定 task ID，入口仍为 `migration_pending`；
  隔离写入效果证据不能冒充整个 Evaluation 的 Provider 收口。
- `GenerationEvidenceCall.request()` 是 ContextVar 内的本地结构/hash 记录，未执行网络或
  文件 I/O；`transport_evidence.py` 只包装现有响应流，不新增 client、派发、重试或路由。
- `managed_gateway.py` 的模块 hash 变化来自派发前 guard、完成契约与安全 usage 记录，
  已有授权传输调用未变化。原 80 个入口及其状态、37 个 workload 映射不变。

扫描器、CI、Registry、Provider 策略和业务生产代码均未修改。覆盖扫描通过：80 个入口、
935 个候选调用点；上游 20 项变异/攻击测试通过。修复前扫描保留在
`r7-new-coverage-probe.json`。此证据仅说明已审阅源码变化，不是实时出网证明。

### 新冻结与回归

`latest-main-inputs/inputs.json` 固定新基线、201 个 R7 文件、本记录、任务卡及覆盖清单，
共 204 个 overlay 路径；SHA-256 为
`9d9fb8a9a7b15c5f86c72e877e2ed693789a474c06f57ab5aff69614b2a699ef`。
使用相同已核验依赖和未修改的 Linux V2 启动器。后续仅补充本记录及任务卡的运行结果，
不修改冻结中的业务源码或门禁清单。

解包后 5,065 个源码文件集合 SHA-256 为
`7c9822e685494795e412bd5f7b29c95a57afc50026e99569516adb8678c354c2`。
最新全量与独立旧运行契约的 `environment.json` 确认输入、源码和启动器摘要一致；
没有在测试运行中更新源码或扩大网络权限。

| 最新基线检查 | 结果 |
| --- | --- |
| Provider 门禁的 20 项离线攻击测试 | 通过，`python -B -m unittest discover -s server/tests -p test_provider_coverage.py -v` |
| `python -B scripts/check_provider_coverage.py` | 通过，80 entries / 935 candidates |
| Planner Managed、显式修复、completion 与安全观测 10 文件重点集 | 185 passed / 0 failed / 0 errors，113.42 秒；包含上述 20 项，不重复累计 |
| 前端 `npm.cmd run typecheck -- --force` | 通过，使用完整应用/测试配置，不依赖旧增量缓存 |
| 前端 `npm.cmd run test:run` | 154 文件 / 1,194 项通过，422.96 秒；随后 11 项代理 Node 检查通过 |
| 原 R7 前端 `npm.cmd run build` | 通过，未把独立构建补丁复制进交付树 |
| `npm.cmd run verify:help-images` | 通过，23 篇文章；不替代帮助教程重放 |
| Python 语法、Diff、交付路径和签名扫描 | 138 文件编译通过；204 个路径、暂存 0、Diff 0、已知秘密签名 0；不等于完整安全审计 |
| 最新基线 Linux 剩余完整 `server/tests/` | 8,516 passed / 32 skipped / 0 failed / 0 errors，退出码 0；pytest 1,649.06 秒；`linux-latest-main-full/results.xml` |
| 最新基线 Linux 旧运行契约独立进程 | 7 passed / 0 failed / 0 errors，退出码 0，7.26 秒；`linux-latest-main-legacy/results.xml` |

两个互不重叠进程合计 **8,523 passed / 32 skipped**；重点测试不再重复计入全量。
32 个跳过保留原结果，不改记通过。既有警告（包括 Dify OpenAPI operation ID 重复）保留，
不在本批额外修补。此为本地隔离回归，不是 GitHub CI 或真实 Provider 验收。

与首批 Linux V2 的 JUnit 做覆盖对照，8,523 个既有测试身份及结果完全相同，新增 20 项
均为上游 Provider coverage 测试且全部通过。另外五项测试 ID 含收集时生成的 XLSX 字节，
原始逐字身份比较因此失败，回执保存在 `latest-full-comparison.json`，没有覆盖。
经单独核对，两次测试源文件 SHA-256 均为
`90dab01a5cb5c90fcbd72f8b3dfc648c5727e02f4ff7c22216c991e1f673c8c2`；
这五项按固定文件元数据、唯一容器特征和预期错误码逐项对应，两次均通过，无遗漏。
对照只证明用例覆盖与结果一致，不宣称含动态时间的二进制输入逐字相同；未修改测试或断言。
安全摘要及原 ID hash 见 `latest-full-reviewed-comparison.json`。

### 组合镜像，不混入交付

在忽略的 `client-candidate/` 中从新冻结归档提取 R7 客户端，再仅叠加独立打包修复的
`package.json`、`tsconfig.build.json`、`scripts/build-typecheck.node.mjs`。
两棵交付工作树不变，无锁文件修改。组合清单 `client-candidate-manifest.json` 的
SHA-256 为 `a88ab866f536b6b30117d7b4df3467ef02b11c31360a504c0f4d61bc73fdf683`。

第一次禁网构建退出 1，失败于 `npm ci` 的 `Exit handler never called!`，尚未进入类型检查，
原日志 `r7-client-composed-image.log` 保留。随后按原 Dockerfile 的正常网络模式构建成功，
该次 npm 依赖层实际命中缓存；未改源码、锁文件或构建断言。

组合镜像 `modelmirror-r7-client-with-packaging:20261003` manifest list 为
`sha256:589a2d9ecdfbfa762175eb7012f2da6cf9d9761ca6d034c5d5ffbe6376fbcd8a`。
专用容器退出 0，Node 22.22.3、无网络、非 root、只读根目录、无宿主端口。
六条页面入口和 JS/CSS 返回 200，运行配置为空，镜像不含源码/测试依赖，R7 代理文件存在。
模拟本地上游断连只派发一次，响应为 502、`generation_outcome=unknown`、`retryable=false`。
没有真实后端、Provider 或业务写入。组合构建通过**以独立打包修复为显式依赖**，
不宣称当前未经该依赖集成的 R7 分支已可独立打包。

## 人工与真实调用边界

旧证据见 [10 月 2 日收口记录](META_PLANNER_CW10_CLOSEOUT_20261002.md)。
固定候选 `proposal_05b916557b914a9fa46d4fdd7312d6a4` r2 的三种场景各两例，
已有路径和隔离 Update 效果证据。它仍绑定旧预览源码，不冒充本次集成后的真实复测。

剩余门禁必须单独处理：

1. 独立打包修复正式集成；临时组合镜像已验证，但两项变更仍未提交。最新基线后端全量已有终态回执，不再列为未完成项。
2. 最新基线上的完整人工修复教程重放、持久截图，以及明确授权的定向模型修复操作证据。
3. Insert/Delete 等未被固定 Update 验收覆盖的人工操作边界。
4. 必要时经精确授权的新基线真实生成/汇总；离线测试不能证明模型泛化成功率。
5. R7 交付范围审阅、显式提交授权，以及提交后 CI 和实际合并状态核验。

本次没有新增模型调用、活表写入、自动审批、发布、Commit、Push、PR、远程 Merge 或部署。
本地集成分支仅 fast-forward 到已核验的上游提交。
R8 前置门禁仍关闭。

本批交付清单保持 204 个路径，暂存区为空。相对最终测试冻结仅允许本记录与任务卡补充
结果；原 201 个 R7 路径及 Provider coverage 清单保持冻结内容。最终语法、范围、Diff 和
已知凭据签名复查见忽略目录内 `final-delivery-checks.json`。扫描范围不包含凭据文件或
Runtime Store，零已知签名命中不构成全面安全审计。

## 提交基线复核

用户确认门禁完成后优先提交 R7，再进入 R8。该授权涵盖相应的 Commit、Push 和 PR，
不涵盖远程 Merge、共享栈部署、新增付费调用或自动批准 Proposal。

### 前置提交与固定输入

独立打包修复提交为 `89b72c676ded01be7b5684052640967e9882c8ef`，
[PR #402](https://github.com/PinkElf-Elysia/ModelMirror/pull/402) 已创建并附加到任务。
本次只读检查其 Frontend quality、Backend quality、Windows Project Host 均为 SUCCESS；
PR 状态 OPEN，mergeCommit 为空。它不是 R7 的功能 PR，CI 也不能冒充 R7 的远端 CI。

当前 R7 集成树 HEAD 为该独立提交，上游为 `2c82d8a5`。相对旧冻结 `f34aca06`，
新增五个 Provider 验收路径和独立打包修复四文件，均与原 201 个 R7 路径无交集。
204 个待提交路径继续单独保留，R7 未 Commit、Push 或创建 PR。

新输入清单 `submission-inputs/inputs.json` SHA-256 为
`3c721d2b70314d963de96686c1fa130c8c99d6c353ee2fd39f47e6c43c872ebb`。
解包后 5,073 个源码文件集合 SHA-256 为
`063da186da6a8f65cad35b032363e9750f3129cb331e5690f0c072b66f95bbe5`。
全量及独立旧契约的源码、输入、V2 启动器摘要相同。Python 3.12.14、Node 24.18.0、
22 项直接依赖仍按锁定版本核对；测试容器无网络、非 root、只读根目录、4 CPU / 8 GiB，
不挂载凭据、共享业务卷或 Docker socket。旧基线回执没有重写或改标。

### 当前自动化终态

| 检查 | 结果及回执 |
| --- | --- |
| 剩余完整 `server/tests/` | 8,540 passed / 31 skipped / 0 failed / 0 errors，退出 0，pytest 2,120.60 秒；`linux-submission-full/results.xml` |
| `test_workflow_run_contract.py` 独立进程 | 7 passed，退出 0，9.41 秒；`linux-submission-legacy/results.xml` |
| `npm.cmd run typecheck -- --force` | 通过，完整应用及测试类型检查 |
| `npm.cmd run test:run` | 154 文件 / 1,194 项 Vitest 通过，546.34 秒；随后 17 项 Node 检查通过 |
| `npm.cmd run build` | 通过，包含本批帮助文字更新；保留大于 4,500 kB 的既有 chunk warning |
| `python -B scripts/check_provider_coverage.py` | 通过，80 entries / 935 candidates |
| `npm.cmd run verify:help-images` | 现有 23 篇文章的资产检查通过，不代表新截图已归档或整个帮助门禁完成 |
| Python 语法及交付范围复查 | 140 个 Python 文件、204 个待提交路径；Diff 检查通过，已知秘密签名零命中，暂存区为空 |

两个后端进程合计 **8,547 passed / 31 skipped**，不把 34 个 subtest 或重点集重复累计。
一次旧契约容器启动曾因重复传入 Python ENTRYPOINT 退出，尚未执行测试；修正调用参数后
重跑的终态见上表。产品、测试及断言均未修改。

与 `f34aca06` 完整 JUnit 对照：新增 23 项 `test_provider_acceptance` 全部通过；
一项既有 `test_python_port_runs_the_deployed_node24_worker_and_upstream_goal` 从 skipped
变为 passed，其测试源码未变、模型回调为模拟，不能作为真实模型或 R8 长运行证据。
其余 8,542 个精确身份及结果相同。五项时间戳型 XLSX 参数按未变测试源码、固定元数据、
唯一容器特征和预期错误逐一对应，两次均通过；不宣称动态二进制输入逐字一致。
未发现旧用例遗漏或结果退化。原始比较与审阅结果分别保存在
`submission-full-comparison.json` 和 `submission-full-reviewed-comparison.json`。
Windows 原失败及其同环境基线对照继续按上文报告，不改记为通过。

当前镜像上下文由新冻结基线与 overlay 提取，只叠加本批已审阅帮助文字，528 个客户端文件
逐一与交付树核对；不复制凭据、依赖目录、构建产物或 Store。其清单
`submission-client-manifest.json` SHA-256 为
`17490560a30a48436227e9e7b04bc382cb820a71187801ea2ce0ed1be8fcee8e`。
禁网构建未命中 npm 安装层，在 141.3 秒时报 `Exit handler never called!`，退出 1，
尚未运行 TypeScript。尝试核验停止本任务构建时进程已退出，身份检查拒绝停止，未影响其他任务。
按原 Dockerfile 正常模式重跑时 `npm ci` 层命中缓存，类型检查和镜像构建通过；
没有修改代码、锁文件或断言。两个构建日志分别保留为
`submission-client-image.log`、`submission-client-image-normal.log`。

新镜像 `modelmirror-r7-client-submission:20261003` 的 manifest list 为
`sha256:235bbe5bcad9f6fa907c622d6dda533d44a8222845b457d536f39417b01b74b3`。
专用禁网、只读、非 root、无宿主端口容器冒烟退出 0，Node 22.22.3；六条静态入口和
JS/CSS 资源返回 200，运行配置为空，镜像不含源码或测试依赖。模拟本地上游断连只派发一次，
502 回执保持 `generation_outcome=unknown`、`retryable=false`。
回执为 `submission-client-smoke.log`，此为真实镜像的静态与模拟代理证据，不是真实后端或 Provider 验收。
源码扫描仅覆盖交付路径的已知凭据签名，不是全面安全审计；缓存及回执均留在忽略目录。

### 最新离线人工操作

新预览 `15639/16639` 使用当前生产 Service/Store 与真实页面，但种子失败由受限合成响应构造，
明确标为 `synthetic_no_provider`。没有复制旧 Store、配对文件或任何凭据。预览保持禁网，
只允许本批指定候选的本地预览和人工修复路径；模型执行、评测运行、业务 CRUD、批准和发布仍阻断。

实际操作及状态核对：

1. 读取失败原稿诊断，定位 `write`；显式加入 `lookup.result -> write.records`，没有自动补线。
2. 预览通过后 Proposal 仍为 r1，payload digest 未变。
3. 第二个全新窗口仅依照帮助步骤重放到预览，保留旧 revision。
4. 首窗口确认应用后 r1 -> r2，状态 pending、validation valid，未创建或发布 Xpert。
5. 第二窗口的过期 Apply 被拒绝并锁定，要求重新加载；重新加载显示 r2。
   未捕获该次 HTTP 状态码，因此只记录 UI 拒绝及持久状态未被覆盖。
6. 只读 API 回查业务表零记录、Xpert 零条，另一个真实修复待用夹具仍为 r1 未变。

安全回执为 `gate-preview/manual-ui-receipt.json`。人工候选
`proposal_729760703835446c85f83481ecd39a8c` 的最终 payload digest 为
`46825094f5540e797b4b20d2a346f754be6ca0b256c24cd0eb51f49ff4c4b70c`。
本批还纠正帮助文字中的历史候选选择假设：页面实际自动恢复最近的待审批候选，
没有任意历史提案选择器。种子准备只通过正式 validate 更新排序，没有修改候选内容或伪造成功状态。

新截图已通过浏览器工具检查，但本机文件写出被权限拒绝，后续浏览器导出路径也被安全策略阻断。
没有绕过限制或伪造截图文件；当前没有新的可提交截图资产。工具内看见图片不等于归档完成。
除本记录、任务卡和帮助文章外，生产源码及交付清单保持冻结；帮助文字变化已执行前端构建。

### 当时剩余门禁（后续进展见下节）

- 一次真实定向模型修复：精确新候选、合成表元数据、模型、最多一次 completion 的授权问题待答复。
- 隔离 Insert/Delete 效果实测：精确合成数据及最多四次下游汇总的授权问题待答复。
- 用户需在新预览显式配置连接；未读取旧凭据，不沿用旧轮次额度授权。在配置另行开放前不要测试或保存连接。
- 同基线新截图归档和教程剩余重放尚未完成；不能以已有 23 篇图片规范检查代替。
- 以上完成后再刷新上游及前置 PR 状态、审阅暂存范围并提交 R7；提交后的 CI 与实际合并另行核验。

本次已提交的仅为独立 PR #402。R7 的提交门禁仍未全部通过，没有 R7 Commit、Push 或 PR；
没有新增模型调用、活表写入、自动审批、发布、远程合并、共享栈操作或 R8 开工。

## 真实修复与隔离写入回执（2026-10-05）

本节补充后续精确授权的操作，不改写前述历史边界。源代码仍为 submission 冻结；
2026-10-05 只读核查远端 `main=2c82d8a53c52fdec507ec5f81ffec5ebc577c750`，
PR #402 仍 OPEN，Frontend quality、Backend quality、Windows Project Host 三项 SUCCESS，未合并。
本批未增加产品补丁或生产依赖；只修改忽略的验收辅助文件以及本记录、任务卡和帮助文章。

### 一次真实修复成功，不覆盖上一笔不确定结果

用户自行保存新连接，仅调用已授权 OpenRouter / `deepseek/deepseek-v4-flash-0731`。
此为 legacy-compatible 路由，不等于 Managed 链路通过。

第一次 `repair_bb68ecbfdd044b96a987d23a28f9bbc5`：15,145 ms、uncertain、无响应、
`provider_dispatched=null`、usage 不可验证。原预约保留，不证明未派发或未计费；
缺少异常分类，不能仅凭时长把根因认定为连接超时。

用户独立追加授权后，第二次 `repair_3ee9988c63b042c89067f4594c99caf4`：
HTTP 200、应用耗时 12,367 ms、传输 11,984 ms、`suggested / repair_preview_passed`，
实际 usage 7,195（输入 7,159、输出 36）。安全观察器只记录阶段、状态码、耗时和异常类别，
不记录凭据、头、Prompt 或响应正文。第二次不是自动重试，也未清空第一次预约。

建议只连接 `lookup.result -> write.records`。真实 UI 载入编辑区并重新预览后，
结构、授权、资源类型、编译、发布预检及控制流证明均通过。未确认 Apply，
原提案 `proposal_0b9c5267ade94ebfa27d0a3c7091f4f9` 仍 pending/r1、`can_approve=false`。
因此证据只覆盖一次定向修复与人工预览，不包含修复建议应用、运行或生成成功率。

### 四项真实隔离效果

两份候选是通过正式编译、校验及 Store 建立的合成夹具，不是新 Planner 调用。
固定空合成表 `table_2bc0bec5e9f645f4953cab849b467386`，Schema v1 checksum
`ce3821170ccc7a38384f6bb8b878614252020aa4f9fd1e72ee6efad0f1e08422`。
初始化只使用获准的合成值，每例私有 Backend 独立；不复制活表记录。

| 操作 | 固定候选 / 数据集 v1 | 运行及结果 |
| --- | --- | --- |
| Insert | `proposal_8069eb7167544793a46f900d17dcb950` r1 / `xeval_dataset_3ed78f4eb8934375b4d8fabaaff3f1d6` | `xeval_run_6f9f7a1e64b44c15be5d004e9da6250c`，2/2 completed，效果均 verified |
| Delete | `proposal_7842dd762f8c483cad88fbcc2a1a0467` r1 / `xeval_dataset_43795341e94c4dd6bc8d83022cc8c407` | `xeval_run_12900fa1320f47ca91787653769e71dc`，2/2 completed，效果均 verified |

从正式评测 UI 选择固定候选、数据集和精确模型，重复 2、并发 1、每项模型调用上限 1、工具 0。
首次 UI 默认 Judge 未清除时，临时护栏在建 Run 及外发前拒绝；改为“不启用 Judge”后运行。
未放宽护栏，也未更改生产默认值。四项均一次真实汇总，持久预算预约四笔、HTTP 200 回执四笔。

| 私有项目 | 实际影响 | 最终私有行数 | 受控账本 |
| --- | --- | --- | --- |
| `xeval_item_b02c95cb84cc46fd9f40c7d651f23202` | Insert 1 | 2 | 1 |
| `xeval_item_43568a43457a438ca91501ea76392d83` | Insert 1 | 2 | 1 |
| `xeval_item_fd02de6862464c68be068d7644b492bb` | Delete 1 | 1 | 1 |
| `xeval_item_5efd06b9c74b4f58b209305c6ef084a3` | Delete 1 | 1 | 1 |

只读 SQLite 检查确认四个不同实例：业务值与预期一致、保护记录不变、剩余记录 revision 均为 1，
各一笔 `controlled.insert/delete` 的 request hash 与效果收据一致，非目标前后摘要一致。
Insert 两笔请求 hash 均为 `3e2ec67b05ebc924909080de56b9c7a40e185fef47a3b6a92c757fd992ff9471`；
Delete 分别为 `796df08b02da11b1e74839817e23e63fea07426da126fb49be091aa66ce18908`、
`47393dc85365bad38a36f344e2d383b30ffb7058724efec5a73f3de8c9af9dd6`。
合成活表记录数仍为 0。验证器路径修正及 WAL 沙箱读取限制是验收工具问题，不记为产品故障。

验证命令（无模型调用）：

```text
python -B .tmp-r7-closeout-20261003/gate-preview/verify_effect_acceptance.py
python -m pytest .tmp-r7-closeout-20261003/gate-preview/test_summary_gate.py .tmp-r7-closeout-20261003/gate-preview/test_connection_gate.py .tmp-r7-closeout-20261003/gate-preview/test_repair_gate.py -q -p no:cacheprovider
```

只读检查退出 0；护栏测试 17 passed。原始安全回执与私有实例保留在忽略目录，不提交原始数据、日志或账本。

证据限制：Delete 实际查询收到同表 Schema v1 的一条可信记录，但未配置独立读取断言，
`resource_evidence=missing`；不能说查询指标通过。四项 Token 仅为报告估算，没有答案文本指标，
不证明回答质量或精确计费。固定夹具通过不证明模型生成泛化稳定性，也不是跨重启真实效果验收。
此前 Update 和恢复证据仍只适用于其已记录的源码、候选及输入。

### 当前剩余门禁

本批文档修改后，`npm.cmd run verify:help-images` 通过（23 篇现有文章），
`npm.cmd run build` 通过（保留既有 4,500 kB chunk warning）。
`verify_delivery.py submission-effects-closeout-20261005` 退出 0：204 个待提交路径、
140 个 Python 文件语法通过、暂存区为空、Diff 通过、已知秘密签名零命中。
相对全量冻结只有本记录、任务卡和帮助文章变化，没有修改已测生产代码。
在正式页面依帮助文字切换 Insert/Delete 已完成报告并查看独立重复项，没有再次执行评测。
截图已在工具中查看，但仍没有可提交的本批截图文件；不把既有资产检查当作这项通过。

- 同基线截图文件归档仍被工具权限阻断；未用其他通道绕过，工具内图像不冒充可提交资产。
- 帮助文章更新后须完成可无副作用重放的查看步骤、文章与资产检查；不重复已耗尽授权的付费操作。
- 完成上述帮助门禁后再作最终源码冻结对照、暂存审阅、提交/推送/PR。#402 仍为明确前置依赖，
  未合并不宣称独立依赖已进入 main；不得自动合并。R7 远端 CI 和实际集成需后续独立核验。

本批没有活表写入、自动批准、发布、共享栈操作或 R8 开工；R7 无 Commit、Push 或 PR。

## 2026-10-06 用户截图归档

四张原图由用户直接提供，解除了此前工具无法导出截图的文件归档阻塞。
本批只增加四张原件、四张等比缩小 PNG、哈希清单及证据说明，并补充帮助引用和状态说明。
完整身份、转换参数与证据限制见 [截图归档](../help-center/evidence/r7-closeout-20261006/README.md)。
已逐张目视检查，原件逐字节保存；派生宽度均为 1000px、单张小于 250 KiB。
没有重新执行评测、调用模型、写活表、应用修复、审批或发布。
帮助资产检查通过（23 篇文章、四张新图全部合规），生产构建通过（3196 modules，
保留原有大 chunk warning）。`verify_delivery.py submission-screenshots-20261006` 退出 0：
215 个待提交路径、暂存区空、140 个 Python 文件语法通过、8 张 PNG 格式与 hash 通过，
Diff 检查通过、已知秘密签名零命中。与全量冻结对照，只有三篇收口文档变化；
额外路径严格限定为八张截图、清单、证据说明及帮助索引的一行基线日期更新。
旧冻结不覆盖、不重写，已测运行代码没有变化；本批不冒充新一次后端全量。

## 2026-10-07 帮助页最终复核

- 直接执行 `node node_modules/vitest/vitest.mjs run --configLoader runner --maxWorkers=1 --fileParallelism=false src/content/help-center/helpContent.test.ts src/pages/HelpCenterPage.test.tsx`：2 文件、58 项通过。
- 首次误用 `npm.cmd run test:run -- <两个文件>` 时，参数被包含 `&&` 的 package 脚本传至尾部 Node 命令，实际启动了全套 Vitest；已停止，退出 1，不计为全量通过，不归因为产品失败。随后上述正确入口通过，没有修改测试脚本或断言。
- 恢复后原预览进程已停止，仅启动本任务 `127.0.0.1:15639` 的前端；未启动后端或读取凭据。
  新标签打开帮助文章，DOM 确认四图 `complete=true`、自然宽度均 1000，实际截图确认图文位置正常。
  这次仅复核静态帮助展示，不冒充再次重放修复、真实调用或效果评测；这些证据沿用前述同代码记录。
- 2026-10-07 只读查询 #402：仍 OPEN、未合并，Frontend quality / Backend quality / Windows Project Host 三项 SUCCESS。
  前次 `git ls-remote` 确认 main 为 `2c82d8a5`；依赖合并后仍须重新刷新，不能直接继承未更新的集成结论。

截图文件阻塞已解除，无需用户再截图或重复付费验收。当前停止在前置依赖合并边界，
不自动合并 #402，不把含前置补丁的分支直接作为无依赖 R7 PR 提交。
依赖合并后核对上游与冻结源码差异、按影响补测、审阅暂存清单并按既有授权提交/推送/创建 PR；
Windows 已复现的基线失败继续单列。R7 远端 CI、最终主线合入与 R8 开工仍未完成。

## 2026-10-07 前置合并与提交基线

用户确认合并后，只读 API 核验 #402 为 MERGED，合并时间 `2026-10-07T12:16:40Z`，
合并提交 `cff3f28d1f7f42abdc19ff68d694742ba6c36424`。其分支三项 Quality 检查均 SUCCESS；
这不是尚未创建的 R7 PR 的 CI 结果。

执行 `git fetch origin main` 后，`git diff --stat 89b72c67 origin/main` 为空；
两个提交 tree 均为 `5f234ab0ffb04f3c9dfde9f11850b19747b19ca4`。
仅对本任务分支执行 `git merge --ff-only origin/main`，保留全部 R7 未提交内容；
不重建共享栈、不重跑模型或写入，不修改原冻结输入和测试结果。
最终检查器只允许这个已核验的合并提交，并再次证明 tree 相等与祖先关系，
其他上游变更仍拒绝。测试结论按同一文件内容继承，不宣称重新跑了全量。

本 PR 提交范围为 215 个既定路径，包括此前分批完成的受控写入、隔离评测、
确定性生成描述展开、失败候选恢复和显式定向修复，以及文档和八张原件/派生截图。
超过五文件是累计交付，不是本次新增功能批次。本次合并后只更新任务卡及本记录，
不新增能力、依赖、业务数据、模型调用或 R8 工作。

待提交后的远端 CI 和实际合并仍需单独核验。真实调用中 uncertain 记录不改为成功，
固定样例与编译夹具不被解释成普遍生成稳定性，Windows 对照失败仍如实披露。

## 回退

仅保留独立集成树和测试证据，继续使用未修改的原工作树与原预览即可；
不需要迁移数据、不撤销业务写入、不清理其他任务的服务或工作树。
