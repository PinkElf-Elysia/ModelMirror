# 部署与运维指南

最后更新日期：2026-08-21
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
| `browser` | 是 | 受控浏览器 sidecar，不映射宿主端口。 |
| `sandbox` | 是 | 无网络沙箱 sidecar。 |
| `omniroute` | 否 | `omniroute` profile；只绑定 `127.0.0.1:20128`。 |
| `office-host` | 否 | `office` profile；实验性 Office Add-in host。 |
| `coding-runtime` | 否 | `coding` profile；单实例代码问答与修改草稿执行面，无宿主端口。 |
| `coding-verifier` | 否 | `coding-verify` profile；无网络的草稿项目验证执行面，无宿主端口。 |
| `coding-project-source` | 否 | `coding-projects` 或 `coding-project-host` profile；把当前项目 HEAD 导入单槽快照，不向 Server 暴露路径。 |
| `coding-project-writer` | 否 | `coding-writeback` profile；只写清单 v3 授权的 `local_clone`。 |
| `coding-applier` | 否 | 独立 overlay 的 `coding-apply` profile；把已验证草稿写入固定专用工作树。 |
| `coding-committer` | 否 | 独立 overlay 的 `coding-commit` profile；只在无远程独立仓库中创建本地提交。 |
| `coding-publisher` | 否 | 独立 overlay 的 `coding-publish` profile；只读发布固定提交链。 |
| `coding-github-egress` | 否 | 发布专用无凭据出口；只允许 GitHub.com 固定域名的 443 端口。 |

Coding 恢复没有新增常驻服务；`docker-compose.coding-recovery.yml` 只给 Server
挂载独立加密存储，并提供一次性、无网络、只读的重建预检容器。
Windows Project Host v2 是单独打包、由用户启动的便携 Windows 应用，不是 Compose
服务，也不会随容器自动启动。

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

### 多 worktree 共享栈

固定使用 `-p modelmirror` 时，各 worktree 会共享容器名、镜像标签、端口和网络，因此同一时间
只能由一个 worktree 重建活动栈。其他 worktree 应等待共享栈空闲，或仅运行不会重建活动容器的
只读/临时测试。newAPI 使用独立 Compose 项目，不得通过手工连接网络代替版本化 Overlay。

## 配置与密钥

后端默认读取 `${MODELMIRROR_DATA_ROOT}/server/.env`。使用外部 OpenAI-compatible
数据面时必须同时显式配置 URL 与 Key，也可只配置 OpenRouter Key：

```bash
LLM_GATEWAY_URL=https://gateway.example/v1/chat/completions
LLM_GATEWAY_KEY=your-gateway-key
OPENROUTER_API_KEY=your-openrouter-key
NEWAPI_WEB_URL=http://localhost:3000
```

`NEWAPI_WEB_URL` 只公开 newAPI 管理界面的外部 HTTP(S) 链接。它由 client
容器在运行时提供给设置页，修改后只需重新创建 client，无需重新构建前端镜像。
该变量不得包含 userinfo、query 或 fragment，也不能代替数据面健康检查。

### 独立 newAPI 数据面

newAPI 不属于核心 Compose。先启动独立栈，再按需加载互联 Overlay：

```powershell
$env:LLM_GATEWAY_URL = "http://new-api:3000/v1/chat/completions"
docker compose -f deploy/newapi/compose.yml -p modelmirror-newapi up -d
docker compose -f docker-compose.yml `
  -f deploy/newapi/modelmirror-overlay.yml `
  -p modelmirror up -d --build
```

独立栈固定官方未修改镜像 `v1.0.0-rc.22` 及多架构 digest，默认只把 UI 绑定到
`127.0.0.1:3000`，并复用 `${MODELMIRROR_DATA_ROOT}/new-api-data`。停止或回退
ModelMirror 不得删除该目录。newAPI 上游为带附加条款的 AGPLv3 项目；本仓库不
嵌入或修改其管理 UI，部署者仍需自行完成许可证与业务使用合规审查。

### Provider Control Plane、v14 Catalog 与 R5E Managed Chat

Provider 管理面仍需外部注入至少 32 字符的
`MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET` 和规范凭据主密钥。升级到 SQLite v14
前，先停止 Server 写入并使用 SQLite Backup API 或等价一致性方式备份 Router 数据库，
同时保留旧主密钥；不得复制正在写入的数据库文件充当一致性备份。

设置入口为：

```text
/settings?section=overview
/settings?section=providers
/settings?section=routing
```

管理员在 Provider 页执行“刷新目录”只会对该连接发送一次受 SSRF/DNS pinning 保护的
模型目录 GET，不会发起 Chat、认证或付费调用。刷新失败或返回截断目录时，最后一次成功
Inventory 会保留并标记 stale；不要通过删除 Router SQLite 来处理目录异常。

受管 Provider 连接与普通 `/api/chat` 当前使用的环境路径相互独立。R4 不会把设置页中的
OpenRouter 或 newAPI 连接自动转成 `LLM_GATEWAY_URL/KEY`；迁移属于 Round 5。若公网
Provider 被报告为受保护网络，先检查容器内 DNS。VPN 的 Fake-IP 模式可能把公网域名解析
到 `198.18.0.0/15` 等保留地址，安全出口会按设计拒绝；应让容器解析到真实公网地址，
不得将保留地址加入内网白名单或降低 SSRF 规则。

工作流 V2 安全 HTTP 节点仍使用 `public_only` 网络策略。若系统 DNS 对一个普通域名的
全部回答都落在 VPN Fake-IP 专用的 `198.18.0.0/15`，运行器会仅把目标主机名发送到
固定数值地址 `1.1.1.1` 的 Cloudflare JSON DoH 重新查询 A/AAAA；不会发送 URL 路径、
查询参数、Header、正文或凭据。DoH 返回值仍必须全部是公网地址，并固定为实际 TCP
目标。IP 字面量、混合公网/Fake-IP 回答、其他私网/回环/链路本地/保留地址及元数据地址
不会走该回退，继续直接拒绝。若部署策略不允许向 Cloudflare 披露目标主机名，应在网络层
关闭 Fake-IP，让容器系统 DNS 直接返回真实公网地址。

只读检查可使用：

```bash
curl -H "Cache-Control: no-cache" "http://localhost:8000/api/models/control-plane-catalog?include_unavailable=true&limit=200"
```

该接口描述当前发现与 Readiness，不是 liveness、默认 Provider 资格或计费账本。回退旧
代码时保留 v14 表；旧版本可忽略这些加法表，无需降级或重写数据库。

R5A 升级至 SQLite v15 前仍需先停止 Server 写入并创建一致性备份。新增表只保存
Managed Chat 的逐能力策略、资格、Gate 和脱敏 Receipt。R5B 可选择接管稳定白名单内的
`gateway=default` 普通文本和已提取文本附件；部署变量：

```bash
MODEL_CONTROL_CHAT_ENABLED=false
```

默认值必须保持 `false`。只有管理员已保存 `newapi_preferred`、稳定模型白名单和合格的
有序 Managed 路由后，显式开启该变量才会接管对应普通文本。开关关闭、策略为 `legacy`
或模型不在白名单时继续使用旧静态网关。Managed 首选目标只允许在 POST 前的资格、目录、
凭据或出口预检失败时选择显式备用；POST 派发后不会调用第二 IP、Provider、模型或 legacy
网关。

R5C 使用同一部署开关，并由设置页租户策略中的 `auto_enabled` 提供独立、默认关闭的
Auto 门禁。开启后只为普通文本 Auto 写入脱敏父运行/尝试证据，不改变 Native 或
OmniRoute 选路。Native 每个实际目标分别记录；OmniRoute 只记录一次 sidecar 边界尝试，
内部 Provider 重试标记为不可观测。若 v15 Receipt 无法在派发前建立，已开启门禁的 Auto
请求失败关闭；关闭 `auto_enabled` 即可恢复旧 Auto 路径。Auto 记录不计入 required
资格样本。

