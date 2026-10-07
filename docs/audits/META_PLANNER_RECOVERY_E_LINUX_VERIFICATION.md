# CW10 Recovery E Linux 核验

日期：2026-09-20；补证与恢复核验：2026-09-22。范围：核验 E2 的环境差异与纯上游失败，
不修改 Planner、音频或模型目录实现。
本记录接续 [E 集成记录](META_PLANNER_RECOVERY_E_INTEGRATION.md)，不覆盖原 Windows 失败证据。

## 固定输入与边界

- 纯基线与 E 均固定 `09e8a7d644f626b3e1aa606fcf148f360fc9aadc`。
- 基线来自 `git archive`；E 在相同归档上叠加 168 个冻结的已修改/新增路径。
  包含 E2 最终 Managed Recipe 夹具及两条协议降级攻击，没有复制 Runtime、凭据或上传数据。
- 纯基线 detached 工作树保持干净。没有修改 A-D 原工作树、既有预览器或共享服务。
- 本次生产代码和仓库测试断言零修改；只增加忽略目录内的取证材料与审计记录。
- 没有真实 Provider 调用、业务表写入、审批、发布、Commit、Push 或 PR。
- 恢复时本地远端跟踪引用已为 `origin/main@23e6c165f6a6571eaad855e84cfef631619c1f98`，
  较固定基线前进 9 个提交、80 个路径，与冻结 168 路径无直接交集；模型目录有变化。
  本次没有 fetch 或合入这些提交，结果仅对应固定 `09e8a7d6`，不声称覆盖最新远端。

### 环境

| 项目 | 固定值与限制 |
| --- | --- |
| Python / Node | 3.12.14 / 22.23.2，与 Quality workflow 的主版本要求一致 |
| 系统 | Debian 13.6 容器、Docker Desktop Linux 6.6 WSL2；不是 GitHub Ubuntu Runner |
| 依赖 | 22 个直接 Python 依赖与当前 requirements 完全一致；复用本地缓存镜像，不声称重新解析出的全套传递依赖与 CI 相同 |
| Worker | 使用现有锁文件版本的本地 dev 模块，容器内 link vendor 并执行 TypeScript 构建；不是新的 npm ci 或独立 Worker test:all 证据 |
| RPG 测试依赖 | 首次全量未准备；补齐时只复用锁文件版本一致的 11 个纯 JS 包，无 native addon，不修改依赖/锁文件 |
| 容器权限 | UID 1000、只读根、cap-drop ALL、no-new-privileges、无网络、无发布端口 |
| 挂载 | 只读源码归档/取证脚本；私有 tmpfs 工作目录；只允许写入各自忽略目录中的证据输出 |
| 隔离 | 不挂载 Docker socket、共享卷、用户配置或凭据；禁用 dotenv，Store 全部重定向临时目录，Python 拒绝非回环网络 |
| Git / 源码 | 禁用系统和用户 Git 配置；归档无 .git，基线使用仓库 blob 字节，不继承 Windows autocrlf |
| pytest | 显式加载 pytest-asyncio/anyio；其余自动插件关闭；contract 与 remaining 按 CI 分两次运行 |
| 线程预算 | 首次默认 OpenBLAS 试图创建 24 线程；补证入口设 `OPENBLAS_NUM_THREADS=1`，未提升容器权限或进程限额 |

因此，这是固定源码与主要语言版本的本地 Linux 验证，不是 hosted CI 或完整 CI 环境等价证明。
多项环境因素同时改变，不能将旧失败消失统一解释成单一 Windows 产品缺陷。

## 结果

