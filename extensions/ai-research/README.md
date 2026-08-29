# 模镜科研 / ModelMirror AI Research

V0.1 是一个可选、同仓但独立构建的 AI/Agent Research Project 工作台。它保留 AR1 的 fixture-only Inspect/MLflow 工程底座，并通过固定 Local Deep Research 适配增加真实文献检索、Zotero 资料库、带引用综述和可核对成果包；文献结果仍需人工复核，不构成科研结论。

## V0 锁定路线

[《模镜科研 V0 锁定路线图》](./AI_RESEARCH_V0_ROADMAP.md) 是本模块 V0 产品定义、开源组合、分轮实施和变更控制的规范性基线。V0.1 只落地 Research Project 与文献工作台，后续假设、研发、评测、分析和报告阶段保持不可用。

## 当前候选状态

- 项目、LDR 会话与运行适配、受限模型桥、成果包、Research Console 和自动化契约已经落地。现场验收已使用管理员固定的真实文本模型和 OpenAlex 完成一项 AI/Agent 文献研究；报告、来源和成果包在 Control/LDR 重启并重新解锁后保持可恢复，完整性为 `verified`。
- LDR 本地资料库已用 4 篇人工上传的公开论文完成固定嵌入模型初始化、629 个分块、语义检索和重启后恢复；这条证据只证明本地索引链。
- Zotero API key 配置和连接测试已成功；测试集合 `ModelMirror V0.1 Zotero Acceptance` 已同步 4 篇论文，使用固定本地嵌入模型完成 4/4 索引并关联到真实研究项目。该项目的上游研究达到 `completed`，但 LDR v1.10.5 的 Quarto ZIP 只有 186 个 BibTeX 条目，却在 QMD 中产生 939 个引用键，其中 753 个无对应条目。Control 保留原始 `completed`，拒绝该成果包并归类为 `infrastructure_error`；Zotero 同步、索引和关联门禁已有真实证据，完整成果包门禁仍未关闭。
- 已按批准的 Amendment V0.1-A1 增加固定目标 `ai-research-model-relay`：Control 仅连接 internal 网络，不能直接解析或访问宿主及公网；relay 只暴露固定模型桥的 models/chat 两条路由。容器反证已确认 Control 的公共 DNS、公共 IP、HTTP 与宿主访问失败，同时固定 relay 路径可达。
- 已按批准的 Amendment V0.1-A2 增加固定 `ai-research-console-gateway`，由单一受限进程承接原有 8790/8791/8793 回环入口；Control、Tracking 与 Inspect View 继续只连接 internal 网络且不直接发布端口。隔离 Full 已通过 Console/API/成果链、MLflow 同步及 Inspect View 递归 EvalLog 访问，伪造 Host/Origin 被拒绝；这项隔离证据本身不替代另行完成的真实文献旅程。
- 已按批准并在收口时补记的 Amendment V0.1-A5 固定 `langgraph-agent + openalex`，模型桥只转发受 schema、大小、调用配对和 `chat_tools` 资格约束的 function-tool 协议；这不是任意工具入口，用户不能选择工具、模型或执行参数。该路径已产生一项完整性 `verified` 的真实成果包。
- 已按批准的 Amendment V0.1-A6 将主站帮助中心更新推迟到 AR3 条件式 Studio 入口同批交付；V0.1 继续保持 `client/` 和默认主包零增量，模块内操作与限制由本 README 记录。
- 已按批准的 Amendment V0.1-A7 将 LDR 候选来源锁提升到 v1.10.6；该版本必须在全新隔离栈完成同一真实 Zotero 成果包旅程后才可替换当前验收栈，版本升级本身不关闭引用完整性门禁。
- LDR 项目源码为 MIT；官方镜像是包含操作系统与 Python 依赖的聚合物。当前只允许按精确 digest 从上游公共仓库外部拉取，不允许模镜镜像化、离线捆绑、修改或再分发该镜像。

## 明确限制

- 工程运行仍只有 `success`、`task_error`、`long_running_cancel` 三个夹具；真实能力仅限独立的文献研究工作流。
- AR1 工程夹具仍固定标记为 `fixture_only` 和 `harness_only`；文献研究固定为 `scientificClaim=none`。
- 文献研究只通过默认关闭、管理员固定模型的受限 S2S 桥调用模镜控制面；扩展不读取 Provider key，不接受用户选择模型、prompt 或搜索参数。
- 不安装 EvalPack，不产生科学分数；不会出现在 Studio、Plugin 或默认 Compose 中。
- Research Console、MLflow UI 与 Inspect View 只绑定本机回环地址，不是多用户服务。
- LDR v1.10.6 官方镜像 SBOM 共 438 个包；60 个包的 declared license 为 `NOASSERTION`，416 个包的 concluded license 为 `NOASSERTION`，交叉后有 38 个有效未知项。`concluded=NOASSERTION` 不等于已确认未知许可证。精确口径和分发边界见 [`LDR_LICENSE_DISPOSITION.md`](./LDR_LICENSE_DISPOSITION.md)。

## 组件

