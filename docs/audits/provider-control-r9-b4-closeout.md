# R9B4 验收归档与发布前审查

## 结论与适用范围

截至 2026-10-11，B4 已在集成基线 `a9ac1f13c160dab6643952d43e78ea95c2006bb4` 完成自动验收：Linux 后端 8925 passed、30 skipped，独立 Workflow 7 passed；前端 1214 passed、1 项获批的同基线失败，其余阶段通过。没有发现豁免范围外的新增失败，也没有把失败报告改成通过。独立合成预览已重放；用户明确批准复用原截图证据，保留其 `e5a808a6` 来源标记，不冒充新基线截图。

初次归档时 Fetch 到上述新主线，存在交叉变更。经授权完成 rebase、恢复改动和重新验收；下面保留的 `acceptance-02` 身份与结果只属于旧 `e5a808a6` 基线，最新结果见“新基线最终验收”。未 Commit、Push、创建 PR、生产启用或调用真实模型。

## 冻结源码与隔离环境

| 项目 | 已核对身份 |
| --- | --- |
| 分支 | `codex/provider-control-r9-b4` |
| 工作树 | `C:/tmp/modelmirror-control-r9-b4` |
| 冻结包 | `C:/tmp/modelmirror-r9b4-acceptance-02` |
| 源码基线 | `e5a808a60b024635a1e13965558f15775c5af86a` |
| 源码内容摘要 | `8c70aa0e1f4c0e4ed20c9bd4fd0d4e92572a9c768d202eb230135d955969f0d3` |
| 镜像 | `sha256:ba5e05d883b035dff66a5b91c76f6de153e8166b160c6ffcd8dbb4354584ab93` |
| 验收 Profile 摘要 | `dabc7b8a065f40619ef29783f61a7337806bf2530a9fc5b1efeb680944088228` |
| Python 依赖清单摘要 | `d2940d69f6e52570646029aea86606739916ab8a8a8c3fe3ed584aa114325bf0` |

所有 harness 阶段使用同一冻结镜像、无网络、无挂载、无端口，清空继承环境并显式关闭模型功能开关。没有将真实凭据、数据库或预览数据复制进镜像。原始 JUnit 和测试日志仅保留在本地验收目录与容器，不提交仓库。

本报告是测试结束后的归档，不在已执行镜像内。任务卡及帮助证据的归档文字更新也晚于镜像；不能宣称更新后的整个工作树哈希等于上述源码摘要。执行代码、测试、部署文件、用户帮助文章和图片必须与该快照保持一致；有任何非归档内容漂移时，本结论失效。

## 验证命令与结果

在 B4 工作树使用隔离测试 Python 运行：

```powershell
& C:/tmp/modelmirror-control-r9-a1/.venv-r9a1/Scripts/python.exe -B scripts/provider_acceptance.py verify --bundle C:/tmp/modelmirror-r9b4-acceptance-02
```

核验源码、镜像、配置与 JUnit 摘要一致。整体退出码仍为 **1**，因为前端全量报告如实为 failed；获批豁免是人工决定，不修改工具判断。

| 阶段 | 范围 | 结果 |
| --- | --- | --- |
| guard | Provider 出口覆盖及验收工具测试 | 43 passed |
| workflow | `test_workflow_run_contract.py` 独立进程 | 7 passed |
| backend | `server/tests/`，上述 Workflow 文件单独执行 | 8921 passed、30 skipped、0 failed |
| worker | `npm run test:all --prefix server/orchestration_worker` | passed |
| catalog | 四个目录/定价/更新脚本 Node 测试文件 | passed |
| frontend_typecheck | `npm run typecheck --prefix client` | passed |
| frontend_tests | `npm run test:run --prefix client` | 1208 passed、3 failed；155 文件中 153 passed、2 failed |
| frontend_build | `npm run build --prefix client` | passed |
| help_assets | `npm run verify:help-images --prefix client` | passed |