| 检查 | 结果 | 证据边界 |
| --- | --- | --- |
| 纯基线：最终绑定复跑 | 123 passed / 3 failed / 0 errors / 0 skipped；126 项，JUnit 15.802 秒 | `baseline-attested-final`；120 个选择器，两个巨型参数改选原测试函数，因此包含 6 项额外参数样例 |
| E：同一选择集合 | 123 passed / 3 failed / 0 errors / 0 skipped；126 项，18.774 秒 | `integration-attested`；相同脚本与选择集，恰好同三个音频失败，不合并为额外覆盖 |
| E：Workflow contract | 7 passed，7.811 秒 | 独立进程，遵守 CI 的拆分方式 |
| E：首次 Linux remaining 全量 | 7957 passed / 9 failed / 0 errors / 32 skipped；7998 项，1341.024 秒 | 加上独立 contract 为 8005 项；与 targeted 重叠，不重复计数 |
| 首次全量中的原 65 文件关键矩阵 | 1328 passed | 只是全量子集，不替代总门禁 |
| 首次全量新增六项：纯上游/E 成对复跑 | 两侧各 2 passed / 4 failed；12.783 / 14.005 秒 | 四项 RPG 缺 ajv，两项 OpenBLAS 失败在独立运行中均未复现 |
| 补齐环境后纯上游六项 | 6 passed，17.031 秒 | 已锁定 RPG 依赖与单线程 BLAS；不是代码修复或新的 CI 通过 |
| 补齐环境后 E remaining 全量 | 7963 passed / 3 failed / 0 errors / 32 skipped；7998 项，1276.186 秒 | 同一源码归档；只剩纯上游同败的三项音频断言 |
| 最终后端汇总 | 7970 passed / 3 failed / 0 errors / 32 skipped；8005 项 | remaining 与 7 项 contract 无重叠；不是全绿，不与 targeted 相加 |
| 最终全量中的原 65 文件关键矩阵 | 1328 passed | 零失败、零跳过；子集不重复计数 |
| 合成音频目录取证 | 完成，0 Provider 请求 | 已复现格式展示扩张；默认仍为 planned，额外私有字段被排除；没有验证真实 Provider payload |

原来的 Coding、Worker、Skill、Hook 等被选失败在两份 Linux 对照中均通过；巨型参数的 Windows
环境长度错误也未出现。这不等于这些模块所有可能路径、所有环境或整个仓库已通过。
首次和最终 remaining 都收集 7998 项，报告内部无重复身份。7993 个完整参数 ID 一致，另外 5 个
来自同一文件的 XLSX/ZIP 动态生成字节，时间戳变化导致 ID 不同；对应两函数的 1+4 项在两次运行
均通过。源码与收集数量相同，但不宣称全部输入字节完全相同。详细统计保存在 `final-summary.json`。

## 残留问题的归属

### Linux 全量的环境准备差异

- 四个 `test_actual_rpg05_compiler_schema_is_accepted` 参数用例在 E 与纯上游同败。
  受限 stderr 复现 `ERR_MODULE_NOT_FOUND: Cannot find package 'ajv'`；此前只准备 Worker 依赖，
  没有准备被该后端测试跨目录调用的 RPG 模块依赖。当前固定 Quality workflow 也未安装该目录依赖。
- `test_capability_audit_generator_is_idempotent` 与
  `test_content_parser_imports_from_production_server_layout` 在首次全量失败时明确报
  `OpenBLAS blas_thread_init: pthread_create failed`。两者在相同容器限制的纯上游/E 单独运行都通过，
  不据此宣称某个产品模块稳定失效；需保留全量进程/线程累积的环境影响。
- 补齐环境只在忽略目录入口解包已锁定 RPG 依赖并固定 BLAS 单线程，未改任何断言、业务源码
  或 CI 文件。纯上游六项通过后，E 最终全量这六项也全部通过；首轮失败结果仍保留。
  这是本地测试环境调整的验证，不是产品缺陷修复或 CI 配置已修复的声明。
- RPG package 自己声明 Node 24，而父 CI 使用 Node 22。即使这四个无 I/O Schema 用例通过，
  也不证明整个 RPG 模块获得 Node 22 支持；应由父 CI/实验模块负责人明确跨目录测试的依赖契约。

### 音频目录与 STT

三个同败用例为：

1. `test_audio_catalog_endpoint_does_not_expose_credentials`
2. `test_audio_catalog_only_marks_verified_interactions_ready`
3. `test_qwen3_asr_snapshots_are_adapted_for_manual_verification`

前两项还断言 `modelmirror-audio-contracts-2026-09-03-mai2`，上游 `1d974dcd` 加入 Muse 后已明确
提升为 `modelmirror-audio-contracts-2026-09-12-muse1`。旧版本断言先失败，后面的凭据排除与 readiness
检查未执行；foundation 假目录也缺少后续断言使用的 Muse 条目。不能只改两个期待字符串就宣布通过。

第三项名为 Qwen，却遍历所有 manual profiles。Qwen 两个目标仍支持公共音频格式；新增 Muse 有意
仅允许 WAV，同文件也单独要求 WAV-only。应校准测试对象，而不是给 Muse 擅自增加格式许可。

另用合成目录输入调用现有纯解析方法，已复现 `audio_catalog.py` 的独立展示问题：

