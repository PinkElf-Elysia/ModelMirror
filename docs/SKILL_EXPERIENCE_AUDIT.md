# Skill 体验治理与候选能力审计

最后更新日期：2026-08-11
状态：Creator V1、资源化创作与私有本地导入闭环已实现；外部市场接入延后

## 1. 当前边界

当前阶段只治理已经进入模镜的三类数据：

- 手工精选条目。
- `anbeime/skill` 已生成目录。
- `VoltAgent/awesome-agent-skills` 已生成索引。

不增加新的公共 Skill 目录，不接入 SkillHub 或其他外部市场，也不把搜索、导入或创建入口作为变相扩充市场的方式。私有控制台可以导入本机 ZIP 或文件夹，但导入记录与市场目录隔离，不对其他用户发布；外部页面仅用于核验现有索引条目的来源，不能自动成为新目录记录。

## 2. 已落地的体验基线

### 2.1 中文说明与分类

- 市场主说明使用清晰中文，优先回答“这个 Skill 能帮我做什么”。
- 上游英文原文保留为 `sourceDescription`，用于搜索、审计和追溯。
- 所有条目统一归入 10 个任务分类：AI 与智能体、开发与测试、数据与研究、自动化与集成、设计与多媒体、内容与办公、营销与增长、产品与协作、安全与运维、商业与专业服务。
- 来源自己的细分类仍保留在原始快照中，不直接占用用户筛选项。

### 2.2 安装状态

| 状态 | 判定 | 页面动作 |
| --- | --- | --- |
| 固定来源可用 | 已证明固定提交中的直接目录或至少两个确定成员 | 直接安装，或进入集合逐项/顺序安装成员 |
| 有安装说明 | 来源页有说明，但尚未完成仓库与目录核验 | 仅查看来源说明 |
| 待核验来源 | 仓库、版本或目录无法证明，或已发现失配 | 不开放安装 |
| 仅资料参考 | 产品页、规范页或非 Skill 包资料 | 仅查看资料 |

完成本轮核验后，没有条目继续停留在“有安装说明”：能够证明的条目升级为一键安装，不能证明的条目降为待核验，避免留下看似可执行但实际靠猜测仓库名的命令。

## 3. 安装来源核验结果

### 3.1 核验方法

统一核验器只遍历当前精选目录、`anbeime/skill` 快照和 VoltAgent 快照中已经存在的链接，并执行以下只读检查：

1. 将 GitHub 默认分支或来源声明的版本解析为 40 位固定提交。
2. 通过 GitHub 公开文件树读取该提交的完整路径，不下载或执行外部 Skill 脚本。
3. 仅当目标目录中存在大小写一致的 `SKILL.md` 时开放安装。
4. 仓库根链接依次检查根目录、唯一 Skill 文件、唯一同名目录和 frontmatter 名称精确匹配。
5. 已声明目录失效时，只允许在同一仓库固定提交中用唯一同名目录或 frontmatter 精确名称修正；不唯一或语义改变时保持禁用。
6. 所有 1,242 条用户可见记录都写入按 Skill ID 索引的结构化证据；网络或 GitHub 临时故障会终止整批生成，不覆盖上一版证据。
7. 安装请求必须携带证据中的固定提交 SHA，避免默认分支变化后安装未经审计的新内容。
8. 对已收录的 OfficialSkills 来源页，只读取“Setup & Installation”中的 GitHub 声明，不执行页面命令；目录失效时只允许同仓库内唯一同名 Skill 或 frontmatter 精确名称修正。
9. 对 GitHub 已声明但失效的 `SKILL.md` 路径，按仓库读取 Git 历史；仅接受连续、唯一的 `R100` 完整内容重命名链，且链终点必须仍存在于当前默认分支提交。
10. 历史提交只用于证明路径变化；安装仍固定到来源仓库当前 HEAD，不安装已删除的历史版本，也不根据名称或描述猜测替代目录。
11. Skill/SkillSet 类型只依据同一仓库固定提交中的 `SKILL.md` 层级：父范围有 `SKILL.md` 的组合包整体安装；无父级文档且包含至少两个顶层 Skill 的集合不伪装成整包来源，只开放成员逐项安装或前端顺序安装全部成员。
12. 顶层成员由最近的 `SKILL.md` 祖先确定；嵌套 Skill 作为父包内容，不重复注册。只有成员目录完整 Git tree SHA 相同时才去重。

维护命令：

