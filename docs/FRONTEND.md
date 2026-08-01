# 前端架构与开发指南

最后更新日期：2026-07-30
维护人：模镜团队

## 技术栈

- React 19
- TypeScript
- Tailwind CSS
- Vite
- React Router
- ReactMarkdown + remark-gfm
- @xyflow/react

## 目录结构

```text
client/
├── src/
│   ├── App.tsx                  # 路由配置
│   ├── main.tsx                 # React 入口
│   ├── components/              # 通用组件
│   ├── components/xpert/        # Agent Studio 内部兼容组件
│   ├── components/workflow/     # 经典自研工作流画布
│   ├── context/                 # React Context
│   ├── data/                    # 静态资源数据
│   ├── pages/                   # 路由页面
│   ├── theme/                   # 主题与资源导航
│   ├── types/                   # TypeScript 类型
│   └── utils/                   # SSE、图片处理、压缩等工具
├── tailwind.config.js
└── vite.config.ts
```

## 路由

| 路径 | 页面组件 | 说明 |
| --- | --- | --- |
| `/` | `Navigate` | 重定向到 `/models`。 |
| `/models` | `ModelListPage` | 模型招聘会。 |
| `/studio` | `StudioHomePage` | 组织工作空间与运行总览。 |
| `/agents` | `AgentsPage` | AI 人才市场。 |
| `/agents/meta-agent` | `MetaAgentPage` | 元智能体任务工作台。 |
| `/agents/studio` | `XpertStudioIndexPage` | Agent Studio 列表。 |
| `/agents/studio/:xpertId` | `XpertStudioPage` | 智能体草稿、版本与发布。 |
| `/agents/xpert/:xpertId/chat` | `XpertChatPage` | 已发布智能体运行页。 |
| `/agents/goals` | `ConversationGoalsPage` | 长期 Goal。 |
| `/agents/automations` | `AutomationsPage` | 智能体自动化。 |
| `/expert-team` | `ExpertTeamPage` | 专家团。 |
| `/mcps` | `McpBrowserPage` | MCP 工具。 |
| `/toolsets` | `ToolsetsPage` | Toolset 管理。 |
| `/skills` | `SkillBrowserPage` | Skill 技能。 |
| `/prompts` | `PromptProfilesPage` | 提示词 Profile 和版本。 |
| `/plugins` | `PluginsPage` | 声明式 Plugin。 |
| `/chat/:modelId` | `ChatPage` | 普通聊天或按 operation 自适应多模态工作区。 |
| `/workflow` | `WorkflowClassicPage` | 经典自研 React Flow 工作流。 |
| `/workflow/:id` | `WorkflowClassicPage` | 工作流草稿入口。 |
| `/workflow/classic` | `WorkflowClassicPage` | 兼容旧入口。 |
| `/workflow-native` | `WorkflowNativePage` | workflow-native 实验入口。 |
| `/rag` | `RagPage` | 本地 RAG 资料库。 |
| `/rag/:kbId/pipeline` | `KnowledgePipelineCanvasPage` | 知识流水线。 |
| `/rag/:kbId/evaluation` | `KnowledgeEvaluationPage` | 检索评测。 |
| `/datax` | `DataXHomePage` | Data X 项目。 |
| `/runtime` | `RuntimeOpsPage` | 运行诊断。 |
| `/settings` | `SystemSettingsPage` | newAPI 控制台 iframe。 |

`Xpert*` 仍是内部组件、类型和兼容 API 名称。面向用户的标题、按钮和帮助文案
统一使用“智能体”“Agent Studio”“Agent App”，不要仅为改名破坏已持久化 ID
或路由。

## ChatPage 自适应入口

`ChatPage` 先根据查询参数和模型 `operations` 选择工作区：

- `chat`：现有文本和图片聊天。
- `transcribe`：音频文件转录。
- `synthesize_speech`：按已验证模型与声线生成语音。
- `generate_audio`：独立音乐生成任务、播放器与临时下载。
- `realtime_voice`：纯语音 WebRTC 工作区，不进入普通消息 SSE。
- `analyze_video`：本地视频或 HTTPS URL 一次性分析。
- `generate_video`：独立异步视频任务，不进入 Chat SSE。

模型被目录收录或 `invocable=true` 不代表当前 UI 已适配。CTA 必须同时检查
operation 和 `interaction_status`。

`/models` 保留原 493 个 OpenRouter 快照模型，并额外展示
`gpt-realtime-2.1-mini` 和 `gpt-realtime-2.1` 两个直接 OpenAI 档案。即使当前
没有可用连接，卡片仍提供“配置实时语音”入口；连接、功能开关和能力档案同时
满足后才显示“开始实时语音”。

Chat 的音频上传与麦克风共用转写设置，默认先转文字并允许用户编辑。单轮录音
按钮与“实时语音”入口必须保持可辨识：前者录完再提交，后者创建最长 10 分钟的
持续 WebRTC 会话。实时语音不会自动组合 RAG、Skill、MCP 或 `/chat/auto`。

## 聊天图片输出链路

图片生成模型的输出路径由以下文件协作完成：

| 文件 | 职责 |
| --- | --- |
| `utils/fetchChatStream.ts` | 读取 SSE，解析 `content`、`delta.images`、`message.images`，把 `image_url.url` 转成 `![图片](URL)`。 |
| `utils/extractImages.ts` | 从消息文本中提取 markdown 图片、内联 SVG、data URL、裸图片 URL，生成图片卡片数据。 |
| `pages/ChatPage.tsx` | 渲染用户上传图片和模型输出图片；点击图片进入 Lightbox，支持保存原图和 SVG 转 PNG。 |

开发约束：

- 不改变 `onDelta(text: string)` 签名。
- 纯文本流使用节流/合帧更新，`message_end` 必须立即 flush，不能截断尾部。
- 不破坏用户上传图片的 `message.images` 展示。
- `data:image/...` 必须可显示为图片卡片，不能只留在 Markdown 文本里。
- Lightbox 统一处理上传图、模型输出 URL、data URL 和 SVG。

## 验证

```bash
cd client
npm.cmd run build
```

手动验收：

1. 打开 `/chat/recraft%2Frecraft-v3`。
2. 输入“画一只猫”。
3. Assistant 消息中应出现至少一张图片卡片。
4. 点击图片应进入 Lightbox。
5. 纯文本模型仍应逐字/逐段流式显示文本。
6. 从 AI 人才市场进入专家面试后，可以通过“退出专家模式”恢复普通模型聊天。

## 开发规范

- 组件文件使用 PascalCase。
- 类型尽量靠近数据源或放入 `types/`。
- 不新增 UI 组件库，优先使用 Tailwind 和现有组件。
- 中文文案必须以 UTF-8 写入，避免 PowerShell 编码导致 mojibake。
- 不在前端保存 API Key。
