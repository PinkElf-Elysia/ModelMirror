# 5 分钟快速上手

最后更新日期：2026-07-28
维护人：模镜团队

## 环境要求

- Node.js 22，建议使用项目已验证的当前 22.x 版本。
- Python 3.11+；Docker 服务镜像使用 Python 3.12。
- Docker Desktop / Docker Compose：推荐，用于运行完整本地栈。
- Git。

Dify 不是当前启动依赖。`/workflow` 和 `/rag` 均由 ModelMirror 原生实现提供。

## 推荐：Docker Compose

复制后端配置：

```powershell
Copy-Item server/.env.example server/.env
```

至少配置一种模型访问方式。推荐先启动 newAPI，在
`http://localhost:3000` 创建本地渠道和 ModelMirror 专用 Key，然后写入：

```bash
LLM_GATEWAY_KEY=your-new-api-key
```

Docker Compose 会把 `LLM_GATEWAY_URL` 指向容器内
`http://new-api:3000/v1/chat/completions`。也可配置 OpenRouter 回退：

```bash
OPENROUTER_API_KEY=your-openrouter-key
```

STT、TTS 和视频能力依赖 OpenRouter。视频入口默认关闭，完成人工费用验收后
在 `server/.env` 启用：

```bash
MULTIMODAL_VIDEO_ANALYSIS_ENABLED=true
MULTIMODAL_VIDEO_GENERATION_ENABLED=true
```

启动：

```bash
docker compose -p modelmirror up -d --build
```

检查：

```bash
docker compose -p modelmirror ps
curl http://localhost:8000/api/health
curl http://localhost:5173/models
```

核心服务为 `client`、`server`、`new-api`、`browser` 和 `sandbox`。
`omniroute` 与 `office-host` 使用可选 profile，不属于默认启动前提。

## 可选：本地热更新

安装后端：

```bash
cd server
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

本地运行后端时，若使用 newAPI：

```bash
LLM_GATEWAY_URL=http://localhost:3000/v1/chat/completions
LLM_GATEWAY_KEY=your-new-api-key
```

安装并启动前端：

```bash
cd client
npm.cmd install
npm.cmd run dev -- --host 0.0.0.0 --port 5173
```

## 页面验收

| 页面 | 地址 | 期望 |
| --- | --- | --- |
| 模型招聘会 | `http://localhost:5173/models` | 目录、筛选和真实可用状态正常。 |
| 普通聊天 | `http://localhost:5173/chat/openai%2Fgpt-4o-mini` | 正常流式返回。 |
| 智能调度 | `http://localhost:5173/chat/auto` | 展示路由策略与回执。 |
| AI 人才市场 | `http://localhost:5173/agents` | 可进入专家面试并退出专家模式。 |
| Agent Studio | `http://localhost:5173/agents/studio` | 可查看或创建智能体草稿。 |
| 工作流 | `http://localhost:5173/workflow` | classic React Flow 画布可用。 |
| RAG | `http://localhost:5173/rag` | 本地知识库与流水线状态可用。 |
| Data X | `http://localhost:5173/datax` | 项目入口可用。 |
| 设置 | `http://localhost:5173/settings` | newAPI 控制台与脱敏状态卡可见。 |

多模态验收需从模型招聘会选择对应 `operation`，不要把 STT、TTS 或视频生成
模型当作普通文本模型调用。

## 提交前检查

```bash
cd client
npm.cmd run build
```

```bash
python -m py_compile server/main.py
python -m pytest server/tests/ -q
```

```bash
docker compose -p modelmirror up -d --build --force-recreate
curl http://localhost:8000/api/health
curl http://localhost:5173/models
```
