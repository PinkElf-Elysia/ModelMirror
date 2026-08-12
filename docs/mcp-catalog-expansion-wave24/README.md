# Wave 24 双源增量审阅快照

本目录保存第三阶段 Wave 24 的 100 项只读人工审阅候选。它与第二阶段 100 项使用同一组冻结
上游提交，因为 2026-08-11 复核时两个源仓库的 HEAD 未变化；GitHub 仓库元数据则已在当日
重新查询。

当前文件不进入产品目录，不包含命令、端点、Header、环境变量、凭据槽、工具策略、allowlist
或可执行标记。全部候选仍为 `pending-human-review`，建议状态仅为 `planned`。人工批准具体清单后，
才允许生成第三阶段的非执行 manifest。

## 固定来源

- `yzfly/Awesome-MCP-ZH@b29e114d95fa26338b092423fd1ede1e5598e4df`
  - README SHA-256：`854802528cb508a6f6d00e2d142b57a44bc5393bfd4321ddd96e1e9a2b10b51a`
- `punkpeye/awesome-mcp-servers@cbcdf8f7700cfe4c0ef9aeb232f64aeebe8a184c`
  - README SHA-256：`d7012abf5a5019f2ff0b66dff3832b2b0c1e8c9dd672f382f3ae677d3b878874`

## 结果边界

- 精确 100 项，两个来源各至少命中 25 项。
- 覆盖至少 10 个分类；单分类不超过 15 项；单仓库不超过 2 项。
- 与现有 200 项按规范仓库、GitHub 重定向和子路径身份零重叠。
- 仓库公开、非 fork/归档/禁用/私有，SPDX 明确，且 2025-08-11 后仍有维护活动。
- 必须有独立 MCP Server 身份；目录、客户端、动态网关、聚合器及 deprecated 实现被排除。

`source-inventory.json` 与 `github-metadata.json` 是已忽略的本地再生成中间文件。提交范围只包括
本 README、`review-candidates.json` 和对应审阅报告。

## 再生成

```powershell
python scripts/mcp_catalog_audit.py `
  --awesome-mcp-zh C:\tmp\awesome-mcp-zh-b29e114d.md `
  --awesome-mcp-servers C:\tmp\awesome-mcp-servers-cbcdf8f.md `
  --current-catalog client\src\data\mcpProjects.ts `
  --current-catalog client\src\data\mcpCatalogExpansionV2.generated.ts `
  --output docs\mcp-catalog-expansion-wave24\source-inventory.json

python scripts/mcp_catalog_github_enrich.py `
  --inventory docs\mcp-catalog-expansion-wave24\source-inventory.json `
  --metadata-output docs\mcp-catalog-expansion-wave24\github-metadata.json `
  --review-output docs\mcp-catalog-expansion-wave24\review-candidates.json `
  --report-output docs\MCP_CATALOG_EXPANSION_WAVE24_REVIEW.md
```

第二条命令只读取 GitHub 公开仓库元数据；CI 不执行网络查询。