| Muse 合成目录输出标签 | 目录展示的输入格式 | Runtime 固定契约 | 默认可调用状态 |
| --- | --- | --- | --- |
| `text` | wav | wav | planned |
| `transcription` | aac、flac、m4a、mp3、ogg、wav、webm | wav | planned |

原因是 `transcription` 分支无条件并入全局格式集合。合成的 api_key/base_url 字段未进入公开投影，
该证据不等于真实端点所有路径都已通过安全测试，也不证明线上 Provider 当前使用哪一种标签。
运行时仍有格式/人工验证/Managed qualification 门禁，本次没有绕过或真实执行它们。

建议交由音频模块做独立最小变更：同步版本与 fixture、限定 Qwen 检查范围，并在保留 WAV-only
安全约束的前提下核对目录合并规则。CW10 不夹带该生产修复。

### 前端模型价格

E2 的两项前端失败在纯上游复现。静态与历史核对显示：`34737ea63` 的目录刷新移除了
`deepseek/deepseek-v4-flash-vision-exp` 的 time_overrides，两项测试仍绑定该易变目录对象。
测试使用固定 UTC 时点，不能归因为当前日期或本机时区。当前该对象的中文 note 仍宣称 UTC 半价档，
与现有结构化目录不一致；这里不仅有测试耦合，也有用户文案待核对。

由模型目录负责人核验真实上游条款后同步数据和说明；定价函数测试应使用固定输入验证时间边界，
不要依靠改价格使断言变绿。本次未请求真实价格数据，也未做 Linux 前端全量。
以上结论限于固定 `09e8a7d6`，不能直接外推到恢复时已前进的模型目录快照。

### 帮助资产

三个未引用 JPG 在纯基线同样失败：`b266b870/studio-beta-headings.jpg`、
`eeb5bbd2/creator-cache.jpg`、`eeb5bbd2/studio-layout.jpg`。
其引入时即没有注册文章引用，校验器还要求被引用图片为 PNG；简单添加 JPG 引用仍不满足门禁。
由帮助内容负责人确定有效文章与图示，或在明确授权后移出公开帮助资产目录，不自动删除或放宽检查。

## 可复查证据

忽略目录前缀为 `.tmp-recovery-e/linux/`，不提交归档、日志、依赖或容器产物。

| 材料 | SHA-256 |
| --- | --- |
| `inputs/baseline.tar` | `5d34b26f82e01438a59401489fe94700bcc18f1def416cb2ba11cf49d023d792` |
| `inputs/overlay.tar` | `9fedf3a04d326b9f2db2eee0a0e3605a1d6fb65680f7a67650f553ed9632459b` |
| `inputs/worker-deps.tar` | `1d47301d431af3e24ec55620cb601d8641ea9b849596fd9e7c6bdb153462fee8` |
| `inputs/rpg-deps.tar` | `798a1da776f5461545b604b76fd6ba21124ddd45564526b2a807df9646270e8e` |
| `run_linux.py`，CI 拆分全量入口 | `d70566fe8ba745d892ae2246252e0b49db33ba0bd19af3bc584c050cde0ba0be` |
| `baseline/results.xml` | `1ebac0a182cf4fe5a49e62ee1f61f5324bcbcf872644625ea4efad163d9bb61e` |
| `integration/results.xml` | `e12c9e911bdf425cb9f36a01a32f56702e427ef76f1d42b058d33f205c79850e` |
| `integration-contract/results.xml` | `b6e4e79856d66225b2783831c8b4523b8c3c0d5fa0312c80c0994715d32817ff` |
| `integration-full/results.xml`，首次 Linux remaining 全量 | `d387617bb5b510909ac79de67eabf2e98d2892b3cd1f06ac4b4b3443bb13fc11` |
| `run_linux_attested.py`，最终成对入口 | `c6d1f020c805bdcfab4b36fa4e35e3f929b625ca1fd150684388dd91034feb37` |
| `inputs/selection.json` | `6d258c06469255c5288f11320d2341b2f734b5cb831930264e7d04e132ceb86b` |
| `baseline-attested-final/results.xml` | `26ffbee5d90964f1ea4bca2091b19c0126abe0c1629500c2e43eb5669576790f` |
| `integration-attested/results.xml` | `31471847eca5925519d8fa4ef1437d5df0dc267bcf2cf39d8b8425331b0c1556` |
| `run_linux_complete.py`，补齐环境入口 | `bc6d78bff18ca72f5c865757cf937eb8c2c126c94cc4ead7cf516fa591ca076e` |
| `baseline-complete-residual/results.xml` | `8217529418ad4163a497d0aec7e97700aa4c208db6f54a3492217dbf5585a4c4` |
| `integration-complete-full/results.xml`，最终 remaining 全量 | `f7f6ecc08e5d5ad2ad197c0d7c7fe029a4f22d3223b19ba41546b6e9e9412365` |
| `probe_audio_catalog.py` | `b4b2f20ba2406e897d58829b62e59dbfd2d3a13bb227a612e44f51e8c7e906ad` |

