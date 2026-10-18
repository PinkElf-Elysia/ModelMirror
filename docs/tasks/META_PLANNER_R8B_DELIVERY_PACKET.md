# R8-B B0/B1/B2 本地交付材料

## 当前判定

2026-10-18，本文是本批交付材料和状态索引。用户已认可相同步骤实操证据、补齐截图并确认提交本批独立 PR；提交前重新核对上游、暂存范围和证据。不重跑任务、不扩大 R8-B、不自动合并；本段授权不等于远端 CI 已通过。

- 工作树：`C:\tmp\modelmirror-r8b-provider-origin-integration`。
- 分支：`codex/r8b-provider-origin-integration`。
- HEAD 与本次 `git ls-remote origin refs/heads/main` 均为 `253ad98a537dd71e6c41761d89ff03d9e6e86d4e`。没有新的远端主线交集，不执行 fetch、合并或变基。
- 交付范围为内部只读准入契约、现有子调用的预算/恢复保护及 Provider 可信来源集成。不是完整 R8-B 能力开放；Planner 仍为 22 类。
- 本材料批只修改本文、三个历史台账的状态索引及帮助证据页，共五文件。不修改生产实现、测试、截图或运行服务。

## 差异与审查分组

| 分组 | 路径 | 目的与边界 |
| --- | --- | --- |
| 生产实现 | `server/main.py`、`server/workflow_deployments.py`、`server/workflow_native/node_contracts.py`、`server/workflow_native/readonly_subworkflow.py` | 固定发布目标的内部准入、调用请求 checksum、持久根预算委托、父 Provider 身份继承及所有权/不确定结果保护；不新增 Planner Adapter |
| B0/B1/B2 反例 | `server/tests/test_workflow_r8b_{admission,call_recovery,entry_evidence,process_recovery,provider_origin}.py` | 准入、来源、输入漂移、跨 Store/进程恢复及根预算反例；五个新增文件均须纳入最终审查，不能只看 tracked Diff |
| Provider 清单 | `docs/audits/provider-coverage.json`、`server/tests/test_provider_coverage.py` | 两个模块摘要及既有委托位置变化经过语义核对；保留分类、授权状态和变异反例，不批量刷新 hash 放行 |
| 已获授权的主线断言修正 | `client/src/data/models.oct10.test.ts`、`models.oct12.test.ts`、`models.oct13.test.ts`、`server/tests/test_coding_applier_tempfile.py` | 已合入目录事实与 Windows 权限语义；未修改模型目录或生产权限实现，不能包装为 B2 新能力 |
| 用户帮助 | `client/src/content/help-center/articles/recover-workflow-run.md`、`client/src/content/help-center/index.ts`、`client/src/content/help-center/helpContent.test.ts`、`docs/help-center/evidence/r8b-preview-20261018.md` 及图片 | 恢复、取消及不要重新派发的指引；新图片与元数据已同步，原图及历史图片归档保留 |
| 工程证据 | 本文及 `docs/tasks/META_PLANNER_R8B_*.md` 的 B0、B1、B2、Provider 集成、全量和预览台账 | 保留原失败及修正过程；通过本索引区分历史阶段与当前状态 |

本次静态核对未发现新增路由、依赖/锁文件、CI 配置或公共能力变更。持久关系新增可选 `call_contract_checksum`；旧关系继续可读，但缺少可信原输入/定义时拒绝复用或重发。没有运行数据迁移。此处是差异范围核对，不冒充独立安全审计。

## 验证证据索引

| 检查 | 状态 | 已有结果与来源 |
| --- | --- | --- |
| 当前基线后端全量 | 通过 | 10806 passed / 36 skipped / 8 warnings / 50 subtests，pytest、容器及包装退出 0；[集成台账](META_PLANNER_R8B_INTEGRATED_GATES.md) |
| 关键身份/预算/恢复 | 通过 | 同次 JUnit 九文件 185 项，零失败/跳过；其中 R8-B 五文件 115 项；[Provider 集成](META_PLANNER_R8B_PROVIDER_ORIGIN_INTEGRATION.md) |
| 前端全量 | 通过 | 164 文件 / 1301 Vitest，加 17 项 Node 测试；不是本材料批重新运行 |
| 后端语法与 Provider 清单 | 通过 | Python 3.12 AST 1171 文件；81 entries / 947 sites / 0 errors；只证明源码检查 |
| Windows 专项 | 通过 | 102 passed / 1 skipped；目录符号链接条件未验证，不能计入通过项 |
| 最新帮助定向检查 | 通过 | 2 文件 / 71 项；33 篇现有文章图片检查；完整类型检查和生产构建退出 0，保留体积告警；[帮助证据](../help-center/evidence/r8b-preview-20261018.md) |
| 独立预览三条功能链 | 通过 | 固定 v1 纯计算父子完成、原任务逾期等待恢复、取消后重启越过原唤醒点；首次协调器配置失败单列；[预览台账](META_PLANNER_R8B_PREVIEW_GATE.md) |
| 新截图及最终帮助渲染 | 通过 | 三组结果已补齐；恢复/取消宽图经许可等比缩至 1000px，原图与旧图留档；元数据 253ad98a / 2026-10-18，新生产构建的只读静态帮助页实际核对通过，细字缩放限制见帮助证据 |
| 本次文案增补的实操证据接受 | 通过 | 2026-10-18 用户明确认可既有相同步骤实操证据用于本次增补，并要求不再重跑；仅关闭本次证据接受决定，不表示更新正文后重新执行过任务 |
| 远端 CI | 未运行 | 未提交或创建 PR，无本批远端 CI 结果 |

