# R9B3：资格系列、续期与历史纪元

## 开工契约

- 基线：`origin/main@114461d3a63ef5c5e0dc8053e66a3ebf1356b0f3`；Fetch 成功，B2 PR #407 已合并。相对上一基线只有 B2 合并，无其他交叉变更。
- Worktree：`C:\tmp\modelmirror-control-r9-b3`；分支：`codex/provider-control-r9-b3`；开始时干净。主工作区、其他工作树、预览和真实数据不动。
- 单一目标：分离认证事件、稳定资格系列与连续有效区间；新认证默认 30 天；同配置按时续期可延续批准，空档、漂移和硬失败不能被续期掩盖。
- 已证实：v18 Chat/Workload 认证只保存 completed_at，没有原 TTL/到期时间；Chat Control/Canary 在读取时使用环境 TTL，默认 24 小时；Workload 复用 Chat 时间判定。批准、派发及 Canary 统计多处直接依赖单次 certification_id。
- 用户决定（2026-10-08）：无法证明旧 TTL 时，保留原记录并标记到期未知，不继承资格、批准或门禁样本；不按当前环境或旧默认值猜测。补齐可信证据或重新认证后才能恢复，绝不自动回 legacy。

## 范围与风险

- 允许：`server/model_router/` 资格、认证、批准与派发校验；相应 schemas/API 的加法字段；专用离线迁移命令与测试；Settings 的资格解释及文档/帮助。
- 禁止：模型 POST 协议、SSE、模型数量口径、默认 Provider、R5 的 500/14 门槛；健康调度、会话、主体、草稿、用量与计费等后续批次；生产依赖。
- 高风险：纯加法 SQLite 迁移与批准连续性。先合成数据库测试，沿用 B2 写锁、Backup API、只读维护边界。真实数据迁移、部署和付费调用另行授权。
- 一次实现小批最多五个文件。新内核先独立验证，不在第一小批改变现有准入或 Schema。

## 实施顺序

1. 任务卡、纯函数资格内核、时间/漂移/失败反例测试（三个文件）。
2. 顺序加法迁移及 Repository：冻结认证 TTL、稳定系列、连续区间、不可变事件关联；失败和 uncertain 保留，原记录不改写。
3. 同事务完成认证事实与资格更新；Chat/Workload/多模态/Batch/Realtime 全部终态覆盖，API 加法返回资格信息。
4. 批准、派发与 R5 观察纪元消费同一连续资格；Receipt 保留真实单次认证引用。Canary 不得被续期清除硬失败。
5. 历史映射先只读 dry-run，逐项给出可继承/不可继承理由；apply 要求显式授权及一致性复核。旧记录和旧样本不修改。
6. 文档、用户帮助、全量门禁与经授权的独立无付费预览；停在提交前。

## 验收与回退

- 先运行 `python -m pytest server/tests/test_provider_qualifications.py -q`，再新增 Repository/服务测试、R5—R8 受影响组及全量测试；任何失败按当前基线复现，不继承 B2 豁免。
- 必测：30 天/较短 TTL/非法 TTL；续期在到期前、恰好到期、到期后；租户及身份隔离；配置往返漂移；失败/uncertain/硬失败不可延续旧批准；旧期限未知；并发、重启及只读 dry-run 零写入。
- 持续有效区间与资格系列分别标识：相同身份可保留系列，但存在空档或失败必须建立新区间；批准和观察证据不能只检查系列 ID。
- 无真实数据和网络回填，不自动启用/认证/延长旧期限。回退保留新增表；旧代码读取及不重放必须验证，不能用整库快照覆盖新运行事实。
- 新资格迁移若不能证明安全恢复、历史证据被错误继承、重复 POST、dry-run 修改状态，立即停止当前门禁。

## 验证状态

实施中；自动测试、迁移/回退、全量、预览均未完成。未 Commit、Push、PR、部署或执行付费调用。

### 第一小批（2026-10-08）

- 新增不联网、不读环境、不写数据库的 `qualifications.py`。系列身份包括租户、认证来源、连接/配置指纹、精确模型、形态、契约、Adapter、协议和 Profile；单次认证 ID 不进入系列身份。连续区间另行标识，不因同系列而推定连续合格。
- 纯函数测试先因模块不存在而红测，首轮实现 38 passed；补充事件重放、较短续期、配置不能延长已存期限、多轮时间乱序后 42 项通过。
- 与回滚边界及原 Chat Control 测试合跑：`53 passed in 4.27s`。命令：`python -m pytest server/tests/test_provider_qualifications.py server/tests/test_provider_qualification_migration_boundary.py server/tests/test_provider_chat_control_service.py -q -p no:cacheprovider --basetemp=C:\tmp\modelmirror-r9b3-tests-20261008-01`。
- 首次合跑因系统 pytest 临时目录权限失败（43 passed / 10 setup errors）；没有改权限或跳过测试，改用已确认不存在的本批专属临时目录重跑原组通过。
- 回滚预检已证实：B2 `migrate_schema` 拒绝 `user_version > 18`，错误为 `provider_storage_schema_newer`；合成数据库拒绝前后字节哈希不变，未创建迁移备份。不能声称 B2 原二进制可忽略下一版本运行。
- 当前暂停第二小批存储迁移，等待回滚兼容方案确认。建议使用明确审核的 B2 兼容回滚包（只接受本次已知加法 Schema，不降版本、不删新表；先显式停用受影响 Managed 入口），而非放宽任意未来 Schema 检查。原 B2 原样回退只允许安全拒绝启动，不算服务恢复成功。
- 当前仅四个新增文件；现有认证、策略、批准、Schema 和运行路径未改。第一小批通过不代表 B3 集成或迁移门禁完成。

### 回滚方案确认后的基础实现（2026-10-08）

