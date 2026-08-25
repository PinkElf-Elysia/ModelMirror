# 模镜科研 / ModelMirror AI Research

AR1 是一个可选、同仓但独立构建的 AI/Agent 评测工程框架与操作控制台。它只证明受控运行、Inspect 终态归一化、取消、MLflow 证据持久化、网页复核和重启恢复，不代表模镜已接入模型或具备科研评测能力。

## 明确限制

- 只有 `success`、`task_error`、`long_running_cancel` 三个工程夹具。
- 所有运行固定标记为 `fixture_only` 和 `harness_only`。
- 不调用模型，不读取模镜配置或密钥，不安装 EvalPack，不产生科学分数。
- 不修改或连接模镜主前后端；不会出现在 Studio、Plugin 或默认 Compose 中。
- Research Console、MLflow UI 与 Inspect View 只绑定本机回环地址，不是多用户服务。
- 为兼容 Docker Desktop 的回环端口发布，Control、Tracking 与 Inspect View 使用普通模块私有 bridge，并非出站阻断网络；只有 Worker 固定 `network_mode: none`。这些服务不挂载平台凭据。

## 组件

- `ai-research-control`：版本化 HTTP API、SQLite 控制账本、证据 outbox。
- `ai-research-tracking`：MLflow 3.15.1、SQLite backend、本地 artifacts。
- `ai-research-worker`：Inspect AI 0.3.260、无网 Linux worker、Unix socket 控制协议。
- `ai-research-inspect-view`：复用 Worker 镜像、只读挂载 EvalLog 的 Inspect View。
- `ui/`：独立 React/Vite/Tailwind 控制台；构建产物进入 Control 镜像，最终镜像不包含 Node 运行时。

## 显式启动

```powershell
docker compose -f extensions/ai-research/compose.yml --profile ai-research up -d --build
```

Research Console 默认地址是 `http://127.0.0.1:8790`，MLflow 是 `http://127.0.0.1:8791`，Inspect View 是 `http://127.0.0.1:8793`。停止时不要附加 `-v`，以保留证据卷。

## 冻结接口

- `GET /healthz`
- `GET /readyz`
- `GET /api/v1/module`
- `GET /api/v1/system`
- `POST /api/v1/runs`
- `GET /api/v1/runs`
- `GET /api/v1/runs/summary`
- `GET /api/v1/runs/{runId}`
- `GET /api/v1/runs/{runId}/events?afterSeq=`
- `GET /api/v1/runs/{runId}/evidence`
- `GET /api/v1/runs/{runId}/artifacts/{artifactName}`
- `POST /api/v1/runs/{runId}/cancel`

请求只允许固定 fixture、case、`local` 租户兼容位和幂等键。不存在任意命令、路径、上传、模型、prompt 或凭据入口。

## 验证

```powershell
cd extensions/ai-research
.\scripts\verify.ps1 -Base origin/main -Mode Full
```

完整验证会先运行独立 UI 的 `npm ci`、类型检查、Vitest 和生产构建，再主动攻击退出码误判、取消竞态、畸形日志、幂等冲突、MLflow 中断、路径逃逸、Worker 网络、SPA 回退和证据篡改。
Windows 验证机需要 Python 3.12.13；若 `python`/`py -3.12` 不可用，请把
`AI_RESEARCH_PYTHON` 指向精确的 Python 3.12.13 可执行文件。镜像内运行时仍由
digest 与哈希锁固定，不读取宿主的模镜配置或密钥。
