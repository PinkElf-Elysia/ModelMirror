# 统一输出资产闭环验收说明

## 范围

本批新增独立输出能力协议 `modelmirror-file-output-capabilities-v1`，不修改现有文件输入协议 v2 或格式注册表 v5。覆盖普通 Chat、Xpert Agent、Workflow，以及它们显式发布的 Sandbox、Browser、MCP 和已持有媒体产物。不会扫描整个工作目录，不会为了持久化而抓取任意远程 URL，也不建设跨模块文件中心。

两个功能开关默认关闭：

- `FILE_OUTPUT_ASSETS_ENABLED=false`
- `CHAT_FILE_OUTPUT_TOOL_ENABLED=false`

关闭后既有文本、图片、音频、视频、文件输入、Xpert workspace 与 Workflow 行为保持原样。数据库迁移只增不删，已存在输出由 7 天硬 TTL 清理。

## 已闭环能力

- 输出记录、任务、确认 revision、重启中断、7 天 TTL、幂等注册与 `cleanup_pending` 重试均复用现有 SQLite 和不透明 FileBlobStore。
- 每轮最多 5 个文件，单文件 50 MiB，合计 100 MiB；生成规格最多 500,000 字符或 2 MiB JSON。
- Chat 仅在 `gateway=default`、确切模型实时验证工具调用、且命中真实供应商金丝雀白名单时注入唯一 `modelmirror_create_file`。首个开放组合仅为 OpenRouter 官方接口、`openai/gpt-5.6-luna`、OpenAI 标准 provider；请求固定 `allow_fallbacks=false`。
- TXT/MD/JSON/CSV 在本地生成；PDF/DOCX/XLSX/PPTX 在 network-none、非 root、只读根文件系统的专用 sidecar 生成。
- 只接收显式发布的 Sandbox、Browser、MCP 和已持有媒体字节；重新限流、嗅探 MIME、计算哈希并复制，不返回源路径。
- SSE 成功顺序固定为正文 delta、`output_file`、`route_receipt`、唯一 `message_end`、`[DONE]`。生成失败发送 failed 输出卡但不吞掉正文。
- 输出卡支持刷新恢复、预览、下载、同一 Chat 会话内的文档及媒体下轮复用、保存资料库、删除和清理重试。媒体复用沿用现有图片、音频、视频输入协议，并在发送前复核服务端 revision 与字节；RAG 入库使用本地结构化解析与本地确定性索引，不触发 OCR、视觉或其他外部模型。
- 保存到资料库创建独立 RAG 文档和 binding，不延长输出原件 TTL；知识库删除隔离时稳定 409，失败回滚。

## 明确未开放

- 图片、音频、视频仅在普通 Chat 内支持“下轮复用”；Agent 与 Workflow 没有等价的模态输入协议，继续 fail-closed 并显示禁用原因。媒体仍不能直接保存到资料库，需在 RAG 入口另行确认处理。
- 媒体或需要视觉/OCR 的扫描 PDF 不可从输出卡直接保存到资料库，必须回到 RAG 入口重新确认处理。
- 不支持统一文件中心、任意跨模块转交、RAG 内部派生物、Data X 导出或 Coding 项目导出。
- 本地 mock 仅证明协议与隔离。除 `openai/gpt-5.6-luna` + OpenAI 标准 provider 外，其他模型即使实时目录声明支持 tools，也保持 `planned`，直到逐个完成另行授权、无重试、无 fallback 的真实供应商金丝雀。

## 真实供应商金丝雀

- `openai/gpt-5.6-luna` / OpenAI 标准 provider：单轮强制工具调用与双轮 `auto -> tool result -> final` 均通过；双轮总费用 `$0.0001496`，模型、provider、文件规格均精确匹配，无重试、无 fallback。模型最终文本产生的虚假 `sandbox:/mnt/data/...` 链接已在服务端降级为输出文件名，真实交付以 ModelMirror 输出卡为准。
- `deepseek/deepseek-v4-pro` / DeepSeek 官方 provider：OpenRouter 返回 404，账户隐私策略与端点不兼容，保持 `disabled/planned`。
- `qwen/qwen3.8-max` / Alibaba provider：供应商返回 400，未产生工具规格，保持 `disabled/planned`。
- 另有 12 个模型通过公开目录、活动端点及断网协议泛化检查；该证据不等同于真实供应商接受，不能用于扩大白名单。

## 发布门禁

```powershell
node scripts/check-file-output-readiness.mjs
docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g -v "C:\tmp\modelmirror-file-output-closure:/workspace:ro" -w /tmp modelmirror-server python -m pytest <file-output and RAG focused tests> -q
cd client
npm.cmd test -- --run <file-output focused tests>
npm.cmd run build
git diff --check
```

人工验收使用独立端口 `15177`、内网 mock、只读源码挂载；不得调用共享 Compose 或触碰 5173/8000。验收通过后才同步最新 `origin/main`、提交、推送并创建 PR。