R5D 使用同一部署开关，将 default MCP 工具模式和受控文件输出分别绑定到
`chat_tools`、`chat_file_output` 的有序 Managed 路由。管理员必须先对每个稳定模型和
每个目标连接完成对应能力认证；`chat_text` 认证不能替代。MCP 工具仍由 ModelMirror
Runtime 按既有权限执行，文件仍由 allowlisted renderer 生成，Provider 不获得工具凭据
或本地文件权限。首次 Provider 派发后，多步模型决策只能继续使用同一连接和预检批准的
IP；连接、策略或资格漂移会失败关闭，不转向备用或 legacy。关闭
`MODEL_CONTROL_CHAT_ENABLED` 即恢复全部旧静态路径；多模态与 Canary 不受影响。

R5E 不增加环境变量，也不会因部署或样本达标自动进入 required。设置页仅在当前
preferred 策略的资格纪元达到 500 次、14 天、99%、逐稳定模型至少 10 次成功且零硬失败
后开放 Go/No-Go 区域。管理员还必须逐项确认故障演练、无未解决 P0/P1、失败关闭语义，
以及 newAPI 额度扣减、Token 日志关联和重启持久化。验收关联引用仅在请求内传输，SQLite
只保存其 SHA-256 哈希；不要输入 API Key、Token 或日志正文。

激活 required 后，普通文本只允许首选 newAPI，任何预检或派发后失败都不会调用备用或
legacy。硬失败会令 Gate 标记 degraded 并撤销批准，但不会自动改变 required 策略。
恢复时先在设置页显式退回 `newapi_preferred`，重新完成受影响模型认证并收集新资格纪元；
不得直接修改 SQLite、伪造 Receipt 或重开旧纪元。紧急回滚可先显式退回 preferred；必要
时关闭 `MODEL_CONTROL_CHAT_ENABLED` 并重启以恢复 legacy 路径，同时保留 v15 表和证据。

Direct Chat 可以读取已连接 MCP Catalog 中明确标记为只读、无需审批、非敏感且非终止性的
工具；调用仍经 Catalog Service 重新执行策略校验。该接入使用独立的 Chat Runtime 能力，
不会把 Catalog 工具加入 Workflow 或 Agent 的既有工具集合。受控文件输出还要求
`FILE_OUTPUT_ASSETS_ENABLED=true`、`CHAT_FILE_OUTPUT_TOOL_ENABLED=true`，且
`FILE_ASSET_STORE_MODE` 为 `shadow` 或 `native`；存储仍为 `legacy` 时能力接口失败关闭，
不得仅因 Managed 资格通过而向前端宣称可用。

超过 90 天的运行与尝试记录可先
dry-run 检查：

```bash
python -m server.model_router.cleanup_chat_receipts --storage-dir /app/model_router
python -m server.model_router.cleanup_chat_receipts --storage-dir /app/model_router --apply
```

第二条命令会实际删除过期 Receipt，执行前必须复核目标存储目录并保留数据库备份。
代码回退时保留 v15 表；旧版本可忽略这些加法表。

R6A 升级到 SQLite v16 前同样需要停止 Server 写入并创建一致性备份。v16 只增加
Agent/Workflow Workload 的资格、Policy、Binding、批准和脱敏 Receipt 表；不会重写 v15
Chat 数据。R6 数据面开关全部默认关闭：

```bash
MODEL_CONTROL_AGENT_SHADOW_ENABLED=false
MODEL_CONTROL_META_AGENT_ENABLED=false
MODEL_CONTROL_WORKFLOW_LLM_ENABLED=false
MODEL_CONTROL_WORKFLOW_AGENT_ENABLED=false
MODEL_CONTROL_WORKFLOW_DEPLOYMENT_ENABLED=false
MODEL_CONTROL_XPERT_ENABLED=false
MODEL_CONTROL_XPERT_APP_ENABLED=false
MODEL_CONTROL_EXPERT_TEAM_PLANNER_ENABLED=false
MODEL_CONTROL_EXPERT_TEAM_DAG_ENABLED=false
MODEL_CONTROL_FUSION_ENABLED=false
MODEL_CONTROL_ROUTE_AGENT_ENABLED=false
MODEL_CONTROL_TEAM_CHAT_ENABLED=false
```

R6B 接入 `agent_shadow`，R6C 接入两个 Meta Agent 生成入口，R6D 接入 Classic Workflow
交互与部署 LLM；其他 Workflow Agent 和 Xpert 入口仍不能激活 Managed Policy。`agent_shadow` 只有在
`MODEL_CONTROL_AGENT_SHADOW_ENABLED=true`、
精确模型的 `chat_tools` 资格与 Binding 有效、且管理员明确批准 fail-closed 后才会接管。
开关为 `false` 或 Policy 为 `legacy` 时继续使用原 Shadow 网关路径；已经激活但失效的
Policy 会保持 `degraded_required`，不会自动回退。紧急回退需显式停用 Policy 或关闭该
开关并重启 Server；保留 v16 Receipt 和 Shadow Workspace 数据。

Meta Agent 只有在 `MODEL_CONTROL_META_AGENT_ENABLED=true`、精确模型的
`chat_json_object` 资格与 Binding 有效、且管理员批准 fail-closed 后才会接管。关闭开关或
显式停用 `meta_agent` Policy 会恢复原静态网关路径；已经激活但资格失效时保持失败关闭。
两个生成接口在 managed 模式均返回脱敏 `provider_route_receipts`，不得从中推断或输出
连接、URL、凭据、Prompt 或模型正文。

Classic Workflow 交互接管要求 `MODEL_CONTROL_WORKFLOW_LLM_ENABLED=true`，部署执行还要求
`MODEL_CONTROL_WORKFLOW_DEPLOYMENT_ENABLED=true`。两类入口须分别配置并批准精确模型的
`chat_text`、`chat_text_unary`、`chat_json_object` Binding；只启用部署开关不能绕过交互/部署
各自的 Policy。关闭对应开关并重启可恢复 legacy；已经激活但资格或 Binding 漂移时保持
`degraded_required` 并失败关闭。恢复部署执行不得自动重放已有模型节点；应由操作者确认后
创建显式新执行。交互节点仅显示脱敏摘要，完整调用证据在管理员 Settings 查看。

同一清理命令从 v16 起同时报告 R5 Chat 与 R6 Workload 的过期记录；第一条仍是 dry-run，
只有第二条显式 `--apply` 才删除已完成记录。不得对正在运行的父运行或逻辑调用执行清理。

规则：

- `.env`、API Key、token 和 master key 不得提交。
- 前端环境变量不得保存后端凭据。
- 日志不得记录 Prompt、音视频正文、URL 查询签名或上游完整错误体。
- 音乐生成和实时语音均可能额外计费；费用未知时不得显示为零。
- 视频生成可能产生费用且不保证 ZDR，启用前必须完成人工验收。
- OmniRoute secrets 必须独立生成，不与模型网关 Key 复用。

音频闭环新增功能默认关闭：

```bash
MULTIMODAL_AUDIO_GENERATION_ENABLED=false
MULTIMODAL_REALTIME_VOICE_ENABLED=false
MULTIMODAL_VOICE_CLONING_ENABLED=false
```

实时语音不能只靠环境开关启用。还需在设置页“模型服务连接”中创建
“OpenAI 音频与实时语音”连接，地址使用 `https://api.openai.com/v1`，用途选择
“音频能力”和“实时语音”。永久密钥仅由后端加密保存，不写入前端或普通聊天
环境变量；该连接不会自动加入 default/auto 模型池。

声音克隆仍处于安全占位状态：即使把开关设为 `true`，在上游无法验证删除临时
音色前也不会创建授权录音或自定义音色资源。

## 可选 profile

OmniRoute 仅用于兼容、诊断和紧急回退：

```bash
docker compose -p modelmirror --profile omniroute up -d omniroute
```

详细版本、摘要、密钥和回退要求见
[OMNIROUTE_INTEGRATION.md](./OMNIROUTE_INTEGRATION.md)。

Office host：

```powershell
$env:MODELMIRROR_DATA_ROOT = (Resolve-Path ".").Path
./scripts/setup-office-dev-cert.ps1 -DataRoot $env:MODELMIRROR_DATA_ROOT
docker compose -p modelmirror --profile office up -d office-host
```

`MODELMIRROR_DATA_ROOT` 必须是绝对路径，并与 Server 使用的数据根目录一致。
Compose 不会自动创建空证书目录；缺少 `localhost.crt` 或 `localhost.key` 时应先运行
上述脚本。Office host 仍需要浏览器/Office 加载项验收，不应因该可选服务异常而
误判默认核心栈不可用。

