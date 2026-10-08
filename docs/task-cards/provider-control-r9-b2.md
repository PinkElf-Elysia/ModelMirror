# R9B2：单写者与存储生命周期

## 开工契约

- 基线：`origin/main@70a016405857951e9273a2b61a6ff40dc517323e`；B1 PR #405 已合并，Fetch 成功。相对上一已验收基线仅 B1，无额外交叉改动。
- Worktree：`C:\tmp\modelmirror-control-r9-b2`；分支：`codex/provider-control-r9-b2`。主工作区、B1 预览、其他工作树和持久化数据保持不动。
- 目标：目录级 OS 单写者锁；构造、迁移、恢复、只读与清理解耦。第二个写入实例在迁移/恢复前拒绝启动，维护写入不能绕过锁。
- 已确认：当前 Repository 构造执行建目录/密钥/迁移；`_initialize` 同时把多类 running/submitting 改为 uncertain。清理命令的 `recover_chat_control_on_startup=False` 只跳过 Chat 专项恢复，不能阻止其他状态更新。
- 范围：Router Repository/生命周期模块、服务启动/退出接线、清理与凭据维护命令、针对性及受影响回归测试、运维/用户帮助证据。每个实现小批最多五个文件；测试构造调用点的机械迁移单独分组。
- 不改：Schema 版本、模型协议、资格有效期、主体/会话、Provider 默认策略、派发次数、业务运行恢复算法、R5 门禁或 Catalog 统计。无新增依赖，无真实 Provider 请求。

## 设计与验收

1. 构造只建立内存配置；显式 `open/start` 才取得写锁、迁移及一次启动恢复。启动失败释放本次锁，绝不 fallback 到 legacy。
2. 锁覆盖规范化存储目录，使用 Windows `msvcrt.locking` / POSIX `flock`，持有到 close/进程退出；不删除锁文件，不靠 PID 或超时接管。
3. 只读维护不解析主密钥、不迁移/恢复、不建目录或文件。对活跃写入者、待恢复 WAL 或不兼容 Schema 明确拒绝，不用不完整快照伪装 dry-run。只读查询使用 SQLite 只读连接。
4. 维护 apply 取得同一写锁，但不执行运行恢复或隐式 Schema 迁移；CLI 关闭连接/锁。凭据迁移使用同一锁与现有 Backup API。
5. 迁移与恢复独立；启动恢复保持原顺序和不重放语义。服务必须在启动任何后台模型工作前取得控制面写锁。
6. 测试：构造零副作用；两进程竞争；异常退出后重获锁；目录别名；dry-run 全目录哈希和状态不变；已有 running 与 outbox 不变；缺失/旧 Schema 不创建；维护写入拒绝活动 writer；恢复只在明确启动执行。

## 实施顺序

- 小批 1：任务卡、生命周期回归测试，先复现 dry-run/构造副作用。
- 小批 2：锁与 Repository 生命周期、清理入口、专项测试。
- 小批 3：服务启动/退出、凭据维护、相应测试。
- 后续：每组最多五文件的既有测试构造/显式重启迁移；不删除断言、skip 或隐藏失败；运维帮助与验收清单。

## 验证与回退

- 先运行 `server/tests/test_provider_storage_lifecycle.py`，再 Router/认证/Workload/Batch/媒体恢复、维护和 R5—R8 受影响回归，最后本轮全量及隔离 Linux 验证。
- dry-run 与并发测试只使用合成临时数据库；禁止用实际 Router 数据取证或迁移。
- 没有 Schema 变更；保留现有记录、密钥和 outbox。回退需停止新写者并核对未决任务，不删除锁文件或用旧代码并行启动，不回退 B1 出口约束。
- 独立预览/部署、Commit/Push/PR 和真实额度均未获本批授权。完成自动门禁后仍需相应审批。

## 当前结果

实施中；未宣称门禁通过。B1 的基线豁免不自动继承到 B2。