- 用户已明确确认最小兼容回滚包方案；授权不包含部署、真实数据迁移、提交或付费操作。
- 第二小批：v19 四张纯加法表（系列、连续区间、事件投影、历史纪元映射）、严格回滚检查及迁移测试。建表参与事务；不回填旧认证，也不重写连接、凭据或旧证据。迁移前沿用 B2 Backup API。
- 第三小批：固定 B2 源码包生成器、往返验证和既有迁移测试。生成器锁定 `114461d3` 及 Repository Git blob `d097d68786bcb1d21ae585bb2063ddca4f7d4ede`，仅替换迁移函数并追加两个回滚支持模块。仅接受已知 v19 表结构；策略必须显式停用、控制开关和认证关闭、没有未决任务或 outbox。原 v18 维护命令仍拒绝 v19，不能用兼容包清理新证据。
- 使用真实 B2 Repository 源码执行 `B2 -> v19 -> 兼容回滚 -> B3` 合成库往返通过，Schema 保持 19，旧认证可读，Provider 调用为零。边界：尚未验证完整旧 Server/镜像启动；单元测试使用离线 Schema 夹具及迁移函数测试替身，不依赖 `.git`。真实源码验证由独立 `--verify-storage` 命令承担，不混淆两类证据。
- 回滚源码包：`C:\tmp\modelmirror-r9b3-rollback-source-01.zip`，SHA-256 `7a09fb6a4eba4dc9ca307fa21346b2724cb9f5ea2c7216bd96bf737a63657b67`。它是当前中间快照，不是已验收发布/部署包；支持 Schema 变化后必须重新生成及验证。
- 第四小批：资格事务模块与测试，要求调用者已处于认证事务；TTL 在新事件开始时冻结，完成记录与区间更新同事务；幂等读取不续期；失败/uncertain、租户、连接变化后恢复原配置均不能复活旧连续区间。尚未接入认证服务和派发准入，不宣称运行时已使用新资格。
- 合跑纯函数、事件、迁移/回滚、生命周期、原 Chat Control 和防旁路检查：`118 passed, 1 skipped, 4 warnings in 25.47s`，临时目录 `C:\tmp\modelmirror-r9b3-tests-20261008-06`。skip 是既有 POSIX fork 用例在 Windows 不适用；警告来自既有 FastAPI startup/shutdown API。
- 前一轮合跑出现 1 项版本断言失败（期望 18、实际 19），已改为当前 Schema 常量，保留备份与恢复前状态的全部断言，并重跑原组通过；不是基线豁免。
- 后续接线前必须覆盖：异步认证 uncertain 后合法 GET 核对的追加事实（不能改写历史未知结果或按旧 completed_at 倒填合格区间）、硬失败/配置漂移屏障、事务内派发复核与历史 dry-run。当前资格事件模块仍属内部基础，不能替代这些门禁。
- 未运行：认证/准入完整接线回归、全部 R5—R8、前后端全量、完整回滚镜像、独立预览和帮助验收。未 Commit、Push、PR、部署或真实付费调用。

### 认证与准入接线进展（2026-10-08，尚未收口）

- 按最多五个文件的小批接入 Chat、Workload、多模态认证终态和重启 uncertain；TTL 在新认证占位时冻结。v19 增加第五张表 `provider_qualification_observations`，保存不可变终态观察；合法 GET 核对 uncertain 后成功的有效期从核对时刻开始，不倒填原始 unknown 时段。原认证记录及业务状态保持原语义。
- Chat/Workload/Canary 时间校验读取已保存期限；没有历史 TTL 证明时返回 `certification_expiry_unknown`。当前环境变量不能延长或重新解释已保存认证。异步只读核对窗口与可执行资格独立判定，允许 GET 不代表资格有效。
- Chat 和 Workload 的批准锚定连续区间；同身份按时续期可保留策略批准及 Chat 观察纪元，并返回最新认证引用。仅相同身份的新认证处于 running 时可继续使用尚有效的上一事件；失败、uncertain 恢复、期限未知、配置漂移或有效期空档不适用此规则。
- R5 尝试没有单次认证 ID，硬失败通过持久化租户、连接、精确模型和能力关联关闭区间；Canary 通过认证引用关闭区间。新增旧在途调用在续期后失败、失败续期后再通过不能复活旧批准、历史未知期限失败关闭等反例。仍需完成 Workload 各形态硬失败、Canary 续期中的完整边界审查，不能宣称全入口已闭环。
- 管理认证响应加法提供 `qualification`（系列、区间、到期时间、续期原因、有效性及稳定原因码），使用显式字段白名单；未改公共 Catalog 口径、Provider POST、SSE 或模型正文。
- 发现 Workload 页面投影重复计算当前资格，已复用同一次校验结果，未放宽超时或安全检查。

#### 本次命令与证据

- Python：`C:\tmp\modelmirror-control-r9-a1\.venv-r9a1\Scripts\python.exe`，同一 Windows Python 环境。
- 综合窄门禁：`python -m pytest server/tests/test_provider_qualifications.py server/tests/test_provider_qualification_events.py server/tests/test_provider_qualification_migration_boundary.py server/tests/test_provider_storage_lifecycle.py server/tests/test_provider_chat_control_service.py server/tests/test_provider_workload_control.py server/tests/test_provider_coverage.py -q --tb=short -p no:cacheprovider --basetemp=C:\tmp\modelmirror-r9b3-tests-20261008-20`：**173 passed, 1 skipped, 4 warnings，37.16s**。skip 为既有 POSIX fork 用例在 Windows 不适用；warning 为 FastAPI 既有生命周期 API。
- Chat Control + Canary Repository + Stable Service 中间版本回归：**53 passed, 1 skipped，55.24s**（临时目录 `...-13`）；管理资格摘要 + Chat/Workload 认证中间版本回归：**104 passed，12.24s**（`...-17`）。它们不是最终全量验收。
- 扩展组（管理认证/API、Chat Control、Canary API、R8B/C/D/E 多模态）：**434 passed, 3 failed，207.59s**（`...-16`）。一项旧过期测试只修改旧 certification.completed_at，已改为修改合成资格事件期限并定向通过，不是基线豁免。
- 两项时间边界用例定向检查（`...-18`）：`test_r8c_runtime_generation_metadata_total_deadline_fails_closed` 通过；`test_r8c_runtime_generation_metadata_attempt_timeout_can_recover` 仍失败。前者原组出现过失败，不能仅凭单独通过视为全量稳定。
- 新建本批干净基线 `C:\tmp\modelmirror-control-r9-b3-baseline`，精确 SHA `114461d3a63ef5c5e0dc8053e66a3ebf1356b0f3`，无源码修改；同 Python 定向复现上述两项，结果 **1 failed, 1 passed，9.98s**。失败为相同 `attempt_timeout_can_recover` / `provider_multimodal_generation_metadata_wait_exhausted`。B2 的旧豁免不继承；B3 尚未批准豁免，Linux 和最终全量仍待运行。
- 当前五表定义下重新运行真实 B2 Repository 合成库往返：`python scripts/provider_qualification_rollback.py --verify-storage C:\tmp\modelmirror-r9b3-rollback-cycle-02`，结果 passed，Provider 调用 0。仍仅 Repository 集成证据，未验证完整旧 Server/镜像。
- 新中间回滚包 `C:\tmp\modelmirror-r9b3-rollback-source-02.zip`，SHA-256 `7b70bbdb6a9aa51eb362b6de8081288ed492aaba34b675a308e9c3050bf82155`；`-01` 保留但不是当前 Schema 对应包。此包未部署，未作为验收交付物发布。
- 本次 `git diff --check` 通过；未新增生产依赖，未修改真实 Provider/预览数据，未 Commit、Push、PR、部署或付费调用。

#### 当时未完成（下节记录后续进展；不替代最终门禁）

1. 历史纪元只读 dry-run、可信证据映射及显式 apply 尚未实现；不能以五表已建成代替迁移完成。
2. Workload 各形态硬失败、正在续期/过期边界、Canary、required 和旧在途派发的完整证伪仍需补齐；准入当前仅有已列出的测试证据。
3. Settings 的资格展示、帮助文档及独立预览尚未更新/验收。
4. 最终同版本 R5—R8 回归、Linux/Windows 全量分类、前端全量/typecheck/build、Compose、完整回滚 Server、秘密扫描和最终 Diff 审查仍未完成。
5. 本批没有新增发布、部署、真实数据迁移或付费授权；进入这些步骤前仍需遵循各自授权边界。

