# 第三轮 Agent/Workflow 教程：起草记录与待验证边界

- 记录日期：`2026-08-29`
- 仓库：`E:\ModelMirror\ModelMirror-new`（当前 `feature/help-center-round2`）
- 内容：`create-repeatable-agent`、`build-first-workflow` 两篇首次成功教程
- 状态：**起草完成，但尚未做隔离预览重放。按审查要求，两篇文章已从公开注册表（`index.ts`）移除，草稿文件保留待将来完成真实预览验证后重新加入。以下"源码核对"不等于"真实预览已验证"。**

## 起草依据（源码核对）

两篇教程的界面名称与流程均从当前仓库源码核对，**未**在隔离预览中逐屏重放。核对文件：

- Agent 创建链路：`client/src/pages/XpertCreatePage.tsx`、`client/src/pages/XpertStudioPage.tsx`、`client/src/pages/XpertStudioIndexPage.tsx`
- 工作流链路：`client/src/components/workflow/WorkflowEditor.tsx`、`client/src/components/workflow/WorkflowRun.tsx`、`client/src/pages/WorkflowClassicPage.tsx`

从源码确认的关键事实：

| 事实 | 依据 |
| --- | --- |
| `/agents/studio` 列表右上角「＋ 创建智能体」→ `/agents/studio/new` | `XpertStudioIndexPage.tsx:181-183` |
| 创建表单字段：名称*、Slug、说明、标签、开场问题；提交按钮「创建并进入 Studio」 | `XpertCreatePage.tsx` |
| Studio 操作：保存信息 / 发布预检 / 发布 vN / 打开聊天 / 归档 | `XpertStudioPage.tsx` 按钮区 |
| 发布预检通过提示形如「预检通过 · N 节点」 | `XpertStudioPage.tsx:436` |
| 经典工作流首次打开为默认模板「输入→AI 智能体→输出」，标题「新建 AI 流水线」 | `WorkflowEditor.tsx:1367-1408` |
| 工具栏「节点库」「保存草稿」；运行面板标题「流水线试运行」、按钮「运行工作流」 | `WorkflowEditor.tsx:7783`、`WorkflowRun.tsx:1929/2249` |
| `/workflow` → `WorkflowClassicPage` → `WorkflowEditor(workflowId="draft")` | `WorkflowClassicPage.tsx` |

## 已改动文件

- `client/src/content/help-center/articles/create-repeatable-agent.md`（新增）
- `client/src/content/help-center/articles/build-first-workflow.md`（新增）
- `client/src/content/help-center/index.ts`（注册两篇文章、新增 `pendingPreviewBaseline`、调整 `choose-model-agent-workflow` 的 nextSlug）
- `client/src/content/help-center/helpContent.test.ts`（文章清单、PENDING 占位允许、基线归属豁免）
- `client/scripts/verify-help-images.mjs`（PENDING 占位按警告处理）
- `docs/help-center/ROADMAP.md`（第三轮进度标注）

## 验证结果

- 帮助中心专项测试：`30/30` 通过（`HelpCenterPage.test.tsx` + `helpContent.test.ts`）。
- 类型检查：`npm run typecheck` 通过，无 TypeScript 诊断。
- `verify-help-images.mjs`：两篇新文章仅产生 PENDING 警告（待补真实截图），不影响通过；历史遗留 9 处资产问题（JPEG 伪装 PNG、超限、孤儿图）仍为失败项，属 P0-1 工程化范围，本轮不处理。

## 待验证边界（合入前必须完成）

1. **隔离预览重放**：以非技术用户身份在最新 `origin/main` 隔离预览中，分别走一遍「创建 Agent 到发布 v1」和「从默认模板搭建工作流到试运行」两条路径。
2. **真实截图**：按 README 截图规则，在真实预览中生成并替换 `/help-center/PENDING/` 占位（每张宽 750–1000px、≤250KB、真 PNG、有效 alt）。
3. **verifiedCommit/Date 回填**：把 `PENDING` 替换为真实预览基线提交与日期，去掉 `pendingPreviewBaseline` 或改回正式基线。
4. **核对界面文案**：源码核对可能与实际渲染有差异（尤其工作流节点右侧配置面板的具体字段名、按钮在窄屏下的布局）。重放时以实际所见为准修正步骤。
5. **费用与数据边界复核**：确认「发布本身不产生调用费用」等表述与当前实现一致。

## 未验证

- 真实模型调用、发布后聊天、工作流试运行的实际模型行为。
- 工作流中除默认模板外的节点（外部 HTTP、RAG、数据表、审批）的完整操作。
- 移动端视口下 Agent Studio 与 Workflow 画布的布局与操作。
