# 后端架构与 API 文档

最后更新日期：2026-07-28
维护人：模镜团队

## 技术栈

- Python 3.11+；Docker 镜像使用 Python 3.12。
- FastAPI、Pydantic、httpx、Uvicorn。
- SQLite、ChromaDB、SQLite FTS5、DuckDB。
- MCP Python SDK。

## 代码边界

```text
server/
├── main.py              # 应用装配和 legacy 入口；不要继续堆积领域算法
├── api/                 # 独立 FastAPI router
├── model_router/        # 连接、目录、策略、熔断、预算、决策与 SQLite repository
├── context_engine/      # 上下文估算、压缩、保真与回退
├── multimodal/          # STT、TTS、视频理解、视频目录与异步任务
├── rag/                 # 本地知识库、流水线、索引与评测
├── workflow_native/     # classic/shared schema、validate 与实验线
├── xperts/              # Agent Studio 内部存储与发布契约
├── xpert_runtime/       # Goal、Handoff、Middleware、审计与恢复
├── mcp/                 # MCP stdio 客户端
├── toolsets/            # Toolset 与凭据
├── datax/               # Data X / DuckDB
└── tests/               # pytest
```

内部包名 `xperts` / `xpert_runtime` 为持久化兼容契约；用户界面显示“智能体”。

## 模型服务优先级

1. `LLM_GATEWAY_URL` + `LLM_GATEWAY_KEY`：newAPI 或其他 OpenAI-compatible。
2. `OPENROUTER_API_KEY`：兼容回退与首期多模态。
3. `/chat/auto` 再根据 `MODEL_ROUTER_ENGINE` 进入 sidecar、shadow、
   native canary 或 native。

默认网关与 auto 调度是两条独立稳定路径。auto 失败不能静默改走普通网关。

## 关键环境变量

| 变量 | 说明 |
| --- | --- |
| `LLM_GATEWAY_URL` / `LLM_GATEWAY_KEY` | 默认 OpenAI-compatible 服务。 |
| `OPENROUTER_API_KEY` | OpenRouter 回退及多模态能力。 |
| `MODEL_ROUTER_ENGINE` | `sidecar`、`shadow`、`native_canary` 或 `native` 运维覆盖。 |
| `MODEL_ROUTER_CANARY_PERCENT` | 稳定会话灰度百分比。 |
| `MODEL_ROUTER_TENANT_ID` | 当前为 `local`。 |
| `OMNIROUTE_ENABLED` / `OMNIROUTE_API_KEY` | 可选侧车兼容。 |
| `MULTIMODAL_VIDEO_ANALYSIS_ENABLED` | 视频理解入口，默认关闭。 |
| `MULTIMODAL_VIDEO_GENERATION_ENABLED` | 视频生成入口，默认关闭。 |
| `MULTIMODAL_CHAT_AUDIO_ENABLED` | Chat 音频附件与直接理解，默认关闭。 |
| `MULTIMODAL_MICROPHONE_ENABLED` | 录音完成后提交，默认关闭。 |
| `MULTIMODAL_STREAMING_AUDIO_ENABLED` | 已验证模型的原生流式语音输出，默认关闭。 |
| `MULTIMODAL_CHAT_VIDEO_ENABLED` | Chat 本地视频附件，默认关闭。 |
| `RAG_STORAGE_DIR` / `RAG_UPLOAD_DIR` | RAG 持久化位置。 |
| `CHROMA_DB_PATH` | Chroma 目录。 |
| `XPERT_STORAGE_DIR` / `AGENT_TASK_STORAGE_DIR` | Agent Studio 与 Runtime 兼容存储路径。 |

完整示例以 `server/.env.example` 为准。不得把服务端变量复制到 `VITE_*`。

## 公共 API 分组

### 健康与目录

- `GET /api/health`
- `GET /api/models/catalog`
- `GET /api/models/router-status`

### 聊天与智能调度

- `POST /api/chat`：OpenAI-compatible SSE；支持文本、图片、route receipt 和
  compression receipt。
- 默认聊天继续使用网关路径；`gateway="auto"` 由路由 engine 决定执行路径。
- 空流、零 Token、只有 `[DONE]` 或上游正文中断必须计为失败，不能生成成功回执。

### 多模态

- `POST /api/multimodal/transcriptions`
- `POST /api/multimodal/speech`
- `POST /api/multimodal/chat/attachments`
- `DELETE /api/multimodal/chat/attachments/{attachment_id}`
- `GET /api/multimodal/audio/models`
- `GET /api/multimodal/video/models`，`refresh=true` 强制重新确认实时能力。
- `POST /api/multimodal/video/analysis`
- `/api/multimodal/video/jobs*`：提交、列表、刷新、内容代理和删除本地记录。

STT/TTS 与视频完整契约见
[MULTIMODAL_FORMAT_AUDIT.md](./MULTIMODAL_FORMAT_AUDIT.md)。音视频附件可进入
Chat，但视频生成仍使用独立异步任务，不复用 `/api/chat` SSE。

### 工作流、RAG 与 Agent

- `/api/workflow/run`：classic 工作流执行。
- `/api/workflow-native/validate`：实验图静态校验，不调用模型或外部服务。
- `/api/rag/*`：本地知识库、流水线、检索、评测和 Inbox。
- `/api/xperts/*`、`/api/agent-*`、`/api/runtime/*`：内部兼容命名的
  Agent Studio 和 Runtime 契约。

### Legacy Dify compatibility

`server/api/dify_proxy.py` 仍挂载 `/api/dify/*`，但没有主前端路由调用，也不是
Compose 健康依赖。它只用于显式配置的历史兼容，不得写成 Workflow/RAG 主路径。

## SSE 约束

- 保持 OpenAI `choices[0].delta` / `choices[0].message` 与 `[DONE]` 兼容。
- 图片可能出现在 content part、`delta.images`、`message.images` 或 data URL。
- 只有明确契约的 ModelMirror 事件可以新增；route receipt 必须在正文结束后
  仅发送一次。
- 客户端取消后应关闭上游响应；未输出正文前才允许路由切换候选。
- 错误响应不得透传密钥、内部 URL、Prompt、媒体正文或上游完整错误体。

## 验证

```bash
python -m py_compile server/main.py
python -m pytest server/tests/ -q
```

高风险聊天、多模态、RAG 或工作流变更还必须运行对应专项测试、前端构建和
Compose 重建。
