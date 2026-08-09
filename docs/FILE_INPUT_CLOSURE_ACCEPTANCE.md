# 文件输入闭环 A–H 验收记录

> 状态：H 人工验收已通过；发布门禁已在以下主线基线上重新执行。
>
> 验收工作树：`codex/file-input-closure`
>
> 分支基线：`952f8094c38b29baffa5de3a5b0caa94e501f45f`
>
> 验收前复核主线：`origin/main@380c747e62193855c724a947d99a84070ca623ff`
>
> 发布 rebase 主线：`origin/main@275da0ba5c8f74a993d65022316ae247dedd229b`

## 1. 本轮冻结范围

H 批次只做累计 A–G 的发布前收尾，不新增格式或供应商能力。当前闭环覆盖：

- Chat：逐文件上传、预览、服务端确认、本地提取与显式 OpenRouter 原生 PDF。
- RAG：安全解析、批量上传、来源元数据、文档与整库的隔离优先删除及可重试清理。
- Data X：CSV、XLSX、Parquet 的结构化导入、持久任务与资源护栏。
- Agent：既有 Xpert 文件上下文的本轮显式选择、兼容归档和显式永久清理；
  通用 `/api/files` Agent 上传保持 fail closed，避免产生 Xpert 无法消费的孤立资产。
- Workflow：固定 `workflow:{workflow_id}` 作用域的资产选择、读取与清理。
- DOCX/PPTX：仅由断网、非 root、只读根文件系统的 Office sidecar 静态提取。

以下能力仍明确延期，不在 H 中伪装为可用：

- Chat 一次性视觉/OCR 入口与任何隐式付费 OCR。
- Agent 统一 FileAsset binding 与跨进程运行租约。
- 多 Uvicorn worker 下的 RAG/Workflow 跨进程写入或读取 claim。
- EPUB、EML、MSG、RTF、ODF、旧 Office、宏 Office 和压缩包批量导入。
- 真实模型供应商调用验收；独立预览只使用内网固定 mock。

## 2. 默认开关与回退

生产默认值保持 fail closed：

```text
FILE_ASSET_STORE_MODE=legacy
CHAT_FILE_INPUT_ENABLED=false
WORKFLOW_FILE_ASSETS_ENABLED=false
```

出现回归时先关闭 Chat/Workflow 文件开关并恢复 `legacy` 模式。不要删除
`file_assets` 数据库、scope tombstone 或 cleanup ledger，也不要降级 schema；这些记录用于
阻止删除后的晚写和恢复未完成的物理清理。

## 3. H 门禁证据

### 后端

- rebase 后断网、只读源码挂载全量：`1813 passed, 1 failed, 12 skipped`。
- Agent 统一入口 fail-closed 与既有 Xpert 兼容聚焦回归：`35 passed`。
- H 修复的 Office/Workflow/XLSX 契约集：`81 passed`。
- Batch G 跨模块影响面：`186 passed`。
- RAG 扩大回归：`96 passed`。

唯一失败来自旧验收镜像的 Node 20 不能直接导入 `.ts`；主线 CI 明确使用 Node 22。
本机 Node 24 已与容器 Python 对同一黄金查询自动比对，结果为
`matcher_parity=true`。验收前的 7 个 General Agent Skillset digest 失败已随主线 rebase
清零。

### 前端与契约

- rebase 后 Vitest 全量：`27 files / 128 tests passed`。
- `npm.cmd run build`：通过；仅保留既有大 chunk 警告。
- `node scripts/check-file-readiness.mjs`：通过，`29 formats / 84 operations / 73 ready`。
- `docker compose config --quiet`：通过。
- `git diff --check`：无错误，仅 Windows CRLF 提示。
- 变更清单审计：`98` 个候选路径、`0` 个运行数据/构建产物、`0` 个已暂存路径；
  密钥模式仅命中 2 个带 `test`/`dummy` 标记的固定测试夹具，未发现真实凭据。

### 独立预览

- 地址：`http://127.0.0.1:15174`。
- backend/mock 仅连接 internal network，无宿主端口；Office sidecar 为 `network=none`。
- client 仅绑定 `127.0.0.1:15174`，PID 1 为 uid/gid 1000、`CapEff=0`、无默认路由。
- `OPENROUTER_API_KEY` 为空，LLM gateway 只指向内网固定 mock。
- Agent 通用能力实时返回 `disabled`，统一 `/api/files` 上传返回 `422`；既有 Xpert
  文件入口保持独立兼容，未产生无法消费的统一资产。
- 整库级联验收：删除与重复删除均为 200；资料库、文档与资产均不可访问；
  资产行和 binding 为 0，上传目录已删除，scope tombstone 保留。

## 4. 发布顺序

1. 人工完成 15174 的 Chat、RAG、Data X、Agent、Workflow 验收。
2. 核对并仅暂存文件闭环范围，排除无关工作树变化和运行数据。
3. 创建清晰的中文提交。
4. rebase 最新 `origin/main`。
5. 重跑 readiness、前端全量/构建、后端全量和独立预览。
6. 推送分支并创建 draft PR，由远端 CI 再次验证。

以上步骤必须保持顺序；任何 rebase 后的产品改动都需要重新执行相应门禁。