- 基线红测：构造创建目录、dry-run 改写 WAL/SHM、对不存在目录 dry-run 创建数据库均已复现（red-02：3 failed）。red-01 另有一项合成夹具漏列，修正后重跑，未冒充产品缺陷。
- 新生命周期测试：Windows 11 passed；覆盖两进程竞争、活动 writer 阻止维护、异常退出、目录别名、错误密钥不触发恢复、只读不读密钥、不迁移旧 Schema、待恢复 WAL 拒绝和关闭后拒绝访问。Windows 锁文件锁定字节不可读，取证核对其 inode/长度，其余文件逐字节 hash。
- 首轮既有生命周期回归 88 passed / 17 failed，均定位为旧测试用“第二个仍活动的 Repository”伪装重启或并发迁移；改为明确关闭旧 writer，同时保留独立非协作 SQLite 写入者的 data_version 竞态测试。原组重跑 105 passed。
- 51 个既有测试文件已按 11 个不超过五文件的小批将构造调用机械改为显式 `open`；后续逐项校正真实重启边界，业务断言不删除。正在执行全部受影响回归。
- 三个冻结 RPG 历史 harness 先构造 Repository 再交给 ModelRouterService；服务现在明确取得注入配置的生命周期所有权，无需修改冻结 harness，也不新增 Provider 调用。

### 收口进度（2026-10-07，尚未完成）

- 追加证伪复现：旧实现可在 SQLite handle 尚存活时释放目录锁；修复为活动连接阻止 close。完成事实 outbox 跨 SQL 事务的文件操作同样保持租约，不能在写文件/删除信封之间交接锁。
- Windows 最终受影响组：`1167 passed, 1 failed, 2 skipped`。唯一失败为 `test_r8c_runtime_generation_metadata_attempt_timeout_can_recover`；同 Python 环境、干净 `70a01640` 工作树复现相同失败。其 100ms 测试总限时未被改大或跳过；隔离 Linux 镜像中该用例通过。两项平台 skip 保留其原理由，不当作验收证据。
- 隔离 Linux 生命周期与上述音频用例：`23 passed`，包括 fork 子进程不能使用/释放父进程锁。
- 固定离线验收包：`C:\tmp\modelmirror-r9b2-acceptance-01`；源码内容 SHA-256 `0b0388003cd249868c84bc6bd1ec8df0425789f688b85d23118e7478152a0a2e`；镜像 `sha256:f309c2a19d95da2039d610a82fd46d5b7f5eb22d99a2a366b5e7b9b862423d75`。容器无网络、无挂载、无发布端口、无真实凭据。此段为构建后证据备注，不属于该镜像源码快照。
- 旧快照已通过 guard 43 项、独立 Workflow 7 项、Worker、Catalog、typecheck、生产 build、help-assets。前端 `154 files / 1196 tests passed`，另有 typecheck 配置测试 `17 passed`。后端全量 `8596 passed, 30 skipped`；下述追加修复不在旧快照内，因此这些结果只保留为中间证据，不替代修复版最终门禁。
- 追加硬链接证伪：同一数据库文件可通过另一目录硬链接绕过目录锁，运行及凭据维护两项反例均为 red。统一在打开数据库前拒绝非普通文件、符号链接和多链接文件；原数据库不删除、不修改。修复版 Windows 生命周期、凭据及 Chat Repository 回测 `43 passed, 1 skipped`（POSIX fork 用例不适用于 Windows）。修复后将重新建立离线验收快照并运行全量，不沿用旧快照结论。
- Core、newAPI、实际 Overlay 的 Compose 配置检查通过。合成数据库经 B2 写入/关闭后由同基线旧代码读取并解密，再交回 B2 成功；没有读取真实 Provider 数据。
- AI Research CI 的独立范围阻塞已在干净基线复现。用户另行授权 `codex/ai-research-core-ci-r9b2` 最小治理分类修复，并明确只更新三项锁描述符；该案不混入 B2。旧基线不得为治理修改自我晋升；治理案尚未合并，B2 不宣称相应 CI 门禁已解除。
- 独立应用预览、帮助页重放、Commit/Push/PR 尚未完成；没有真实 Provider 调用，主工作区和现存预览未改动。