```bash
node scripts/verify-skill-install-sources.mjs
node scripts/verify-official-skill-install-sources.mjs
node scripts/audit-official-skill-source-resolver.mjs
node scripts/audit-github-skill-path-history.mjs
node scripts/audit-github-skill-tree.mjs
node scripts/audit-skill-experience.mjs
```

核验脚本不会执行外部 Skill 中的脚本，也不会从页面发现或导入目录外的新条目。

### 3.2 批次结果

- 统一核验覆盖 1,242 条用户可见记录；第二轮加入 OfficialSkills 来源解析后，共复核 197 个 GitHub 仓库。
- 原 963 项“可一键安装”中，772 项通过固定提交复核，191 项因目录或仓库证据不足降为待核验。
- 117 项 GitHub 仓库根链接中，61 项通过确定性规则升级为可安装，56 项继续待核验。
- 第二轮读取 153 个既有 OfficialSkills 待核验来源页：152 页声明了唯一 GitHub 来源，1 页缺少可核验声明；其中 24 项通过同仓库唯一同名目录修正为固定提交安装，129 项继续待核验，没有既有可安装项被降级。
- 第三轮复查剩余 247 个 GitHub 待核验项，其中 182 个失效路径进入 Git 历史核验；没有发现满足连续、唯一 `R100` 证据且终点仍存在的升级项，171 项确认路径已删除，11 项确认声明路径在默认分支历史中从未出现。
- 最后一轮对全部记录重新执行结构分类。44 项原普通 Skill 被证明确实包含多个安装单元，升级为 SkillSet；6 项仅因名称含 `skills`、`bundle`、`suite` 或 `pack` 而被误标的条目恢复为普通 Skill。
- 经结构证明的 SkillSet 共 80 项：10 项是父范围自身存在 `SKILL.md` 的组合包，可固定提交整体安装；70 项是没有父级文档的成员集合，只能展开后逐项安装。成员按完整目录树去重后共有 3,541 个固定提交安装源。
- 原来因“多目录 SkillSet 暂不支持安装”而禁用的确定性集合已转为成员安装模式；仍无法形成唯一成员树的记录继续待核验，不进行名称或描述推断。
- 最终 922 项具备固定提交安装能力，其中成员集合以成员逐项安装满足 `ready` 约束；311 项待核验，9 项参考资源仍保留 `no-install-source`。

聚合去重后的 1,242 项资源状态为：

| 状态 | 数量 |
| --- | ---: |
| 可用固定来源 | 922 |
| 有安装说明 | 0 |
| 待核验来源 | 311 |
| 仅资料参考 | 9 |

页面按核验后的结构展示 1,159 个普通 Skill 和 83 个 SkillSet；后者包含上述 80 个可核验集合及 3 个参考型集合。集合成员索引独立于首屏目录按需加载，不参与本轮全局需求匹配。

“待核验”不代表来源恶意，只表示当前证据不足以让模镜替用户执行安装。

### 3.4 第三方 Skill 信任与兼容性基线

固定提交来源核验只证明“仓库、提交和目录真实存在”，不等同于脚本安全、依赖可用或运行权限兼容。第一轮信任治理新增纯本地 `SkillTrustReceipt`：按完整 Git tree 读取原始字节，不执行任何第三方代码，并以结构安全、脚本、依赖、权限、网络、凭据、文件写入和宿主能力生成可解释 findings。

风险分级固定为：纯说明、reference 或文本 asset 为低风险；本地 Python/JavaScript、Sandbox 写入、被动二进制或非标准依赖为中风险；网络、凭据、浏览器、MCP、宿主文件系统、包管理器、Shell、桌面控制及敏感能力为高风险；秘密、链接逃逸、可执行或归档文件、未知二进制、动态下载执行、混淆内容及扫描不完整为严重风险。自定义 frontmatter 字段保留为兼容性发现，不改写上游文件，也不把“可安装”表述成原生规范认证。

信任治理现已完成安装、迁移、统一激活门和市场体验，默认模式为 `enforce`。固定 SHA 安装会在 checkout 后重新核对 HEAD、tree、文件模式与 package digest；既有 Git Skill 只有在来源、SHA 和本地字节同时匹配时才补可信状态，否则保留查看与卸载并标记为 `unverified_legacy`。除高置信秘密、逃逸对象、扫描不完整、摘要不可得或无法按固定提交复制等确定恶意或不可安装内容外，其余风险均允许本地控制台在阅读原因并确认精确凭据后安装。动态下载执行、未知二进制、归档或混淆迹象等可疑项以 `routerEligible=false` 从 Agent Router 发现和动作中排除；通过语法、引用和来源闭合检查的 Python/JavaScript 脚本可进入 Router。安装与激活分离：当前运行缺少工具、网络、凭据或宿主能力不会阻止保存已审计包，但会在聊天、工作流或 Router 实际激活时返回不兼容。市场、SkillSet 成员、已安装治理、Router 审批、聊天和工作流选择器使用同一份服务端凭据与激活结论；前端只负责解释，服务端执行最终门禁。扫描公共固定提交仍只发生在维护期，运行时不会联网判断风险；需要回退时可改为 `audit` 或 `off` 并重启 Server。

