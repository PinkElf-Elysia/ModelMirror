# OmniRoute 侧车集成

OmniRoute 作为可选路由侧车和回退路径接入“模型招聘会”。默认聊天仍走
newAPI/OpenRouter；`auto/*` 的实际引擎由本地策略决定，可处于
`sidecar → shadow → native_canary → native`。当前原生阶段 0–4 的实现、
用户契约和门禁见 [MODEL_ROUTER_NATIVE.md](./MODEL_ROUTER_NATIVE.md)。

> **进度冻结（2026-07-28）**：本地路由仅完成初步验收，尚未定级为稳定
> 原生实现。暂停一切原生增量和新的 OmniRoute 行为对齐；仅继续缺陷修复
> 与回归验证。侧车必须保留为兼容层、能力验证环境和故障隔离边界，
> `native` 不得设为默认。冻结期间转向与本模块解耦的其他产品优化，后续
> 是否继续原生化必须由项目维护人完成全面检验后明确决定。

长期方向仍是从侧车验证逐步收敛到稳定的 ModelMirror 本地原生实现，但这
只是保留的架构方向，不代表当前排期、稳定性承诺或删除侧车的授权。

## 供应链固定

- 运行镜像：
  `diegosouzapw/omniroute:3.8.48@sha256:badb560971fdc23c2fb84b3e8695116239ff215b4cca4b07076201a8efae7f0d`
- 接口契约审计基线：`release/v3.8.49`，commit `36f8fd10052f`
- 许可和差异说明见仓库根目录 `THIRD_PARTY_NOTICES.md`

上游没有发布 `3.8.49` Docker Hub 镜像，因此运行时暂固定在已发布的
`3.8.48` 摘要；不得把 tag 改成 `latest`。

## 启用

1. 从 `.env.example` 复制并生成四个互不相同的高熵值：
   `OMNIROUTE_JWT_SECRET`、`OMNIROUTE_API_KEY_SECRET`、
   `OMNIROUTE_INITIAL_PASSWORD`、`OMNIROUTE_STORAGE_ENCRYPTION_KEY`。
   生产模式还必须生成独立的 `OMNIROUTE_WS_BRIDGE_SECRET`。JWT 和 WebSocket
   密钥至少 32 字符，API Key 密钥至少 16 字符；禁止使用常见弱口令。
2. 拉取固定摘要，并在启用前生成或扫描 SBOM：

   ```powershell
   docker pull diegosouzapw/omniroute:3.8.48@sha256:badb560971fdc23c2fb84b3e8695116239ff215b4cca4b07076201a8efae7f0d
   docker sbom diegosouzapw/omniroute:3.8.48
   docker scout cves diegosouzapw/omniroute:3.8.48
   ```

3. 启动侧车：

   ```powershell
   docker compose -p modelmirror --profile omniroute up -d omniroute
   ```

4. 打开本机 `http://127.0.0.1:20128`，完成供应商端点配置，并创建只供
   ModelMirror 使用的 API Key。该 Key 只允许模型目录、聊天和自动路由，
   不授予管理权限，并设置模型、端点及预算上限。
5. 把专用 Key 写入后端运行环境的 `OMNIROUTE_API_KEY`，设置
   `OMNIROUTE_ENABLED=true`，重新创建 `server` 服务。
6. 验证：

   ```powershell
   curl.exe http://localhost:8000/api/models/router-status
   curl.exe http://localhost:8000/api/models/catalog
   ```

控制台只绑定 `127.0.0.1`。不得把控制台端口公开到公网，也不得将
OmniRoute 或供应商密钥写入前端环境变量。

## 状态和降级

- `online`：目录为 30 秒内实时结果。
- `stale`：上游失败，使用最近一次成功目录，最多保留 10 分钟。
- `offline`：侧车不可达且没有可用缓存。静态资料仍可浏览，但不可作为
  OmniRoute 在线候选人调用。
- `disabled`：未启用侧车。现有静态目录及默认网关行为保持不变。

`auto/*` 请求失败时不会跨网关静默回退；供应商级回退由 OmniRoute
内部完成。路由回执只暴露允许名单内的模型、供应商、成本、延迟和请求
标识，不透传内部决策体。