### 修复版门禁与独立预览（2026-10-07）

- 用户已授权独立无付费预览；没有授权 Commit、Push 或 PR。
- 修复版验收包 `C:\tmp\modelmirror-r9b2-acceptance-02`，源码内容 SHA-256 `e3f50e30ded21a519432e96e35ced4a266b61de32532cdea25eba95f5f9de5ed`，镜像 `sha256:ac2d20ed27bf3865fde9a74f72f325836b2632be77680f16c7eaeebf087ae8b9`。追加本节前核对全部 63 个变更路径与快照完全一致；本节是构建后证据，不属于镜像源码。生产代码不再修改。
- 修复版 Linux 生命周期、凭据维护、Chat Repository `44 passed`；Windows 同组 `43 passed, 1 skipped`。两项硬链接反例均通过，Linux 同时覆盖 fork 所有权。
- 修复版 guard、Workflow、Worker、Catalog、前端全量、typecheck、生产 build、help-assets 已通过。后端全量仍在运行，最终结论待退出结果。
- 预览仅使用新项目 `modelmirror-r9b2-preview`、新合成 Router 卷与回环端口 `15161`；未复制真实 Provider 配置。Server 只连接 internal 网络，无默认网关；全部控制开关关闭、无真实 Provider 凭据。客户端独立入口网只为发布本机端口，不把 Server 加入外网。
- Docker 默认子网池耗尽的首次启动失败保留为环境证据；核对现有 Docker/主机网段后，只为本批指定未占用网段，未删除或修改其他网络、容器或全局地址池。
- 浏览器实操：未配对时“其他集成”仍显示；合成配对后总览、Provider 与 Catalog、路由与实验三个页签可读；保存“速度优先”成功。活动 Server 下清理和第二写入者均报 `provider_storage_writer_busy`；停机后 dry-run exit 0，三个文件的字节哈希、大小和修改时间完全不变；重启并重新配对后“速度优先”仍选中，运行方式仍为稳定模式，无模型调用。
- 管理会话在重启后失效是当前既有合同，持久化属于 R9C2，不在本批提前实现。预览只证明本次合成配置的保存/重启与维护边界，不证明真实 Provider、付费或生产环境可用。
- Help Center Impact：不修改普通用户页面、交互步骤、数量口径、模型协议或已有帮助截图；浏览器核对管理页现有步骤兼容。本批新增运维约束（目录锁、离线维护、链接拒绝、回退次序）已写入 Deployment/Architecture，不把维护命令塞入普通用户帮助。无真实密钥配置或模型操作。
- 用户已批准 Windows 音频 100ms 基线失败作为 B2 非阻断豁免、另案处理；不豁免任何 B2 单写者、dry-run、恢复测试或修复版 Linux 全量。独立 AI Research 治理修复已通过 Windows/Linux 各 `214 passed, 2 skipped`，但尚未提交合并，不能视为 B2 CI 阻塞已解除。

### 最终本地收口（2026-10-08）