## 4. 候选能力审计

本节记录三项候选能力的审计结论与当前落地边界。“按需求寻找 Skill”、私有控制台 Creator V1（含资源化创作）和本地 Skill 导入已实现；外部市场继续延后。

### 4.1 允许用户上传 Skill

当前实现：私有 `/skills/import` 工作台接受单个 ZIP 或浏览器文件夹清单，流式规范化后生成不可变 `local_import` 记录与本地 `SkillTrustReceipt`。该流程不调用模型、网络或第三方脚本，也不会自动转换为 Creator 草稿或发布到公共市场。

传输边界：ZIP 只接受 stored/deflate，文件夹上传使用服务端重新校验的路径清单；绝对路径、`..`、NUL、Unicode/大小写碰撞、Windows 保留名、链接、设备项、嵌套归档、可执行文件、未知二进制和配额超限均失败关闭。根目录可直接包含 `SKILL.md`，或剥离唯一一层包装目录；多个 Skill 根会拒绝导入。原始 ZIP 不长期保存，失败或阻断记录只保留脱敏 findings。

信任与安装：高置信秘密、逃逸、扫描不完整或无法形成 Skill 包的内容阻断；其他脚本、网络、依赖或高权限风险可在用户核对精确凭据后安装。合法 Python/JavaScript 可在静态检查和运行能力满足时进入 Router，可疑内容即使确认安装也保持 `routerEligible=false`。同名本地来源替换必须绑定旧安装摘要、新 import revision/digest/fingerprint，并展示有界文本 diff 或二进制摘要变化；不能覆盖 Git、插件、内置或 Creator 来源。

体验与回退：`/skills` 按需加载本地导入列表，详情页按“选择来源 → 扫描规范化 → 核对风险 → 安装或替换”恢复。HTML、SVG 与二进制不在浏览器主动渲染。`SKILL_LOCAL_IMPORT_ENABLED=false` 只关闭新上传、重扫和替换，不破坏已安装且凭据有效的本地 Skill；紧急信任回退仍由 `SKILL_TRUST_GATE_MODE` 控制。

### 4.2 辅助用户寻找满足需求的 Skill

当前实现：`skill-need-local-v3` 在浏览器和服务端使用同一组中英文规范化、任务词、工具别名、分类、IDF 权重和稳定排序合同。浏览器 matcher 继续作为离线回退；服务端 Search Index 覆盖 4,735 个公共市场候选，并将其中 4,333 个可运行候选逐项绑定 Runtime/Trust 指纹。可选语义层只重排这份词典召回集，不能新增候选、改变信任状态或绕过 Router 门禁。

排序依据依次覆盖名称、子技能、标签、中文介绍、分类和来源介绍。安装状态只在相关度接近时用于排序；内部召回最多保留 24 项，公开结果仍最多返回 6 项并展示命中的字段和关键词。高度相关的待核验项可以出现，但只有 `ready` 项显示安装按钮。

安全与边界：

