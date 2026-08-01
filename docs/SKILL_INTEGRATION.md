# Skill 扩展包系统集成说明

Skill 是模镜为 AI 打工人准备的“岗位手册”。每个 Skill 是一个包含 `SKILL.md` 的目录，可选携带脚本、模板、参考资料等资源。模镜后端负责安装、卸载和读取 Skill，前端负责在技能市场展示、管理已安装 Skill，并在面试间把选中的 `SKILL.md` 注入为系统提示词。

最后更新日期：2026-06-16  
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

## 2. 如何添加新的 Skill 到市场

市场数据位于：

```text
client/src/data/skillProjects.ts
```

新增条目时，优先填写 `installSource`：

```ts
{
  id: "my-pdf-skill",
  name: "PDF 文档处理技能",
  repoName: "owner/repo",
  repoUrl: "https://github.com/owner/repo",
  category: "文档处理",
  description: "一句话说明这个 Skill 能帮用户完成什么。",
  readmeSummary: "README 摘要。",
  stars: 1200,
  language: "Markdown / Python",
  updatedAt: "2026-06-16",
  installCommand: "git sparse-checkout 示例命令",
  installNote: "安装说明。",
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
- 没有 `installSource` 的条目会显示为“仅作参考”，不能一键安装。

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
  -d '{"repo_url":"https://github.com/anthropics/skills","sub_path":"skills/pdf"}'
```

后端会执行：

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/anthropics/skills <tmp>
git -C <tmp> sparse-checkout set skills/pdf
```

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

`/skills` 页面分为两个标签：

- `技能市场`：展示 `skillProjects.ts` 中的 Skill 卡片，提供安装按钮。
- `已安装`：调用 `/api/skills/installed`，展示本地已安装 Skill，提供卸载按钮。

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

## 5. 测试指南

后端测试不依赖外网，会在临时目录创建本地 git 仓库作为 mock Skill 源：

```bash
python -m pytest server/tests/test_skill_integration.py -q
```

覆盖范围：

- 安装本地 mock Skill。
- 列出已安装 Skill。
- 读取 `SKILL.md` 原文。
- 卸载 Skill 并清理目录。
- 默认生产配置拒绝非 GitHub 来源。

手动验收：

1. 启动后端和前端。
2. 打开 `/skills`。
3. 安装 PDF 或 XLSX Skill。
4. 切换到 `已安装`，确认可见。
5. 打开任意 `/chat/:modelId`，在 Skill 下拉框选择刚安装的 Skill。
6. 发送“你能做什么？”，观察回复是否体现该 Skill 的能力。