- 修复版原后端进程已正常结束，未重新派发或覆盖证据：`8598 passed, 30 skipped, 9 warnings, 34 subtests passed`，pytest 用时 `2532.38s`，容器及验收脚本退出码均为 `0`。墙钟间隔与进程/pytest 用时不一致，可能经历宿主机暂停；不把墙钟间隔当作连续测试耗时，报告保留原始起止时间。
- 执行 `python -B scripts/provider_acceptance.py verify --bundle C:/tmp/modelmirror-r9b2-acceptance-02`，退出码 `0`：guard、Workflow、backend、Worker、Catalog、前端 typecheck、前端全量、生产 build、help-assets 九阶段均通过；源码、镜像、有效配置和 JUnit 摘要关联校验通过。
- 后端证据为验收包内 `backend.json` / `backend.xml`，JUnit SHA-256 `d9f7bf381161a7a4b247b0a0713844b85d9da824583be6b27a49595f63a0f952`。修复版前端日志复核为 `154 files / 1196 tests passed`，另有 `17` 项 typecheck 配置测试通过。
- 63 个变更路径与验收快照逐文件复核，只有本任务卡的构建后证据备注不同；生产代码、测试与其他文档没有漂移。变更路径凭据格式扫描未命中，`git diff --check` 通过，暂存区为空。原始测试日志及 JUnit 仅留在外部验收目录，不纳入 Git。
- B2 本地自动与独立合成预览门禁完成；Windows 音频时限失败仅按用户本批批准作非阻断基线豁免。此结论不是 GitHub CI、真实 Provider 或生产启用证明。
- 发布依赖仍未解除：独立 AI Research 治理修复尚未提交/合并，需要独立人工治理审批；合并后再刷新基线、复核交叉差异及对应 CI。不得沿用 B1 豁免或把候选治理规则当作已生效规则。
- 本批未 Commit、Push、创建 PR 或执行真实付费调用；停在提交前，不进入 R9B3。回退遵循 Deployment 的离线交接步骤，保留数据库、凭据、outbox 与锁文件，不允许旧新写入者并行。

### 前置治理 PR（2026-10-08）

- 用户授权提交 PR 后成功 Fetch，远端仍为 `70a01640`，无新增交叉改动。
- 按依赖顺序先发布独立治理 Draft PR [#406](https://github.com/PinkElf-Elysia/ModelMirror/pull/406)，提交 `f0e3d758352283a4ad892ba8a449b75b72ec0bd0`，恰好五文件；三项锁描述符与提交 Git blobs 匹配，其余锁字段未变。
- 治理 PR 需要人工审阅，旧 base 拒绝治理候选自我晋升的边界不取消；创建后远端检查尚在运行，未宣称 CI 通过、未合并。B2 本身仍未暂存、提交或创建 PR，待前置治理合并后重新核对基线和对应门禁；不把本地全绿等同于远端发布依赖已解除。

### 前置合并后的发布复核（2026-10-08）

- 用户确认合并并授权提交；远端查询证实 #406 已合并，最新 `origin/main` 为 `ce79771868e338b527bf6719595540b30681b229`。从 `70a01640` 仅增加该治理五文件，与 B2 的 63 个变更路径交集为零；独立 B2 分支已快进，无冲突、未覆盖本地改动。
- 合并后定向回归命令：`python -B -m pytest server/tests/test_provider_storage_lifecycle.py server/tests/test_model_router_credentials.py server/tests/test_provider_chat_control_repository.py server/tests/test_ai_research_bridge.py server/tests/test_provider_chat_stable_service.py -q -p no:cacheprovider`，结果 `163 passed, 2 skipped`，退出码 `0`；使用独立合成临时根及外部 JUnit。
- `python -B scripts/check_provider_coverage.py` 通过：`80 entries, 935 registered candidates`，仅证明源码覆盖。旧固定验收包九阶段重新 verify 通过，B2 生产代码和测试逐文件哈希不变，只有任务卡追加证据不同。
- 旧全量证据仍绑定原 `70a01640` 加 B2 修复快照，不重标为 `ce797718` 上全量重跑；新增治理文件由 #406 独立负责，本 PR 的新 SHA 远端 CI 另行核对。Windows 音频基线豁免范围不扩大。
- 发布前差异/凭据格式扫描通过，无依赖变更或暂存产物；提交后使用已合并的基线审阅器检查固定 base/candidate 分类，结果随 PR 记录。不合并 PR、不进入 B3、不执行付费调用或生产部署。
