# Skill 扩展包系统集成说明

Skill 是模镜为 AI 打工人准备的“岗位手册”。每个 Skill 是一个包含 `SKILL.md` 的目录，可选携带脚本、模板、参考资料等资源。模镜后端负责安装、卸载和读取 Skill，前端负责在技能市场展示、管理已安装 Skill，并在面试间把选中的 `SKILL.md` 注入为系统提示词。

最后更新日期：2026-08-11
维护人：模镜团队

## 1. 概述

模镜的 Skill MVP 遵循 agentskills.io 风格的目录约定：

```text
some-skill/
├── SKILL.md
├── scripts/
├── templates/
└── references/
```

集成架构：

```text
┌────────────────────┐
│ /skills 技能市场    │
│ /chat 面试间        │
└─────────┬──────────┘
          │ HTTP
          ▼
┌────────────────────┐
│ FastAPI /api/skills │
│ SkillManager        │
└─────────┬──────────┘
          │ git sparse-checkout
          ▼
┌──────────────────────────────┐
│ server/skills/installed/      │
│ skill_id/SKILL.md             │
│ installed.json                │
└──────────────────────────────┘
```

当前 MVP 支持从 GitHub 仓库的指定子目录安装 Skill。生产默认只允许 `https://github.com/{owner}/{repo}` 来源，避免 SSRF 和任意路径读取。测试环境可以显式打开本地仓库来源。

市场资源按固定提交中的真实目录结构分为两种类型：

- `skill`：核验范围内只有一个顶层 `SKILL.md`，直接安装该目录。
- `skillset/package`：范围根目录自身有 `SKILL.md`，并包含其他后代 Skill；按父目录整体安装并保留其中资源。
- `skillset/members`：范围根目录没有 `SKILL.md`，但包含至少两个顶层 Skill；集合本身不安装，用户可在详情中逐项安装，或由前端按顺序安装全部确定成员。

类型不再根据名称中的 `skills`、`bundle`、`suite` 或 `pack` 推断。当前基线包含 80 个经结构证明的 SkillSet：10 个父级组合包和 70 个成员集合；成员来源按完整目录 tree SHA 去重后共 3,541 项。成员索引位于独立 JSON 文件，仅在打开集合详情时加载，不进入首屏包，也暂不参与全局需求匹配。

目录来源分为三层：