- 推荐只读取目录元数据，不执行来源说明，也不会自动安装。
- 无可靠结果时明确提示目录暂未覆盖，不伪造 Skill、不查询外部市场。
- 词典式匹配可解释且稳定，但不会推断复杂权限、数据边界或深层语义；这些约束仍需用户阅读来源与安装提示确认。
- 需求输入限制为 500 个字符，结果和理由由同一份本地目录确定，便于离线复现。
- `SkillSearchIndexV1` 只为语义层准备名称、类别、标签、能力说明、触发边界和父集合等有界公共摘要；不包含 `SKILL.md` 正文、资源、安装记录或信任详情。服务端索引、前端摘要、Runtime 与 Trust 任一指纹不一致都会失败关闭。
- `skill-rerank-eval-v1` 固定 71 条合成金标（50 条正向、21 条近似反例/无匹配），报告 Recall@24、MRR@6、nDCG@6、Top-1、近似反例误召率和策略违规数。Router 只有在 Provider 成功率、P95、质量回归、有效提升、近似反例和候选策略全部通过后，才能由本地控制台显式晋级。
- 可选语义层使用独立 `SkillSemanticRerankService`：市场仅在请求显式开启时发送最多 500 字符查询和 24 份公开摘要；本地导入、Creator、插件及其他私有候选不外发且固定保留词典槽位。专用 API 优先，`auto` 仅在 `SKILL_RERANK_ALLOW_LLM_FALLBACK=true` 时使用显式配置的 ModelMirror LLM 网关。
- 市场超时 8 秒、Router 超时 3 秒；配置缺失、网络、HTTP、响应校验或治理 Store 失败均返回词典结果。Router 默认 `shadow`，真实输出仍为词典顺序，影子 Store 只保存查询哈希、候选 ID/指纹、建议名次差异、耗时和错误码。晋级凭据绑定 Provider/模型、策略和 Search/Runtime/Trust 指纹；任一变化会自动退回影子模式，环境变量 `off` 始终优先。
- `/skills` 的语义开关只在当前页面会话有效，刷新后恢复关闭；开启前会提示外发边界。只有用户点击“相关/不相关”时才把规范化查询与排名凭据写入本机反馈 Store，最多保留 2,000 条或 30 天，不自动训练、调权或上传。
- 私有治理页 `/skills/rerank` 可查看固定金标、显式反馈和 Router 影子统计，运行评测、确认晋级、清理反馈或立即回退。晋级不授予 Skill 权限，也不影响安装和激活的服务端信任复核。

验收由 `scripts/audit-skill-need-matcher.mjs` 与 `python scripts/audit_skill_rerank.py` 覆盖 PDF、表格分析、网页自动化测试、数据库安全、中英文表达、状态排序、解释理由、无匹配场景、三份索引一致性及固定金标指标。

真实 Provider 验收（2026-08-12）：在独立一次性容器中使用 SiliconFlow `BAAI/bge-reranker-v2-m3` 完成全部 71 条合成金标。65 次实际 Provider 调用成功率为 100%，P95 为 1,043 ms，策略违规为 0；Recall@24 保持 0.94，MRR@6 从 0.575667 提升至 0.656333，nDCG@6 从 0.577733 提升至 0.652080，Top-1 从 0.46 提升至 0.54，近似反例误召率保持 0.714286。全部晋级硬门通过，但验收未执行人工晋级，Router 仍保持 `shadow`。评测只发送合成查询与公共目录摘要；Key、原始响应和查询正文未写入仓库或治理 Store。

### 4.3 通过 skill-creator 创建 Skill

当前实现：Creator V1 已完成“意图定义 → 可信素材 → 不可变草稿 → 三个真实用例 → baseline/with-skill 隔离对照 → 人工反馈与迭代 → 质量门 → 单独安装确认”核心闭环。`SKILL_CREATOR_V2_ENABLED` 默认开启私有控制台；公共 Xpert App 不提供 Creator 入口、Creator 工具或可信素材来源。

评测合同：客观 Skill 必须完成与当前 digest 绑定的恰好 3 个用例；主观创作类 Skill 可运行同一三例，或由本地控制台填写原因并二次确认豁免。新 Skill 的 baseline 不加载 Skill；升级 Skill 的 baseline 固定为会话开始时已安装的 digest；Candidate 使用当前不可变 Overlay。两侧使用同一实际模型与参数，只开放 `skill_read`、`skill_stage` 和固定离线 Sandbox；不开放网络、MCP、浏览器、安装或 HITL。接受或豁免均不会自动安装，当前 digest 仍需单独确认全局安装。

资源化创作在行为质量门之前增加了“澄清 → 资源计划 → 用户确认 → 逐资源生成与验证 → 最终 `SKILL.md` → 提案确认”的阶段：

- 根据任务重复性与确定性，主动判断是否应生成 `scripts/`，并要求脚本具有清晰入口、失败行为、语法检查和实际测试证据；不为凑目录生成脆弱脚本。
- 将较长的领域规则、格式约定和查证材料拆入 `references/`，在 `SKILL.md` 中提供按需导航；运行时支持按文件读取，并为大量参考资料提供受限的 `rg` 检索路径，避免一次性注入全部上下文。
- `assets/` 继续是可选资源，只在任务确实需要模板、示例或输出素材时生成和加载，不作为完整度门的必填目录。
- 资源计划必须与工作流步骤和需求覆盖图闭合；不存在的路径、未引用资源、孤儿文件和重复内容继续阻断批准。

