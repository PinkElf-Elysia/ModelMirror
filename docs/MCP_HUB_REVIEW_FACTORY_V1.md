# MCP Hub Review Factory V1

## 目标与信任边界

Review Factory 把官方 MCP Registry 中的匿名、固定公网 HTTPS、Streamable
HTTP、tools-only 候选转化为可审计的执行契约。目标是复用自动化复核 SOP，
不是自动把 Registry 记录判定为安全，也不是增加通用 MCP 执行器。

Factory 仅面向可信的本地部署运维者。当前 `tenant_id` / `owner_id` 是部署时
固定的本地主体，不构成多租户管理员、RBAC 或共享 SaaS 隔离。客户端只能提交
Registry 的 `server_name`、`version` 和 `remote_id`；URL、Header、环境变量、
凭据、命令、工具参数和动态 endpoint 都不能进入复核接口。

Runtime 只读取已发布且未撤销的 `HubReviewedContractV1`。Registry 元数据、远程
annotations、临时预检结果和人工备注都不是执行信任来源。

## 开关与回退

```dotenv
MCP_HUB_REVIEW_FACTORY_ENABLED=false
MCP_HUB_LOCAL_CONTRACT_PUBLISH_ENABLED=false
MCP_HUB_CONTRACT_SIGNING_KEY=
```

- 三项默认关闭；Factory 开关只显示/运行复核，不隐式开启 Hub 或远程桥。
- 未开启本机发布时仍可生成证据、决定和仓库契约导出。
- 签名密钥缺失不会阻止主服务启动，但本地发布和已有本地契约全部 fail closed。
- 回退时关闭前两个开关。本地 revision、证据和 Registry 缓存保留用于审计，
  现有仓库契约继续工作。

## 稳定模型与指纹

- `HubCandidateSnapshotV1` 冻结 Registry 身份、版本、remote、Origin、transport、
  publisher 和 source digest。
- `HubEvidenceBundleV1` 冻结网络/能力/Schema/effect/代表调用摘要与清理断言。
- `HubReviewedContractV1` 冻结完整工具集合、允许工具子集、逐工具 `read` effect、
  资源限制、evidence digest 和 SOP 版本。

`contract_id` 只由规范化的 server/version/URL 身份稳定生成。执行字段使用排序、
无空白的 canonical JSON 计算 `contract_fingerprint`；evidence digest、发布时间和
展示备注不进入执行指纹。evidence digest 仍保留在契约 revision 中并由 HMAC 签名，
但同一冻结执行权限的再次真实复核不会因只读结果变化制造永久碰撞。仓库契约与
本地契约使用同一 Schema：同一身份出现不同执行指纹时返回
`hub_contract_collision`，不会采用“本地优先”或“最新优先”。

本地 revision 是 SQLite 中的不可变记录，以外部密钥做 HMAC-SHA256。错误密钥、
缺失密钥或签名不匹配的 revision 不会加载。本地撤销是覆盖层；再次发布同一
指纹会写入新 revision 并解除撤销，不修改历史记录。

Qt Documentation 0.2.0 的旧硬编码放行已迁移为仓库只读契约文件，原完整
Schema digest 和逐工具 digest 均未改变。迁移契约保留 V1 时的 source 兼容；
候选仍必须命中当前 Registry 的 source/remote，Factory 新发布契约则精确冻结
source digest。

## `anonymous_https_tools_v1` SOP

| 阶段 | 自动恢复 | 说明 |
| --- | --- | --- |
| snapshot | 是 | 从服务端 Registry 快照冻结身份 |
| static_policy | 是 | 检查 active/latest 与静态匿名 HTTPS 准入 |
| network_preflight | 否 | sidecar DNS/出口/capability 门禁 |
| initialize | 是 | 官方 SDK initialize |
| capability_check | 否 | 仅接受静态 tools 能力 |
| tools_list | 是 | 有界 tools/list |
| schema_freeze | 否 | 完整集合和逐工具 digest |
| effect_proposal | 否 | 确定性风险候选分类 |
| call_proposal | 否 | 生成不可变代表调用提案 |
| call_approval | 否 | 本地运维者逐次批准提案摘要 |
| representative_call | 否 | ledger `started` 后只调用一次 |
| cleanup | 否 | 关闭临时会话并撤销 capability |
| human_decision | 否 | 绑定 evidence digest 和只读工具子集 |
| contract_publish | 否 | 绑定 expected contract fingerprint |