- `ai-research-control`：版本化 HTTP API、SQLite 控制账本、证据 outbox。
- `ai-research-console-gateway`：无持久化的固定本机入口，只将 8790/8791/8793 分别转发给 Control、Tracking 与 Inspect View，不接受任意上游。
- `ai-research-tracking`：MLflow 3.15.1、SQLite backend、本地 artifacts。
- `ai-research-worker`：Inspect AI 0.3.260、无网 Linux worker、Unix socket 控制协议。
- `ai-research-inspect-view`：复用 Worker 镜像、只读挂载 EvalLog 的 Inspect View。
- `ai-research-model-relay`：不发布端口、禁用环境代理和重定向，只向本机固定 AI Research 模型桥转发受限模型请求。
- `ai-research-ldr`：按 digest 拉取的 Local Deep Research v1.10.6，承载检索、Library、Zotero 与报告导出。
- `ai-research-ldr-assets`：一次性下载并校验固定 revision 的本地嵌入模型。
- `ui/`：独立 React/Vite/Tailwind 控制台；构建产物进入 Control 镜像，最终镜像不包含 Node 运行时。

## 显式启动

操作员需要分别为主服务和扩展配置相同的短期 S2S token，并在主服务显式开启桥、固定文本模型。扩展不读取主服务 `.env`。随后显式启动：

```powershell
docker compose -f extensions/ai-research/compose.yml --profile literature up -d --build
```

Research Console 默认地址是 `http://127.0.0.1:8790`，MLflow 是 `http://127.0.0.1:8791`，LDR 是 `http://127.0.0.1:8792`，Inspect View 是 `http://127.0.0.1:8793`。停止时不要附加 `-v`，以保留项目、资料库、模型与证据卷。

固定嵌入模型由 `ai-research-ldr-assets` 校验后保存在只读资产卷，LDR 内通过 `/data/models` 访问，`Local Search Embedding Model` 必须为 `/data/models/sentence-transformers/all-MiniLM-L6-v2`。早期本地预览若保存过 `/models/sentence-transformers/all-MiniLM-L6-v2`，需在 LDR Settings 中改为上述路径，并对受影响集合执行一次明确的重新索引；重新索引会重建向量索引，但不会删除原始文档。

与其他本机实例并行验收时，可以通过 `AI_RESEARCH_COMPOSE_PROJECT` 使用独立 Compose 项目名，并通过 `AI_RESEARCH_CONTROL_PORT`、`AI_RESEARCH_MLFLOW_PORT`、`AI_RESEARCH_LDR_PORT` 和 `AI_RESEARCH_INSPECT_VIEW_PORT` 改用独立端口。若另一实例仍占用默认 `10.254.76.0/25`，还必须把 `AI_RESEARCH_TRACKING_SUBNET`、`AI_RESEARCH_INSPECT_VIEW_SUBNET`、`AI_RESEARCH_LITERATURE_CONTROL_SUBNET`、`AI_RESEARCH_LITERATURE_EGRESS_SUBNET`、`AI_RESEARCH_MODEL_BRIDGE_EGRESS_SUBNET` 和 `AI_RESEARCH_LOCAL_GATEWAY_SUBNET` 一并改为六个互不重叠且不与宿主冲突的 `/28`；不允许只改一部分。验收脚本会读取同一组端口，并拒绝任何非 HTTP loopback 的显式验收 URL，避免把本地测试误发到远程服务。

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
- `POST /api/v1/projects`
- `GET /api/v1/projects`
- `GET /api/v1/projects/{projectId}`
- `PATCH /api/v1/projects/{projectId}`
- `GET|POST|DELETE /api/v1/literature/session[...]`
- `POST /api/v1/projects/{projectId}/literature/runs`
- `GET|POST /api/v1/projects/{projectId}/literature[...]`
- `GET /api/v1/projects/{projectId}/sources`
- `GET /api/v1/projects/{projectId}/review`
- `GET /api/v1/projects/{projectId}/artifacts/{artifactName}`
- `GET|POST /api/v1/literature/library[...]`
- `GET|POST /api/v1/literature/zotero[...]`

请求只允许固定夹具或固定文献 profile、项目字段和幂等键。不存在任意命令、路径、上传、模型、prompt、Provider key 或 Zotero key 入口。

## 验证

```powershell
cd extensions/ai-research
.\scripts\verify.ps1 -Base origin/main -Mode Full -DistributionMode ExternalPull
```

完整验证默认只启动 `ai-research` profile，运行独立 UI 的 `npm ci`、类型检查、Vitest 和生产构建，再主动攻击退出码误判、取消竞态、畸形日志、幂等冲突、MLflow 中断、路径逃逸、Worker 网络、SPA 回退和证据篡改。只有显式设置 `AI_RESEARCH_LIVE_ACCEPTANCE=1` 时才会增启 `literature` profile，并要求锁定 LDR 镜像、固定真实模型、OpenAlex 和用户授权的 Zotero 测试库；未设置时的 Full 通过不能替代 V0.1 真实旅程验收。
边界检查必须显式声明分发模式：当前支持 `scripts/validate_boundary.py --distribution-mode external-pull`，它只允许 Compose 按精确公共上游 digest 拉取原始 LDR 镜像。`redistributable-bundle` 模式会在镜像再分发义务完成前按设计失败，不存在宽泛的许可证绕过开关。
Windows 验证机需要 Python 3.12.13；若 `python`/`py -3.12` 不可用，请把
`AI_RESEARCH_PYTHON` 指向精确的 Python 3.12.13 可执行文件。镜像内运行时仍由
digest 与哈希锁固定，不读取宿主的模镜配置或密钥。