`MODEL_ROUTER_DEFAULT=omniroute` 只为仍以 `gateway=default` 调用的
`auto/*` 兼容请求选择侧车。用户显式选择的模型 ID 始终走统一
newAPI/OpenRouter 网关；否则 `poolside/...`、`xiaomi/...` 等 OpenRouter
发布者前缀会被侧车误解为需要独立凭据的本地供应商。

若本地 newAPI 明确返回 `model_not_found` 或 `No available channel for model`，
且后端已配置 `OPENROUTER_API_KEY`，显式模型请求会在正文输出前使用同一个
模型 ID 有限回退到 OpenRouter。普通 `503`、Batch 契约错误和已经开始输出的
响应不会触发该回退；成功回退后的计费与数据处理遵循 OpenRouter 连接配置。

运行时 `3.8.48` 不提供 `release/v3.8.49` 文档中的 API Key 范围
`/v1/auto-combo/{channel}/candidates` 接口。适配器因此以同一次
`/v1/models?configuredOnly=true` 返回的 `auto/*` 模型作为可调用性依据：
已广告的精选路由标记为 `live`，候选数保持未知，不再因候选管理接口 404
误标为 `degraded`；认证、权限或上游服务错误仍会关闭对应路由并明确降级。

### 3.8.48 路由兼容与空流护栏

本轮真实故障演练确认了两个不能只靠接口文档推断的版本差异：

- `3.8.48` 虽会接收 `X-OmniRoute-Mode`，但实测仍把 `model=auto`
  记录为 `Zero-config routing variant: default`。适配器因此同时保留请求头并
  把 ModelMirror 模式映射到已发布的调用别名：`fast → auto/fast`、
  `quality → auto/smart`、`cheap → auto/cheap`、
  `reliable → auto/lkgp`、`offline → auto/offline`。这是协议兼容映射，
  不复制 OmniRoute 的候选评分算法。
- `3.8.48` 实测不会执行逐请求预算头。默认
  `OMNIROUTE_BUDGET_HEADERS_ENABLED=false`，只要请求携带预算，ModelMirror
  就返回明确的 `501`，避免把未执行的严格预算伪装成安全约束。只有固定并
  验证支持该契约的 `3.8.49+` 镜像、通过极小预算 `402` 冒烟检查后，才能
  显式改为 `true`。

OmniRoute 的匿名/no-auth 供应商可能在未完成本机认证时仍返回 HTTP 200、
零 token 和只有 `[DONE]` 的空流，随后被 Last Known Good Provider 反复
复用。ModelMirror 现在把这种结果转换为明确错误，不再显示“模型没有返回
内容”的假成功。生产验收还必须在 OmniRoute 的“Providers / No Auth”
区域停用未认证供应商，并把 ModelMirror 专用 API Key 的
`allowedConnections` 限制到已认证且测试通过的连接。

若只有一个已认证连接，例如只有 `openrouter/auto`，不同 auto 变体可以进入
不同的 OmniRoute 策略入口，但候选池仍只有一个目标，不能承诺一定选择不同
模型。要验收策略差异，至少配置两个独立、已认证且健康的候选连接，或在
OmniRoute 内建立多个边界清晰的候选组合；ModelMirror 不在本地伪造差异。

## 本轮复盘

本轮侧车接入验证了目录、自动路由和状态探测的技术可行性，但产品层出现了
三个需要优先纠正的问题：

- **UI 割裂**：上游模型标识、别名和路径直接进入招聘会后，模型名称变得
  难以理解；新增调度控件也没有沿用模型联邦卡片和聊天页的既有交互语言。
  路由能力不应改变展示 ID、卡片视觉层级或普通聊天流程。模型联邦卡片继续
  保留原有 UI，只把入口指向 `auto`。
- **单租户倾向**：全局 API Key、全局路由配置和管理员控制台适合本地单
  操作者验证，却没有为未来的租户级凭据、策略、预算、配额和审计留出清晰
  边界。侧车控制台不能直接成为面向最终用户的多租户产品界面。
- **配置复杂**：环境变量、初始密钥、供应商端点、权限和侧车生命周期要求
  普通用户理解过多基础设施概念。默认路径必须做到无需 OmniRoute 知识即可
  使用，进阶配置应转化为分步引导、连通性测试、可解释默认值和明确错误。
