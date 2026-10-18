# R8-B B2 剩余门禁收口

> 历史阶段记录：本文保留旧基线、原失败和当时的待办，不代表最新待办清单。最新基线 `253ad98a` 的全量、Provider 集成及三条纯计算预览已完成；当前判定与未关闭项以 [本地交付材料](META_PLANNER_R8B_DELIVERY_PACKET.md) 为索引。图片、最终帮助验收和提交授权仍未关闭，未创建 PR。

## 2026-10-18 续收口范围

- 起点仍为 `HEAD@99e50f99`，已有全部 B0/B1/B2、Provider 清单、目录与截图差异原样保留，暂存区不作变更。
- 本批只补原生 Windows 验证、核对上游交集、整理剩余门禁；更新本卡及已复现的平台测试断言，不新增 Planner/Headless 或资源权限，不修改生产实现。
- `git fetch origin` 后远端为 `253ad98a`，新增 PR #419 Provider 可信来源持久化与 #420 模型目录更新，共 115 个文件。先审查语义交集，不直接合入脏工作树，不用旧全量代表新上游集成。
- 验收：Windows 原生 Applier/Project Host 及临时文件清理测试；核对 Provider 父身份、恢复校验与 B2 根预算/子调用桥；核对上游是否已收录截图修复。测试只写新建临时目录，禁用外部模型和实际业务操作。
- 本机仅发现 Python 3.13.9；Windows 结果单独标明版本，不能代替此前 Python 3.12 Linux 全量或远端 CI。没有新增依赖、公共接口或数据迁移。
- 回退只撤销本批台账增补和平台测试断言修正；不清理旧证据、Store、工作树或共享预览。

### 本次核验与待集成事项

- Windows Python 3.13.9 原命令为 `python -B -m pytest -p pytest_asyncio.plugin server/tests/test_coding_applier_windows_permissions.py server/tests/test_coding_applier_tempfile.py server/tests/test_coding_project_host_windows.py server/tests/test_coding_project_host.py::test_host_commit_windows_native_apply_commit_and_undo server/tests/test_coding_project_host.py::test_host_commit_windows_private_object_cleanup_rejects_replacement -q -ra --tb=short -p no:cacheprovider --basetemp <独立临时目录>`。原结果 **99 passed / 1 failed / 1 skipped，101.10s**；跳过原因为本机不能创建目录符号链接。
- 失败定位为 `test_prepare_success_preserves_content_and_closes_descriptor` 把存在 `os.fchmod` 误认为可验证全部 POSIX 权限位。当前解释器确有原生 `fchmod`；Python 3.13 开始在 Windows 支持它，但只控制只读属性。`engine.py` 与两个权限测试在 `HEAD..origin/main` 无差异，B2 也未修改它们。不是据失败数量推断为 B2 回归。
- 仅修正测试：POSIX 继续核验完整权限位，Windows 核验写权限位；新增模式覆盖。Windows `_prepare_temp_file` 对只读模式原本就应拒绝且不创建临时文件，不能因扩大测试而放宽这项生产保护。原子替换、句柄所有权及错误清理实现不变。文档参考：<https://docs.python.org/3.13/library/os.html#os.fchmod>、<https://docs.python.org/3.13/library/os.html#os.chmod>。
- 扩展测试首次把只读模式也误列为 Windows 成功路径，得到 **101 passed / 1 failed / 1 skipped，102.18s**；核对现有只读拒绝分支后改为断言 `PermissionError`、无权限调用且临时目录为空，没有修改生产保护。随后最小测试 **7 passed，0.33s**；原完整 Windows 命令复跑 **102 passed / 1 skipped，101.07s**，唯一跳过仍是宿主目录符号链接能力。新结果不是一次未经失败的全绿，也不抹掉原失败证据。
- 同一修改文件在缓存 `modelmirror-r9a1-linux-validation:4932e3b4` 的独立禁网、只读容器中复跑 `python -B -m pytest server/tests/test_coding_applier_tempfile.py -q -ra --tb=short -p no:cacheprovider --basetemp /tmp/pytest`：**7 passed，4.85s**。`server/` 为当前工作树只读挂载，临时数据仅在 tmpfs，不使用镜像中的旧权限实现。未重新执行本轮全部后端测试。
- 最新上游 #419 在 Managed Workflow Gateway 加入可信 Provider origin、恢复来源校验和稳定子任务身份；B2 根预算委托不是 Provider origin。部署子调用的实际来源、actor 与父 origin 必须在集成时明确并测试，不能把预算 task ID 直接塞入 Provider execution ID。当前只完成静态交集核对，尚未证明新上游与 B2 联合运行通过。
- Provider 清单存在语义重叠：本地 `scaledown_text` 与上游 `openrouter_scaledown` 指向同一入口，集成不能保留双份入口；须保留上游 `model_delegate` 分类、可信来源审计及本地 B2 委托改名反例，不直接刷新 hash 放行。
- 上游与本地 ScaleDown PNG 完全相同：163489 bytes，SHA-256 `6d7b2580e2fef50cd4fa4408e7df159747cff6bd3edd7a51c747d861cbec7460`。Jev PNG 不同：本地 224561 bytes / `f34bcf8e8278176cd1dee9647a799033354b7bd0f0c56409985e9ea4cf66d179`，上游 143512 bytes / `f3cedc73d999a2e14df2d66f15ed5e600721a8f08dd4ac4db01056876b086798`；公开引用虽都改为 PNG，仍须核对可读性与原图归档再去重。
- 上游 worker 锁文件将 `js-yaml` 从 4.3.1 升至 4.3.2；既有验证镜像不能凭旧锁文件缓存冒充最新集成环境。下批须先确认新锁定依赖与构建产物，再运行对应测试。
- 当前未合入这 115 个上游文件；旧全量仅证明 `99e50f99 + B0/B1/B2` 快照。新上游语义集成、集成后重点/全量和同基线独立预览仍未关闭，不据本批通过项宣称 PR 就绪。
- 本次最终 `git diff --check` 通过，暂存区为空。本批仅新增一处测试文件修改和本卡证据；没有提交、推送、PR、真实模型调用、业务写入或共享栈变更。