### 实验性代码助手

代码助手默认关闭。配置写入 Compose 读取的根 `.env` 或启动命令环境，不要写入
前端，也不要提交：

```bash
CODING_AGENT_ENABLED=true
CODING_AGENT_MODE=readonly
CODING_AGENT_MODEL=your-coding-model-id
CODING_AGENT_MODEL_BASE_URL=https://your-coding-provider.example/v1
CODING_AGENT_GATEWAY_KEY=your-dedicated-gateway-key
CODING_PROVIDER_NETWORK_NAME=modelmirror-coding-provider
```

`CODING_AGENT_MODE` 默认为 `readonly`。只有显式设置为 `draft`，代码助手才会在
容器内一次性副本中新增、修改、删除或移动 UTF-8 文本；结构化文件操作默认开启，
但它仍不能操作目录、执行 Shell/Git 或把命令产物导入草稿。可选的项目验证只能由
用户手动启动，验证范围和命令由服务端固定。宿主写入必须另行逐项目授权并由用户确认。

该 Key 只注入 `coding-runtime`，不注入 FastAPI。启动并重建：

```bash
docker compose -p modelmirror --profile coding --profile coding-verify up -d --build --force-recreate
docker compose -p modelmirror --profile coding --profile coding-verify ps
curl http://localhost:8000/api/coding/capabilities
```

`coding-runtime` 通过 `internal: true` 网络和 Unix socket 连接 FastAPI，并另外加入只供
Coding 使用的外部 `coding_provider` 网络以访问显式 Provider URL。该网络必须由独立
Provider 栈或运维预先创建；它不复用 `modelmirror-provider`，ModelMirror 也不管理其
Provider、凭据或生命周期。缺少 URL 或外部网络时，Coding profile 应失败关闭，而不是
回退到 newAPI 或 localhost。
构建时会排除私有环境文件、密钥和运行产物，只保留仓库追踪的安全占位模板，再把
净化源码快照复制进只读镜像目录；会话副本位于 256 MiB 的 `nosuid,noexec`
tmpfs，容器根文件系统仍只读，且不映射宿主端口或宿主仓库。它是实验性本地
单实例能力，不应直接暴露到公网。完整边界和人工验收见
[CODING_AGENT_INTEGRATION.md](./CODING_AGENT_INTEGRATION.md)。

#### 受控本地项目草稿

本地项目选择是可选能力。先准备一个专用、范围尽可能小的绝对根目录，并在根目录
创建 `.modelmirror-coding-projects.json`：

```json
{
  "version": 3,
  "projects": [
    {
      "name": "团队示例",
      "path": "team/example-project",
      "writeback": { "enabled": true }
    }
  ]
}
```

清单最多 50 项；`path` 只能是根目录内规范化相对路径。每项必须是有有效分支和 HEAD
的干净独立 Git 克隆，工作区、索引和未跟踪文件均为空。worktree 指针、alternates、
子模块、符号链接、重复/大小写冲突路径和越界路径都会被拒绝。v1/v2 清单及未声明
`writeback.enabled` 的项目保持只读草稿能力并允许存在 remote；要开放本地写入，项目
必须是无 remote、固定分支 `coding/local-draft` 的独立克隆。不要把用户主目录、真实
开发工作树或包含密钥的宽泛目录设为根目录。

在 Compose 读取的根 `.env` 或当前 PowerShell 环境中配置：

```powershell
$env:CODING_PROJECTS_ENABLED = 'true'
$env:CODING_PROJECTS_ROOT = 'C:\absolute\coding-projects'
docker compose -f docker-compose.yml -f docker-compose.coding-projects.yml -p modelmirror --profile coding up -d --build --force-recreate server coding-runtime coding-project-source
curl http://localhost:8000/api/coding/projects
```

必须显式加载 `docker-compose.coding-projects.yml`；变量缺失或不是绝对有效目录时应在
重建前停止。只有无网络的 `coding-project-source` 只读挂载整个根目录，Server 与
Runtime 只经私有 socket 和单槽快照卷取得所选项目的 HEAD 内容。快照上限为 20,000
个文件、192 MiB，单文件 32 MiB；只读取根目录 UTF-8 `AGENTS.md`（最多 64 KiB），
仓库内 OpenCode、插件、MCP、provider 和可执行配置均不会生效。

清单项目支持问答、结构化文本文件操作、Diff、轻量检查、确认后的离线验证、下载和
重启恢复。只有清单 v3 显式授权且满足无 remote/固定分支条件的 `local_clone` 支持
Writer 写入与单轮本地提交；仍不支持 GitHub 发布或多轮提交。内置 ModelMirror 仍是
默认项目并保留完整闭环；停止
Project Source 后也必须继续可用。共享栈重建前应先确认根路径为绝对路径、清单可解析、
所有登记仓库符合其能力要求，并取得独占窗口；任一预检不通过时不要重建。

#### 自定义项目受控写入与本地版本

写入功能必须显式加载 `docker-compose.coding-writeback.yml`。该 overlay 只给
`coding-project-writer` 可写挂载 `CODING_PROJECTS_ROOT`；Server、Runtime、Verifier 和
Project Source 的挂载权限不扩大。Writer 无网络、宿主端口、Docker socket、模型密钥、
远程凭据，工作区预演位于 512 MiB `nosuid,noexec` tmpfs。

先确认清单 v3 中目标项目设置 `writeback.enabled=true`，仓库无 remote、分支为
`coding/local-draft` 且 `git status --short` 为空，再设置功能开关并加载 overlay：

```powershell
$env:CODING_FILE_OPERATIONS_ENABLED = 'true'
$env:CODING_PROJECT_WRITEBACK_ENABLED = 'true'
docker compose -f docker-compose.yml -f docker-compose.coding-projects.yml -f docker-compose.coding-commands.yml -f docker-compose.coding-writeback.yml -p modelmirror --profile coding --profile coding-verify --profile coding-writeback up -d --build --force-recreate
docker compose -f docker-compose.yml -f docker-compose.coding-projects.yml -f docker-compose.coding-commands.yml -f docker-compose.coding-writeback.yml -p modelmirror --profile coding-writeback ps
curl http://localhost:8000/api/coding/capabilities
curl http://localhost:8000/api/coding/projects
```

`CODING_FILE_OPERATIONS_ENABLED=false` 只关闭代码助手的删除/移动工具；既有新增、修改和
只读能力不受影响。`CODING_PROJECT_WRITEBACK_ENABLED=false` 或省略 Writer overlay 会让
清单项目回到只读草稿、验证和下载，不会撤销已有本地文件或提交。应用/提交前 Writer
再次复核项目 ID、基准 HEAD、固定分支、remote、索引、工作区、Patch 和文件哈希；异常
只影响所选项目写回能力，不得导致 ModelMirror 或其他自定义项目不可用。

`coding-verifier` 通过同一私有 socket volume 接收 Worker 生成的 Patch，不加入
任何网络。容器使用非 root、只读根文件系统、1 GiB `nosuid,noexec` tmpfs，并固定
为 2 CPU、3 GiB 内存和 256 PIDs；不挂载宿主仓库、密钥或 Docker socket。镜像
内预装当前锁定的 Python 和前端依赖，运行时不会下载新依赖。源码变化后必须同时
重建 Runtime 与 Verifier，快照指纹不一致时验证会显示“未运行”，不影响草稿。

若只需第二轮草稿能力，可省略 `coding-verify` profile。此时页面会明确提示验证
服务未启动，但查看 Diff、轻量检查和下载仍可使用。

#### Windows 本地项目助手直接写入

`host_git` 不使用 `CODING_PROJECTS_ROOT` 或容器 Writer。Windows Project Host v2 是
宿主项目路径的唯一持有者，在用户明确选择的干净独立 Git 项目内执行原子写入、撤销、
当前分支本地提交、撤销提交和精确对账。项目可已有 remote，但助手不会读取或返回 URL，
不会执行 fetch、push、ls-remote 或创建 PR。v1 助手和写回开关关闭时仍可选择项目、问答、
生成草稿、查看/下载 Diff，但始终不显示写入入口。