该增强轮也不将 `eval/`、`evals/`、`README.md` 或 user-meta 写入 Skill 包；评测数据继续由 ModelMirror 的独立 Evaluation Store 管理，用户与来源元数据继续由 Creator Session 和可信运行记录管理。保存草稿、通过评测、安装到本地和未来发布到共享市场仍是彼此独立的权限动作。

阶段性结论：Creator V1 已能先规划再生成，并按实际复杂度组织可评测初稿；资源数量不是质量指标，简单 Skill 可以保持零附加资源，复杂 Skill 才按需拆分。

资源化增强 PR 1 建立了 `resource-authoring-v2` 的不可变规划合同：固定 Creator 先提出必要澄清问题，再给出可编辑、可冻结的工作流与资源计划；简单 Skill 可以明确选择零附加资源。该阶段不生成文件，不写 Authoring Proposal，也不改变既有评测和安装门。

资源化增强 PR 2 补齐了服务端构建合同：确认后的计划按依赖顺序逐项生成，单个资源可在服务端内部拆成最多三个 8 KiB 片段，完整组装、静态校验与脚本离线实测后才进入一次用户评审。`skill_authoring_v1` Sidecar 配置将 `inputs/` 与 `skills/` 设为只读，只允许 `work/`、`.tmp/` 写入及 `python`、`python3`、`node`、`rg` 命令；脚本 receipt 与内容 digest 绑定。所有资源确认后才生成 `SKILL.md`，通过资源闭环与 Creator 初稿完整度门后形成普通待审提案，仍不能绕过三例行为评测或独立安装确认。

资源化增强 PR 3 将该合同接入工作台：新 Session 默认使用资源化流程，旧 Session 只读兼容并需用户主动迁移；页面按完整资源展示一次接受或重做，支持直接编辑生成新构建 revision，并在资源全部确认后单独评审最终 `SKILL.md` 与全包差异。`SKILL_CREATOR_RESOURCE_AUTHORING_ENABLED` 默认开启；设为 `false` 可回退到旧提案流程，不影响 Creator 的评测与安装质量门。

### 4.4 已安装 Skill 生命周期

生命周期基础层使用独立的 `SkillLifecycleStore` 保存不可变版本元数据和按 package digest 去重的原始字节文件树。首批只覆盖固定 SHA 的 Git Skill、`local_import` 与 Workspace Creator 草稿；插件继续使用版本化 Skill ID，内置 Skill 跟随镜像版本。迁移会重新读取实际安装目录，并分别核对 Git 信任凭据、本地 Import receipt 或 Creator 不可变 revision。来源、摘要、路径或扫描完整性不匹配时只记录结构化 `migration_blocked`，不删除或改写现有安装。

PR 1 默认保持 `SKILL_LIFECYCLE_ENABLED=false`，只提供状态、只读迁移审计和需要明确确认的迁移入口；安装、卸载、运行绑定和 Router 行为均保持原状。Store 顶层损坏时失败关闭且不覆盖原文件，单条损坏记录只隔离摘要与大小，不保留原记录内容。默认保留当前版本与最多 5 个非当前版本，内容寻址存储上限为 1 GiB；后续 PR 才接入统一安装事务、卸载恢复点、固定版本运行绑定、回滚与永久清理。

### 4.5 外部市场

SkillHub 和其他外部市场继续延后。此次来源页读取仅为核验当前索引中的既有条目，不产生新目录、不做市场搜索、不自动同步外部条目。

## 5. 验收与回退

可重复运行：

```bash
node scripts/audit-skill-experience.mjs
python scripts/audit_skill_trust_index.py
node scripts/audit-skill-need-matcher.mjs
node scripts/audit-official-skill-source-resolver.mjs
node scripts/audit-github-skill-path-history.mjs
cd client && npm.cmd run build
python -m pytest server/tests/test_skill_integration.py -q
```

体验策略集中在 `client/src/data/skillCatalogPolicy.ts`，统一核验证据位于 `client/src/data/skillSourceVerification.generated.ts`，需求匹配位于 `client/src/data/skillNeedMatcher.ts`，早期来源页核验明细仍保留在 `client/src/data/officialSkillInstallSources.generated.ts`。如发现误映射，应修正核验器规则后重新生成整份证据并运行审计；这不会删除原始目录记录或已安装 Skill。固定提交安装能力与需求匹配可分别回退对应提交，不影响共享栈。