## 范围与基线

- 2026-10-15，从 `ec79e120` 核对并快进到 `origin/main@99e50f99`。上游与 B2 的 `main.py` 交集仅为模型目录枚举，恢复 B2 差异后仍为 135 行新增、15 行删除。
- 本任务的单文件恢复备份为 stash `f542ed5929522a78aa9afa8f3d0f2a9be708e079`，暂时保留；未触碰其他 stash、未提交或推送。
- 本批仅关闭已有 B0/B1/B2 的审计与验证缺口，不进入 B3，不开放 Planner 节点、不执行外部模型调用或业务写入。
- 允许修改：本卡、Provider 清单与其离线反例测试、独立进程恢复测试。每个目标先复现再修改；不批量刷新未审查的 hash。

## 验收与回退

1. 区分最新主线与本批 Provider 漂移，逐个核对出口、身份、授权和回执边界后才登记。
2. Python 3.12 离线运行 Provider 清单、反例、R8/预算/部署回归及全量后端；前端使用锁定依赖验证构建。
3. 独立进程模拟崩溃与重载，验证未知请求不重发、完成结果复用、输入漂移及根预算约束。
4. 记录运行环境缺口及独立预览未覆盖项，不以模拟 Provider 作为真实模型验收。

回退仅撤销本批审计或测试改动，不删除 Store、请求摘要或未知请求证据。B2 运行代码与上游快进分别保留可核对差异。PR、部署、外部调用不在本次默认权限内。

## Provider 逐项核对

对照清单最后一次修改 `eb13d6ea`、当前干净主线 `99e50f99` 和 B2 差异：