写回资格仅支持标准 files refs 后端，并在任何对象/status/事务命令前拒绝
`extensions.refStorage`/reftable、replace refs、grafts、partial clone、promisor、alternates、
配置 include/filter/credential 与外部 excludes。Helper 本地 DPAPI registry 绑定所选 root
和 `.git` 的文件身份；旧 path-only 记录或同路径换仓必须由用户重新选择，不能自动继承授权。

先固定绝对数据根，并在该根对应的 `${MODELMIRROR_DATA_ROOT}/server/.env` 中设置：

```dotenv
CODING_PROJECT_HOST_WRITEBACK_ENABLED=true
```

该变量由 base Compose 的 `server.env_file` 注入；只在 PowerShell 设置同名环境变量不会
把它传给 `server`。`CODING_PROJECT_HOST_ENABLED` 则由 Project Host overlay 读取：

```powershell
$env:MODELMIRROR_DATA_ROOT = 'C:\absolute\path\to\stable\data\workspace'
$env:CODING_PROJECT_HOST_ENABLED = 'true'
docker compose -f docker-compose.yml -f docker-compose.coding-project-host.yml `
  -f docker-compose.coding-commands.yml -p modelmirror `
  --profile coding --profile coding-verify --profile coding-project-host config --quiet
```

这条命令只做配置预检。实际 `up -d --build --force-recreate` 必须等用户确认共享栈独占
窗口和最新基线后，从批准的验收集成工作树执行。若清单 `local_clone` 与 Windows 助手
同时启用，overlay 顺序固定为 base → `docker-compose.coding-projects.yml` →
`docker-compose.coding-project-host.yml` → `docker-compose.coding-project-host-full.yml`，
之后再追加当前部署已使用的 commands、writeback、recovery 等 overlay；缺少 full overlay
会破坏 Project Source 的隔离列表或挂载合并。只使用其中一种项目来源时不要加载 full。

当前 `docker-compose.coding-recovery.yml` 仍同时预检 legacy Applier/Committer 的
`CODING_IMPLEMENTATION_WORKTREE` 和 `CODING_COMMIT_REPOSITORY`，且 Compose 会在 profile
过滤前插值。因此不能把它描述为无需其他变量的全新 host-only recovery overlay；共享栈
应继续提供现有受控目标变量并保留当前拓扑，或在后续拆出 host 专用恢复预检后再简化。
不得用虚构路径绕过检查。

便携助手必须与容器代码来自同一实现 HEAD，并在临时输出目录重新打包：

```powershell
$helperRoot = 'C:\tmp\modelmirror-project-host-v2'
python -m venv "$helperRoot\venv"
& .\scripts\build-coding-project-host.ps1 `
  -Python "$helperRoot\venv\Scripts\python.exe" `
  -OutputRoot "$helperRoot\package"
```

脚本会在传入的解释器环境中安装固定 `websockets==16.0`、`pyinstaller==6.14.1`；因此
必须使用专用 venv，不能污染部署者默认 Python。它没有新增第三方依赖；生成
`ModelMirrorProjectHost-windows-x64.zip`、打印 SHA-256，并在压缩包超过 40 MiB 时失败。
产物、spec、build、dist 和助手日志不得提交。便携包不内置 Git；目标电脑必须预装
Git for Windows 或兼容 `git.exe`，并让助手进程可从 `PATH` 找到。助手只接受
`http://127.0.0.1:<port>`（默认 8000），不接受 `localhost`、非回环地址、HTTPS、认证信息、
路径、query 或 fragment。完成容器重建后还必须启动这份 v2 助手，
核对 `/api/coding/project-host` 的协议、`direct_writeback`、可用性和原因，再真实走一次
“选择→快照→草稿→apply→commit→undo→revert”协议冒烟；旧便携包不能作为写回验收。

重建前必须另行检查绝对 `MODELMIRROR_DATA_ROOT` 及其 `server/.env` 中 Coding 模型 Key
和上述开关是否存在且非空，不得把值打印到终端或日志；当前 recovery preflight 不代替
这项检查。把 `CODING_PROJECT_HOST_WRITEBACK_ENABLED=false` 写入该 `server/.env` 并在
批准窗口内重启或重建 Server 后，即恢复第十二轮只读助手；设置
`CODING_PROJECT_HOST_ENABLED=false` 并在后续启动中省略 Project Host overlay 可完全关闭
助手。两种回退都不会自动撤销已写文件、删除本地提交或移除用户项目。

#### 受控应用到专用工作树

受控应用必须显式加载 `docker-compose.coding-apply.yml`。未加载时，基础 Compose
不会读取或创建任何宿主目标路径，第三轮 Draft、Diff、验证和下载保持原样。

先从将要构建镜像的同一提交创建一个干净、分离 HEAD 的专用工作树。以下路径仅为
本地验收示例，不要指向当前主工作树或正在开发的工作树：

```bash
git worktree add --detach C:\tmp\modelmirror-coding-apply-target-v4 <implementation-head-sha>
```

确认目标 `git status --short` 为空，并在 Compose 读取的根 `.env` 或当前启动环境
设置绝对路径：

```bash
CODING_APPLY_WORKTREE=C:\tmp\modelmirror-coding-apply-target-v4
```

然后显式加载基础文件与 overlay：

```bash
docker compose -f docker-compose.yml -f docker-compose.coding-apply.yml -p modelmirror --profile coding --profile coding-verify --profile coding-apply up -d --build --force-recreate
docker compose -f docker-compose.yml -f docker-compose.coding-apply.yml -p modelmirror --profile coding --profile coding-verify --profile coding-apply ps
curl http://localhost:8000/api/coding/capabilities
```

overlay 使用长语法 bind mount 和 `create_host_path: false`；变量缺失或目录不存在时
配置/启动失败，不会自动创建目录。Applier 只挂载固定 `/target`，并把工作树的
`.git` 指针文件单独只读挂载。容器无网络、端口、模型密钥和 Docker socket；
Runtime 与 Verifier 也不能访问它的独立 socket。

应用前目标必须仍与镜像基准完全一致：除 `.git` 外不能有修改、额外文件或符号
链接。成功应用后页面可在当前会话内执行一次安全撤销；如果有人又编辑了目标，
撤销会拒绝覆盖。未加载 `docker-compose.coding-recovery.yml` 时，Server 重启后不保证
保留撤销凭据，此时应人工确认并从相同提交重建专用工作树；启用恢复后，加密意图、
公开回执和 Applier 日志精确一致可恢复撤销，不明确则只读冲突。不要让自动清理脚本
删除用户未确认的工作树。

#### 保存为隔离本地提交

本地提交必须同时加载 `docker-compose.coding-apply.yml` 与
`docker-compose.coding-commit.yml`。提交目标不能是 Git worktree；必须从最终实现
HEAD 创建无硬链接、无远程的独立克隆，并固定到 `coding/local-draft`：

```bash
git clone --no-local --no-hardlinks <implementation-worktree> C:\tmp\modelmirror-coding-repository-v5
git -C C:\tmp\modelmirror-coding-repository-v5 remote remove origin
git -C C:\tmp\modelmirror-coding-repository-v5 switch -C coding/local-draft <implementation-head-sha>
```

确认 `git status --short` 和 `git remote` 均无输出，再设置目标。Applier 与 Committer
使用同一独立克隆；前者只写工作区且只读 `.git`，后者只读工作区且只写 `.git`：

```bash
CODING_APPLY_WORKTREE=C:\tmp\modelmirror-coding-repository-v5
CODING_COMMIT_REPOSITORY=C:\tmp\modelmirror-coding-repository-v5
CODING_COMMIT_AUTHOR_NAME=ModelMirror Coding Assistant
CODING_COMMIT_AUTHOR_EMAIL=coding@modelmirror.local
```

作者变量可由部署者覆盖，浏览器不能指定。显式加载三个 Compose 文件：

```bash
docker compose -f docker-compose.yml -f docker-compose.coding-apply.yml -f docker-compose.coding-commit.yml -p modelmirror --profile coding --profile coding-verify --profile coding-apply --profile coding-commit up -d --build --force-recreate
docker compose -f docker-compose.yml -f docker-compose.coding-apply.yml -f docker-compose.coding-commit.yml -p modelmirror --profile coding --profile coding-verify --profile coding-apply --profile coding-commit ps
curl http://localhost:8000/api/coding/capabilities
```