Registry 读取、静态检查、initialize 和 tools/list 是唯一可安全恢复的阶段。
代表调用写入 `started` 后遇到超时、断链或服务重启统一为 `unknown_outcome`，
候选污染并销毁会话；原提案不可重放。

## 状态与资源上限

单 owner 同时最多一个活动批次，每批 1–20 项，并发处理最多 2 项，每候选只用
一个临时会话。单项异常不回滚其他项，也不会发布不完整契约。

```text
queued → running → evidence_ready → awaiting_call_approval
       → awaiting_decision → approved → published

blocked / failed / interrupted / unknown_outcome / cancelled / drifted / revoked
```

单项证据最多 512 KiB 和 200 个固定事件。代表调用只持久化参数摘要、结果
digest、大小、结构类型和断言；最多 4 KiB 的脱敏结果预览只在当前 HTTP 响应
返回，不写入数据库。远程正文、无限日志、Secret 和物理路径不得进入证据。

## 代表调用门禁

提案仅从对象 Schema 中确定性生成：必填字段最多 3 个，只支持 bounded string、
enum、integer、boolean；URL/path/file/command/header/token/secret/password/account/
publish/delete/trade/device 等字段直接拒绝。系统使用固定探针字符串、最小整数、
首个 enum 和 `false`，不填可选字段。

没有安全提案的候选进入 `manual_call_unavailable`，V1 不允许发布。没有通用工具
调用 API，客户端给提案端点附带 arguments 会返回
`hub_review_arbitrary_arguments_denied`。

自动 effect 分类只有 `read_candidate / artifact_candidate /
state_write_candidate / dangerous_candidate / unknown`。只有本地运维者最终确认为
`read` 的冻结子集可进入契约；Runtime 仍固定要求逐次审批、敏感、非只读、不可
并行、不可用于 Public App、不可重试。

## API 与持久化

Review API 位于 `/api/mcp/hub/reviews/status`、`/api/mcp/hub/review-runs...` 和
`/api/mcp/hub/contracts...`。所有写接口使用当前固定 tenant/owner，Pydantic
模型拒绝多余字段；服务端不写 Git、不提交 PR。

`hub.sqlite3` 只新增以下表，不修改既有 Registry、candidate 或 execution ledger：

- `hub_review_runs`
- `hub_review_items`
- `hub_review_stage_events`
- `hub_review_call_proposals`
- `hub_review_call_ledger`
- `hub_local_contract_revisions`
- `hub_contract_revocations`

契约导出是 canonical 单契约 JSON，不包含 HMAC、远程原始结果或运维备注；必须
经代码审查后才能进入 `server/mcp/hub_contracts/`。

## 验收门禁

自动门禁覆盖 canonicalization/HMAC/碰撞/撤销、状态机与 owner 隔离、确定性提案、
调用重放与 unknown outcome、工具子集、漂移、证据脱敏、前端批次/批准/发布流程。

真实规模验收必须从固定完整 Registry 快照按种子 `hub-review-factory-v1` 选择 20
项，20 项完成静态分类、至少 5 项完成真实隔离 preflight，并有一个不同 publisher
的新匿名只读候选完成代表调用。若真实候选不满足门禁，规模验收必须保持未通过，
不得用 fixture 制造本地契约。fixture 只用于覆盖超时、429、Schema 漂移、中断
恢复和 `unknown_outcome`。

两个运维验收入口分别承担不同证明，不能互相替代：

```bash
python -m server.tests.smoke_mcp_hub_review_factory \
  --storage-dir /acceptance/storage \
  --export-dir /acceptance/exports \
  --required-selection 20 \
  --required-preflight 5

python -m server.tests.smoke_mcp_hub_runtime_approval \
  --storage-dir /acceptance/storage \
  --server-name <已发布候选的 Registry 名称> \
  --upstream-tool <契约允许的代表工具>
```

第一个入口证明确定性批选、隔离复核、双层发布与导出；第二个入口在没有通用
工具调用 HTTP API 的前提下，走生产 `HubMCPToolsetProvider` 和 HITL middleware，
验证一次显式审批、一次真实调用、`retry_on_failure=false`，以及撤销后会话立即
断开且 Runtime 工具立即消失。两者都只接受已存在的 Registry/契约身份，不接受
URL、Header、环境变量或任意工具参数。
