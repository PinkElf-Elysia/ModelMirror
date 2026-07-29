# 术语表

最后更新日期：2026-07-28
维护人：模镜团队

| 中文名称 | 英文 / 缩写 | 解释 | 在项目中的使用场景 |
| --- | --- | --- | --- |
| 模镜 | ModelMirror | AI 资源发现与协作平台。 | 页面标题、README、后端应用名。 |
| 模型招聘会 | Models Job Fair | 模型浏览页的主题化表达。 | `/models`。 |
| 面试间 | Chat Room | 与模型或智能体对话的页面。 | `/chat/:modelId`。 |
| 智能体 | Agent | 带有人设、专长和系统提示词的 AI 角色。 | `/agents`、专家团、自动路由。 |
| Agent Studio | Agent Studio | 创建智能体草稿、发布不可变版本并运行的工作区。 | `/agents/studio`。 |
| Xpert（内部兼容名） | Xpert | 历史内部类型、API、Store 和路由标识；不再用于普通用户文案。 | `XpertVersion`、`server/xperts/`、`/agents/xpert/...`。 |
| MCP | Model Context Protocol | 让模型连接外部工具和上下文的协议。 | `/mcps`、MCP 工具注册表、workflow 工具节点。 |
| Skill | Agent Skill | 可复用的智能体能力包或技能说明。 | `/skills`、聊天 Skill 注入。 |
| RAG | Retrieval-Augmented Generation | 检索增强生成，把资料库片段作为上下文提供给模型。 | `/rag`、聊天知识库引用。 |
| SSE | Server-Sent Events | 服务端向浏览器持续推送文本事件的协议。 | `/api/chat`、工作流运行、专家团。 |
| newAPI | newAPI | OpenAI 兼容模型网关，用于统一管理模型渠道和 API Key。 | Docker Compose、`/settings`、`LLM_GATEWAY_URL`。 |
| OpenRouter | OpenRouter | OpenAI 兼容模型网关。 | 未配置 newAPI 时的回退模型调用。 |
| Dify compatibility | Legacy Dify Adapter | 保留的历史代理与旧 iframe 组件，不是 Workflow/RAG 主路径。 | `/api/dify/*`，仅显式兼容场景。 |
| 图片生成模型 | Image Generation Model | 输入文本或图片，输出图片 URL 或 data URI 的模型。 | `recraft/recraft-v3`、Flux、SDXL 等。 |
| Image URL Part | `image_url` | OpenAI 兼容多模态内容中的图片字段。 | 用户上传图片输入、模型图片输出。 |
| `delta.images` | Streaming Image Field | 部分网关在 SSE chunk 中返回图片数组的字段。 | 图片生成模型输出接收。 |
| Data URL | `data:image/...` | 把图片二进制内联为字符串 URL。 | 图片生成模型返回 base64 图片时使用。 |
| Lightbox | Lightbox | 点击图片后全屏预览的交互。 | ChatPage 图片预览和下载。 |
| Markdown 图片 | Markdown Image | `![alt](url)` 形式的图片文本。 | 后端/前端把图片输出规范化为可渲染文本。 |
| 多模态 | Multimodal | 文本、图片、音频、视频等输入或输出能力。 | 模型筛选、聊天输入、图片生成输出。 |
| Operation | Model Operation | UI 已适配的交互任务，如 chat、transcribe、synthesize_speech、analyze_video。 | 模型 CTA 与 ChatPage 自适应工作区。 |
| Interaction Status | Interaction Status | `ready/planned/unsupported`，表示当前 UI 是否能完成该任务。 | 与 `invocable` 分开判断。 |
| Vision 格式 | OpenAI Vision Content | `content` 为 `text` / `image_url` 数组的消息格式。 | `/api/chat` 多模态请求。 |
| 工作流 | Workflow | 多节点编排的 AI 流程。 | `/workflow`、`/workflow-native`。 |
| 经典画布 | Classic Canvas | React Flow 自研工作流编辑器。 | `/workflow`、`/workflow/classic`。 |
| workflow-native | workflow-native | 自研工作流实验线，提供静态校验和增量节点验证。 | `/workflow-native`、`/api/workflow-native/validate`。 |
| 工作流校验 | validate | 执行前检查节点、连线、变量引用和拓扑顺序。 | workflow-native 护栏。 |
| XpertVersion | Immutable Agent Version | 发布后不可变的智能体工作流快照；字段名因兼容保留。 | Agent Studio、Handoff、Goal、App 部署。 |
| Deployment Revision | Deployment Revision | Agent App 每次部署或回滚产生的递增部署记录。 | Agent App 管理与审计。 |
| 未列出 App | Unlisted App | 只有持有分享 token 的用户可访问的 Agent App。 | `/apps/:appSlug`。 |
| Candidate Index | Candidate Index | Knowledge Pipeline 执行后、尚未激活的隔离索引版本。 | RAG 预览、激活和回滚。 |
| Fusion | Model Fusion | 多模型并行回答后由裁判模型综合。 | 专家团。 |
| AI Team | AI Team | 多智能体串行或协作处理任务。 | 专家团。 |
| 自动路由 | Auto Routing | 根据用户需求匹配最合适的智能体。 | `/api/route-agent`。 |
| 智能调度 | Model Routing | 在满足权限、能力、预算和健康约束后，为 `auto/*` 选择实际回答模型。 | `/chat/auto`、`server/model_router/`。 |
| 上下文优化 | Context Optimization | 在保护系统提示、最新问题和结构化内容的前提下，减少重复的历史、RAG 与工具内容。 | `server/context_engine/`、聊天路由回执。 |
| 本地试运行 | Native Canary | 按稳定会话哈希逐步启用原生调度，支持随时回到稳定模式。 | 设置页智能调度。 |
| Prompt Engineering | Prompt Engineering | 设计高质量模型输入的方法。 | 提示词助手、超级提示词模式。 |
| localStorage | localStorage | 浏览器本地键值存储。 | 偏好模型、高级参数、工作流草稿。 |
| FastAPI | FastAPI | Python Web 框架。 | 后端 API 服务。 |
| Pydantic | Pydantic | Python 数据校验库。 | 后端请求体校验。 |
| Vite | Vite | 前端开发与构建工具。 | `client/`。 |
| React Flow | React Flow / XYFlow | 节点画布库。 | `/workflow`。 |