- **版本契约不能由 tag 文档代替运行验证**：`release/v3.8.49` 文档中的
  路由和预算头不能直接视为 `3.8.48` 镜像已实现。今后每次固定或升级镜像
  都必须保存“模式别名、严格预算、空流、最终 SSE telemetry”四类冒烟结果。
- **候选健康不能只看 HTTP 状态**：上游返回 200 不等于生成了可用回答。
  Token、正文或多模态输出必须至少有一项可观测，空成功要计入失败并退出
  LKGP 候选；专用 key 还必须用连接 allowlist 缩小故障面。

同时确认一条兼容性红线：路由选择必须与聊天能力解耦。除 `auto` 专属页面
外，普通模型聊天、知识库组合、专家团、工作流和 RAG 应继续遵循 ModelMirror
原有契约，不能因接入新的路由来源而丢失能力或改变入口。上游调用 ID 只能
存在于适配和调用边界，不得直接替代面向用户的模型名称与资料 ID。

## 本地原生演进路线

后续不整体搬运 OmniRoute，也不机械复制其控制台或内部架构。仅在 MIT 许可
和供应链审计通过后，选择可独立测试、边界清晰且能直接提升用户价值的模块或
算法思路；复制实质性代码时保留原版权、许可头、上游路径和固定 commit，并
增加行为一致性测试。实现方式优先贴近 ModelMirror 现有的 FastAPI 服务边界、
前端模型资料结构、聊天契约和设置体验。

按以下顺序推进，每一阶段都必须完成“实现—测试—小范围启用—故障演练—
回退验证”的闭环，未通过验收不得提前进入下一阶段：

1. **恢复产品基线**：招聘会和非 `auto` 聊天页回到原有信息架构与交互，
   修复知识库组合能力；建立展示 ID、调用 ID、供应商别名的规范化边界。
   验收标准是关闭侧车后全部稳定路径无回归。
2. **原生目录与健康状态**：先本地实现模型目录归一化、缓存、可调用性和
   端点健康状态。侧车只作为可选数据源，任何上游路径都先映射成用户可读名称。
   验收标准是在线、缓存、离线三种状态可解释且静态浏览始终可用。
3. **原生 `auto` 路由最小闭环**：在模型联邦原卡片后接入候选筛选、健康
   熔断和有限重试，只服务 `auto`，不侵入普通模型聊天。可优先评估复用
   OmniRoute 中纯函数式、无私有 workspace 依赖的候选排序或熔断模块。
   验收标准是选择结果可解释、失败可观察，并能一键切回默认网关。
4. **预算、回执与策略治理**：在稳定路由之后再加入成本估算、预算约束、
   路由回执和可审计策略。内部契约从第一天携带 `tenant_id`，凭据引用、
   路由策略、预算、配额、用量和审计记录均按租户隔离；本地单租户仅是默认
   租户的特例。验收标准包含严格预算不超支和跨租户不可见。
5. **普通用户配置闭环**：用 ModelMirror 设置页提供供应商连接向导、预检、
   推荐默认值、错误修复建议和安全的凭据托管；高级控制台只保留为开发诊断
   工具。完成迁移与故障演练后，再评估默认关闭或移除生产侧车。

每个候选复用模块都必须同时满足四项条件：解决当前明确痛点、可脱离
OmniRoute 运行、能纳入 ModelMirror 的测试与权限边界、对未来多租户没有
结构性阻碍。否则只参考行为和接口，不复制实现。

## 侧车回退

原生调度紧急回退时先显式设置 `MODEL_ROUTER_ENGINE=sidecar` 并重新创建
后端。该值会只读覆盖 SQLite 策略，不删除任何原生记录：

```powershell
docker compose -p modelmirror up -d --force-recreate server
curl.exe http://localhost:8000/api/router/status
```

若需要完全关闭 auto 侧车能力，再设置 `OMNIROUTE_ENABLED=false`、
`MODEL_ROUTER_DEFAULT=newapi`，重新创建后端并停止侧车：

```powershell
docker compose -p modelmirror --profile omniroute stop omniroute
```

该操作不迁移或修改 ModelMirror 数据库。OmniRoute 的持久化数据仍保留在
`omniroute-data` volume 中，可在复盘后单独处理。
