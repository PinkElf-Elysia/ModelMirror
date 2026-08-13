# MCP Wave 28—31 收口

最后更新：2026-08-13

## 结果

- Wave 28：`greptimeteam-greptimedb-mcp-server` 晋级 ready。复用数据库 sidecar，只开放固定表描述、固定时间/数值列范围读取和常量健康检查；任意 SQL/TQL、写入、管理与动态资源不可发现。
- Wave 29：`takashiishida-arxiv-latex-mcp` 晋级 ready。只接受规范 arXiv ID，固定访问 `export.arxiv.org`，四项工具仅解析有界内存源包；不落盘、不编译 TeX、不加载外部资源。
- Wave 29：`nameetp-pdfmux` 转为 `blocked-license-runtime-dependency`。PDFMux 1.8.7 自身为 MIT，但固定依赖 PyMuPDF 1.27.2.3 和 pymupdf4llm 0.3.4，二者要求 AGPL-3.0 或 Artifex 商业许可；当前无商业许可证据，镜像不包含这些依赖。
- Wave 30：`victoriametrics-community-mcp-victoriametrics` 晋级 ready。复用数据库 sidecar，只允许固定目标、固定 metric 的 metrics、labels、instant query 与最多 24 小时的 range query。
- Wave 31：YFinance 因第三方金融数据条款不可证明转 blocked；重复 YouTube 实现转 superseded blocked；OpenDocsWork 因发布物许可证元数据冲突转 blocked。PatSnap 保持 planned，不以高风险候选补足 ready 数。

目录最终为 `79 ready / 61 planned / 160 blocked`；Wave 24 的 100 项子目录为 `8 ready / 34 planned / 58 blocked`。

## 运行证据

- arXiv LaTeX：真实读取 `1706.03762`，得到 25 个章节；initialize、tools/list、冻结 Schema、代表调用、超时、同 host 单次重定向、会话断开和 PID1 清理通过。验收镜像 manifest list 为 `sha256:3f172ab9e8e49d2394bf1957262da2475e9223d216e91c1e2ef9d5cc5a520a8a`。
- GreptimeDB：沿用 Wave 27 已验收的官方 v1.1.4 服务、原生 `ro` 账号、两轮 UDS 调用、拒写、provider timeout 与精确清理证据，并在本批重新冻结目录与 allowlist 契约。
- VictoriaMetrics：以官方 v1.148.0 镜像完成真实 metrics/labels/instant/range 代表调用、写工具不可发现、超时、会话断开与清理；服务镜像只用于隔离验收，不进入 ModelMirror sidecar。最终数据库 sidecar 验收镜像 manifest list 为 `sha256:f83d497ce543b9075be904f2c9a74887c8ee52f9e664402ad902e24488ee32ff`。
- 自动化回归：MCP 全集 `468 passed / 14 skipped`；后端 CI 命令 `3090 passed / 29 skipped`；前端 typecheck 与生产构建通过。前端测试为 `292 passed / 1 failed`，唯一失败是最新 `main` 同步引入且与本分支无文件交集的 `NodePalette.test.ts` NodeContract V3 测试替身缺口；同一 `main` SHA 的 GitHub Quality 也为 Frontend quality 失败，而 Backend quality 与 Windows Project Host 通过。Compose 解析与 `git diff --check` 通过。
- 429 映射、Schema 漂移、任意参数、Secret 脱敏、私网目标、跨项目与输出上限由定向自动化覆盖；没有把模拟的 429 描述成真实供应商限流事件。

## 安全与回退

- 公共读取只重试幂等 read；数据库查询由 sidecar 生成，客户端不能提交 DSN、SQL、PromQL、Header 或动态 endpoint。
- 凭据只从服务端加密槽解析；日志、目录 JSON 与前端不包含明文 Secret。
- 回退无需数据迁移：从精确 Compose/proxy allowlist 移除对应 ID，将生成目录状态恢复为 planned/blocked，并断开现有目录会话。
- Wave 21 状态化资源、Wave 22 多租户/OAuth、任意执行、桌面宿主、云资源写入、账号发布、交易与设备控制继续冻结。