补充检查：

- 最终 Windows 准入专项：Readiness、Chat control/stable、Workload、Catalog 服务/API 六个文件，135 passed、1 skipped；不取代 Linux 全量。
- 前端 `test:run` 的 Vitest 失败会中断后续 Node 链。在同一最终镜像、无网络容器 `r9b4-final-node-contracts-20261011` 中单独执行 `node --test server-headers.node.mjs scripts/build-typecheck.node.mjs`：17 passed。没有跳过原先失败的全量命令。
- Core 使用 `.env.example`，newAPI 独立 Compose 与实际 B4 合成预览 Compose 均执行 `config --quiet` 成功；只做配置解析，没有部署或修改数据。
- `git diff --check` 通过。新增 tracked 行和新文本文件的已知凭据签名扫描零命中；这不是对任意秘密的形式化证明，仍保留人工范围审查。
- 合成预览及帮助重放见 [帮助证据](../help-center/evidence/provider-catalog-r9b4-e5a808a6.md)。不将合成可用状态当作真实 Provider 认证或生产验收。

## 精确豁免与后续处置

仅以下项目获用户批准为 B4 非阻断基线豁免，不自动延伸到其他 SHA、平台或批次：

| 平台 | 精确测试 | 归因和处置 |
| --- | --- | --- |
| 前端 | `models.refresh.test.ts` 的 `reconciles the refreshed counted catalog totals` | 同 SHA、同镜像依赖与时钟复现；预期 live 585、实际 569，另案处理 |
| 前端 | `models.refresh.test.ts` 的 `keeps only explicitly expired models inactive` | 同基线复现；到期列表预期 14、实际 30，另案处理 |
| 前端 | `ExpertTeamPage.test.ts` 的 `finds exact managed models beyond the featured prefix and preserves selections` | 同基线复现；固定模型位置为 -1，预期至少 48，另案处理 |
| Windows | `test_r8c_runtime_generation_metadata_attempt_timeout_can_recover` | 同 SHA、同 Python 复现 100ms 时限失败；Linux 对照及最终全量通过 |
| Windows | `test_r8c_runtime_generation_metadata_total_deadline_fails_closed` | 同 SHA、同 Python 复现 500ms 时限失败；Linux 对照及最终全量通过 |

最终前端日志的三个失败与上表完全对应，没有扩大豁免。旧快照的 R8C inactive 断言已修正并通过最终 Linux 全量，不属于豁免。RPG 四项编译失败补齐已有锁定依赖后在双方通过，也不属于豁免。详见 [任务卡](../task-cards/provider-control-r9-b4.md)。未创建远程 Issue，另案处置不代表已修复。

## 报告完整性索引

以下文件位于上述本地冻结包，哈希仅绑定证据，不包含原始测试正文：

| 文件 | SHA-256 |
| --- | --- |
| source.json | `ebc9874ae0855f4676bbac50745787a0f87e59574b32a508222fb893919b8396` |
| image.json | `98f237ca1a001c8b5909fa33d6d5bcb4a8d739f46e98b02b2f3cb7116e6a2235` |
| guard.json | `5e270ad7389b9f7f33a8be45db28dea013d8f0fc4c778d47d09f5f7994c1cb1a` |
| workflow.json | `fda75a51234ebbae23a7ad6f645fdd2aa3d101e3c36d84cad3584cf2fe002a6b` |
| backend.json | `40cafd6763ab0c41a81ad4a6c41508c8d53705ecddbe9cdc58d2216222d00956` |
| worker.json | `dfef7b1eda517556d0f56d1a4943f5ba1a97894b9208e26e5dc01192286635e8` |
| catalog.json | `0ad4591c6d21e16d746e2cea5e42fab37ad1decbd9306ae1dc42b05ef980678a` |
| frontend_typecheck.json | `3c961f0ad0716154cee212b707c605f748141df1f8c178ac43db046234688911` |
| frontend_tests.json | `a9162069497cef8711200b7ebfc5d6bbcfa777e0dac82224295522a0c4353d28` |
| frontend_build.json | `0b502afd6b108182ee7c2c97e82f0f2898b445b29d1eeac882a05eef1eb0eac3` |
| help_assets.json | `a84d2889e09558d05bbb61fd3c3bad382efc9fe7605ec6e5b2a54f2c369d711d` |