### 收口推进与反例修复（2026-10-08，仍未完成）

- Workload 终态硬失败与资格关闭同事务；旧在途调用的硬失败会关闭续期后的同系列区间。失败的父运行最终化会使终态与资格写入一起回滚；租户隔离及 transient/未派发反例保留。
- uncertain 的合法 GET 截止时间改为首次不可变 uncertain 观察，后续 GET 不再刷新截止时钟。Canary 资格失效后仍优先显示已有 `automatically_paused` 原因，保持失败关闭与原 API 语义。
- 历史审计 CLI 的默认 dry-run 不写数据库、不恢复运行、不创建缺失目录；显式 apply 仅备份并追加拒绝映射，按计划摘要重检。当前对旧纪元缺少不可变配置/认证关联的情况拒绝继承；**还没有接受可信外部历史证明及继承样本的实现**，不能称为完整历史迁移。
- Settings 增加共用资格摘要，显示保存期限、连续续期、过期、失效及历史期限未知，不额外联网、不触发认证、不改模型数量；帮助和部署文档同步说明。界面未在 B3 独立预览验收；15161 仍是 B2。
- 复查发现示例环境和 Compose 默认仍为 86400。已仅修改模板默认至 2592000，并补测试证明显式 86400 继续有效；没有改当前部署或真实环境。
- 派发竞态证伪：调用准备后，最新认证改变 Profile 或已过期，旧事件仍有效时可被错误标记 dispatched。合成数据库 red 为 **2 failed / 3 passed**（`...-29`），没有 Provider 请求。修复在 `BEGIN IMMEDIATE` 内重选当前精确路线认证，核对同一连续区间及最新期限；相同身份的 pending/成功续期仍可用。该小批修改三个文件，未改变 Transport 或重试策略。

#### 新增验证证据

- 资格/历史/迁移/生命周期：`...-28`，**79 passed, 1 skipped, 4 warnings，29.77s**。此前 `...-27` 的 Windows 锁字节读取失败已将锁检查改为 inode/长度、其余文件仍逐字节哈希，重跑同组通过；不放宽 dry-run。
- 最新派发竞态修复与资格/Workload：`python -m pytest server/tests/test_provider_qualification_events.py server/tests/test_provider_workload_control.py server/tests/test_provider_qualifications.py -q --tb=short -p no:cacheprovider --basetemp=C:\tmp\modelmirror-r9b3-tests-20261008-30`，**127 passed，15.64s**。
- 默认期限/Chat Control/Canary：`...-31`，**65 passed，7.62s**。
- 扩展 R5—R8 中间组：`...-24`，**587 passed, 2 failed**；一项 uncertain 过期夹具已更正为首次不可变观察并回测，另一项仍为同基线 Windows 音频 100ms 边界失败。`...-26` 对前者及 Canary/事件回测 **56 passed，15.73s**；不以此替代最终扩展组。
- Settings 三文件定向：**29 passed**；`npm run typecheck` exit 0；`npm run build` exit 0，保留既有大 chunk 警告。先前 typecheck 缺少仓库内 RPG 子包依赖，按其现有 lockfile 安装后重跑通过，无依赖或锁文件改动。
- `npm run test:run`：**154 files passed / 1 failed；1201 passed / 2 failed，431.43s**。失败均在 `models.refresh.test.ts`：live 预期 574 实际 573、过期列表多 `baidu/ernie-4.5-vl-424b-a47b`。干净 `114461d3` 同 Node/lockfile 定向 **72 passed / 2 failed**，断言相同；本批目录和统计源码无修改。尚无 B3 人工豁免。
- 全量中的 SkillCreatorCaptureButton fork 清理超时警告未造成额外失败；同基线定向 **15 passed**，不推断已证明环境根因。Vitest 失败使 npm 的后续 `&& node --test` 未执行，已独立补跑原两项脚本，**17 passed**；整体前端门禁仍非绿。

#### 当前剩余门禁

1. 可信历史期限/配置证明的接受、逐样本继承与审批边界尚未闭环；不能用拒绝所有历史替代要求中的可证明证据保留。
2. 更广的各形态硬失败分类、资格漂移和续期竞态审查；最新修改后 R5—R8 回归及后端全量。
3. 本轮隔离 Linux 源码/镜像/配置绑定验收、基线失败处置；B2 豁免不继承。
4. 完整旧 Server 回滚、Compose、帮助资产、敏感扫描与最终 Diff；B3 独立预览未部署/未获本次部署授权。
5. 未 Commit、Push、PR、部署、迁移真实数据或发起付费请求。

### 续接收口（2026-10-08）

- 用户答复暂未找到可信历史证据。因此当前旧记录维持到期未知、保留但不继承；不导入猜测 TTL、不重写历史样本、不自动重新认证。尚未实现的可信证据接受通道仍如实列为功能边界，不能以本答复宣称已经实现。
- 上次镜像构建 `modelmirror-r9b3-acceptance-01` 只有开始标记，没有完成记录；恢复时已无构建进程，保留该目录，不覆盖或冒充成功。普通沙箱 Git 读取曾提示不在工作树内，受权只读检查确认工作树与原改动完整；未修复或改动主库 Git 元数据。
- 新离线包 `C:\tmp\modelmirror-r9b3-acceptance-02`：源码 `24dd77c703adab9eba5d7d9b755008cd97cb78bab49e9b7ce34d44fa46cb5e1c`，镜像 `sha256:981f1ad9b1f9fa36de7a34c64fd24b650162fb34e35716d70cf1dcd4c383bbe4`。无网络、无挂载、无端口、无真实凭据。guard **43 passed**、独立 Workflow **7 passed**；后端全量已启动，尚无最终退出结果。此快照不含下述追加修复，不能替代最终源码门禁。
- 组合组 `...-32`：**211 passed, 1 failed, 1 skipped**。失败安全断言搜索 SQLite 任意 `0.1`，只读检查合成库命中时间戳 `10:31:50.142699` 等，而非向量。改用六个有限长小数哨兵逐项扫描，并增加两条合成输入不可落库断言，原组 `...-33` 重跑 **212 passed, 1 skipped，50.45s**；未跳过泄露检查，也未读取真实库。
- Canary 新反例 `...-34`：过期资格仍可标记派发、重复派发标记未拒绝，两项 red；没有执行 Transport 或产生真实重复 POST。Repository 改为同事务重验当前资格和 `dispatched=0`；Canary Repository/API/资格事件原组 `...-35` **54 passed，10.77s**，保持原暂停 API 语义。
- Profile `A -> B -> A` 反例 `...-36` 证实旧 A 连续区间被错误复用。修复不再只在相同系列中选前一事件，而是核对同一精确路线真实前序资格；其他 Rerank access mode 独立过滤，避免把 Dedicated/LLM JSON 当成漂移。Chat/Workload/Canary 组合 `...-37` **155 passed，38.79s**；补 access mode 隔离后事件组 `...-38` **43 passed，11.50s**。
- 帮助资产 `npm run verify:help-images` 通过；只证明注册截图资产，不代替 B3 新资格 UI 浏览器验收。Compose 检查正在运行。
- 仍未部署 B3 预览；15161 未动。未提交、推送、PR、真实数据迁移或付费调用。最新源码需重新冻结测试快照；`-02` 完整结果只作为中间回归证据。

