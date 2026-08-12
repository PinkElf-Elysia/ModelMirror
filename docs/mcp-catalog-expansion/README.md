# MCP 双源目录审计数据

本目录保存第二阶段目录扩充的审查、批准与适配判定快照。100 项候选已经逐项归入
`ready`、`planned` 或 `blocked`，当前精确为 **26 ready / 13 planned / 61 blocked**。
批次 14—15 的五项固定只读子集复用 Token Sidecar；批次 16A 的 DuckDuckGo、shadcn/ui
与 Docker Hub、批次 16B 的 BioMCP 与 SafeDep Vet、批次 17A 的 open-webSearch、Idea Reality 与
GitMCP 复用匿名公共读取 sidecar。批次 18A—18B 的六个确定性文件产物/分析兼容层已通过
隔离镜像与人工验收并进入文件 sidecar 精确 allowlist；批次 19A 的 Prometheus、Qdrant 与
Elasticsearch 已通过真实只读服务验收和用户验收并进入数据库 sidecar 精确 allowlist。
批次 17B 的四个 Token 数据适配器与批次 19B 的三个图与向量库，共七项仍为默认关闭的
staged 候选；其余 70 项只有展示资料和非执行 manifest，不含命令、
端点、凭据槽、工具策略或功能开关绕过路径。

## 固定来源

- `yzfly/Awesome-MCP-ZH@b29e114d95fa26338b092423fd1ede1e5598e4df`
  - README SHA-256：`854802528cb508a6f6d00e2d142b57a44bc5393bfd4321ddd96e1e9a2b10b51a`
- `punkpeye/awesome-mcp-servers@cbcdf8f7700cfe4c0ef9aeb232f64aeebe8a184c`
  - README SHA-256：`d7012abf5a5019f2ff0b66dff3832b2b0c1e8c9dd672f382f3ae677d3b878874`

上游 README 不提交到本仓库。原始解析结果 `source-inventory.json` 和完整公开 GitHub
元数据 `github-metadata.json` 是可再生成的本地中间文件，已由本目录 `.gitignore` 排除。
仓库只保存经过硬门禁和配额选择的 `review-candidates.json`。

## 可重复生成

先从两个固定提交下载 README，再执行：

```powershell
python scripts/mcp_catalog_audit.py `
  --awesome-mcp-zh C:\tmp\awesome-mcp-zh-b29e114d.md `
  --awesome-mcp-servers C:\tmp\awesome-mcp-servers-cbcdf8f.md `
  --current-catalog client\src\data\mcpProjects.ts `
  --output docs\mcp-catalog-expansion\source-inventory.json

python scripts/mcp_catalog_github_enrich.py `
  --inventory docs\mcp-catalog-expansion\source-inventory.json `
  --metadata-output docs\mcp-catalog-expansion\github-metadata.json `
  --review-output docs\mcp-catalog-expansion\review-candidates.json `
  --report-output docs\MCP_CATALOG_EXPANSION_REVIEW.md
```

第二条命令使用已认证的 `gh api graphql` 只读查询公开仓库元数据。CI 不运行网络查询；
测试只验证解析器、筛选规则和已提交审查清单的静态契约。

人工批准后，执行完全离线的目录集成生成器：

```powershell
python scripts/mcp_catalog_integrate_approved.py
python scripts/mcp_catalog_integrate_approved.py --check
```

生成器固定产出 `client/src/data/mcpCatalogExpansionV2.generated.ts`、
`server/mcp/catalog_expansion_v2.py` 和适配判定报告，并把人工决定写回审查快照。生成结果
本身不含运行命令、MCP 端点、凭据字段或工具 Schema；22 个 ready 项的私有执行契约由
`server/mcp/catalog.py` 与对应公共、Token、文件或数据库 Sidecar 单独维护。

## 人工门禁

`review-candidates.json` 中每项均有固定 `catalog_id`、`decision: approved`、明确的
`proposed_availability` 与 `decision_reason_code`。快照不保存命令、端点、凭据字段或
`executable` 标记；当前分布由生成器强制断言为 22/20/58。

后续从 `planned` 进入适配时，仍必须逐项核对 MCP Server 身份、锁定版本、安装与传输
契约、工具副作用和安全前置条件。只有固定工具契约及相应隔离、凭据、审批、限流和真实
代表调用全部通过，单个条目才可以在独立适配 PR 中变为 `ready`。
