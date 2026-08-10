# MCP 双源目录审计数据

本目录保存第二阶段目录扩充的人工门禁数据，不是运行时配置。当前提交只生成候选清单，
不会改变 `/api/mcp/catalog/adapters`、前端 100 项目录、后端 manifest、功能开关或共享栈。

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

## 人工门禁

`review-candidates.json` 中每项当前均为：

- `decision: pending-human-review`
- `proposed_availability: planned`
- 无命令、端点、凭据字段或 `executable` 标记

下一步必须逐项核对 MCP Server 身份、安装与传输契约、用途、分类及安全前置条件。
失败项从同一候补池按原评分与配额规则递补。只有人工确认后的 100 项才能进入单独的目录
集成 PR，且仍只能是 `planned` 或有固定原因的 `blocked`。