### 硬失败跨 Adapter 与完整回滚验证（2026-10-08）

- 继续按用户确认保留无可信历史证明的旧记录，不猜测期限，不继承样本；可信外部证明的接受通道尚未完成。本节不改变这一交付边界。
- 查验真实 Adapter 代码发现：Embedding/Rerank 使用各自错误码命名空间，先前 B3 分类只覆盖 Workload 通用码。补入明确的 401/402/403/404、两类模型不一致和多模态非法 SSE；不以宽泛前缀匹配，不把 429/422、取消或未派发失败当作硬失败。
- 合成事务反例 `...-39` 为 **25 failed**（旧实现没有关闭对应区间）；修复后资格事件/纯函数/Workload 原组 `...-40` **189 passed，39.95s**。保留租户隔离及父运行最终化失败时整体回滚断言。
- 真实 Rerank/Embedding Adapter 经 MockTransport 的原专项组 `...-41` **27 passed，76.51s**；新增模型不一致及 401/403 后 `degraded_required`、429/422 保持资格、状态读取零新 POST 的检查。不是实际 Provider 验收。本小批共五个文件（一个实现、三个测试及本任务卡）。
- `-03` 离线快照源码 `8ee1f59d599f6d1741ffb5fc1d1d7643c448df427a39215f608538d55bc02d80`，构建镜像 `sha256:68d6ebf78b3e734f5b502a03906fbefed58e87fa703a9725280a5a41e4658487`。它不含本节硬失败补充，不能用作最终全量快照。
- 完整 B2 兼容源码包回滚测试：断网容器 `modelmirror-r9b3-rollback-server-check-01`，无挂载、无端口、无真实数据；固定已记录 SHA-256 的 `rollback-source-02.zip`，未替换旧 Server 启停函数。两次独立 Python 进程通过完整 FastAPI TestClient startup/health/shutdown；v19 五张资格表前后内容相同，原认证可读，再切回 B3 Repository 成功，容器 exit 0。脚本 `C:\tmp\modelmirror-r9b3-rollback-server-probe.py`。该证据补齐合成完整 Server 生命周期，不代表实际部署回滚或 UI 验收。
- Core 与独立 newAPI Compose 配置 exit 0；Overlay 首次缺少必填网关地址而拒绝，随后仅进程内设置合成网关地址重跑 exit 0，原环境恢复；未部署。
- Linux `-02` 中间快照后端全量仍在执行；不能把进行中状态计为通过。前端两个同基线目录断言、Windows 音频时限失败仍无本批人工豁免。B3 新 UI 独立预览和最终源码门禁仍未完成。

### 全量结果与预览授权更新（2026-10-08）

- `-02` 中间快照后端全量结束：**8704 passed, 3 failed, 30 skipped, 34 subtests passed，2783.04s**，exit 1；JUnit SHA-256 `d45d668a412eb7bd3ec3b4cfae2068f1f12c0f812d07011969448e21f512f281`。三项都是 Workflow 防重放测试的错误码优先级：硬失败后资格先使 Policy degraded，返回 `provider_workload_policy_not_active`，尚未到旧运行重放检查。
- 未放宽防重放断言：先检查 degraded 阻断，再仅用合成认证记录和显式 Policy 批准恢复资格，仍要求旧运行返回原 `provider_workload_logical_run_replay_blocked`、总 POST 不变。Workflow 专项 `...-42` **13 passed，51.79s**；仍需最新快照全量。
- `-04` 镜像构建失败保留：BuildKit `t5zo91fynshlegtgk7o8ioywv` 显示 `files.pythonhosted.org` 下载 ReadTimeout，pip exit 2；尚未进入测试。不得用 `-03` 旧镜像标称最新生产源码。
- 用户明确授权 B3 独立无付费预览，以及仅两项前端目录断言作为本批非阻断基线豁免、另案处理；不豁免 Windows 音频时限失败、B3 资格或其他安全门禁。
- 预览拟使用 `modelmirror-r9b3-preview`、15162、新合成卷和独立网络；15161/B2 不动。测试夹具明确标记 synthetic，认证及所有模型开关关闭。此处只是部署准备，尚未完成浏览器验收。

### 独立无付费预览与重启证据（2026-10-08）

- 已执行用户批准的合成预览，地址 `http://127.0.0.1:15162/settings?section=providers`，仅 loopback 发布。独立项目、容器、卷和网络均带 `modelmirror-r9b3-preview` 前缀；15161/B2 未动。Server 仅连接 internal 网络，所有模型入口开关关闭，没有真实 Provider 凭据。
- `-05` 新源码镜像下载既有 uvicorn 依赖时发生 PyPI 连接超时，BuildKit `acwemufs7cgoiu11obxrd1ojv`，未执行测试。保留 `-04/-05` 失败记录，不把它们写成构建成功。
- 预览复用 `-03` 固定依赖运行时 `sha256:68d6ebf78b3e734f5b502a03906fbefed58e87fa703a9725280a5a41e4658487`；五个依赖清单/lockfile 与 `-05` 相同。断网构建覆盖 `-05` 源码，内容摘要 `ec01a528218b58bd7ef6c172268ff91b96ee4df1697244a36f3cd48c5d956f7b`，派生预览镜像 `sha256:8c82ef96477731ec4c2f6a6f517d64e67fa7b800b5fb8e73ad54ea0fc5a6e417`。这是固定依赖的派生运行时，不冒充全新依赖安装或标准验收 harness 的成功结果。
- 启动夹具曾因命名 `server.py` 遮蔽应用包失败，改为 `bootstrap.py` 后健康 HTTP 200；只修改本批外部预览脚本，保留合成卷，未改生产启动路径。
- 浏览器已验证配对、历史到期未知、已过期、按时续期且有效三类展示，认证按钮禁用，Marble 在未配对时仍可见。旧 `stale` 标题错误归因为配置变化，现改为中性“认证当前不可用于调用”，补断言防止未知期限被错误描述；两文件定向 **10 passed，2.30s**。
- 文案修复仅同步到开发预览 client，文件 SHA-256 `a667fb6828acbcbe4b03aeec3c7cc33c62b4d5b2750b988f34c8508f0b7e45b4`，宿主与容器一致；它是已标明的单文件源码覆盖，不声称原派生镜像已包含该修复。浏览器再次确认文案生效。
- 仅重启本批 Server 后，只读 SQLite 检查全部五张资格表和认证/调用表摘要前后相同：4 条合成 Chat 认证、3 条事件、3 条观察、2 个系列、2 个区间；Chat/Workload/Canary/Batch 运行和尝试计数均为 0。检查脚本不实例化 Repository。重启后按当前会话契约重新配对；会话持久化属于后续 C2，不在 B3 宣称完成。
- 合成浏览器证据不替代真实资格；未知/过期派发拒绝仍由事务级自动测试证明，本预览没有启动 Managed Policy 或模型调用。
- 最新后端源码的断网、无挂载、无端口全量容器 `modelmirror-r9b3-final-backend-01` 已启动，结果仍待收集。该运行使用上述派生运行时；后端源码与 `-05` 完全相同，后续仅前端文案及本任务卡改变。
- 截图在浏览器工具中已观察，文件保存被本机工具权限拒绝；尚无可交付的本地截图文件，不伪称已保存。未 Commit、Push、PR、真实迁移或付费调用。