Committer 无网络、端口、Docker socket、模型密钥或 Git 凭据；目标路径缺失时
`create_host_path: false` 会失败关闭。它拒绝远程地址、worktree gitfile、
alternates、共享 Git 目录、错误分支、脏索引和基线不匹配。有效提交存在时应用撤销
会被阻止；先撤销提交可保留文件，再选择重新提交或撤销应用。

#### 最近任务恢复与重建预检

恢复功能必须显式加载 `docker-compose.coding-recovery.yml`。第一次启用前，由
部署者明确创建专用目录；Compose 和预检都不会代为创建宿主路径：

```powershell
New-Item -ItemType Directory -Force "$env:MODELMIRROR_DATA_ROOT\server\coding-recovery"
```

配置只使用绝对稳定数据根目录，默认保留 604800 秒（7 天）：

```text
CODING_RECOVERY_ENABLED=true
CODING_RECOVERY_RETENTION_SECONDS=604800
```

每次重建共享栈前先取得独占窗口，再从实现工作树运行只读预检。它检查绝对数据
根目录、非空 `server/.env`、恢复目录、实现与目标树指纹、固定分支、remote、
alternates 和 Git 状态；任一失败返回非零，不打印密钥、不创建路径、不修改仓库：

```powershell
$env:CODING_IMPLEMENTATION_WORKTREE = (Get-Location).Path
docker compose -f docker-compose.yml -f docker-compose.coding-apply.yml -f docker-compose.coding-commit.yml -f docker-compose.coding-recovery.yml -p modelmirror --profile coding-recovery-preflight run --rm coding-recovery-preflight
if ($LASTEXITCODE -ne 0) { throw "Coding 恢复重建预检未通过" }
```

预检通过后才能重建；必须同时写出四个 Compose 文件：

```powershell
docker compose -f docker-compose.yml -f docker-compose.coding-apply.yml -f docker-compose.coding-commit.yml -f docker-compose.coding-recovery.yml -p modelmirror --profile coding --profile coding-verify --profile coding-apply --profile coding-commit up -d --build --force-recreate
```

SQLite 与 `recovery-master.key` 必须作为一组备份和恢复。不要只复制、删除或替换其中
一个文件；错误密钥、损坏密文和不支持的 schema 会失败关闭，旧记录不会被覆盖。

#### 发布为 GitHub 草稿 PR

GitHub 发布必须继续使用上文的无 remote 独立克隆，并显式加载
`docker-compose.coding-publish.yml`。GitHub App 只安装到一个固定仓库，权限限制为
Contents `Read and write`、Pull requests `Read and write`、Metadata `Read-only`；
不要授予 Administration、Actions、Workflows 或仓库删除权限。

将 App 私钥保存为宿主上的只读文件，并在 Compose 读取的根 `.env` 或启动环境中只
填写标识和绝对文件路径，不要粘贴私钥正文或安装令牌：

```text
CODING_GITHUB_PUBLISH_ENABLED=true
CODING_GITHUB_APP_ID=<positive-integer>
CODING_GITHUB_INSTALLATION_ID=<positive-integer>
CODING_GITHUB_REPOSITORY_ID=<positive-integer>
CODING_GITHUB_REPOSITORY=<owner/repository>
CODING_GITHUB_APP_PRIVATE_KEY_FILE=C:\absolute\path\coding-github-app.pem
```

`CODING_GITHUB_REPOSITORY_ID` 必须是 App 安装范围内仓库的数字 ID；名称只用于再次
核对身份。目标基础分支固定为 `main`，浏览器不能指定仓库、分支或 Git 参数。
`CODING_GITHUB_ALLOW_SYNTHETIC_DNS` 默认 `false`，只供明确使用合成 DNS 地址的
隔离测试环境开启，普通部署不得设置。

共享栈重建前仍须先运行恢复预检。通过后再加载五个 Compose 文件；Publisher 只读
挂载 `CODING_COMMIT_REPOSITORY`，私钥只读挂载，缺失路径因
`create_host_path: false` 在启动前失败：

```powershell
$env:CODING_IMPLEMENTATION_WORKTREE = (Get-Location).Path
docker compose -f docker-compose.yml -f docker-compose.coding-apply.yml -f docker-compose.coding-commit.yml -f docker-compose.coding-recovery.yml -p modelmirror --profile coding-recovery-preflight run --rm coding-recovery-preflight
if ($LASTEXITCODE -ne 0) { throw "Coding 发布重建预检未通过" }
docker compose -f docker-compose.yml -f docker-compose.coding-apply.yml -f docker-compose.coding-commit.yml -f docker-compose.coding-recovery.yml -f docker-compose.coding-publish.yml -p modelmirror --profile coding --profile coding-verify --profile coding-apply --profile coding-commit --profile coding-publish up -d --build --force-recreate
docker compose -f docker-compose.yml -f docker-compose.coding-apply.yml -f docker-compose.coding-commit.yml -f docker-compose.coding-recovery.yml -f docker-compose.coding-publish.yml -p modelmirror --profile coding-publish ps
curl http://localhost:8000/api/coding/capabilities
```

Publisher 无宿主端口、Docker socket 或工作区写权限，只能连接内部出口代理；出口
代理不持有 App 私钥、JWT 或安装令牌。健康检查只证明 socket/进程可用，首次人工
验收仍须确认固定仓库身份、精确 `main` SHA、Draft PR 和 Ready 二次确认。测试 PR
和远端分支由用户在 GitHub 手工清理，产品没有远端删除权限。

## 反向代理

`/api/chat` 和工作流运行使用 SSE。Nginx 必须关闭代理缓冲：

```nginx
location /api/ {
  proxy_pass http://modelmirror-server:8000;
  proxy_http_version 1.1;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_buffering off;
  proxy_read_timeout 3600s;
}
```

Provider 管理会话根据 ASGI `request.url.scheme` 决定是否设置 Secure Cookie。
TLS 终止代理必须覆盖（而不是追加或透传客户端提供的）`X-Forwarded-Proto`，并在
`server/.env` 通过 `FORWARDED_ALLOW_IPS` 只信任该代理的精确 IP 或最小 CIDR；
不要使用 `*`。未被 Uvicorn 信任的转发头不会改变请求 scheme，管理配对会安全拒绝，
核心数据面仍保持可用。

视频内容代理可能返回较大响应，应设置合理的超时和响应体限制，但不得缓存含
授权语义的上游临时地址。

