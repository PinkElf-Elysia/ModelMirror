# Xpert 与 Skill 自编写

## 目标

自编写能力让私有 `workflow_agent` 分析已有资源并提出 Xpert 或 Skill 变更，但它不是自动发布器。完整链路固定为：

1. Agent 通过 Runtime Toolset 读取允许范围内的资源摘要或草稿。
2. Agent 创建版本化 `AuthoringProposal`。
3. 人工在 Xpert Studio 或 Skills 页面查看正文、编辑并校验。
4. 人工批准后只创建或更新草稿。
5. 用户另行执行 Xpert 发布或 Workspace Skill 安装。

公开 Xpert App/API 不允许部署 `xpert_authoring` 或 `skill_creator`。

## 中间件

`xpert_authoring` 和 `skill_creator` 仅可绑定到 `workflow_agent`，并要求 `toolMode=mcp_tools`。两者通过既有 Tool Permission Policy、HITL、audit、LLM 工具选择器和 checkpoint 管线执行。

`xpert_authoring` 提供资源目录、草稿读取、创建/更新提案和提案校验工具；`skill_creator` 提供对应的 Skill 工具。配置中的 `allowed_xpert_ids` 与 `allowed_draft_ids` 是运行时访问边界；来源 Xpert 只能额外读取自己的草稿。

每个 run 最多创建 5 条提案，每个来源最多保留 20 条 pending 提案。工具不提供 publish、install、delete 或 archive 动作。

## 提案状态与并发

提案类型为 `xpert_create`、`xpert_update`、`skill_create`、`skill_update`，状态机为：

```text
pending -> approved / rejected / cancelled / conflict
```

所有编辑和动作必须携带当前 `revision`。更新提案还固定目标草稿的 `base_revision`；审批时若人工已修改目标草稿，提案进入 `conflict`，不会覆盖人工修改。

提案持久化在 Runtime storage 的 `authoring_proposals.json`。列表 API 默认不返回 payload；完整正文仅由可信管理详情接口返回。audit 与 checkpoint 只记录 ID、类型、大小、状态和截断错误摘要。

## Workspace Skill 草稿

批准 Skill 提案会创建 Workspace Skill 草稿，不会进入已安装 Skill 目录。草稿支持必需的 `SKILL.md`、`scripts/`、`references/`、`assets/` 和唯一允许的 Agent 配置文件 `agents/openai.yaml`。

安全限制为最多 40 个文件、单文件 1MB、整个包 5MB。绝对路径、`..`、隐藏路径、`.git`、symlink 和其他 `agents/` 文件均被拒绝。用户在 `/skills` 显式安装后，包会以新的稳定 Skill ID 安全复制到现有 Runtime；已经安装的包不会被原地覆盖，脚本仍只能在离线 Sandbox 中执行。

## API

提案管理：

- `GET /api/runtime/authoring-proposals`
- `GET/PATCH /api/runtime/authoring-proposals/{proposal_id}`
- `POST /api/runtime/authoring-proposals/{proposal_id}/validate`
- `POST /api/runtime/authoring-proposals/{proposal_id}/approve`
- `POST /api/runtime/authoring-proposals/{proposal_id}/reject`
- `POST /api/runtime/authoring-proposals/{proposal_id}/cancel`

Skill 草稿：

- `GET /api/skills/drafts`
- `GET/PATCH /api/skills/drafts/{draft_id}`
- `POST /api/skills/drafts/{draft_id}/validate`
- `POST /api/skills/drafts/{draft_id}/install`
- `POST /api/skills/drafts/{draft_id}/archive`

## 验收护栏

- 校验和拒绝提案不得改变 Xpert 或 Skill 资源。
- 批准 Xpert 提案后 `published_version` 必须保持不变。
- 批准 Skill 提案后 `installed_skill_id` 必须为空。
- stale proposal 或 stale target revision 必须拒绝或进入 conflict。
- App deployment preflight 必须通过中间件 registry 契约阻断两类能力。
- Runtime storage、Skill 草稿内容、`.env`、密钥和构建产物不得提交。

Xpert AGPL 实现仅用于领域行为参考，本模块使用 ModelMirror 原生 Workflow JSON、Store 和 Runtime Toolset 独立实现。