### 保留原范围：正向继承核验基础（2026-10-08，未完成接线）

- 用户明确要求不拆分正向继承，B3 继续保留原交付范围。没有真实历史证明时，实际旧记录仍保留但不继承；以下全部为合成验证，不能给真实记录补造期限。
- 新增独立纯核验模块：资格链要求精确身份、原 TTL、按时续期和有界观察时段；到期空档、失败/uncertain、配置 A→B→A、重复事件和时间倒置均拒绝。较短续期不会沿用旧较长 TTL，样本不能超出已核验时段。
- 逐样本核验要求同租户/运行、原策略、稳定模型、首选 newAPI 单次派发、真实用户与普通文本；Auto、Canary、取消、备用、多派发、实际模型不一致均拒绝。可证明的瞬时失败仍作为失败样本，不过滤成全成功集合；既有硬错误码即使被旧记录误标为 transient 仍拒绝。
- 新增离线证明输入边界：显式人工来源审核及 manifest digest、原数据库快照摘要、部署文件摘要、原期限和生效窗口；拒绝 JSON 重复键、未知字段、文档自选路径、篡改及超限。只计算部署文件摘要，不输出或保存正文。哈希仅证明完整性，不能替代来源审核，也不能单独授予资格。
- 该基础尚未接入 CLI 正向导入、数据库快照逐项比对、持久化接受映射或 R5 门禁聚合；不宣称正向继承已完成。后续必须同时证明原样本不重写、失败样本不漏计、无空档继承、同样本不重复统计、目标策略漂移使映射失效，以及 500/14 与人工 required 激活不降低。
- 合成历史核验/证据输入/原 dry-run 组 `...-48`：**61 passed，5.81s**。此前原资格/事件/历史/迁移/生命周期组 `...-43`：**189 passed, 1 skipped，43.46s**（既有 Windows POSIX fork skip）；当前前端 typecheck 与 build exit 0，保留既有 chunk 体积警告。
- `modelmirror-r9b3-final-backend-01` 在新增正向核验模块前启动，因此即使通过也只能对应 `-05` 基础快照，不能当作新增核验模块的最终全量门禁；仍待退出结果。最终快照需包含这些新增文件，并重新绑定测试证据。

### 正向继承补齐（2026-10-08，合成验证，尚非最终门禁）

- 已接入离线 CLI：v2 manifest 要求单独的来源审阅、批准摘要、原一致性快照、原 TTL 资料以及完整配置连续性资料；v1 哈希解析不能直接用于正向继承。没有真实可信资料时继续拒绝实际导入。本次未读取真实历史资料、未迁移真实存储。
- 快照使用固定摘要的自包含 Backup API 文件，以 immutable/read-only 连接读取；不执行 Repository 初始化、迁移、恢复或源库写入。比对原策略指纹、有序路线、模型集合、原认证与当前记录；串联所有认证结果，不按 passed/相同指纹筛掉中间失败或 A→B→A。
- 原 TTL 只用于历史证明，不回填/延长旧认证；目标必须已有有效的当前资格。配置资料必须覆盖原资格至目标批准锚点，后续续期仍依据保存的不可变事件。原纪元正常 policy/qualification invalidation 只有在完整证明通过后才可映射；实际 degraded/hard failure 不得洗成迁移失效。
- v19 新增第六张表 `provider_qualification_inherited_samples`。显式 apply 先一致性备份，再事务内重验计划摘要并原子追加纪元和逐样本映射。原样本、认证、策略、批准不改写；同一原样本只能被继承一次，失败样本仍计入分母。
- R5 状态与 required 原子激活共用聚合路径。源纪元完整账据摘要、样本摘要和映射集合摘要必须保持一致；丢失失败样本或映射不得静默变成更高成功率。目标资格/连接/策略漂移立即阻止继续使用继承证据。通用 Receipt 清理保留已接受映射引用的完整源纪元，不放宽其他普通记录的过期清理。
- 第一组纯证明/输入边界 `...-49`：57 passed；快照组初次夹具缺 masked_key、随后 Backup API WAL-header 内存反序列化失败，改为自包含 immutable 只读快照，`...-52` 13 passed。未以失败的底层前置检查冒充漂移反例通过。
- 集成组发现备份连接和测试夹具 SQLite 句柄未显式关闭，修复后 `...-54` 51 passed；CLI/旧历史审计/迁移/R5 组 `...-55` 60 passed。
- 500/14/99% 边界、重复映射、清理、资格及单写者组 `...-56` 为 144 passed / 1 failed / 1 skipped；失败为重复映射夹具违反一个租户只能有一个活动纪元。修正夹具先关闭旧目标后，原组 `...-57`：145 passed / 1 skipped / 4 warnings，52.72s。Windows POSIX fork skip 保持原平台约束。
- R5/Workload/Canary/RAG/Workflow/覆盖受影响组 `...-58`：272 passed，89.50s；它早于后续映射集合摘要防丢行修复，不代替最终同快照回归。摘要表达式首次有语法错误（`...-59`），修复后原导入加迁移组 `...-60`：35 passed，24.56s。
- 旧 `-05` 全量容器已结束：8774 passed、30 skipped、9 warnings、34 subtests passed，2285.79s，exit 0。只对应 `ec01a528...` 旧快照，不含本节新实现。
- 尚需：最新源码重新冻结及全量、六表完整旧 Server 回滚、最终 Diff/秘密扫描、帮助截图资产收口。15162 预览仍是旧合成快照，未宣称已部署本节实现；15161/B2 未动。未 Commit、Push、PR、部署、真实迁移或付费调用。

### 正向继承最终快照与回滚复测（2026-10-08）

