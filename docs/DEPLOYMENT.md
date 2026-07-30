# 部署与运维指南

最后更新日期：2026-07-29
维护人：模镜团队

## 支持边界

当前交付目标是本地单租户 Docker Compose。仓库尚未提供完整公网身份、RBAC、
多租户隔离、SLA 或灾备承诺，因此不要直接把管理控制台暴露到公网。

Dify 不是部署依赖。`/workflow` 和 `/rag` 分别由 classic 工作流和本地知识系统
提供。

## Compose 服务

| 服务 | 默认 | 职责 |
| --- | --- | --- |
| `client` | 是 | 前端静态站点，宿主端口 `5173`。 |
| `server` | 是 | FastAPI，宿主端口 `8000`。 |
| `new-api` | 是 | OpenAI-compatible 网关，宿主端口 `3000`。 |
| `browser` | 是 | 受控浏览器 sidecar，不映射宿主端口。 |
| `sandbox` | 是 | 无网络沙箱 sidecar。 |
| `omniroute` | 否 | `omniroute` profile；只绑定 `127.0.0.1:20128`。 |
| `office-host` | 否 | `office` profile；实验性 Office Add-in host。 |
| `coding-runtime` | 否 | `coding` profile；单实例只读代码问答执行面，无宿主端口。 |

启动默认栈：

```bash
docker compose -p modelmirror up -d --build
docker compose -p modelmirror ps
```

重建验收：

```bash
docker compose -p modelmirror up -d --build --force-recreate
curl http://localhost:8000/api/health
curl http://localhost:5173/models
```

如从其他 Git worktree 构建并复用原工作区数据，可在根 `.env` 设置：

```bash
MODELMIRROR_DATA_ROOT=C:\absolute\path\to\stable\data\workspace
```

该变量会改变 bind mount 来源；使用前必须核对绝对路径，避免连接到错误环境。

## 配置与密钥

后端默认读取 `${MODELMIRROR_DATA_ROOT}/server/.env`。最低配置为 newAPI Key
或 OpenRouter Key：

```bash
LLM_GATEWAY_KEY=your-new-api-key
OPENROUTER_API_KEY=your-openrouter-key
```

规则：

- `.env`、API Key、token 和 master key 不得提交。
- 前端环境变量不得保存后端凭据。
- 日志不得记录 Prompt、音视频正文、URL 查询签名或上游完整错误体。
- 视频生成可能产生费用且不保证 ZDR，启用前必须完成人工验收。
- OmniRoute secrets 必须独立生成，不与模型网关 Key 复用。

## 可选 profile

OmniRoute 仅用于兼容、诊断和紧急回退：

```bash
docker compose -p modelmirror --profile omniroute up -d omniroute
```

详细版本、摘要、密钥和回退要求见
[OMNIROUTE_INTEGRATION.md](./OMNIROUTE_INTEGRATION.md)。

Office host：

```bash
docker compose -p modelmirror --profile office up -d office-host
```

Office host 需要独立证书和浏览器/Office 加载项验收，不应因该可选服务异常而
误判默认核心栈不可用。

### 只读代码助手

代码助手默认关闭。配置写入 Compose 读取的根 `.env` 或启动命令环境，不要写入
前端，也不要提交：

```bash
CODING_AGENT_ENABLED=true
CODING_AGENT_MODEL=your-new-api-model-id
CODING_AGENT_GATEWAY_KEY=your-dedicated-gateway-key
```

该 Key 只注入 `coding-runtime`，不注入 FastAPI。启动并重建：

```bash
docker compose -p modelmirror --profile coding up -d --build --force-recreate
docker compose -p modelmirror --profile coding ps
curl http://localhost:8000/api/coding/capabilities
```

`coding-runtime` 仅加入 `internal: true` 网络并通过 Unix socket 连接 FastAPI，
源码挂载为只读，不映射宿主端口。它是实验性本地单实例能力，不应直接暴露到
公网。完整边界和人工验收见
[CODING_AGENT_INTEGRATION.md](./CODING_AGENT_INTEGRATION.md)。

## 反向代理

`/api/chat` 和工作流运行使用 SSE。Nginx 必须关闭代理缓冲：

```nginx
location /api/ {
  proxy_pass http://modelmirror-server:8000;
  proxy_http_version 1.1;
  proxy_buffering off;
  proxy_read_timeout 3600s;
}
```

视频内容代理可能返回较大响应，应设置合理的超时和响应体限制，但不得缓存含
授权语义的上游临时地址。

## 健康与诊断

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/models/router-status
curl http://localhost:8000/api/multimodal/video/models
curl http://localhost:5173/studio
```

最低观测：

- HTTP 状态、耗时、脱敏错误码。
- 模型路由 engine、actual model、request ID、空流和失败切换。
- RAG active version、候选版本和流水线状态。
- 视频任务状态与连续轮询错误；临时网络错误不直接写成任务失败。
- Browser、Sandbox、newAPI 和 server health。
- 启用后检查 Coding capabilities、Worker health、取消清理和源码 Git 状态。

## 备份与恢复

持久化清单见 [DATABASE.md](./DATABASE.md)。升级或恢复前：

1. 记录镜像版本和当前 feature flags。
2. 停止写入或停止 `server`。
3. 备份 bind-mounted 数据及 credential master keys。
4. 重建后验证健康、连接、RAG active version、Agent 发布版本和任务恢复。

## 回退

- 前端：回退镜像或静态产物，不迁移业务数据。
- 后端：回退镜像；schema 变更必须保证旧数据仍可读取。
- 视频生成或分析：将两个 `MULTIMODAL_VIDEO_*_ENABLED` 设为 `false`，保留任务元数据。
- Chat 音视频：分别关闭 `MULTIMODAL_CHAT_AUDIO_ENABLED`、
  `MULTIMODAL_MICROPHONE_ENABLED`、`MULTIMODAL_STREAMING_AUDIO_ENABLED`
  和 `MULTIMODAL_CHAT_VIDEO_ENABLED`；独立 STT、TTS、视频分析及旧视频任务不受影响。
- 智能调度：切回 `MODEL_ROUTER_ENGINE=sidecar` 或 default/newAPI，保留 SQLite。
- OmniRoute：停止 profile，不删除 `omniroute-data`。
- 代码助手：设置 `CODING_AGENT_ENABLED=false`，停止 `coding-runtime`；没有
  持久化会话或数据迁移需要恢复。
- 可选 profile 故障不得通过删除核心数据解决。

legacy `/api/dify/*` 健康只表示兼容代理配置状态，不是平台健康门禁。
