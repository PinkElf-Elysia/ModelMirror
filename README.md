# 模镜 ModelMirror

模镜是一个 AI 资源浏览与协作平台，面向模型、智能体、MCP、Skill、提示词、工作流、RAG 和聊天场景。项目主题是“AI 牛马招聘会”：把模型与智能体看成候选人，把工具和能力看成岗位技能，让用户可以快速发现、比较、调用和组合 AI 能力。

最后更新日期：2026-07-27
维护人：模镜团队

## 当前能力

- 模型招聘会：模型筛选、价格展示、能力标签和聊天入口。
- 智能调度：`/chat/auto` 支持六种策略、稳定会话灰度、健康熔断、预算回执和上下文优化；默认仍可回退 OmniRoute 侧车。
- 面试间：OpenAI 兼容流式聊天、图片输入、高级参数、提示词助手、模型输出图片预览。
- 图片生成模型：支持 `content` 多模态 parts、`delta.images` / `message.images`、`image_url` 和 `data:image/...` 输出；前端会转成图片卡片并接入 Lightbox 放大与下载。
- AI 人才市场：智能体角色浏览、面试入口、专家团能力。
- MCP 工具：原生 stdio MCP 客户端、多会话管理和工具注册表。
- Skill：Skill 安装、管理和聊天注入。
- 工作流：`/workflow` 使用经典自研 React Flow 画布；`/workflow-native` 保留实验线。
- RAG：`/rag` 使用本地 RAG 资料库，支持文档上传、切分、向量检索和聊天引用。
- Data X：`/datax` 支持 CSV/XLSX/Parquet 快照、语义模型、版本化指标、受限分析和指标提案审批。
- Xpert Studio：创建草稿、发布不可变版本，并通过 Goal、Handoff、文件、记忆与 Knowledge Pipeline 组合执行。
- Xpert App/API：把已发布版本固定部署为未列出分享 App，并提供带密钥、配额和回滚的 OpenAI 兼容接口。
- newAPI：`/settings` 以内嵌 iframe 接入 newAPI 控制台，后端可优先走 OpenAI 兼容网关。

## 技术栈

- 前端：React + TypeScript + Tailwind CSS + Vite + React Router + ReactMarkdown + @xyflow/react。
- 后端：FastAPI + Pydantic + httpx + ChromaDB + DuckDB + MCP Python SDK。
- 本地部署：Docker Compose，包含 `client`、`server`、`new-api` 服务。

## 快速启动

复制环境变量示例并填写密钥：

```bash
copy .env.example server\.env
```

推荐通过 newAPI 管理模型渠道：

```bash
LLM_GATEWAY_URL=http://localhost:3000/v1/chat/completions
LLM_GATEWAY_KEY=your-new-api-key
```

也可以回退到 OpenRouter：

```bash
OPENROUTER_API_KEY=your-openrouter-key
```

启动 Docker Compose：

```bash
docker compose -p modelmirror up -d --build
```

常用入口：

```text
http://localhost:5173/models
http://localhost:5173/chat/recraft%2Frecraft-v3
http://localhost:5173/workflow
http://localhost:5173/agents/studio
http://localhost:5173/agents/goals
http://localhost:5173/rag
http://localhost:5173/datax
http://localhost:5173/settings
http://localhost:3000
```

## 本地开发

后端：

```bash
cd server
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

前端：

```bash
cd client
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

## 验证命令

```bash
cd client
npm.cmd run build
```

```bash
python -m py_compile server/main.py
python -m pytest server/tests/ -q
```

图片生成模型的手动冒烟：

```bash
curl -N -X POST http://localhost:8000/api/chat ^
  -H "Content-Type: application/json" ^
  -d "{\"model_id\":\"recraft/recraft-v3\",\"messages\":[{\"role\":\"user\",\"content\":\"画一只猫\"}]}"
```

预期：SSE 中出现 `image_url` 或 `data:image/...`，前端 `/chat/<modelId>` 中显示至少一张可点击图片。

## 文档

项目文档入口见 [docs/README.md](docs/README.md)。原生调度的状态、门禁和回退见
[docs/MODEL_ROUTER_NATIVE.md](docs/MODEL_ROUTER_NATIVE.md)。开发前请先阅读
[AGENTS.md](AGENTS.md) 和 [docs/HARNESS_ENGINEERING.md](docs/HARNESS_ENGINEERING.md)。
