# R9A2：可复现基线与验收证据关联

## 范围与基线

- 仅建立单租户控制面的工程验收关联；不启用入口、不发起真实模型调用、不改 API/SSE、数据库、Provider 策略、模型数量或运行代码。
- Fetch 成功；A1 PR #400 已合并。基线 `f34aca06e3e494d42c40ff6ebff22f24bd637bfc`；与上一基线的差异仅为 A1 五文件，无额外业务交叉变化。
- 分支 `codex/provider-control-r9-a2`，工作树 `C:\tmp\modelmirror-control-r9-a2`。主工作区、其他容器/数据保持不动。
- 本批五文件：本任务卡、`docs/audits/provider-acceptance-profile.json`、`scripts/provider_acceptance.py`、`scripts/provider_acceptance.Dockerfile`、`server/tests/test_provider_acceptance.py`。
- 不新增生产依赖；测试镜像仅安装已有 requirements/lockfiles。Commit/Push/PR/部署/启用均需另行批准。

## 验收合同

`modelmirror-provider-acceptance-v1` 关联 Git commit/tree、实际源码字节和权限摘要、固定基础镜像、构建镜像 ID、有效隔离配置、测试命令、退出状态和 JUnit 摘要。

- Git SHA 不能代替未提交内容摘要；脏改动须以精确 `--include` 明确列入，不自动导出未跟踪或忽略文件。
- 镜像标签不是镜像 ID；构建后读取实际 image ID，并核验镜像内嵌的 source/profile 证据。
- 有效配置来自任务自建容器的定向 inspect；不读取其他容器完整配置或 Env。运行前后校验 image/network/mounts/ports/entrypoint/command/workdir/privileged。
- 模型入口开关显式关闭；容器无外网、无挂载、无端口，测试进程通过 `env -i` 不继承 Provider Key、代理或主机配置。
- 每个 stage 使用独立容器和文件层；无业务服务启动。构建阶段需要依赖网络，但测试阶段完全禁网。
- 结果只追加；失败/中断不自动重跑。修复后的新测试需创建新 bundle，不覆盖历史失败。
- 缺失阶段为 `not_run`，失败不会被后续命令的 exit 0 掩盖，旧 SHA/镜像/配置的报告不可混用。
- JUnit 只向汇总提取数量与失败标识哈希；原始报告仅限隔离本地证据，不进仓库、API 或管理存储。
- 这是本地工程关联，不是远程可信证明/签名账本；有权改写全部文件的操作者仍是信任边界。仅可重建配置，不声称浮动 apt/Python 传递依赖的构建位级可重复；实际镜像和包清单摘要固定验收对象。

## 使用与回退

1. `python scripts/provider_acceptance.py prepare --bundle <全新工作树外目录>`。未提交工作仅对本批五文件追加精确 `--include` 参数。
2. `python scripts/provider_acceptance.py build --bundle <同目录>`。只构建测试镜像，不部署。
3. `python scripts/provider_acceptance.py run --bundle <同目录> --stage <profile 中阶段>`。每次运行保留容器和独立证据；不自动重试。
4. `python scripts/provider_acceptance.py verify --bundle <同目录>`。只读核对；所有阶段成功才 exit 0，跳过项仍需单独审查。

基础镜像摘要和阶段配置见 profile。发布前必须更新到实际提交源码并生成新证据；旧报告不自动成为后续发布凭证。

回退只涉及本批五文件，不需要恢复数据库、重启预览或回退安全出口。任务自建测试容器可停止并保留；不得清理现有应用容器/卷。R9A2 不授权 R9B1。

## 首次源码、镜像与证据对象（保留失败）

- 本次隔离 bundle：`C:\tmp\modelmirror-r9a2-acceptance-01`（不提交）。
- 基线 commit：`f34aca06e3e494d42c40ff6ebff22f24bd637bfc`，叠加本批明确列出的五文件。
- 实际源码摘要：`a035b4d61745e393b9e0c0bbf90928169fd28a7cf928be0b6952b9e49b594247`。
- Profile 摘要：`dabc7b8a065f40619ef29783f61a7337806bf2530a9fc5b1efeb680944088228`。
- 实测镜像：`sha256:5a564b9168ed5b199cd357b2569297264423de4e4fcc33f5739fb1c5aadedad5`。
- 环境：Docker 29.4.3 / Linux amd64；Python 3.12.11、Node 24.18.0；基础镜像 digest 见 profile，Python 实际包清单及摘要保存在 bundle。
- 首次全量出现 1 项失败：Skill Creator 的 JavaScript 离线执行。新测试镜像仅在 `/opt/node/bin` 安装 Node；现有 SandboxEngine 固定 PATH 为 `/usr/local/bin:/usr/bin:/bin`，独立无网探针得到 `node: No such file or directory`。这是本批引入的测试环境缺项，不能归类为基线豁免。
- 最小修复：仅在测试 Dockerfile 将同版本 Node 复制到 `/usr/local/bin/node`（与既有生产 Dockerfile 一致），增加固定 PATH 的构建探针。不改变沙箱 PATH、权限、业务实现或测试断言。修复后创建全新 bundle，不覆盖首轮失败。

## 同 SHA 基线核验与责任边界