## 差异审查与发布阻塞

当前 B4 Diff 的运行改动限于 Chat/Workload 共用准入内核、只读快照及 Catalog 投影；保持实际 Egress 授权、单 IP、凭据快照、运行关联和派发复核。新增 API 字段为脱敏入口/形态状态，不把同模型的另一入口资格当作当前入口资格；不更改数据库、生产依赖、默认策略或模型市场数量。

针对性测试覆盖开关关闭、legacy、required Gate、首目标限制、资格缺失/到期/凭据漂移、不同入口及只读无副作用。required 缺失资格记录的证伪已加入精确集合完整性判定。未发现需扩大 B4 范围的新增缺陷；本结论不是跨全部后续业务入口的完整覆盖审计。

最新主线 PR #410 的 16 个路径中，有两个与 B4 直接交叉：

- `client/src/content/help-center/index.ts`：双方新增不同帮助文章和导航，集成时必须保留双方，不覆盖排序或元数据。
- `client/src/content/help-center/helpContent.test.ts`：双方新增 slug、独立验证基线断言，不能通过删除其中一方期望来解决冲突。

此外主线修改了 `models.ts`、模型市场快照、`models.refresh.test.ts`、多模态 Readiness 清单、目录审计脚本和 Decisions 模型白名单。虽然未直接修改 B4 Router 核心文件，仍影响目录投影输入、前端到期行为和原豁免的适用性。必须重新归因，不预判这三项已修复或继续豁免。

授权接入后，帮助测试唯一冲突已保留双方文章基线断言解决，帮助目录合并结果也已核对。完整恢复快照和保留的 stash 见任务卡。新基线已重新冻结、回归并完成失败归因；主线接入授权不包括 Commit、Push 或 PR。发布前仍须刷新上游并复核交叉变化，不把本次归档当作永久有效的发布批准。

## 新基线最终验收

2026-10-11 已在 `a9ac1f13c160dab6643952d43e78ea95c2006bb4` 重新冻结 `C:/tmp/modelmirror-r9b4-acceptance-03`：源码内容摘要 `e200f4489b464eb51ff77149c763396709b4bf3b05c8818ebc0e60cc0033491e`，镜像 `sha256:6e201d40668ffcb551e6246c58e2c8c9ae481da60fe890ee1df981417ec22d7b`，Profile 摘要仍为 `dabc7b8a065f40619ef29783f61a7337806bf2530a9fc5b1efeb680944088228`。本节归档更新晚于冻结，不改变运行源码、测试或帮助资产。

