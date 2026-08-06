# Skill 扩展包系统集成说明

Skill 是模镜为 AI 打工人准备的“岗位手册”。每个 Skill 是一个包含 `SKILL.md` 的目录，可选携带脚本、模板、参考资料等资源。模镜后端负责安装、卸载和读取 Skill，前端负责在技能市场展示、管理已安装 Skill，并在面试间把选中的 `SKILL.md` 注入为系统提示词。

最后更新日期：2026-08-06
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

市场展示层将来源细分类收敛为 10 个稳定任务大类，并把晦涩英文说明转换成面向用户的中文能力说明。上游原文保留在 `sourceDescription` 中，用于搜索和追溯。完整治理规则与候选能力结论见 [Skill 体验治理与候选能力审计](./SKILL_EXPERIENCE_AUDIT.md)。三项候选能力没有排期或实施承诺，新增目录和 SkillHub 等外部市场继续延后。

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

`/skills` 页面包含四个标签：

- `技能市场`：合并手工精选与两个生成目录，支持关键词、功能分类、Skill/SkillSet、可安装状态筛选；默认分批渲染 48 项，避免一次挂载全部索引卡片。
- 父级组合包显示“安装技能包”；成员集合显示“成员可安装”和“查看成员”，详情支持本地名称/路径搜索、每页 50 项分页、成员逐项安装，以及按顺序调用现有接口的“一键安装全部成员”。该操作会跳过已安装成员并在首个失败处停止，不使用整仓安装或新增后端批量协议。
- `已安装`：调用 `/api/skills/installed`，展示本地已安装 Skill，提供卸载按钮。
- `工作区草稿`、`待审提案`：保留现有创作工作区；它们不等同于审计中暂缓的上传 Skill 或 skill-creator 引导流程。

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