| 模块 | 来源与审查结果 |
| --- | --- |
| SpeechWorkspace | 主线新增 HeyGen 默认语速限制、UTF-8 字节费用展示；请求仍走同源后端 |
| speechAudio | 主线只新增三条公开声线名称；无新请求 |
| models | 主线目录、价格、展示顺序及 ScaleDown 专用入口元数据；本模块不派发请求 |
| ScaleDownPage | 主线新增同源 `/api/scaledown` POST；不把服务端模型调用误记为普通 HTTP |
| audio_catalog | 主线新增 Argon 输入契约，仍标记未真实调用；原目录出口不变 |
| image_catalog | 主线从通用图像目录排除 ScaleDown；不增加出口 |
| stt | 主线新增 AssemblyAI 目录及调用前完整 WAV 校验；现有 Managed/legacy 出口不变 |
| tts | 主线公开声线、速度限制、44.1kHz 响应校验；现有派发出口不变 |
| main | 主线新增 ScaleDown HTTP client 和真实模型 POST；B2 将既有 Runner 委托移入受所有权保护的 body，并传递预算委托 |

ScaleDown 单独登记 `scaledown_text / migration_pending`：无统一工作负载授权、无持久逻辑操作 ID、无 Managed 回执和 UsageEvent；超时未知且不自动重试。登记是源码覆盖，不赋予 Managed 资格，不在 R8-B 中修复其治理缺口。

清单增加 1 个业务入口、净增 3 个候选，更新 9 个已核对模块摘要。扫描器和 Workload Registry 未修改。新增反例要求 ScaleDown 必须为模型派发而非 HTTP 豁免，B2 委托再次改名必须重新触发未审查与模块漂移。

## 验证记录

- 最新基线 Provider 扫描通过：81 entries / 947 registered candidates。只证明源码覆盖，不证明 Provider 可用或治理完成。
- 本机现有锁文件离线安装前端及嵌入式 RPG 依赖；`npm.cmd run build`、`npm.cmd run typecheck` 通过。构建保留大 chunk 警告，未调整阈值掩盖警告。
- 进程反例先出现 1 failed / 38 passed：根预算耗尽产生了新的失败子执行，测试误用 Store 列表首项作为原子执行。修正为按崩溃前记录的固定子 ID 核对，生产代码未改。四项独立进程测试复跑 4 passed，71.02s。
- 四项分别覆盖：完成后 `os._exit(73)` 再启动复用；改输入拒绝；重新认领后下一子项仍受已耗尽根预算阻断；模型派发计数持久化后 `os._exit(73)` 未结算结果拒绝重发。每项新进程禁止 socket connect，独立文件账本始终只有一次合成派发。
- 全量验证环境的首次执行缺少 Agency worker 构建且缺少临时容器降权权限；在 38 failed / 2084 passed / 22 skipped 后主动中断（354.43s），不作为代码回归结论。首个 Agency 失败已独立复现 `build output is unavailable`，其他权限失败显示容器内 chown/setuid 被拒绝。
- 环境准备另遇 Windows 绑定盘读取 ENOMEM、PowerShell 归档空条目及缓存 `.bin` 符号链接被解引用；改用原生二进制归档流、校验 Git 文件数量、保留依赖符号链接，且复制前确认两个 Node 锁文件完全一致。全量复跑结果待记录，不减少测试或跳过失败项。
- 最终全量环境为无网、只读根文件系统的独立临时容器；为既有降权/所有权测试仅补充 CHOWN、SETUID、SETGID、DAC_OVERRIDE、FOWNER，仍禁止提权且不挂载主机数据。Agency worker 先构建，`npm run test:all --prefix server/orchestration_worker` 通过；并非共享栈部署。
- 归档发现一个中文文档 `docs/工作流UI优化-阶段汇报.md` 遗漏，已在测试到达该阶段前补入并核对原件 SHA-256 为 `f648a1f978d659b5c08299394043a5207884da05a272c4b618d655df5196b15c`；没有忽略归档警告或把缺文件环境当成完整验证。
- 主机与全量容器十个关键源文件/清单的 SHA-256 一致；九个相关 Python 文件在 Python 3.12 中 UTF-8 / AST 检查通过。
- Provider 独立 `unittest discover -s server/tests -p test_provider_coverage.py -v`：25 passed，4.600s；包含新出口分类和委托改名反例。仍仅证明源码审计，不证明 ScaleDown Managed 就绪。
- 用户批准独立修复最新主线三项过期目录断言和两张帮助截图；范围、反例、原图归档及验证见 `MAIN_CATALOG_HELP_GATE_20261015.md`，不修改 B2 生产代码。
- 扩大快照一致性核对：`server/`、`scripts/`、`experiments/` 下 Git 清单内 3770 个文件逐一 SHA-256 比对，主机与全量容器无缺失或差异；不会把镜像旧代码当作本轮结果。
- 前端修复后全量：162 files / 1265 tests 通过（424.26s），随后 17 项 Node headers/build-typecheck 通过；帮助图片 32 篇通过，构建通过。上述主线失败未删测、未调整检查阈值。

