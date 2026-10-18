# R8-B 当前集成基线全量门禁

## 范围与回退

- 2026-10-18，独立分支 `codex/r8b-provider-origin-integration`，主线基线 `253ad98a537dd71e6c41761d89ff03d9e6e86d4e`。保留原 B2 工作树，当前树的 Provider 集成和未提交内容不回退。
- 用户批准本批完成全量后端、前端构建与测试，失败先定位再最小修复；不提前开放 Planner 节点。人工预览、帮助重放集中在 PR 前；本批不提交、不推送、不部署、不调用真实模型或业务写入。
- 修改范围为本卡、明确复现的三个前端目录测试及一个 Windows 临时文件权限测试，共五个文件。其他失败必须单独定位，不为通过门禁更改业务契约、快照、检查强度或新增 skip。
- 回退仅撤销独立断言修正；不改变生产模型目录、Provider 许可或 B2 执行行为。测试产物放在仓库外 `C:\tmp\modelmirror-r8b-gates-20261018`，不纳入提交。

## 环境核验

- 前端使用当前锁文件离线 `npm.cmd ci --offline --no-audit --no-fund`；根 RPG 与 `card-replica` 依赖也按各自锁文件安装。初次 typecheck 因缺少后者的 React 类型失败，补齐依赖后原 `npm.cmd run typecheck` 通过，无源码修正或降级。
- 后端使用既有 Python 3.12 / Node 24.18 验证镜像，但只运行 Git 白名单归档的当前源码。5426 文件经提取后逐一 SHA-256 核验，零缺失/差异。初次 Windows 文档名归档因文本 stdin 解码失败，改为明确二进制 UTF-8 后重新生成完整快照，未遗漏中文文件。
- 上游 Worker 锁定 `js-yaml 4.3.2`；旧镜像仅缓存 4.3.1，离线安装明确失败。随后从宿主缓存导出 4.3.2 原始包，与锁文件 SHA-512 integrity 精确比对后补入临时容器缓存，不联网或改锁。
- 临时盘默认 `noexec` 导致 esbuild 安装自检 EACCES，已用 `/proc/mounts` 复现。仅对本批隔离测试容器临时盘设 `exec,nosuid`；保留无网、只读根文件系统、no-new-privileges 和最小文件权限测试能力，不影响共享栈。
- 第三次环境运行已开始 pytest；补查跨模块依赖时明确复现 `ui-host/setup.mjs` 缺少 `ajv`。主动停止该自有容器并保留中断日志；第四次先按锁文件安装 RPG 依赖，再从头执行 Worker 构建、测试及完整后端。中断进度不视为通过。
- 第四次前段 14 项失败归属 Coding Project 三个文件。单例复现明确为默认 `/project-uploads` 在只读根文件系统中不能创建；独立探针仅设置 `CODING_PROJECT_UPLOAD_ROOT=/tmp/probe-project-uploads` 后，三个文件 **24 passed，2.09s**，生产源码未改。第四次在约 35% 主动停止并保留日志，不计为全量结果；第五次将上传临时目录固定在容器 tmpfs，重新归档全部最新源码后完整复跑。

## 前端失败归因与最小修复

- `npm.cmd run test:run` 首轮：**3 files failed / 161 passed；4 tests failed / 1297 passed，433.12s**。因 Vitest 失败，后续 Node 测试尚未执行。
- 三个失败文件及模型目录在复现时相对 HEAD 无差异，属于基线既有断言漂移，不归因于 B2。定向复现保存到 `catalog-repro.log`。
- `models.oct10.test.ts`：已合入的 Step 5 `context_length=1048576`，旧值为 1000000；付费 Sante 与免费档都在 `uncertainCatalogModelIds`，旧断言仍要求付费档为 live。只修正预期，继续检查 active、原 ID、能力、价格及入口。
- `models.oct12.test.ts`：旧 `~perplexity/pplx-decider-27b-latest` 已列为 uncertain，其他决策模型仍 live；改为逐 ID 精确预期，保留单价、专用入口与图片未适配说明。
- `models.oct13.test.ts`：#420 / `99612f4e` 将 HeyGen Turbo 单价由 7.5 改为 4，`docs/OPENROUTER_UPDATE_2026_10_15.md` 及新增 October 16 测试均明确同一事实。只修正旧断言，不改变字符计价单位、其他声线价格或模型能力。
- 前端生产构建首次通过，Vite 16.20s，原大 chunk 警告保留；断言修正后的定向、全量、完整类型检查和构建需再次记录，不用定向通过替代完整命令。

## 本轮前端复跑结果

- 修正后目录定向测试：**4 files / 26 tests passed，8.87s**。
- 完整 `npm.cmd run test:run`：**164 files / 1301 tests passed，422.31s**；原命令后续 **17 项 Node headers/build-typecheck 测试全部通过**，退出码 0。
- `npm.cmd run typecheck` 与 `npm.cmd run build` 再次通过，后者 Vite **14.88s**；保留原大 chunk 警告，没有修改警告阈值。
- `npm.cmd run verify:help-images`：**33 篇已注册文章通过**，无未注册草稿；仅为资产自动检查，不代替同基线人工预览、可读性或帮助重放。

