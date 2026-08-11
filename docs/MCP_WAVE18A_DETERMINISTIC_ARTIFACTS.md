# MCP 第 18A 批：确定性文件产物

## 当前结论

本批为 Markdownify、MCP Pandoc 与 AntV MCP Server Chart 增加了断网文件 sidecar 兼容层。
隔离镜像验收与用户验收均已通过，三项现已晋级 `ready`，并已加入生产 Compose 的精确
`MCP_FILE_ALLOWED_ADAPTERS`；本轮未启动或重建共享栈。

| 目录 ID | 审阅上游 | 固定本地工具 | 运行依赖 |
|---|---|---|---|
| `zcaceres-markdownify-mcp` | v1.1.0 / `024f97cea9a94cd842c445eea4503c442c79bd71` / MIT | `pdf-to-markdown`、`docx-to-markdown`、`xlsx-to-markdown`、`pptx-to-markdown` | MarkItDown 0.1.7 |
| `vivekvells-mcp-pandoc` | v0.11.0 / `120dc78f1fe243b72631029ee0f33ab77034ea34` / MIT | `convert-contents` | Pandoc 3.10.1 / GPL-2.0 |
| `antvis-mcp-server-chart` | 0.9.10 / `2ed4a03b12e2fd82f6d2d0ece337f6ddb12966b9` / MIT | `generate_line_chart`、`generate_bar_chart`、`generate_pie_chart` | Matplotlib 3.10.7 本地兼容渲染 |

## 固定边界

- 三项都只在封存工作区和服务端产物目录内运行；容器 `network_mode: none`，客户端不能提交
  URL、Host、Header、环境变量、命令、MCP endpoint 或宿主路径。
- Markdownify 只保留四种本地 Office/PDF 转 Markdown 工具。YouTube、Bing、网页、Git、图片、
  音频和绝对路径工具均不可发现；插件保持关闭，Markdown 产物最大 4 MiB。
- Pandoc 只允许 `markdown/html/txt` 输入与 `markdown/html/docx` 输出。固定启用
  `--sandbox`，不公开 `contents`、input/output path、filters、defaults、reference document、
  template、Lua/custom writer、PDF、TeX 或任何外部进程参数。DOCX ZIP 时间戳会归一化，
  产物最大 32 MiB。
- AntV 官方 0.9.10 MCP 会把配置 POST 到 `antv-studio.alipay.com` 并返回远程图片 URL，
  因而不能原样用于本批。模镜仅保留上游 line/bar/pie 的名称与受限数据形状，由断网
  Matplotlib 固定版本生成 PNG；地图、远程图表、动态脚本、外部数据、任意样式代码和
  `VIS_REQUEST_SERVER` 全部关闭。界面和文档必须称其为本地兼容 facade，不能声称运行了
  官方 AntV 远程渲染服务。
- 所有工具均标记 `artifact-create`，管理器不得自动重发；输入只读，重复调用只能生成新的
  可清理产物，不覆盖源文件。

## 冻结 Schema 摘要

- Markdownify: `3980779d679e49797985fbb20bd537362a6c8049d1e8f1cc1b72e0b9536e03d7`
- MCP Pandoc: `33c536ccdb70ec575d105ad0931d40a737bb61b9b3171011d2f7297c3e4a5166`
- AntV Chart compatible facade: `2762f1d064817d7c5ccb203b221bb2c26414c59e48b7ceaa8b41a69357e6c15f`

## 供应链与回退

Pandoc 3.10.1 的官方 amd64/arm64 tarball 分别校验 SHA-256
`72948bf5784f560d5ad1876709daca27e0667f262da727bb33f77b58e52df2f5` 与
`cd3963da375793a4804c65ae538b4f7b9c23f87cac7f6c74a1cf5e2fff7e8d59`；镜像同时包含
固定版本的 GPL COPYING 文件。其余 Python 依赖继续来自现有完整文件 sidecar lock。

回退不需要数据迁移：将三项恢复为 `planned`，从精确 allowlist 移除对应 ID，断开目录
会话并删除其临时工作区即可。现有 Wave 3 文件适配器、共享卷格式和产物索引不变。

## 隔离验收证据

2026-08-10 从当前 `Dockerfile.files` 与隔离工作树重新构建
`modelmirror-mcp-files:wave18a-staged`，镜像 manifest 为
`sha256:e51f2cb3253f16aa996aa0fa6a87353849c51c9cf144a8738742fb3f966eb4fc`。
运行边界为 `network=none`、只读根文件系统、UID/GID 65532、`cap_drop=ALL`、
`no-new-privileges`、1 GiB、1.5 CPU、128 PID，并使用 256 MiB `noexec` 临时目录。

- 三项均完成真实 `initialize`、`tools/list` 与冻结 Schema 校验；每项使用两个新会话执行
  代表调用，所得产物逐字节一致，输入哈希不变，结束后临时根目录清空。
- Markdownify 实际完成 DOCX→Markdown；Pandoc 实际完成 Markdown→HTML/DOCX；AntV
  本地兼容 facade 实际完成 line/bar/pie PNG。
- 验收时使用 staged 默认拒绝配置，未显式加入 allowlist 的 Wave 18A 会话会在子进程启动前以
  `mcp_adapter_denied` 拒绝；用户验收后才将三个精确 ID 加入生产 Compose allowlist。
- Pandoc 大输入通过生产 UDS/file-server 路径触发客户端超时；连接断开后整个进程组回收，
  容器内无 Pandoc 残留。离线适配器没有上游 429/限流语义，因此网络限流测试不适用。
- 未公开工具与未知参数均被拒绝；Schema 固定 `additionalProperties=false`。

验收成功行：

```text
wave18a_file_runtime_smoke=ok network=none source_immutable=true deterministic=true default_deny=true timeout=verified cleanup=verified
```