- 最新历史导入、快照、证明、迁移、R5 和单写者组合 `...-61`：149 passed、1 skipped、4 warnings，55.32s；skip 为 Windows 下既有 POSIX fork 条件，非本批安全门禁豁免。
- 冻结包 `C:\tmp\modelmirror-r9b3-acceptance-06`：基线 `114461d3a63ef5c5e0dc8053e66a3ebf1356b0f3`、43 项覆盖、5114 个文件，源码摘要 `abab63a3d627d0eeaf8ebdd583347cd4d04bab7c5bb786881b3f95cfa3cc730d`。固定父依赖运行时 `68d6ebf7...` 的断网派生镜像为 `sha256:a344382d5146d66df66fce3e47721fe695e5df90b45bbb3effca0d844eaf5a39`，标签 `modelmirror-r9b3-history-validation:abab63a3`。它不是全新依赖安装，也不冒充标准 harness verify 已通过；最新元数据在 `/opt/r9b3-validation`，父镜像的旧验收报告不可作为本快照结果。
- 六表兼容旧源码包 `C:\tmp\modelmirror-r9b3-rollback-source-03.zip`，SHA-256 `467e63af5a4864bcfb4aa4a03275de769d15c0e346d921d01c60a441978ebf78`。第一次完整 Server 测试失败：验证脚本漏传 seed 使用的合成主密钥，旧 Server 正确拒绝不匹配密钥；未归因为迁移损坏，未放宽密钥校验。
- 补齐验证脚本的同一合成主密钥后，断网、无挂载、无端口容器 `modelmirror-r9b3-rollback-server-check-04` exit 0：两次真实 FastAPI startup/health/shutdown，六表及两个已接受继承样本内容不变，再切回 B3 读取成功。只证明本合成库回滚，不代表部署回滚；未读取真实密钥。
- 同镜像独立 `test_workflow_run_contract.py`：7 passed、4 warnings，7.85s。后端全量按既有 harness 分组单独运行该文件，其他 `server/tests/` 由 `modelmirror-r9b3-final-backend-02` 执行；结果尚未结束。前端全量和 typecheck/build/help 资产另以该冻结镜像执行，结果尚待收集。
- 本节是冻结后的文档记录，不在上述源码 tar 中；生产实现及测试仍对应冻结摘要。未部署新镜像到 15162，未 Commit、Push、PR、真实迁移或付费调用。
- 同快照前端 typecheck、production build、已注册帮助截图检查全部 exit 0。前端全量 1201 passed / 2 failed，227.60s；仍为已获本批豁免的同基线 `models.refresh.test.ts` 两项断言（574/573 和多一个 expired），没有其他失败。完整脚本并非绿测，后续 Node 脚本另跑 17 passed，不掩盖前述失败。
- 辅助断网组 exit 0：coverage/acceptance 43 passed + 34 subtests；Worker 75 passed；目录脚本 21 passed；前端附加脚本 17 passed。
- 浏览器只读复核 15162 既有合成预览：未知原期限、已过期、按时续期三类状态仍可见，付费认证禁用。该页面仍对应先前已记录预览快照，不代表新历史导入模块已经部署；截图可观察但帮助中心所需本地截图资产仍未补齐。
- 后端最新全量仍在 `modelmirror-r9b3-final-backend-02` 运行（断网、无挂载、无端口）。在取得最终退出结果和补齐剩余资产前，B3 不判定满足 PR 提交门禁。

### 隔离测试配置纠正与完整失败集回测（2026-10-08）

- `modelmirror-r9b3-final-backend-02` 最终 exit 1：478 failed、8389 passed、30 skipped、9 warnings、34 subtests，2202.47s。原容器和 JUnit 保留；本地证据 `C:\tmp\modelmirror-r9b3-backend-02-failed.xml` 的 SHA-256 为 `8f388f3c93236c1a0b2e082f29c92c9d89e140b1f47361e1bcc610ac3e1011eb`。
- 根因定位：本轮手工启动命令额外注入 `MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED=false`，而仓库既有离线 harness 不设置该变量。该开关同时关闭 Chat 和 Workload 认证，导致 Mock 认证前置条件被拒绝、期望错误码被提前替换；两个音频取消测试等待未开始的 Mock 流而超时。部署安全检查正确生效，未通过修改生产逻辑或降低断言解决。
- 同镜像受控对照：保留额外 false 覆盖时，Chat 和 Batch 的首例分别复现认证禁用；不覆盖认证开关时，Chat、Batch、Rerank 三组共 42 passed，24.81s。Fusion 独立组 9 passed，不将其误归为首批失败。
- 仅调整本批外部测试启动器 `C:\tmp\modelmirror-r9b3-backend-recheck.ps1`：固定镜像/源码，直接调用现有 `provider_acceptance.expected_environment()` 从冻结 tar 生成环境，禁止额外认证开关覆盖，并在启动前核对镜像、命令、entrypoint、无网络、无挂载、无端口及非 privileged。未修改仓库 harness、业务代码、测试断言或预览配置。
- 中间 `modelmirror-r9b3-final-backend-03` 缺少显式 `MODEL_CONTROL_CHAT_ENABLED=false`，虽默认值仍为 false，仍不计作标准配置验收；已经停止并保留其记录。最终 `modelmirror-r9b3-final-backend-04` 的有效环境逐项匹配冻结 harness，使用同一 `abab63a3...` 源码及 `a344382d...` 镜像，完整后端复测进行中。
- 所有失败涉及的 12 个模块独立复测：635 passed、4 warnings，235.17s，exit 0。逐一比对原 JUnit 的 478 个失败 ID，全部在新 JUnit 中通过，缺失或未通过为 0；包括两个取消等待超时。复测 JUnit `C:\tmp\modelmirror-r9b3-failed-modules-recheck-01.xml` SHA-256 `6ef842f6ebff7b655b083e660d466e18fe591bb35cd020a8650ec70fc13e2735`。该针对性通过不替代仍在执行的完整后端门禁。
- 真实 Provider 数据、15162 预览及其禁用认证配置不变。没有付费调用、提交、推送或 PR；帮助中心本地截图资产仍待收口。

### 最终后端结果与证据归档（2026-10-08）

- 修正环境后的 `modelmirror-r9b3-final-backend-04` 正常结束，exit 0、无 OOM：**8867 passed、30 skipped、9 warnings、34 subtests passed，2173.78s**。对应冻结源码 `abab63a3d627d0eeaf8ebdd583347cd4d04bab7c5bb786881b3f95cfa3cc730d` 和镜像 `sha256:a344382d5146d66df66fce3e47721fe695e5df90b45bbb3effca0d844eaf5a39`；不是旧 `-05` 快照结果。
- 原容器保留。JUnit 已复制到本批独立证据文件 `C:\tmp\modelmirror-r9b3-backend-04-passed.xml`，SHA-256 `29f5c899931ba094b3667aee87cbe5bd9860e9cd1bae5240a62bc370ddae9933`。XML 汇总 8931 个条目（含子测试），errors=0、failures=0、skipped=30；独立 Workflow 合同组仍以本节之前记录的 7 passed 为准，不重复计入主全量。
- 浏览器再次只读核对 15162：历史到期未知、已过期、按时续期有效的合成状态与帮助说明一致。该预览仍是已记录的旧合成快照及单文件文案覆盖，不冒充最新历史导入模块部署。
- 帮助截图已重新截取不含连接地址和掩码凭据的资格说明区域；保存到本机独立文件时仍返回 EPERM，未形成可交付资产。不通过虚构截图或移除帮助门禁绕过此限制。
- 本次仅归档已结束测试并更新此记录，未改生产代码、测试断言、运行配置或真实数据；未进行认证、模型 POST、Commit、Push、PR 或部署。帮助截图落盘及最终完整 Diff/秘密扫描仍需完成，B3 尚不宣称达到 PR 提交门禁。
- 归档后的只读核对：冻结清单全部 5114 个文件逐项比对，只有本任务卡的收尾记录与冻结包不同；业务代码和测试未漂移。43 个改动/新增文件的高置信凭据格式扫描为 0 命中（仅格式扫描，不代替内容审查）；`git diff --check` 通过，暂存区为空。已复核历史映射写入、源账据完整性及派发资格的关键路径；剩余完整 Diff 审查不以此次抽查冒充完成。

