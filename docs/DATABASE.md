# 数据模型与存储方案

最后更新日期：2026-07-28
维护人：模镜团队

## 当前结论

模镜已经包含多种本地持久化实现，不能再描述为“没有自建数据库”。当前是
本地单租户、按领域选择存储的架构，并未形成统一关系数据库或多租户数据层。

| 领域 | 存储 | 说明 |
| --- | --- | --- |
| 模型静态快照 | `client/src/data/models.ts` | 展示、能力与离线目录；实时可调用性来自后端目录。 |
| 模型路由与视频任务 | `server/model_router/storage/router.sqlite3` | 连接、策略、候选统计、决策、压缩与视频任务元数据。 |
| 路由凭据 | SQLite 加密字段 + `credential-master.key` | 只在后端解密，API 返回脱敏摘要。 |
| RAG 元数据与流水线 | `server/rag/storage/` | metadata、候选/激活版本、处理和视觉产物。 |
| RAG 向量 | Chroma，失败时可使用本地 JSON fallback | 由 `CHROMA_DB_PATH` 配置。 |
| RAG 全文 | `lexical_index.sqlite3` / SQLite FTS5 | 与向量索引组成混合检索。 |
| Agent Studio | `server/xperts/storage/` 文件型 Store | 草稿 revision、不可变发布版本、App 和文件记忆。 |
| Agent Runtime | `server/xpert_runtime/storage/` 文件型 Store | Goal、Handoff、自动化、审批和执行恢复。 |
| Toolset | `server/toolsets/storage/` | Toolset 版本、凭据摘要和本地 master key。 |
| Data X | 项目元数据 + 项目隔离 DuckDB | 文件快照、语义模型和受限查询。 |
| 工作流草稿 | 浏览器 `localStorage` | classic 画布本地草稿；发布 Agent 时进入后端不可变版本。 |
| 媒体正文 | 不持久化 | STT/TTS/视频请求媒体及视频生成 Prompt、首帧不进入任务数据库。 |

Dify 数据库不属于当前主路径或备份范围。legacy Dify 代理只在显式配置后可用。

## 租户边界

- 原生路由、连接、决策、压缩和视频任务从第一天携带
  `tenant_id`，当前固定为 `local`。
- 文件型 Agent/Runtime Store 仍是可信本地管理面，不等同于完整多租户隔离。
- 所有 repository 查询必须显式携带租户；不得在前端传入任意租户覆盖。
- 进入多租户前必须完成用户身份、组织、RBAC、密钥托管和数据迁移设计。

## 备份范围

使用默认 Compose bind mount 时，应备份：

```text
new-api-data/
server/model_router/storage/
server/rag/storage/
server/rag/uploads/
server/xperts/storage/
server/xpert_runtime/storage/
server/datax/storage/
server/toolsets/storage/
server/skills/installed/
```

不要只备份 JSON 而遗漏 SQLite、DuckDB、Chroma 目录或对应 master key。没有
`credential-master.key` 时，加密凭据不能恢复。

备份前建议停止写入或停止 `server`，避免跨文件快照不一致。恢复后先运行健康检查，
再抽查 RAG active version、Agent 发布版本、路由连接和视频任务状态。

## 数据更新原则

- 不提交上述持久化目录、上传文件、数据库或本地密钥。
- 静态目录、UI 和数据迁移分开提交。
- schema 变更必须具备向前迁移、旧数据读取测试和不删除数据的回退说明。
- 价格、模型能力和外部仓库元数据应记录快照日期。
- 任务审计只保存必要元数据，不保存 Prompt、媒体正文、完整工具结果或凭据。

## PostgreSQL 迁移方向

SQLite repository 已隔离于路由服务实现，可在多租户阶段增加 PostgreSQL adapter。
迁移顺序建议为：

1. 身份、租户与权限。
2. 连接、策略、任务和审计。
3. Agent/Runtime 文件型 Store。
4. 需要集中查询的 RAG/Data X 元数据。

Chroma、对象正文和项目 DuckDB 是否迁移，应按检索规模、保留策略和部署拓扑
单独决策，不能通过一次通用数据库替换解决。
