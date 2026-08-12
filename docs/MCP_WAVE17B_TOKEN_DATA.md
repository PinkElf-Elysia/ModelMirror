# MCP 第 17B 批：Token 数据适配

## 当前结论

Google Maps、Opik 与 Keboola 已完成固定只读 facade、工具 Schema 冻结和默认关闭接线，
但缺少真实账号只读预检，因此继续保持 `planned`，且未加入 `MCP_TOKEN_ALLOWED_ADAPTERS`。
Vectorize 0.4.3 的仓库 `LICENSE` 为 MIT、package metadata 为 ISC；截至 2026-08-11 的最新官方
发布仍未消除冲突，因此已转为 `blocked-license-metadata-conflict`，并从 proxy、sidecar contract、
Schema 与 builder 中移除，不再保留可执行入口。

| 目录 ID | 审阅上游 | 只读工具 | 固定出口 | 服务端配置 |
|---|---|---|---|---|
| `cablate-mcp-google-map` | v0.0.53 / `6c34d268126a31390f2e236d888b0a00fed59f11` / MIT | `maps_search_places`, `maps_place_details` | `places.googleapis.com` | `api_key` |
| `vectorize-io-vectorize-mcp-server` | 0.4.3 / `bea6442bf77165ff26fc66fb4107b741811ae1a9` / LICENSE MIT、package metadata ISC | blocked；无工具 | 无运行时出口 | 无配置或凭据槽 |
| `comet-ml-opik-mcp` | 0.2.15 / `8ce6f3375068768adca3df2f804f12f0213dbb65` / Apache-2.0 | `list`, `read` | `www.comet.com` | `api_key`, `workspace` |
| `keboola-keboola-mcp-server` | v1.75.2 / `653118bc42fab00fe0268b6feaf4c4ad032dbf7c` / MIT | `get_project_info`, `get_buckets`, `get_tables` | `connection.keboola.com` | `storage_token` |

## 冻结边界

- Google Maps 只调用 Places API New，强制固定 FieldMask、最多 10 个地点；评论、照片、路线、
  静态地图、天气、空气质量和写入均不可发现。
- Vectorize 的许可证元数据冲突按 fail-closed 处理：目录仅保留展示身份，运行时不再接受该 ID，
  也没有工具、Schema、Host、配置字段或凭据槽。只有固定发布物许可证一致或上游提供明确官方
  说明后，才能重新进入验收。
- Opik 只保留官方 0.2.15 的 `list` / `read` 产品身份，并把实体类型收窄为 project、trace、
  test_suite、experiment、prompt；`write`、`schema`、`ask_ollie`、`run_experiment` 全部关闭。
- Keboola 首版只支持固定美国生产栈的项目 Token 预检、bucket 和 table 元数据；SQL、Job、
  Component、Flow、OAuth、共享 bucket 链接和所有更新工具全部关闭。
- 客户端不能提交 URL、Host、Header、环境变量、MCP endpoint 或命令；凭据只通过项目级加密槽
  注入。每次响应仍受 256 KiB 上限和 Token sidecar 的 DNS pinning / 公网 HTTPS 策略约束。

## Schema 摘要

- Google Maps: `186785bce37ec786aa86bfa2b3fdfeb6918633eb309e0000de8d291d7a7650a6`
- Opik: `084588762fe49f9cc6be8c82e4e1b6a4eb2fc361cbf9156b792465a49d7d50b9`
- Keboola: `fc72f9c337b51f7ae45c6bb566256e6a7e163f98635df6bc406f15d11f027f3c`

## 晋级与回退

Google Maps、Opik 与 Keboola 必须分别提供对应只读账号并完成一次真实预检和代表调用，才可
单独从 `planned` 晋级 `ready` 并加入精确默认 allowlist。Vectorize 只能在许可证冲突消除后重新
立项；本批不引入数据迁移，也不需要启动共享栈。