## 健康与诊断

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/models/router-status
curl "http://localhost:8000/api/multimodal/audio/models?refresh=true"
curl http://localhost:8000/api/multimodal/video/models
curl http://localhost:5173/studio
```

最低观测：

- HTTP 状态、耗时、脱敏错误码。
- 模型路由 engine、actual model、request ID、空流和失败切换。
- RAG active version、候选版本和流水线状态。
- 音频目录中的 `ready / planned / disabled` 与状态原因；实时档案为零时优先检查
  功能开关、直接 OpenAI 连接、`audio + realtime` 用途及连接测试结果。
- 实时语音默认 10 分钟，不提供严格预算承诺；断网后只允许用户显式重连，
  不得自动创建新的付费会话。
- 视频任务状态与连续轮询错误；临时网络错误不直接写成任务失败。
- Browser、Sandbox、newAPI 和 server health。
- 启用后检查 Coding capabilities、项目清单、Project Host 协议/心跳/direct writeback、
  Project Source/Worker/Verifier/Project Writer/Applier/Committer/Publisher health、
  出口域名拒绝、验证取消清理、快照指纹、恢复 pending/retention 和源码 Git 状态。Capabilities 与
  日志不得返回目标绝对路径、恢复密钥或密文负载。

## 备份与恢复

持久化清单见 [DATABASE.md](./DATABASE.md)。升级或恢复前：

1. 记录镜像版本和当前 feature flags。
2. 停止写入或停止 `server`。
3. 备份 bind-mounted 数据及 credential master keys。
4. 重建后验证健康、连接、RAG active version、Agent 发布版本和任务恢复。

## 回退

- 前端：回退镜像或静态产物，不迁移业务数据。
- 后端：回退镜像；schema 变更必须保证旧数据仍可读取。
- 图片识别或生成：分别关闭 `MULTIMODAL_IMAGE_ANALYSIS_ENABLED` 和
  `MULTIMODAL_IMAGE_GENERATION_ENABLED`；静态目录仍可浏览，文本、音频和视频链路不受影响。
- 视频生成或分析：将两个 `MULTIMODAL_VIDEO_*_ENABLED` 设为 `false`，保留任务元数据。
- Chat 音视频：分别关闭 `MULTIMODAL_CHAT_AUDIO_ENABLED`、
  `MULTIMODAL_MICROPHONE_ENABLED`、`MULTIMODAL_STREAMING_AUDIO_ENABLED`
  和 `MULTIMODAL_CHAT_VIDEO_ENABLED`；独立 STT、TTS、视频分析及旧视频任务不受影响。
- 音乐生成与实时语音：分别关闭 `MULTIMODAL_AUDIO_GENERATION_ENABLED` 和
  `MULTIMODAL_REALTIME_VOICE_ENABLED`；已有 STT/TTS、普通 Chat 和视频链路不受影响。
- 智能调度：切回 `MODEL_ROUTER_ENGINE=sidecar` 或 default/newAPI，保留 SQLite。
- OmniRoute：停止 profile，不删除 `omniroute-data`。
- 代码助手：若只回退第十三轮，在绝对数据根对应的 `server/.env` 设置
  `CODING_PROJECT_HOST_WRITEBACK_ENABLED=false` 并重启或重建 Server，即可保留第十二轮
  只读 Windows 助手；
  再设置 `CODING_PROJECT_HOST_ENABLED=false` 并省略 `docker-compose.coding-project-host.yml`
  可完全关闭助手。已有宿主文件、本地提交和授权不会被自动清理。若还要回退第十一轮，
  设置 `CODING_PROJECT_WRITEBACK_ENABLED=false`、
  `CODING_FILE_OPERATIONS_ENABLED=false`
  并省略 `docker-compose.coding-writeback.yml`；这不会自动撤销已写入文件或已有本地
  提交。如需进一步关闭自定义项目，再设置 `CODING_PROJECTS_ENABLED=false` 并在后续
  启动中省略 `docker-compose.coding-projects.yml`，即可恢复第八轮固定 ModelMirror
  行为；项目上下文表会被旧逻辑忽略，受控源仓库不会被修改。再设置
  `CODING_GITHUB_PUBLISH_ENABLED=false` 并在后续启动中省略
  `docker-compose.coding-publish.yml`，即可恢复第七轮本地多轮能力；这不会删除已创建
  的 GitHub 分支或 PR，远端内容只能由用户在 GitHub 明确处理。再设置
  `CODING_RECOVERY_ENABLED=false` 或在后续启动中省略
  `docker-compose.coding-recovery.yml`，即可恢复第五轮内存行为；这不会撤销已应用
  文件或删除本地提交。恢复存储只在用户明确授权后单独清理。再停止 `coding-committer` 并不再加载
  `docker-compose.coding-commit.yml`，即可恢复第四轮受控应用能力，已有本地提交
  不会被删除。再停止 `coding-applier` 并省略 `docker-compose.coding-apply.yml`
  可恢复第三轮验证能力；无需改变或删除专用目标。再省略 `coding-verify` profile
  可恢复第二轮草稿能力。设置
  `CODING_AGENT_MODE=readonly` 可关闭草稿编辑；需要完全关闭时设置
  `CODING_AGENT_ENABLED=false` 并停止 `coding-runtime`。
- 可选 profile 故障不得通过删除核心数据解决。

legacy `/api/dify/*` 健康只表示兼容代理配置状态，不是平台健康门禁。

## Coding Worker V14 部署

V14 默认关闭，使用独立 overlay `docker-compose.coding-worker-v14.yml`。它增加两个 Provider、两个单槽 Executor、持久 Store/Workspace 卷，以及可选的网络 egress proxy；不替换 v13 Project Host、Coding Recovery 或 Agent Workspace 数据。

在绝对 `MODELMIRROR_DATA_ROOT` 对应的 `server/.env` 中设置以下值，不要把真实值提交到仓库：

```dotenv
CODING_WORKER_V14_ENABLED=false
CODING_WORKER_MAX_ACTIVE_TASKS=2
CODING_WORKER_RETENTION_SECONDS=604800
CODING_WORKER_NETWORK_ENABLED=false
CODING_WORKER_SLOT_A_TOKEN=<random>
CODING_WORKER_SLOT_B_TOKEN=<random>
CODING_WORKER_EXECUTOR_A_TOKEN=<random>
CODING_WORKER_EXECUTOR_B_TOKEN=<random>
CODING_WORKER_MODEL_ID=<controlled-model-id>
CODING_WORKER_ROUTE_KEY=<dedicated-route-key>
CODING_WORKER_BUILTIN_REVISION=<full-source-commit>
```

`CODING_WORKER_MODEL_BASE_URL` 必须显式设置，且不得从 Provider Control Plane、Router
SQLite 或 newAPI 互联 Overlay 继承。若启用 `develop_networked`，还必须显式设置
`CODING_WORKER_NETWORK_ENABLED=true`、随机 `CODING_WORKER_EGRESS_GRANT_KEY` 和最小
`CODING_WORKER_NETWORK_DOMAINS`，并加载 `coding-worker-network` profile。不要把模型
网关密钥复用为 slot、executor 或 egress token。

只做配置展开检查、不启动服务：

```powershell
docker compose -f docker-compose.yml -f docker-compose.coding-worker-v14.yml -p modelmirror --profile coding config --quiet
```

Host Snapshot 场景还需按既有顺序加载 Project Host overlay；若同时启用清单项目，继续遵守 project-source/full compatibility overlay 的顺序。`CODING_IMPLEMENTATION_WORKTREE` 必须是当前验收 HEAD 的绝对只读来源，`CODING_WORKER_BUILTIN_REVISION` 必须是同一完整 commit。

只有在用户确认共享栈独占窗口、最新主线和正式环境变量后，才可执行重建：

```powershell
docker compose -f docker-compose.yml -f docker-compose.coding-project-host.yml -f docker-compose.coding-worker-v14.yml -p modelmirror --profile coding up -d --build --force-recreate server coding-worker-provider-a coding-worker-provider-b coding-worker-slot-a coding-worker-slot-b
```

网络默认保持关闭；需要依赖下载时再追加 `--profile coding-worker-network`，批准结束后关闭 profile。Provider 在 `coding_internal` 访问受控模型网关，Executor 仅在内部 `coding_worker_tools`；只有 egress proxy 同时加入工具网络和外部网络。

验收至少包括：两个任务并行、第三个排队；失败检查后的自动修复与复测；审批拒绝/过期；SSE 断线补发；Server、两个 Provider/Executor 逐个重启；Host Snapshot 经过 v13 写回。真实 OpenCode、Windows Helper 和用户项目写回必须单列人工结果，不能用 Fake Provider 或 Compose `config` 代替。

回退只需设置 `CODING_WORKER_V14_ENABLED=false` 并重建 Server，使新会话回到 legacy。不要删除 `coding_worker_state`、slot Workspace 卷、v13 Recovery 或 Agent Workspace 数据；已有任务保持可审计，已有宿主副作用不会自动撤销。

## Coding Worker V15 部署

V15 在 V14 overlay 之后可选加载 `docker-compose.coding-worker-v15-claude.yml`。以下开关默认
全部关闭；前三项写入绝对 `MODELMIRROR_DATA_ROOT` 对应的 `server/.env`，单独设置浏览器参数
或 TaskSpec 不能开启能力：

```dotenv
CODING_WORKER_V15_ENABLED=false
CODING_WORKER_SHELL_ENABLED=false
CODING_WORKER_CODE_INTELLIGENCE_ENABLED=false
```

Claude overlay 的值用于 Compose 插值，必须由部署脚本/宿主环境或 Compose project `.env`
显式提供；Server 的 `env_file` 不会替代这些插值：

```dotenv
CODING_WORKER_CLAUDE_ENABLED=false
CODING_WORKER_CLAUDE_MODEL_ID=<controlled-model-id>
CODING_WORKER_CLAUDE_SECRET_FILE=C:\\absolute\\path\\claude-api-key.secret
CODING_WORKER_CLAUDE_PROXY_TOKEN=<random-url-safe-token>
```

Claude secret 文件不得放入仓库或数据导出；sidecar 只接受普通、非链接、单硬链接的只读文件。
缺少或不安全的 secret 只禁用相应内部路由，不影响 OpenCode、Fake、legacy 或已有 V14 任务。
Claude Provider 不挂载 Workspace、Docker socket、宿主目录或 Server 密钥；独立出口代理只允许
`api.anthropic.com:443`，Executor 仍无模型网络。`CODING_WORKER_CLAUDE_MODEL_ID` 是部署者控制
的内部映射，不进入公共 TaskSpec。

只展开最终配置而不启动服务：

```powershell
docker compose -f docker-compose.yml `
  -f docker-compose.coding-worker-v14.yml `
  -f docker-compose.coding-worker-v15-claude.yml `
  -p modelmirror --profile coding config --quiet
```

重建、真实 secret、真实双引擎任务和逐组件重启必须在用户确认的共享栈窗口执行。自动测试、
镜像版本探针或 Compose 展开不等同于真实 Claude/OpenCode 验收。回退时先关闭
`CODING_WORKER_CLAUDE_ENABLED`，再按需关闭 code intelligence、Shell 与 V15 总开关并停止接收
新 V15 任务；不要删除 V14 Store、Workspace、Evidence、v13 Recovery 或 Agent Workspace 数据。
已开始的任务必须进入明确的 `interrupted`/终态，未知 operation 只能对账，不能重放。

## Coding Worker V16 部署

V16 不新增外部 Provider 或 Executor overlay，复用 V14 双槽与 V15 Provider 私有边界。以下开关必须写入当前绝对 `MODELMIRROR_DATA_ROOT` 对应的 `server/.env`，默认全部关闭：

```dotenv
CODING_WORKER_V16_ENABLED=false
CODING_WORKER_INTERACTION_ENABLED=false
CODING_WORKER_SESSION_CONTROLS_ENABLED=false
CODING_WORKER_SUBAGENTS_ENABLED=false
CODING_WORKER_DOCUMENTATION_EGRESS_ENABLED=false
```

总开关关闭时新任务回到 V15；交互、回合控制、子任务和官方文档出站均可独立关闭。关闭或重启不得删除 Worker Store、Workspace Fork、Evidence、v13 Recovery 或旧任务；运行中 V16 任务进入 `interrupted`，未知工具/合并结果只按原 operation ID 对账。

部署前先加载 V14 overlay；需要 Claude 时再按 V15 既定顺序追加 Claude overlay。`config --quiet`、Fake Provider 和容器健康只证明配置/协议，不证明子任务调度或真实模型质量。人工验收至少包括：父任务停车后子任务取得空槽、第三任务仍排队、只读子任务修改被拒、两个非重叠 implement 顺序合并、同文件冲突不覆盖父树、合并后父检查重跑、Server/Provider/Executor 逐个重启、Host Snapshot 经 v13 写回。

真实 24 项 × 两侧 × 三次的 144 次对照与连续两轮认证未通过前，保持 Experimental。回退只关闭上述开关并停止接收新 V16 任务；不得用回退删除持久数据或声称撤销外部副作用。

## Coding Worker V17 与 parity profile

V17 新任务依赖 V16 交互、会话控制和子任务能力，新增开关仍必须写入当前绝对数据根对应的 `server/.env`，默认关闭：

```dotenv
CODING_WORKER_V17_ENABLED=false
CODING_WORKER_PARITY_ENABLED=false
```

关闭 V17 后，新任务回到 V16；已创建的 V17 任务进入 `interrupted` 并保留 Store、Workspace、Turn、Evidence、Artifact 和 v13 Recovery，不降级 checkpoint，也不重放旧 operation。任务级 capability 失败关闭不因回退重新变成全局“可用”。

认证环境只在独立认证 worktree 加载 `docker-compose.coding-worker-v17-parity.yml`，不得加入共享产品栈。运行前必须冻结候选 commit、公开 fixture/manifest、runner image digest、受控 route catalog/receipt digest 和只读密封 checker bundle，并设置彼此不同的随机 RPC token：

```dotenv
CODING_PARITY_NATIVE_TOKEN=<random>
CODING_PARITY_WORKER_TOKEN=<random>
CODING_PARITY_CHECKER_TOKEN=<random>
CODING_PARITY_ROUTE_CATALOG_SHA256=<sha256>
CODING_PARITY_ROUTE_RECEIPT_SHA256=<sha256>
CODING_PARITY_CHECKER_BUNDLE=C:\absolute\path\sealed-checker.bundle
```

只做配置展开，不启动 runner：

```powershell
docker compose `
  -f docker-compose.yml `
  -f docker-compose.coding-worker-v14.yml `
  -f docker-compose.coding-worker-v17-parity.yml `
  -p modelmirror-v17-certification `
  --profile coding `
  --profile coding-worker-parity `
  config --quiet
```

Native runner 是唯一同时持有受控模型 route key 和原版 OpenCode 1.18.9 的对照进程；Worker runner 只调用正式 Worker API；checker `network_mode:none` 且唯一挂载密封隐藏检查；controller `network_mode:none`，不挂载 hidden bundle、route key、Docker socket 或 Workspace。两个 runner 都只能导出终态不透明 Artifact，checker 复核 run/task/tree/artifact/check 绑定后才执行隐藏检查。

CLI 的 `validate`、Fake `smoke`、`report` 和 `certify` 可以在自动门禁中使用；真实 `run-round` 必须处于发布认证窗口。不同 round 的 run ID 必须不同，两个 round 必须绑定相同 candidate、manifest、route、bundle 和 runner image。当前实现交付时尚未运行两轮共 288 次真实模型认证，也未完成无 P0/P1 的 Console 人工门禁，因此 V17 只能保持 Experimental，禁止使用任何“接近 OpenCode”表述。

## Coding Worker V18 Harbor 校准环境


Harbor `0.21.0` 是评测工具，不是 Server 依赖，也不得加入共享产品栈。任务包位于 `benchmarks/coding-worker-v18/`，原生对照固定 OpenCode `1.18.9`。公开任务包不得保存隐藏 checker 正文；发布窗口另行提供仓库外只读绝对目录，并通过 `--sealed-checker-root` 交给 `validate`、`task-gate` 与 `run-round`。CLI 在临时任务副本中注入正文并绑定实际 bundle digest。先用专用 Python 环境安装固定 Harbor，再运行 `compile --write`、`validate` 和确定性任务门禁；不得修改 Server 的生产 requirements。

评测 Server 只有在独立校准环境中才加载 `docker-compose.coding-worker-v18-harness.yml`；默认保持关闭，并且不能与 parity v2 同时启用。宿主变量只提供公开 fixture bundle 的绝对路径，overlay 把它只读挂到固定容器路径：

```dotenv
CODING_WORKER_HARNESS_V3_FIXTURE_FILE=C:\absolute\evaluation\fixture-bundle.json
CODING_WORKER_HARNESS_CONTROLLER_TOKEN=<random-at-least-32-bytes>
```

overlay 内固定 `CODING_WORKER_HARNESS_V3_ENABLED=true`、`CODING_WORKER_PARITY_ENABLED=false` 与容器路径 `/harness-v3/fixture-bundle.json`，并清空 V14 overlay 的单一 builtin source root/revision，让 Server 只注册公开的 12 项 fixture。Controller token 只进入评测 Server 与本次 Harbor Worker Agent 环境，不进入 route binding、日志或 Artifact。受控故障接口只在该 profile 可见，只能绑定唯一待批准的 mutate shell operation。overlay 不挂载 solution、公开 verifier 或密封 checker，不能加入共享产品栈。

Docker Desktop 无法满足 Harbor 标准动态出站控制时，确定性 Oracle/Nop/near-miss 仍只能使用仓库提供的 `StaticNoNetworkDockerEnvironment`；该环境对所有阶段强制 `network_mode:none`。评测专用 `DockerDesktopAllowlistProbeEnvironment` 可让少量明确选择的场景经 exact-host sidecar 做真实模型探测，但 `run-round` 会硬拒绝该适配器，因此结果不是校准证据。探测 overlay 使用显式可路由子网；将其标成 `internal:true` 会在到达 sidecar 前阻断 DNS/egress，真正的边界是任务网络中只允许访问 exact-host sidecar，直连仍拒绝。真实 `run-round` 必须迁移到 Linux、使用 Harbor 标准 Docker environment，并先验证动态 egress preflight。容器化 controller 的仓库和 jobs 参数必须使用 Docker host 看到的真实绝对路径；把 controller 内 `/jobs` 直接交给 Docker API 会导致 verifier 产物对宿主不可见，必须传入 Docker host 可解析的同一绝对路径。

`run-round` 固定 48 次并要求显式 Worker URL、公共 model route、随机 seed、当前候选 SHA、独立 jobs/runs 目录、外置密封 checker 目录，以及至少一个冻结模型网关的精确 `--allow-agent-host`；通配符、IP 字面量和 localhost 均失败关闭。它只能在固定 Linux controller 容器内运行，通过 Docker daemon 把当前 container ID 对账到不可变 image ID，拒绝宿主直接运行、零值和调用方填写的 runner digest。Worker URL 必须是带显式端口的本机回环 HTTP `/api/coding-worker/v1` 基址，禁止 userinfo、query 与 fragment。候选 SHA 必须等于当前 HEAD，且整个评测 checkout 必须干净。Harbor 仅在 `agent.run()` 阶段开放模型网关，Verifier 继续无网络。两侧 Agent 超时固定为 900 秒。原生 OpenCode 使用默认拒绝权限和经过认证的仅回环 Session API；只有问题任务开放 question，文件/Todo 与任务冻结命令按精确规则开放。OpenCode 1.18.9 的内置 LSP 会继承 Server 环境且可能加载仓库插件，本轮原生对照因此失败关闭 LSP；Worker 侧已有隔离 LSP 不变，这项非对称限制必须进入工具配置摘要并在校准报告中保留，不能用于等效结论。OpenCode Server 固定以 root 持有认证与模型 route 环境，全部冻结 Shell 则强制经平台 wrapper 使用 `env -i` 最小环境及 uid/gid 65534 执行，Workspace 只向该 UID 开放写入，任务代码不能读取 root-only 控制目录。控制器按真实状态执行回答、steering、显式 compaction 和故障后 reconcile，并保存不含隐藏思维链的 `native_ledger`。冻结的 restart 场景由同一监督 wrapper 在命令成功后写入结果标记并阻塞工具回执，随后终止整个 OpenCode 进程组；只有原 call ID、intent hash、成功结果 hash、故障事件和 resume 全部一一闭合，原 operation 才能由 unknown 转为 completed。缺台账、消息哈希、实际 question/compaction 事件或该闭环时，即使 checker 通过也不能 accepted。每条 run 仍对账模型、checker bundle、candidate、实际 controller image，以及在线 Server/Provider/route/代码包 attestation。

原生 Session 控制硬门禁已经实现，当前 fixture bundle 与外置密封 checker 的只读 `validate` 也已重新闭合。12 项 fixture 已全部绑定同一 daemon-attested 离线 runtime，最终 bundle 的 Oracle、Nop 与 near-miss 分别从零重跑 `60/60`、`60/60`、`60/60`，且三阶段异常数均为零。该 180 次只证明确定性任务有效性，不是模型校准。明确授权的小规模真实探测完成三项 Session：结构化提问与 steering/compaction 两项 accepted；restart/reconcile 一项按事实 rejected。后者的 unknown/reconcile 台账已闭合且四项协调诊断为零，但模型尝试了未批准 Shell，并生成错误的稳定索引顺序，所以仍是有效的 `policy/agent outcome` 失败。该 `2/3` 结果不等于 runner 可认证；48 次校准未运行。真实校准不应在普通构建或共享栈重建中自动启动；下一步仍须在固定 Linux controller 中冻结干净候选、完整 runtime、route 与 checker。回退只停止评测 profile；admission receipt 与已有产品数据保留。

## Coding Worker V19 Substrate 切换

V19 不增加环境开关、数据库迁移或新 sidecar。部署继续使用 V14–V18 的 Worker、Provider、Executor、网络和保留期配置；差异仅在 Server 内部由 `runtime.py` 组装 `CodingSubstrateHandle`，API/SDK/v13 handoff 不再取得具体 Service、Store 或 Workspace Broker。

启动检查：

1. 生产 profile 未启用 `CODING_WORKER_PARITY_ENABLED` 或 `CODING_WORKER_HARNESS_V3_ENABLED` 时，不应加载 `coding_worker.parity`、`coding_worker.harness_v3` 或 `coding_worker.evaluation`。
2. `/api/coding-worker/v1` 的 JSON、SSE sequence、错误码、任务保留期和模型 route 行为应与 V18 一致。
3. Host Snapshot 任务只有在 completed 且 Acceptance 对当前 tree 有效时才能生成写回候选；Helper exact-head 与 apply/commit/undo 仍由 v13 执行。
4. Worker 关闭时 handoff 保持 404，Worker 已启用但底座不可用时保持 503；不存在的 task 必须先于 preview path 校验返回 404，未准备 Workspace 的评测导出保持 `workspace_not_ready`。

回退不需要数据操作：恢复上一 Server 镜像或回退 PR C 即可。不得删除 Worker Store、Workspace、Evidence、Artifact、Provider checkpoint 或 v13 Recovery。评测 profile 与真实模型校准仍按 V18/V24 的独立发布窗口执行，V19 部署不授权真实模型调用。

## Coding Worker V20 Harness Kernel

V20 只迁移新建任务，默认关闭：

```dotenv
CODING_WORKER_HARNESS_V20_ENABLED=false
```

开启后，Server 仅在目标 route 的每个固定槽均实时提供 Provider-v4 descriptor、`broker_only` 工具所有权、`session_resume` 或更高持久性，以及完整必需能力时创建 V20 任务。冻结的 descriptor、Schema 摘要、sidecar generation 与 controller generation 会写入现有加密 capability snapshot；调度取得槽位后会再次逐项核对，任何变化都在 Provider 会话打开前阻断。

关闭开关后不再创建 V20 任务；已存在且非终态的 V20 任务进入 `interrupted`，保留 capability snapshot、checkpoint、Workspace、Evidence 和 operation。任务不会降级到 Provider-v4 旧路径，也不会自动重放副作用。V14–V19 任务不带 V20 snapshot 标记，继续使用原兼容恢复路径。

OpenCode 1.18.9 与 Claude Code 2.1.89 的 checkpoint 都只保存公开上下文并新开引擎会话，因此 V20 descriptor 如实标记 `session_resume`，不得写成 `durable_checkpoint`。本开关不启用 ACP/Codex evaluation，不授权真实模型测试、校准或认证。

### V20 标准 Driver evaluation profile

ACP/Codex 标准 Driver 使用另一个 Compose 文件，默认不属于共享栈：

```env
CODING_WORKER_ACP_EVALUATION_ENABLED=false
CODING_WORKER_CODEX_EVALUATION_ENABLED=false
```

```powershell
docker compose -f docker-compose.coding-worker-v20-evaluation.yml config -q
docker build -f server/coding_worker/Dockerfile.evaluation-acp -t modelmirror-coding-worker-acp-evaluation:local .
docker build -f server/coding_worker/Dockerfile.evaluation-codex -t modelmirror-coding-worker-codex-evaluation:local .
```

部署者必须在构建后读取精确 image ID，为每个镜像生成私有
`EvaluationDriverManifest`，并通过 `CODING_WORKER_*_EVALUATION_MANIFEST` 只读挂载；
同时设置同一 image ID、至少 32 字节的随机 sidecar token，以及与 manifest
完全一致的固定 executable。相对路径、符号链接、超过 64 KiB 的 manifest、任意
字段不匹配或同时启用两个 Driver 都会失败关闭。manifest、token、executable、cwd
和环境变量不属于任务或公共 API 输入。

两个 profile 仅连接 `internal: true` 的 evaluation 网络，不挂载 Workspace、生产
Provider socket、Docker socket、宿主用户目录、SSH 或模型密钥。ACP SDK 只存在于
ACP evaluation 镜像；Codex 镜像只保留 0.149.0 App Server 所需的主二进制，移除
Code Mode Host 与内置命令资源。Codex 工具所有权固定为 `unknown`，不能注册生产
route；ACP 的 `broker_only` 也只用于 conformance profile，不等于生产准入。

回退只需停止两个 evaluation profile 并保持两个开关为 false；生产 Server、历史
checkpoint、Workspace、Evidence 和 operation 无需迁移或删除。供应链与许可证资料见
`server/coding_worker/EVALUATION_NOTICES.md` 和
`server/coding_worker/evaluation_sbom.cdx.json`。