测试镜像 `modelmirror-cw10-e-linux-test:20260920` 固定为
`sha256:e991b42f9e5f096a5662790fe3e6cd3ed6555c35282b190023be56f05d9ff53b`。
仅从已缓存测试镜像复制已缓存 Node 22 二进制构建，`--pull=false --network=none`，没有下载镜像或包。
首次两份 targeted 的旧环境记录没有脚本/选择集 checksum，保留为历史结果，不作为最终绑定依据。
最终 `baseline-attested-final` 与 `integration-attested` 均记录相同脚本与选择集 hash，各自有
`container.json` 安全投影。`/usr/bin/env -i`、NetworkMode none、只读挂载和无端口都在启动记录中。

`baseline-archive-proof.json` 记录从真实 Git 对象再次执行 `git archive`，得到相同归档 hash，
不只依赖 PAX comment 自报提交。两份源码树 manifest 分别为：

- 基线 4792 文件：`bc9a6c8a357b243575129c6ff9efe9600f1d93663c8e35cf87dfbbd5241adeac`。
- E 4900 文件：`729097757324fa359a5b83f31bcde7a81d5ab0432364e51bfeb1b5a70c6cae1c`。

`source-comparison.json` 确认 60 个修改、108 个新增，恰好为冻结 168 路径，无额外差异。
`dependency-archive-proof.json` 核验 Worker 的 324 项、RPG 的 750 项均严格位于各自 node_modules，
没有越界成员、symlink 或 hardlink，不能覆盖已哈希的业务源码。恢复时 168 文件 hash 仍无漂移。
归档 hash 固定的是测试源码；本次之后追加的审计文字不属于该已测源码快照。

各独立容器将相应脚本只读挂载为 `/run_linux.py`，实际执行参数为：

| 脚本 | 运行参数 | 输出目录 |
| --- | --- | --- |
| `run_linux_attested.py` | `python -B /run_linux.py baseline` | `baseline-attested-final` |
| `run_linux_attested.py` | `python -B /run_linux.py integration` | `integration-attested` |
| `run_linux.py` | `python -B /run_linux.py integration contract` | `integration-contract` |
| `run_linux_complete.py` | `python -B /run_linux.py baseline residual` | `baseline-complete-residual` |
| `run_linux_complete.py` | `python -B /run_linux.py integration remaining` | `integration-complete-full` |

入口最终调用原 `pytest` 用例，未过滤失败、额外替换测试结果或软化断言；用例本身仍包含仓库既有的
Provider 模拟与临时 Store，因此不能替代真实模型和业务效果验收。
Sol 独立只读复核了指定的成对证据、归档/容器绑定与文档口径，未发现新的实质证据问题；
该复核不是独立执行测试，也不包含其完成后才结束的最终全量结论。最终全量由 Astra 核对 JUnit。

## 停止边界

- 本地环境核验不会豁免纯上游失败，E2 和 PR 门禁仍未通过。
- 不继续向 Planner 增加补丁来解决音频、价格目录或帮助资产问题。
- 不进入 E3、不启动新的正式预览器、不调用外部模型，不沿用旧预算授权。
- 最终命令于 2026-09-22 完成，退出码 1 与三项断言失败一致；本轮测试命令均已结束，
  本任务命名的独立容器列表为空。没有停止、重启或改动共享栈。
- 只补充本记录、E 集成记录与批次任务文档；169 个既有/新增待提交路径中无 Runtime、SQLite、
  node_modules、构建/证据产物进入 Git，暂存为空。生产源码与冻结已测内容无漂移。
- 下一步先刷新和审查上游，对仍存在的音频、目录/帮助以及 CI 依赖准备问题独立收口，再以实际
  提交基线重跑门禁；不得仅因这些失败可归属上游就绕过它们或直接进入 E3/PR。
- 回退只停止使用忽略目录内的核验工具；不撤销任何生产或历史业务操作。