- 窄后端回归（含 Decisions）：158 passed、1 skipped。
- Guard 43 passed、独立 Workflow 7 passed；Worker、Catalog、typecheck、production build、help assets 均通过。
- 前端全量：1214 passed、1 failed（156 个文件中 155 passed、1 failed）。两项旧目录失败已通过，不再列为新基线豁免。
- 唯一前端失败仍是 `ExpertTeamPage.test.ts` 的 `finds exact managed models beyond the featured prefix and preserves selections`：固定 `qwen/qwen3-8b` 的位置为 -1，预期至少 48。干净 `a9ac1f13` 归档、同镜像依赖、同日时钟复现相同断言；四文件基线回放为 99 passed、1 failed。用户明确批准仅此项为新基线 B4 非阻断豁免，另案处理，不涵盖准入、安全或其他失败。
- 干净基线归档 `C:/tmp/r9b4-clean-a9ac1f13-20261011.tar` 的 SHA-256 为 `0851aedb50a90e92b2a0641befa7cc5309cba0c9b3ed1753597bd6be97c513cb`；回放容器为 `r9b4-a9ac-baseline-frontend-20261011`，无网络和持久挂载。
- 原前端命令保留失败退出码。被 Vitest 中断的 Node 合同测试在同镜像、无网络的 `r9b4-a9ac-node-contracts-20261011` 中单独运行：17 passed。
- 新基线 Core、newAPI 独立栈及实际 B4 预览 Compose 配置解析通过，`git diff --check` 通过，暂存区为空。
- 新基线 Linux 后端：8925 passed、30 skipped、0 failed，另有 34 subtests passed；耗时 2334.86 秒。容器退出码 0，无 OOM。独立 Workflow 的 7 项通过不重复计入该后端数字。旧 Windows 豁免未自动继承。
- `python -B scripts/provider_acceptance.py verify --bundle C:/tmp/modelmirror-r9b4-acceptance-03` 已核验源码、镜像、有效配置及 JUnit 摘要；九个阶段均有最终报告。总体退出码仍为 1，仅因前端报告保留获批基线失败；没有改写报告或工具判定。

最终证据索引（上述本地验收包）：

| 文件 | SHA-256 |
| --- | --- |
| source.json | `46a1568a4fef66f0418bb4dac002d49f2dc82365d8e3e2643b9dea535bc1fcb2` |
| image.json | `c831ecc89f31a3a9b7fbc9ed38916ca2cb5521d1fcfb1344a146f96894cfbc6c` |
| backend.json | `c30584dcb28bca6916631c7987abba695c883bd531999e4ad173eed947ccb912` |
| frontend_tests.json | `a79a17520dcf187cc0dd6144650a3e0ec28b7a9243f528ee84a23318e0e9344c` |

归档审查逐一比较冻结清单的 5138 个文件，只有本报告、任务卡、帮助证据三份文档不同；运行代码、测试、部署文件、帮助文章和 PNG 与已测快照一致。本批共 20 个候选变更路径（13 个 tracked 修改、7 个 untracked），按任务卡拆分小批次实施；未包含依赖锁、数据库、运行日志、凭据或容器数据。19 个文本文件的已知凭据签名扫描零命中，PNG 保持已审查的合成图哈希；该扫描不等于对任意秘密的形式化证明。

最终 Diff 审查未发现新的阻断代码问题：required 首目标及 Gate 检查保留；公开与运行复用精确入口准入；Scoped Chat、legacy 和专用 Adapter 边界未删除；实际 Egress、原子派发和漂移复核仍在。此结论为本任务主审查，不冒充独立审查或远程 CI 通过。

2026-10-11，用户明确批准截图证据复用，并授权完成收尾后提交 PR（含本分支提交与推送）。发布前 Fetch 成功，`origin/main` 仍为 `a9ac1f13c160dab6643952d43e78ea95c2006bb4`，没有新增交叉。再次核验 `acceptance-03` 报告完整性，结果与上述一致。已有帮助 PNG 仍如实标记 `e5a808a6`；复用批准仅覆盖该截图证据，不豁免准入、安全测试或未来基线变化。

用户批准的独立合成预览运行于 `http://127.0.0.1:15165`，使用上述不可变镜像、独立 `modelmirror-r9b4-preview-02_synthetic_router` 数据卷；Server 仅连接 internal 网络，无公网出口。15164 和其他预览未修改。浏览器已重放帮助、配对、四类目录状态与路由阻断，详见帮助证据的新基线重放节。没有真实 Provider 请求、Commit、Push 或 PR。

## 回退与保留数据

本批没有数据库迁移或生产启用。若回退，仅在获批后撤回本批代码和帮助增量；不得撤销此前安全出口、防重放或失败关闭约束，不恢复整库快照覆盖新事实。保留 B4 合成预览、原截图、本地验收包与失败证据，未清理其他工作树、镜像、容器或持久化数据。