### 帮助资产与有限预览同步（2026-10-08）

- 用户上传 unknown、expired、renewed 原图并授权确定性裁切、等比缩放和 PNG 编码；原图不变。三张 750px 宽资产均不超过 250KiB，不含连接地址或掩码凭据。首次较大裁切超过限制，已缩小到资格说明区域后按原规则通过，没有降低检查门槛。
- 新增资产及其原图摘要、裁切坐标和输出摘要见 `docs/help-center/evidence/provider-qualification-r9b3-114461d3.md`；恢复指南明确说明合成状态及日期不代表真实资格。
- 最后一次前端帮助资产检查、typecheck、production build 全部 exit 0；构建 3197 modules、13.83s，保留既有 chunk 体积提示。本次没有修改业务或测试代码，未把此前两项已获豁免的目录测试失败改写为全量绿测。
- 按用户四文件限定授权，只同步三张 PNG 和帮助文章到 15162 client，逐项哈希一致。未重启/重建 Server，未改资格、策略、凭据或其他预览。浏览器滚动后已实际显示三张图及图注，DOM 自然尺寸和加载完成状态匹配文件，文章中的合成边界说明可见。
- 补充复核 Repository 的资格派发再检查、续期关联、硬失败失效、统一门禁统计和清理保护；再次核对正向导入的备份/事务重验、原记录不改写、失败样本分母、映射丢行阻断和 500/14/99% 回归断言，未发现新的可定位缺陷。该结论不替代真实历史资料审核或生产迁移验收。
- 本次后端实现仍使用 `-06` 冻结快照证据；新增帮助资产、文章及收尾文档是后续文档覆盖，不冒充原冻结包已包含。尚未 Commit、Push、PR 或执行真实历史导入、付费认证、模型调用。
- 收尾检查：当前共 47 个改动/新增路径，`git diff --check` 通过，高置信凭据格式扫描 0 命中，暂存区 0 文件。分支 `codex/provider-control-r9-b3`、HEAD `114461d3a63ef5c5e0dc8053e66a3ebf1356b0f3` 未变。本次未 Fetch 或进行发布前上游交叉审计；发布前仍须重新核对上游，不把本地检查当作远端 CI。

### 发布前上游审计及帮助基线修复（2026-10-08）

- 用户授权提交 PR 后成功 Fetch；主线前进到 `debd7509a20ac1302f7efb3132307cf4dd6dfc16`（PR #408，ElevenLabs 与 Decisions/Catalog 更新）。25 个上游路径与当时 47 个 B3 路径无直接交集；已核对音频 Profile/契约、Decisions、目录及帮助目录变化。分支无本地提交，使用 fast-forward 保留全部 B3 修改，没有重置用户文件。
- `-07` 冻结源码 `325a389e1c999bd45154398382f0aedadc03b35cdd1dc48edfd902e452d544c5`，固定已有依赖运行时的离线派生镜像 `sha256:11c1d4c06dd1560115dde2494bacb4d71ef3bf55f11b2bad7dd9976a4081a8e2`。五个依赖清单与固定父运行时来源逐项一致；不是全新依赖安装或标准 harness verify 的完成声明。
- 新基线前端首次全量：1206 passed / 1 failed，241.86s。旧两项目录断言现在通过，不沿用旧豁免。唯一失败是 B3 截图使用 `114461d3`，但恢复指南整篇元数据为 R8C 的 `ae284fbb`；属于本批文档缺陷。
- 四文件最小修复：资格说明拆为独立文章，原指南链接过去；新增文章目录及语义回归，截图基线断言保持不变。最窄帮助测试 16 passed，3.83s。未改图片内容、原图来源或业务行为。
- `-08` 冻结源码 `f729068f75aa4911d9838a591d68f3663f09200dcba2cc55917b90aa1a9d4e42`，镜像 `sha256:459198de7dc061ef61d0acbf408375ff0b8325db255c4a3439afe9aab1363072`。与 `-07` 逐项比较，仅上述四个前端帮助文件不同；全部后端、测试与依赖完全一致，因此保留正在运行的 `-07` 后端全量，并对 `-08` 重跑完整前端门禁。
- 新基线辅助门禁：coverage/acceptance JUnit 43 passed，Workflow 合同 7 passed，目录脚本 22 passed，Worker 脚本 exit 0。前端修复后 typecheck exit 0；全量、production build 和帮助资产仍待最终结果。
- 新基线六表完整 Server 回滚复测 `modelmirror-r9b3-publish-rollback-07` exit 0：固定兼容旧源码包，两次完整 Server 生命周期，六表及两个已接受合成映射不变，切回 B3 成功，网络 none、Provider 调用 0。仍只证明兼容源码包，不声称未经修改的旧 B2 二进制支持 v19。
- Core、独立 newAPI、Core + newAPI Overlay、15163 实际预览 Compose 配置检查全部 exit 0；Overlay 仅使用进程内合成 URL，不持久化秘密，不执行部署。
- 用户批准的最新基线独立无付费预览为 15163；Server 只连内部网络、独立合成卷。重启前后六表及认证/调用表摘要一致，调用记录全为 0。浏览器重新配对后核对 unknown、expired、renewed、认证禁用以及独立帮助页；旧 15161/15162 保留。详细截图沿用与重放边界见帮助证据文档。
- 本节为后续证据记录，不包含在前述冻结 tar 内；未修改后端实现，未执行真实认证、历史导入或模型调用。最新基线后端全量仍在运行，暂不宣称 PR 门禁完成；尚未 Commit、Push、PR。

### 当前时钟复测及限定基线豁免（2026-10-08，本机日期）

- `-08` 全量首次为 1205 passed / 4 failed，324.23s。其中两项目录断言和一项 Expert Team 固定模型选择断言在干净 `debd7509`、同依赖和当前时钟下定向复现：基线与修复版均 83 passed / 3 failed。运行期间跨过 UTC 2026-10-09 00:00，目录按现有 `Date.now()` 逻辑将到期模型移出 live；`qwen/qwen3-8b` 的到期值为 1791504000。B3 未改目录、专家团实现或这些断言。
- 用户明确批准仅上述三项作为 B3 非阻断基线豁免、另案处理；不沿用旧基线豁免，不包含视觉等待失败，不豁免资格、安全或后端测试。
- 第四项为 `ChatVisualAnalysisPanel` 一秒等待区域超时。保留首次失败；同模块定向基线与修复版均通过，未改生产逻辑、测试或超时。修复版原全量命令再次运行，结果 **1206 passed / 3 failed，354.23s**，视觉模块 5 passed；三项失败正是已批准的限定豁免。这里只确认复测通过，未证明间歇等待失败的根因。
- `-08` typecheck、production build（19.02s）及帮助资产检查全部 exit 0。全量脚本仍为 exit 1，不写成绿测；因前段失败未自动执行的附加 Node 脚本单独运行 **17 passed / 0 failed**。
- 干净基线全量对照首次缺少独立 RPG card-replica 的依赖路径，四个套件加载失败；这是外部验收环境不完整，不归为产品缺陷或豁免。根依赖别名尝试也不作为精确环境证据；最终对照从原基线镜像派生，为 engine 与 card-replica 分别链接冻结运行时已安装的对应依赖树，遵循仓库 harness 的分模块 lockfile 布局。全程无网络、未安装或升级依赖，全部失败记录保留。
- 当前干净基线全量及最新后端全量尚未收集终态；提交门禁保持待定。未执行任何模型 POST、真实认证或历史导入。