## 收口判定边界

- B2 本批验证命令为 `python -B -m pytest server/tests/ -q -ra --tb=short -p no:cacheprovider`，不删减测试范围；最终 **10455 passed / 36 skipped / 8 warnings / 45 subtests passed，2564.93s**。测试进程与验证容器退出码均为 0。
- 外层归档包装命令仍退出 1：此前 tar 的空条目/中文文件告警不会因事后补入而改写其退出码。中文文档已补入并核对哈希，3770 个后端、脚本及实验文件逐一比对无差异；因此如实记录为“pytest 通过、归档包装存在已定位告警”，不把整个外层命令写成全绿。下次完整环境构造须先消除归档告警再启动测试。
- 后续独立复核已消除归档输入缺口：以 Git NUL 清单构造 JSON，Python 从二进制 stdin 解码 UTF-8，使用 Windows 扩展绝对路径读取源文件；tar 仅保存原相对路径。无网只读新容器不提取、不执行源码，仅逐文件流式校验 SHA-256。最终 **5391 files / missing 0 / hash mismatches 0，archive exit 0 / verifier exit 0**，包含中文和超过 260 字符的路径。该结果是归档复核，不声称重新执行了完整 pytest。
- 临时核验脚本首次误用 TarFile 迭代入口，随后明确定位到 stdin 本地编码及 Windows 长路径问题；失败均保留，终止的只有已核验属于本次脚本的阻塞生产进程。未修改仓库生产源码、未隐藏缺文件、未触碰其他预览容器。
- 36 项跳过由原测试环境条件触发：16 项 Windows 原生句柄/Host、5 项固定语言服务器镜像、1 项文件资产 Office sidecar、11 项 Office parser、1 项隔离 renderer、2 项缺失 docx/matplotlib。未增加 skip/xfail；这些平台/专用镜像路径未由本次 Linux 全量验证。
- 8 项警告包含 FastAPI 生命周期弃用、PyPDF2 弃用、Pydantic lifespan 前向引用、Dify 重复 OpenAPI ID 和多线程进程 fork；没有关闭警告或修改阈值。
- B0/B1/B2 局部门禁与整个 R8-B 交付分开判定。B1 实际资源 Store 权威接入、后续顺序项恢复、Planner/Headless 开放不在本批中提前实施。
- PR 前仍须完成受影响恢复行为的同基线独立预览及帮助说明，并重新核对上游；本批的离线进程崩溃测试不是浏览器人工验收或真实 Provider 证据。
- 本批没有真实模型调用、业务写入、提交、推送、PR 或部署。无新生产依赖；回退不得删除原发生标识或账本后自动重发未知请求。
- 最终 Diff 空白检查通过，暂存区为空；变更路径未包含 Runtime Store、数据库、上传、日志或构建产物，有限密钥模式无匹配。原始 JPEG 留在文档证据目录，公开引用仅使用已核验 PNG。有限扫描不等于绝对无敏感信息证明。