| 来源 | 快照 | 导入结果 |
| --- | --- | --- |
| 手工精选 | `client/src/data/skillProjects.ts` | 4 个基线条目 |
| [anbeime/skill](https://github.com/anbeime/skill) | `011d53f4f238cf7cc8e2cdae8452fffaec7eb1ae` | 从本地 `skill-main` 与远端核对后生成 64 个项目，其中 2 个 SkillSet |
| [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) | `6c82fb77e5bf84de77d1074b2d98d65d75ea730e` | README 索引去重后生成 1,178 个项目；原始索引有 475 个明确 GitHub 子目录，来源治理累计核验 425 个补充来源并阻止 153 个失配来源 |

VoltAgent 仓库自身不包含 `SKILL.md`，不能把仓库根目录当成 Skill 安装。README 明确给出 GitHub `tree/.../<目录>` 或 `blob/.../SKILL.md` 的条目可直接生成 `installSource`；其余既有来源已经按来源页声明、GitHub 固定提交和实际 `SKILL.md` 路径完成审计。当前没有条目停留在“有安装说明”：核验通过的条目升级为一键安装，证据不足或失配的条目进入“待核验来源”。

市场展示层将来源细分类收敛为 10 个稳定任务大类，并把晦涩英文说明转换成面向用户的中文能力说明。上游原文保留在 `sourceDescription` 中，用于搜索和追溯。完整治理规则与候选能力结论见 [Skill 体验治理与候选能力审计](./SKILL_EXPERIENCE_AUDIT.md)。私有本地导入与公共市场目录保持隔离；新增目录和 SkillHub 等外部市场继续延后。

### 1.1 第三方目录信任索引

目录中具备固定提交安装源的顶层 Skill 与 SkillSet 成员，会在维护期按完整 Git tree 生成本地、确定性的 `SkillTrustReceipt`。扫描过程不执行脚本、不加载 Skill、不调用模型，也不会在运行时访问 GitHub；同一个 `repoUrl + subPath + verifiedCommit` 只保存一份凭据，重叠集合成员共享该凭据。

凭据同时绑定目录 tree SHA、按排序路径与原始字节计算的 package digest、扫描器版本和凭据指纹。扫描范围包括严格 YAML、路径、凭据、引用、Python/JavaScript 静态语法、脚本与命令、依赖、网络、宿主能力和被动二进制资源。PNG、JPEG、GIF、WebP、PDF、WOFF/WOFF2、MP3、WAV、MP4 只在扩展名与 magic 一致时作为不透明资源记录，不解析或执行。只有高置信秘密、链接或 Git 对象逃逸，以及无法完整扫描、定位、摘要或按固定提交复制的包才形成 `blocked` 凭据；动态下载执行、归档、未知二进制、magic 失配、活动文本或混淆迹象等可疑但仍可精确复制安装的内容改为显式确认，并从 Agent Router 候选中排除。合法的 Python/JavaScript 脚本、Git 可执行位、仓库根参考文件和自定义资源目录本身不构成阻断；语法、引用与来源闭合后可进入 Router，实际运行仍受能力门约束。

信任门由 `SKILL_TRUST_GATE_MODE=off|audit|enforce` 控制，当前默认 `enforce`：安装器会解析固定来源、checkout 后复核 HEAD、Git tree、文件模式和原始字节摘要，并把信任状态写入安装元数据。Git 来源必须使用 40 位固定提交；索引缺失或失配、确定恶意或无法完整安装的内容、未确认的条件凭据和未核验旧安装会拒绝安装或激活。用户对其他风险的精确版本完成本机确认后可以安装；安装不要求当前聊天或工作流已经具备全部运行能力。激活时才检查必需工具、命令、网络、凭据与宿主能力，不兼容时保持已安装但拒绝本次激活。可疑内容的手工确认不会把它加入 Router；通过语法与引用校验的本地 Python/JavaScript 脚本可以进入 Router。`audit` 只记录相同判断而不改变旧行为，`off` 完整回退旧行为。

中高风险及可疑内容的本机授权绑定 `skill_id + trustFingerprint`，凭据或版本变化后自动失效；普通可路由候选的 Router 人工批准只进入当前运行恢复状态，不写永久授权。`routerEligible=false` 的可疑凭据不会出现在 `skill_find` 结果中，也不能通过伪造候选调用 `skill_enable` 或 `skill_install`。市场、SkillSet 成员和已安装页面均展示同一份服务端凭据；聊天与工作流选择器保留被阻断项但禁用选择，服务端仍执行最终门禁。统一激活检查覆盖静态 `skill_ids`、`skill_read`、`skill_stage`、`skill_enable`、`skill_install` 与插件 Hook；即使已确认，运行能力不足仍会返回不兼容。Workspace Creator、插件和内置 Skill 继续使用各自既有质量与来源合同，不套用第三方目录评级。三份索引共享同一目录指纹，任一映射或指纹不一致都会失败关闭，但不会拖垮 Server 其他模块启动。

### 1.2 已安装 Skill 生命周期基础层

`SkillLifecycleStore` 是与实时安装目录分离的不可变历史 Store。它按原始字节 package digest 去重保存文件树，并将版本绑定到来源 revision、Git 固定提交、信任凭据或 Creator 质量证据。首批支持 Git、`local_import` 和 Workspace Creator；插件仍以版本化 Skill ID 管理，内置 Skill 仍跟随镜像。

`SKILL_LIFECYCLE_ENABLED=true` 是私有控制台的默认值；设置为 `false` 并重启可回退到旧的 current-only 路径。`GET /api/skills/lifecycle/status`、`GET /api/skills/lifecycle/skills` 与只读迁移审计用于恢复治理状态；`POST /api/skills/lifecycle/migration` 始终要求 `confirmed=true`。完成迁移的 Git、`local_import` 和 Workspace 草稿使用统一事务记录安装、替换、卸载恢复点与回滚。新运行持久化不可变版本绑定，历史 `skill_read`/`skill_stage` 不受之后全局版本切换影响；已有运行没有绑定时保持兼容读取当前版本。

版本列表由 `GET /api/skills/{skill_id}/versions` 返回；回滚接口要求 Lifecycle revision、当前版本 ID、目标 package digest 和 `confirmed=true` 全部匹配。Creator 质量凭据与第三方信任凭据均随版本冻结，回滚不会自动扩大长期授权。无法证明来源、摘要或目录完整性的既有安装仍保留查看和卸载能力，并在 Lifecycle Store 标记 `migration_blocked`。`/skills?tab=installed` 提供迁移、版本时间线、卸载恢复和回滚入口。默认容量为当前版本加 5 个非当前版本、全局 1 GiB；容量满时失败关闭，不自动删除历史，永久清理继续延后。

## 2. 如何添加新的 Skill 到市场

手工精选市场数据位于：

```text
client/src/data/skillProjects.ts
```

自动生成目录位于：

```text
client/src/data/anbeimeSkillCatalog.generated.json
client/src/data/voltagentSkillCatalog.generated.json
```

新增条目时，优先填写 `installSource`：

```ts
{
  id: "my-pdf-skill",
  name: "PDF 文档处理技能",
  repoName: "owner/repo",
  repoUrl: "https://github.com/owner/repo",
  category: "文档处理",
  kind: "skill",
  description: "一句话说明这个 Skill 能帮用户完成什么。",
  readmeSummary: "README 摘要。",
  stars: 1200,
  language: "Markdown / Python",
  updatedAt: "2026-06-16",
  installCommand: "git sparse-checkout 示例命令",
  installNote: "安装说明。",
  installStatus: "ready",
  installSource: {
    repoUrl: "https://github.com/owner/repo",
    subPath: "skills/pdf"
  },
  tags: ["PDF", "文档摘要"]
}
```

要求：

- `installSource.repoUrl` 必须是 GitHub 仓库地址。
- `installSource.subPath` 必须指向包含 `SKILL.md` 的目录。
- `installStatus` 必须是 `ready`、`manual`、`pending`、`reference` 之一。
- 没有 `installSource` 的条目不能标为 `ready`，也不能一键安装。
- `kind` 由统一结构核验证据覆盖，不得仅依据名称或用途声明 SkillSet。
- `ready` 必须是具有一个固定提交来源的 `direct` 模式，或具有至少两个固定提交成员来源的 `members` 模式。
- `members` 集合不得设置整体安装源；每个成员必须指向同一来源仓库、同一固定提交下包含 `SKILL.md` 的目录。
- 批量来源不要手改 `*.generated.json`，应重新运行同步脚本。

### 2.1 同步 anbeime 本地目录

先确认本地副本与远端提交一致，再运行：

```bash
node scripts/sync-anbeime-skill-catalog.mjs <anbeime-checkout> \
  --commit <verified-main-sha> \
  --stars <repo-stars> \
  --updated-at <YYYY-MM-DD>
```

生成器会扫描实际 `SKILL.md`、按标准化内容去重、排除 `_template` 与占位文件、识别嵌套 SkillSet，并从 `scripts/`、`references/`、`assets/` 生成标签。

同步过程保留第三方 `SKILL.md` 原文，不会替上游重写 frontmatter。当前 64 个 anbeime 项目中，33 个通过 Codex `quick_validate.py` 的严格格式检查；另外 31 个主要使用了上游自定义的 `dependency` 字段。模镜安装器只读取 `name`、`description` 并支持标题回退，因此这些条目可进入市场，但“可安装”不等于“已通过 Codex 严格格式认证”。使用前仍需审查第三方依赖、脚本和密钥要求。

### 2.2 同步 VoltAgent 索引

```bash
node scripts/sync-voltagent-skill-index.mjs <awesome-agent-skills-checkout> \
  --commit <verified-main-sha> \
  --stars <repo-stars> \
  --updated-at <YYYY-MM-DD>
```

同步器只读取 README，不抓取或执行外部 Skill。它保留原始来源链接；展示层负责统一中文说明、稳定分类和安装状态，且仅对后端当前可验证的 GitHub 子目录开放一键安装。

两个脚本都支持 `--check`。传入与生成时相同的来源和快照参数，可验证已提交 JSON 是否过期。

## 3. 后端 API 文档

### 3.1 列出已安装 Skill

```bash
curl http://localhost:8000/api/skills/installed
```

响应：

```json
{
  "skills": [
    {
      "skill_id": "anthropics-skills-skills-pdf",
      "name": "PDF Skill",
      "description": "Extract and summarize PDF documents.",
      "repo_url": "https://github.com/anthropics/skills",
      "sub_path": "skills/pdf",
      "installed_at": 1781616000.0
    }
  ]
}
```

### 3.2 安装 Skill

```bash
curl -X POST http://localhost:8000/api/skills/install \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/anthropics/skills","sub_path":"skills/pdf","ref":"b29e7cf65e5cb78a5ac33d582270551bc74a14eb"}'
```

提供 `ref` 时，后端只接受完整 40 位 Git 提交 SHA，并执行固定提交安装：

```bash
git init <tmp>
git -C <tmp> remote add origin https://github.com/anthropics/skills
git -C <tmp> sparse-checkout init --cone
git -C <tmp> sparse-checkout set skills/pdf
git -C <tmp> fetch --depth 1 origin b29e7cf65e5cb78a5ac33d582270551bc74a14eb
git -C <tmp> checkout --detach FETCH_HEAD
```

没有 `ref` 的既有手工条目继续使用浅克隆默认分支的兼容路径。

然后复制子目录到 `server/skills/installed/{skill_id}/`，并写入 `installed.json`。

常见错误：

| 状态码 | 场景 |
| --- | --- |
| 400 | 非 GitHub 来源、子目录非法、找不到 `SKILL.md`、git 执行失败 |
| 500 | 未预期的安装管理器异常 |

### 3.3 读取 Skill 内容

```bash
curl http://localhost:8000/api/skills/anthropics-skills-skills-pdf/content
```

响应：

```json
{
  "skill_id": "anthropics-skills-skills-pdf",
  "content": "# PDF Skill\n..."
}
```

### 3.4 卸载 Skill

```bash
curl -X DELETE http://localhost:8000/api/skills/anthropics-skills-skills-pdf
```

响应：

```json
{"ok": true}
```

## 4. 前端组件说明

`/skills` 页面包含六个标签：

- `技能市场`：合并手工精选与两个生成目录，支持关键词、功能分类、Skill/SkillSet、可安装状态筛选；默认分批渲染 48 项，避免一次挂载全部索引卡片。
- 父级组合包显示“安装技能包”；成员集合显示“成员可安装”和“查看成员”，详情支持本地名称/路径搜索、每页 50 项分页、成员逐项安装，以及按顺序调用现有接口的“一键安装全部成员”。该操作会跳过已安装成员并在首个失败处停止，不使用整仓安装或新增后端批量协议。
- `已安装`：调用 `/api/skills/installed`，展示本地已安装 Skill、来源摘要、信任凭据、Router 状态和卸载按钮。本地导入项可回到精确 import 记录查看原始包摘要和扫描结论。
- `本地导入`：懒加载私有 Import Store 摘要；`/skills/import` 接受单个 ZIP 或文件夹，完成确定性扫描、风险确认、安装或同源替换，不执行包内脚本、不调用模型或网络。
- `工作区草稿`、`待审提案`：保留通用创作与审批入口；默认开启的私有 `/skills/create` 提供 Creator V2 工作台。新 Session 先确认资源计划，再按需生成和验证 `scripts/`、`references/`、可选 `assets/`，最后评审 `SKILL.md`；简单 Skill 可以不生成附加资源。客观 Skill 使用固定三类核心案例和最多 9 条用户确认回归案例，并在进化后以 baseline / previous / candidate 三侧复跑；主观创作类仍可记录明确人工豁免。新增退化必须逐项确认并说明原因，质量门通过后仍需独立确认安装，评测不会自动安装。详细边界见 [Skill 体验治理与候选能力审计](./SKILL_EXPERIENCE_AUDIT.md#43-通过-skill-creator-创建-skill)。

触发描述闭环由 `SkillTriggerSuiteV1`、`SkillTriggerReceiptV1` 和纯本地 `SkillTriggerEvaluator` 提供。验证器把 Creator 草稿作为内存中的 `workspace_draft` 候选，直接复用生产 Finder/Router 的 Top 6 与 Top 24 词典排序，不写入安装目录或全局索引；凭据绑定套件、描述、ranker 及 Runtime/Trust/目录指纹。固定私有 Trigger Optimizer 只接收 Creator 意图、冻结正反例、当前描述和有界公共竞争候选摘要，使用 `toolMode=none` 一次提出最多三个描述；模型不能提交名次、候选 ID、指纹或门禁结论。服务端逐项重跑生产排序并按最差正例名次、正例名次总和、反例安全距离、长度和摘要确定稳定推荐项。

用户也可在没有模型 Key 时手工维护正反例与单行描述，再调用同一个本地验证器。新建资源化 Creator Session 默认必须先确认一个通过描述，随后资源计划确认、资源构建提案和 Workspace draft 安装都会在服务端重新验证；资源内容变化但 Skill 名称、描述与触发套件不变时可复用确认，描述或合同变化则失效。Step 2 默认只展示正例命中、反例避开与唯一下一步，Top 6/24、匹配词和竞争候选收进折叠诊断。旧 Session 只有用户主动启用时才迁移，既有 Creator 安装、Git、本地导入和插件不追溯阻断。`SKILL_CREATOR_TRIGGER_OPTIMIZATION_ENABLED=false` 可完整回退原 Creator 流程，已保存的触发 Store 数据不会删除。

社区资源卡片同时显示原始来源与收录索引。安装第三方条目只会复制目录，不会在安装阶段自动执行脚本；用户仍需在激活前检查依赖、外部服务和凭据要求。

面试间 `/chat/:modelId` 增加 Skill 选择器：

1. 页面加载时读取 `/api/skills/installed`。
2. 用户选择 Skill 后，前端调用 `/api/skills/{skill_id}/content` 并缓存 `SKILL.md`。
3. 发送普通聊天时，前端在 `messages` 最前面插入：

```json
{
  "role": "system",
  "content": "当前激活 Skill：PDF Skill\n\n# PDF Skill\n..."
}
```

4. 如果同时选择知识库，前端会把 Skill 说明与用户问题一起传入 RAG 查询，不改变现有 RAG API。

私有 Xpert、工作流、Goal 与 Handoff 可选择启用本地目录发现和审批式固定 SHA 安装；完整运行时边界见 [私有 Agent Skill 按需路由](./SKILL_RUNTIME_ROUTER.md)。

### 4.1 Skill Runtime Guidance V2 基础门

`SKILL_RUNTIME_GUIDANCE_V2_ENABLED=true` 默认在经典 Workflow 及其发布后的私有 Xpert 中启用真实应用门。用户显式选择、或本轮通过 `skill_enable` / `skill_install` 激活的 Skill 属于 `required`；插件附带及 `auto_discover` 候选保持 `available`，不会因为可见就冻结全部已安装 Skill。

服务端在模型调用前固定 Skill 版本、内容摘要与信任指纹，并在接受最终答案或执行副作用、敏感、审批及 terminal 工具前核验同一运行的 `SkillApplicationReceipt`。存在必用 Skill 时会跳过基于模型的 Tool Selector，保留完整已授权工具集合，避免选择器先消耗模型额度或移除 Skill 所需能力。首次缺少 `skill_read` 时只允许一次服务端纠偏；再次遗漏会以稳定错误终止 Workflow，不创建审批卡或放行工具。`SKILL_APPLICATION_RECEIPT_MODE=off` 时该门失败关闭。

绑定 `skills_runtime` 且开启 V2 时，运行器会同时提供 `sandbox_list_files`、`sandbox_read_file` 和 `sandbox_search_files`，用于消费 `skill_stage` 暂存的 UTF-8 reference、脚本源码和文本 asset；不会自动提供写入、Shell、网络或宿主文件访问。模型必须先读取 `SKILL.md`，仅在说明确实引用包内资源时暂存，并使用 `skills/<skill-id>/<relative-path>` 精确路径或有界搜索。被动二进制只能暂存，不能作为文本读取证据；脚本只有在工作流另行绑定 `sandbox_shell` 且信任、命令白名单和审批条件同时满足时才兼容。

资源访问凭据只保存 Skill 相对路径、实际/预期摘要和工具名称，不保存搜索词、正文或工具参数。`skill_stage` 后的服务端映射绑定当前 Workspace、Skill 版本和内容合同，审批恢复继续使用同一映射；资源发生变化时 receipt 转为 `unverified`。

工作流配置面板以可删除标签维护必用 Skill；自动发现、目录检索和审批式安装收在高级选项中。WorkflowRun 与私有 Xpert Chat 使用同一张应用卡展示 `required / available / reading / staged / resource_accessed / repair_requested / verified / failed`，刷新后从持久化执行事件恢复。卡片只显示 Skill ID、版本标识、资源数量和有界相对路径；调用 `skill_read` 证明正文已交付给模型，但不等于输出语义质量已认证。

旧节点无需迁移即可采用 V2，节点数据和既有 receipt 不会被重写。回退只需设置 `SKILL_RUNTIME_GUIDANCE_V2_ENABLED=false` 并重启 Server；已有 Workflow、安装数据和 receipt 均保留。

### 4.2 Skill Hook V2 包合同

Hook V2 的唯一标准入口是 `hooks/manifest.json`，版本固定为 `modelmirror-hook-manifest-v2`；实现脚本仍放在 `scripts/`。Manifest 只能声明稳定 Hook ID、四类固定事件、`annotation / validation / guard` 模式、工具事件的精确工具名、包内 `.py / .js` 脚本、用途、验收条件和 1–60 秒超时。服务端根据扩展名选择运行时，不接受 executable、argv、cwd、环境变量、宿主路径、regex 或通配工具名。根目录 `modelmirror-hooks.json` 仅属于旧 `legacy_argv` 节点，不是 V2 包入口。

`SkillApplicationReceiptV2` 在保留 V1 receipt ID 和普通应用合同的同时增加 `hook_execute` 及有界 Hook evidence；旧 V1 记录读取时得到空 evidence，不补造执行事实。Evidence 只保存 Hook/事件/模式、manifest/script/context/result 摘要、脱敏 code、结果类型和 verified 状态，不保存参数、正文、stdout/stderr 或模型输出。涉及 Hook 的后续评测必须另外取得 verified V2 evidence。

当前 `SKILL_PLUGIN_HOOK_V2_ENABLED=true`。新拖入的 `plugin_hooks` 节点默认使用 `typed_v2`；缺少 `hook_mode` 的历史节点仍走 `legacy_argv`，只有用户显式升级当前画布后才切换。Typed V2 在固定版本的受保护 `skill_authoring_v1` Sidecar profile 中运行：PreToolUse 先于 HITL，`guard + deny` 不创建审批卡；PostToolUse validation 失败会明确标记副作用已经发生且不会自动回滚。Annotation 技术故障告警后继续，validation/guard 的超时、损坏结果或 Sidecar 故障失败关闭。

类型化 Hook 只接收有界脱敏 context，并把严格 JSON 写入服务端冻结的 result 路径。脚本不能选择 executable、argv、cwd 或权限，也不能改写工具参数、输出或审批结果。每次执行结束会以 `{}` 覆盖 context/result；Application Receipt 和 `skill_hook_status` 只保留摘要、稳定 code 与状态。并行工具在任何 provider 调用前完成整批 PreToolUse 检查，同一中间件的 Hook 脚本串行执行以隔离临时 context；工具本身仍可按原策略并行。审批恢复会在同一 Server 进程内复用已 sealed 的 Skill workspace，服务重启只复用 verified evidence，不持久化 Sidecar capability。

Creator 的资源计划把 Hook 作为独立结构化合同，而不是模型可自由编辑的第四类文件。只有需求明确包含会话或工具事件检查时 Planner 才能提出 Hook；用户确认后先生成并实测绑定脚本，再由服务端确定性生成只读 `hooks/manifest.json`。Hook spec、脚本或 manifest 摘要变化会使 receipt 失效；普通 Skill 默认保持零 Hook。工作流编辑器只列出 `hookCapability.runnable=true` 的已安装 Skill，并从 manifest 只读展示事件、模式、工具范围和故障策略。WorkflowRun 与私有 Xpert Chat 从持久化 `skill_hook_status` 恢复稳定运行卡。设 `SKILL_PLUGIN_HOOK_V2_ENABLED=false` 可停止 Typed V2 创作和执行，旧 Legacy 节点、普通 Creator 资源流程及已安装数据均保留。

## 5. 测试指南

后端测试不依赖外网，会在临时目录创建本地 git 仓库作为 mock Skill 源：

```bash
python -m pytest server/tests/test_skill_integration.py -q
```

目录快照检查：

```bash
node scripts/sync-anbeime-skill-catalog.mjs <anbeime-checkout> <其余快照参数> --check
node scripts/sync-voltagent-skill-index.mjs <voltagent-checkout> <其余快照参数> --check
node scripts/audit-github-skill-tree.mjs
node scripts/audit-skill-experience.mjs
python scripts/audit_skill_trust_index.py
cd client && npm.cmd run build
```

覆盖范围：

- 安装本地 mock Skill。
- 列出已安装 Skill。
- 读取 `SKILL.md` 原文。
- 卸载 Skill 并清理目录。
- 默认生产配置拒绝非 GitHub 来源。
- anbeime 目录无未分类项目、无重复 id，Skill 与 SkillSet 类型合法。
- 市场主说明均为可读中文，同时保留上游原文用于搜索与追溯。
- 所有项目都归入 10 个稳定分类和 4 个安装状态之一。
- VoltAgent 索引只为明确子路径或已完成批次审计的条目生成 `installSource`。
- SkillSet 分类只依赖固定提交中的真实 `SKILL.md` 层级；组合包根目录必须存在 `SKILL.md`，成员集合不得具有整体安装源。
- 全局成员注册表不存在重复安装映射或跨仓库成员，重叠集合通过同一成员 ID 同步已安装状态。

手动验收：

1. 启动后端和前端。
2. 打开 `/skills`。
3. 安装 PDF 或 XLSX Skill。
4. 切换到 `已安装`，确认可见。
5. 打开任意 `/chat/:modelId`，在 Skill 下拉框选择刚安装的 Skill。
6. 发送“你能做什么？”，观察回复是否体现该 Skill 的能力。