原始日志位于仓库外 `C:\tmp\modelmirror-r8b-gates-20261018`。本次读取 `backend-exit-05.json` 与全量日志尾确认退出码和结果；没有重新执行全量、运行工作流或调用 Provider。

本次逐文件复核 `source-manifest.json`：5427 个文件全部存在，只有帮助正文、帮助内容测试与集成台账三个文件的 hash 不同。生产实现、后端测试和 Provider 清单与已测快照一致。快照之外新增的帮助证据/预览材料另列文档差异，不宣称它们已包含在旧归档。帮助源码变化已有上表的定向测试、类型检查和构建证据。

早期材料整理时的检查：`git diff --check` 退出 0；当时 11 个已跟踪修改加 15 个非忽略新增文件，共 26 个候选路径，暂存路径 0。全部候选路径的禁止产物检查与常见凭据模式扫描均零匹配；本材料批五份文档的 10 个本地 Markdown 链接均存在。扫描不读取运行凭据，也不是穷尽式秘密检测。新增生产文件虽未出现在 `git diff --stat` 中，已经单独读取核对；不能据此省略最终暂存审查。

提交前追加核对：截图及归档纳入后共 40 个原始变更路径（包含两对原图移动，Git 可识别为重命名）。本次对全量归档清单中的 `server/`、`scripts/`、`experiments/` 及 Provider 清单共 3796 项重新计算 hash，零缺失、零漂移。此核对只绑定当前代码与已有测试证据，不表示重新运行了测试或工作流。

## 提交与远端门禁

1. 实际提交前再次核对上游与最终暂存范围；任何新增差异须按影响重新验证，不能让本次检查变成未来变更的通行证。
2. 用户已经明确授权本批提交、推送和创建独立 PR；远端 CI 在该阶段另验，如实报告失败、排队及跳过，不以本地通过冒充。不自动合并。

图片、元数据、相关测试和静态帮助渲染已经完成，详情及派生图片 hash 见 [帮助证据当前状态](../help-center/evidence/r8b-preview-20261018.md)。本次没有重跑工作流；新构建只在无 API 的临时静态页检查，没有替换用户保留结果的原预览。

### 已关闭的本次证据接受决定

2026-10-18，用户明确表示“认可已有相同步骤的实操证据用于本次帮助文案增补，不再重跑任务”。据此，本次不再要求重复派发父流程、等待或取消任务，也不再次请求同项授权。接受范围仅限现有五步操作未变的帮助文案增补；更新正文后的空状态完整重放事实上没有执行，不改写为已执行。新增异常说明依据已记录的协调器配置故障与原任务恢复事实。该决定不是以后帮助变更的通用豁免，不扩大 Provider、根预算、资源权限或 Planner 能力证据，也不替代三张新图、最终渲染和元数据核对。

## 证据与范围限制

- 预览只使用合成数据与纯计算；经典试运行预算快照为空，不证明真实模型根预算扣费或未知外部请求的现场行为。相关自动化使用模拟传输，不等于真实 Provider 资格。
- 36 项 Linux 条件跳过中的 Windows 路径另有专项；专用语言服务器镜像、Office sidecar/parser、隔离 renderer、docx/matplotlib 和宿主目录符号链接仍未全部验证。它们是明确的平台限制，不据此把整个系统标为生产就绪。
- B1 `resource_validator` 仍要求可信组合根接入实际资源权威；本批不完成该接入，不开放顺序迭代、Planner/Headless、公共 App、通用 Goal/Handoff 或新的附件权限。
- 单实例持久执行，不支持多后端共享目录。未结算外部请求必须核对原回执，不因 lease 过期自动重发；不承诺外部服务恰好一次或自动补偿。
- 首次预览 timer 配置错误导致延后恢复，修正后同一任务完成；不声称首次全绿或按期唤醒。

## Help Center Impact 本地材料

- **Yes**：经典工作流 `/workflow` 的等待恢复、取消、结果不确定时的操作预期，以及 `/help/recover-workflow-run`。
- 正文与内容回归测试已经更新；操作顺序仍为运行、继续跟踪、取消原运行和核对终态。新原图已收到，公开恢复/取消图经许可确定性等比缩小；原图和旧 `6ff70de6` 图片均归档保留。图片引用、验证日期和基线元数据已同步。
- 实操基线为 `253ad98a` 加当前 B0/B1/B2 集成差异，日期 2026-10-18；源码归档和安全回执摘要见帮助证据页。基线 SHA 本身不包含未提交功能。
- 用户已接受既有相同步骤的实操证据用于本次文案增补，不再重跑；页面渲染、图片同步和元数据核对已完成。静态渲染不是新运行，仍保留真实 Provider、预算及平台未验证边界；不据此宣称整个 R8-B 已完成或自动创建 PR。

## 回退与交接

- 本材料批回退仅撤销新增索引与状态说明，不修改已有历史证据、任务或预览。
- 功能回退先停止新子调用的准入/派发，保留既有任务、关系 checksum、预算与 Provider 回执；不能直接部署旧代码并让其重发不确定子任务。存在在途执行时，应保留兼容读取与结算保护，另行验证回退方案。
- 不删除运行目录、不清账本、不自动撤销业务写入；不操作共享栈。
- 本卡记录提交前状态；实际 commit、PR 与远端 CI 以 Git/GitHub 回执为准，不预写成功结果。用户补图及帮助收口已经完成，原结果页与预览进程保持不动。