## Windows 独立回归

- 当前集成树原临时文件测试再次得到 **1 failed / 4 passed，0.66s**。失败仍为 Windows 原生 `fchmod` 仅控制只读属性，测试却要求 POSIX `0640` 完整权限位；生产实现仍明确拒绝只读目标。
- 移入原 B2 树已经定位的单测试修正：POSIX 保留完整权限位断言，Windows 校验可写位，另覆盖只读拒绝且不创建临时文件。句柄关闭、内容及异常清理断言保留，不改生产实现。本次结果须重新验证；此前原树通过不替代当前集成证据。
- 当前原生 Windows Python 3.13 专项复跑：临时文件、权限、Project Host Windows 及两项 Windows 原子提交/清理用例，**102 passed / 1 skipped，110.79s**，退出码 0。唯一跳过为宿主不支持目录符号链接；不将其描述为已验证。`99e50f99..253ad98a` 的生产权限实现和原测试无差异。

## 最新全量后端与审计结果

- 第五次完整归档 **5427 个文件**，提取后逐文件 SHA-256 核对零差异；包含本批 Windows 测试修正。Worker 使用当前锁文件离线安装，构建及 `test:all` 先行通过，随后执行完整 `server/tests/`，没有选择性删测。
- 命令等价于 `python -B -m pytest server/tests/ -q -ra --tb=short -p no:cacheprovider --basetemp /tmp/pytest --junitxml=/evidence/backend-full-05.xml`。结果 **10806 passed / 36 skipped / 8 warnings / 50 subtests passed，2176.54s**。pytest、容器及外层包装命令均退出 **0**；完成回执为 `2026-10-18T14:34:41.655Z`。
- 从本次 JUnit 核对 R8-B 准入、调用恢复、进程恢复、Provider origin、Managed Workflow 和根预算九个文件的 **185 项用例，零失败、零跳过**；其中 R8-B 五文件 115 项。沿用上一批身份继承、根预算及不确定请求不重发的反例，没有只根据总通过数判断这些边界。
- 36 项原有条件跳过：16 项 Windows 原生句柄/Host、5 项固定语言服务器镜像、1 项 Office 文件 sidecar、11 项 Office parser、1 项隔离 renderer、2 项缺少 docx/matplotlib。Windows 另有上述原生专项；其余专用环境仍未验证。没有新增 skip/xfail 或安装生产依赖来隐藏边界。
- 8 条警告为 FastAPI 生命周期弃用、PyPDF2 弃用、Pydantic lifespan 前向引用、Dify 重复 OpenAPI ID、多线程进程 fork；未屏蔽警告。
- 当前快照 **1171 个后端 Python 文件**以 Python 3.12 AST 语法检查通过。原 Provider 审计扫描 **81 entries / 947 candidates / 0 errors**，仅用 Git 白名单替代容器不可用的 Git 路径枚举；没有更改检测规则、来源分类或清单。本批仅测试与文档修正，不产生新 Provider 出口。
- 帮助截图遗留已核对：继续采用上游 Jev PNG `f3cedc73d999a2e14df2d66f15ed5e600721a8f08dd4ac4db01056876b086798` 与 ScaleDown PNG `6d7b2580e2fef50cd4fa4408e7df159747cff6bd3edd7a51c747d861cbec7460`，两图均打开核对可读性。两个原始 JPEG 的哈希与上游帮助记录相符，不另加原树重复转码图、不删除原树。它们仍是历史基线截图，不声称当前 R8-B 预览证据。
- `git diff --check` 通过，暂存区为空；21 个变更/非忽略新增路径的禁止产物与有限密钥模式扫描零命中。有限扫描不代表绝对无泄露证明。远端 `main` 再核验仍为 `253ad98a537dd71e6c41761d89ff03d9e6e86d4e`。

## 判定与 PR 前剩余项

> 本节保留全量结束时的状态。后续三条授权纯计算预览及帮助检查已记录在 [预览台账](META_PLANNER_R8B_PREVIEW_GATE.md) 与 [本地交付材料](META_PLANNER_R8B_DELIVERY_PACKET.md)；下文“尚未执行”仅指当时阶段。图片、最终帮助验收及本批提交授权现已关闭，远端 CI 另行核验。

- **本批当前集成基线的全量后端、前端完整测试、类型检查、生产构建、Provider 审计与源码语法门禁通过**。首轮失败及中断环境证据保留，不将整个过程描述为首次全绿。
- 人工同基线独立预览、受影响恢复行为及帮助说明重放集中留在 PR 前；尚未执行，不以离线自动化或历史截图代替。
- 专用 sidecar、语言服务器镜像及宿主目录符号链接条件仍需单列；本批不声称这些未执行环境通过。
- PR 前再次核对上游、最终差异、Help Center Impact 与提交授权。B1 实际资源权威接入、后续顺序迭代和 Planner/Headless 开放不混入本批。
- 本批未提交、推送、创建 PR、部署、调用外部模型、写业务表或操作共享栈。Planner 保持 22 类，子流程与迭代仍未开放。