### 最新完整终态与新增发布阻塞（2026-10-08，本机日期）

- 精确分模块依赖的干净基线全量结束：**1197 passed / 3 failed，317.21s**，154 个测试文件；修复版为 1206 passed / 3 failed，155 个文件。两边失败项完全一致，均为已批准的三项断言；视觉模块在两边完整复测均通过。基线源码摘要 `528e4ede942572cbe080466404f9d4709e38f66379c516e9e45137df00afd54b`，镜像 `sha256:9e3986b22280535a0a3ad02bec9c5afcd8dce7d2fecbd9fab14f85ea855c6933`；原不完整依赖及根别名运行不替代这一结果。
- `-07` 最新后端全量结束：**8881 passed / 2 failed / 30 skipped / 34 subtests passed，3215.87s**，exit 1。JUnit SHA-256 `9188de97f0bb593095f5ef0b93801b4990852240e83a13e88f75ae0ff3fc31e1`；独立 Workflow 合同 7 passed 不重复计入主全量。
- 两项为 `test_multimodal_chat_foundation.py::test_audio_catalog_only_marks_verified_interactions_ready` 和 `::test_audio_catalog_endpoint_does_not_expose_credentials`，均在 Catalog 版本断言停止：期待 `modelmirror-audio-contracts-2026-10-02-deepgram-flux`，实际主线为 `modelmirror-audio-contracts-2026-10-08-elevenlabs`。不能因此声称这两个测试后续断言已执行。
- 干净同基线、相同冻结依赖、无网络的原模块复现 **21 passed / 2 failed，3.63s**；两项失败 ID 摘要与修复版完整 JUnit 完全一致。B3 未修改 `server/multimodal/` 或该测试文件。
- 这两项后端失败不在用户刚批准的三项前端豁免内。已单独请求是否允许本批非阻断、另案修复；收到决定前不 Commit、Push 或创建 PR。所有原始失败及复测证据保留，不改旧断言或写成全量绿测。
- 再次 Fetch 成功，HEAD 与 `origin/main` 仍为 `debd7509a20ac1302f7efb3132307cf4dd6dfc16`；`git diff --check` 通过，暂存区为空。未执行真实模型调用或数据迁移。

### 音频版本断言最小修复（2026-10-09）

- 用户选择定位修复两项失败，而非批准后端豁免。根因对应上游 PR #408：`5884cfe2` 更新 `AUDIO_PROFILE_REGISTRY_VERSION` 为 `modelmirror-audio-contracts-2026-10-08-elevenlabs`，但没有同步基础模块的两处固定预期；上游说明也明确未执行后端全量。B3 没有改变这条运行契约。
- 本小批仅修改 `server/tests/test_multimodal_chat_foundation.py` 两行固定预期，保持精确版本断言，不改成非空判断或与被测常量自比较；其余模型状态、格式、配置门禁、认证头和响应脱敏断言原样保留。另更新本任务卡作为证据，不修改生产实现、Catalog、依赖、预览或持久化数据。
- 在固定 `-08` 镜像的独立无网络、无挂载、无端口容器内，仅覆盖该测试文件；启动器逐项核对其余全部后端文件与冻结包一致，并证明文件差异恰为旧版本字符串的两次替换。复制后回读哈希一致，原全量失败报告不覆盖。
- 原两个失败定向复测 **2 passed，1.88s**；原完整模块 **23 passed，1.75s**。这次两项测试均运行到结束，之前未到达的凭据与 URL 不泄露断言已执行通过。相关八模块回归尚在运行；本节不把窄测试写成最新全量后端通过。
- 无用户可见行为变化，不需要为这两行测试修复新增帮助截图。回退本小批只需撤回两行测试预期，不涉及服务或数据回滚。未 Commit、Push、PR 或真实调用。
- 相关八模块（ElevenLabs、STT、TTS、Catalog snapshots、Chat Audio、R8C、R8D 及 R8D Chat）**483 passed、4 warnings，134.28s**；没有隐藏的后续断言失败。测试文件 SHA-256 `525061358fe834207182a26fa66ee9eb495c9d6734022e734146381687a64fe9`。`git diff --check` 通过，暂存区为空。
- 已启动独立 `modelmirror-r9b3-audio-version-10-backend` 完整后端复测，沿用既有 harness 将 Workflow run contract 分组执行的命令；当前尚无终态，不改写上一轮 8881 passed / 2 failed 报告。最新证据位于 `C:\tmp\modelmirror-r9b3-audio-version-recheck-10`；不是对预览 Server 的部署或重启。

### 提交前最终门禁归档（2026-10-09）

- 修复后全量正常退出，exit 0、无 OOM：**8883 passed、30 skipped、9 warnings、34 subtests passed，2337.51s**。独立 Workflow 合同另有 7 passed。原两项后端失败已实际修复并完整回测，不列入任何豁免。
- 对应冻结 `-08` 源码 `f729068f75aa4911d9838a591d68f3663f09200dcba2cc55917b90aa1a9d4e42`、镜像 `sha256:459198de7dc061ef61d0acbf408375ff0b8325db255c4a3439afe9aab1363072`，加上述唯一测试文件覆盖（`52506135...`）；全量 JUnit SHA-256 `c8b164028d339f773190c6d88f55d30b60ce181df45a9e6a858bbc0e0d5af97e`。不能把未覆盖该两行的原镜像写成已经包含补丁。
- 前端仍如实报告 1206 passed / 3 failed；仅使用同 SHA 同依赖复现且用户明确批准的三项本批豁免。typecheck、production build、帮助资产及附加 Node 测试通过。视觉等待首次失败保留，后续定向与完整复测通过，不宣称已定位其间歇原因。
- 发布前再次 Fetch，`origin/main` 与 HEAD 仍为 `debd7509a20ac1302f7efb3132307cf4dd6dfc16`。本次只继续 PR 前归档与最终核对，不启用任何生产入口、不导入真实历史、不续期真实认证。真实资料仍缺失，正向继承只有合成证明和自动测试证据。
- 本 PR 的回滚边界仍为固定 B2 + v19 兼容源码包，不是原始 B2 二进制；正式启用与迁移另行批准。Commit、Push、PR 和远端 CI 状态须以之后的实际操作为准，本节不预先宣称已发布。