重新读取 [f34aca06 Quality #37140498946](https://github.com/PinkElf-Elysia/ModelMirror/actions/runs/37140498946)，三个 job 均成功；不是沿用 A1 豁免：

- Ubuntu 后端：6967 passed / 32 skipped，Workflow 独立合同 7 passed；Agency core 75 passed，OpenRouter guards 21 passed。
- 前端：149 文件 / 1109 测试，另 1 项 Node header 测试；typecheck、build、help assets 成功。
- Windows Project Host：88 passed，Applier 临时文件清理 5 passed。
- 本地 Windows 在相同 f34aca06 源码重新执行 `test_coding_applier_engine.py::test_apply_is_atomic_and_idempotent`：1 failed，原因是 `server/coding_applier/engine.py:943` 使用 Windows 不提供的 `os.fchmod`。该文件与用例本批无变更。归属 Coding Applier 的平台兼容问题；本批不修复、不跳过、不将失败改记成功。后端全量采用仓库 CI 的 Linux 执行环境；Windows 专项以本 SHA 的独立 CI 证据限定结论，不宣称 Windows 全量可运行。
- 同次 CI 的 npm 安装报告：Agency worker 1 high；前端 16 项（2 low / 5 moderate / 9 high）。归属相应依赖维护，未在本批升级依赖或执行自动修复；这不是安全清零或人工风险豁免。构建/测试成功不能替代漏洞可达性审计。

## 修正后的独立验收对象

- 新 bundle：`C:\tmp\modelmirror-r9a2-acceptance-02`；未覆盖 `-01` 的失败报告。
- 源码摘要：`b2966f1000fc45f513ec9df901dcb7cfac47d0c63a24084b7413eaabdb272dd7`；profile 与首轮相同。
- 镜像：`sha256:9a83bdc7d2173a6e7f8314928f7dbe3dc196778c660e5146f9eab79bbe2a3f06`。
- 原失败用例在该镜像独立执行：1 passed。固定 PATH 构建探针亦成功；随后使用该镜像重新执行九阶段验收，不复用首轮通过记录。
- 本任务卡最终结果为测试后附录；源码、Dockerfile、profile、专项测试以 bundle 的逐文件摘要核对，不将事后文档或未来提交错误归入已测镜像。

首轮 30 个跳过项逐条从 JUnit 提取：Windows 原生合同 9；mcp-files 专用 Office 解析 10、隔离 sidecar 2；固定语言服务器镜像 5；部署上游 Node worker 1；隔离 renderer 1；可选 docx/matplotlib 各 1。未新增 skip、未放宽断言。它们属于专用环境/可选依赖覆盖缺口，不能称为已真实验收；Windows 本 SHA CI 仅补充其对应原生合同。Ubuntu CI 与本地 Debian 测试镜像的条件跳过数量不同，不能据数量差异推定等价覆盖。

## 验证记录

| 检查 | 状态 | 证据 |
| --- | --- | --- |
| 证据关联/漂移/脱敏专项 | 通过 | 主机 unittest 23 passed；隔离镜像 guard 43 passed（包含 A1 20 项） |
| 防旁路清单 | 通过 | 当前源码及镜像均检查到 80 entries / 930 registered candidates；仅证明源码覆盖 |
| 同 SHA 全量基线 | 已核验 | 上述 f34aca06 三项 CI；Windows 全量平台限制另列，不继承 A1 结果或豁免 |
| Linux 后端全量（首轮） | 保留失败 | 6991 passed / 30 skipped / 1 failed；独立 Workflow 7 passed；原因见上文 |
| Linux 后端全量（修正版） | 通过 | `-02`：6992 passed / 30 skipped / 0 failed；原 JavaScript 用例在全量中通过且未跳过 |
| 前端全量 | 通过 | 149 文件 / 1109 测试 + 1 项 Node header；typecheck、production build、help assets exit 0 |
| Worker 与 Catalog | 通过 | `npm run test:all --prefix server/orchestration_worker`、四组 Node Catalog guard 均 exit 0 |
| Core/newAPI/Overlay 静态配置 | 通过 | 三套 `docker compose ... config --quiet` 均 exit 0；Overlay 使用合成 URL，不部署 |
| 重复执行证伪 | 通过 | 已完成 guard 再次请求执行返回 exit 1；未新建容器、未覆盖原报告 |
| 缺失证据证伪 | 通过 | 全量尚未完成时 verify 返回 exit 1，不把进行中阶段计为通过 |
| Diff/敏感信息/保留数据 | 通过，提交前再次核对 | 五文件含 untracked 的 diff check 无诊断；凭据模式 0 命中；无业务容器、持久数据或密钥操作 |

上述九阶段均已在修正版 `-02` 镜像重新通过；不是沿用 `-01` 结果。`python -B scripts/provider_acceptance.py verify --bundle C:\tmp\modelmirror-r9a2-acceptance-02` 返回九阶段 passed、exit 0；修正版 30 个跳过项与首轮的逐项分类相同。

## 提交结论与授权边界

- A2 工具、隔离配置与新镜像 Linux 自动验收完成；未修改应用运行路径、数据库或生产依赖。Context7 Docker CLI 文档用于核对实际镜像 ID、定向 inspect 和隔离容器的证据边界。
- 当前 Windows `os.fchmod` 基线失败已在本 SHA 复现并划清责任；在明确披露该平台限制与本批不修复的范围后，用户于 2026-10-03 授权提交本批。该授权仅允许按已披露边界提交，不把失败改记通过，不宣称 Windows 全量兼容，也不构成未来批次的自动豁免。
- 条件跳过的专用 sidecar/可选依赖不计为已验收；npm 告警仍待依赖维护评估。A2 不作生产安全清零、全平台覆盖或真实 Provider 成功声明。
- 用户已授权本批 Commit、Push 和 PR；本记录写入时正在执行提交前核对。Merge、部署、启用入口及付费调用不在授权范围，不自动进入 B1。最终提交 SHA 与远端 CI 以 PR 为准；本地镜像证据仍保持上述基线与明确源码叠加的原始身份，不改写为新提交的运行记录。

Help Center Impact: None。仅离线工程验收工具，无用户可见合同、操作、截图或运行状态变化。没有部署预览，不宣称真实 Provider 或专用 sidecar 集成已验收。
