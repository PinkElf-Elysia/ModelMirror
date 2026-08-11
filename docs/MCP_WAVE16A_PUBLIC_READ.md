# MCP 批次 16A：匿名公共读取适配

最后更新日期：2026-08-10

## 结论

批次 16A 将 DuckDuckGo、shadcn/ui 与 Docker Hub 收敛为三个固定、匿名、只读的
`mcp-public` 兼容契约。三项后端 manifest 为 `ready`，并已在独立真实验收后获得人工批准，
进入生产 Compose 的精确默认 allowlist。

目录总状态为 **53 ready / 48 planned / 99 blocked**；第二阶段扩充的 100 项为
**8 ready / 34 planned / 58 blocked**。

## 固定契约

| 目录 ID | 上游身份 | 固定 Host | 开放工具 | 明确关闭 |
| --- | --- | --- | --- | --- |
| `nickclyde-duckduckgo-mcp-server` | `nickclyde/duckduckgo-mcp-server` v0.6.1，commit `ad2e681bfb4461c969d3032b47ac5b3cd513f0a9`，MIT | `html.duckduckgo.com` | `search` | `fetch_content`、任意 URL、关闭 SafeSearch、Header/代理配置 |
| `jpisnice-shadcn-ui-mcp-server` | `Jpisnice/shadcn-ui-mcp-server` v2.0.0，commit `d750f1645bb0fe10c6fbf5e246bc3b12d3807c05`，MIT | `api.github.com` | `list_components`、`get_component_metadata` | 源码/Demo/Block/主题/Token、`apply_theme`、仓库/分支/路径输入 |
| `docker-hub-mcp` | `docker/hub-mcp` tag `dockerhub-mcp/v0.18.0`，commit `98cf1b9cbec64316ea2b465462468a2d2204a406`，Apache-2.0 | `hub.docker.com` | `search`、`getRepositoryInfo`、`listRepositoryTags` | 账号/命名空间/DHI、创建或更新仓库、认证、镜像拉取与执行 |

shadcn/ui 目录固定到 `shadcn-ui/ui` commit
`d14b6e69a91f0fc99e31a7adb26a48d661df9911` 的
`apps/v4/registry/new-york-v4/ui`。同一 MCP 会话只读取一次目录并复用内存快照；辅助文件
`_registry.ts` 不作为组件返回，每个 `.tsx` 仍逐项验证规范化名称、固定路径、Git SHA 和
大小。

三项均不运行上游包，不接受 Token、命令、环境变量、Header、任意端点或宿主路径。
所有 HTTPS 请求继续经过 DNS 全答案、非公网地址、TLS hostname、重定向和响应大小门禁。
工具输出最大 128 KiB，原始响应最大 1 MiB。

## 冻结 Schema

- DuckDuckGo：`9a10fcfb68759337ab6af5fcfe76f5a7ebc87f3724e34a2017ea25807e4cc197`
- shadcn/ui：`8a04ba4e5da26f151bc0a563e63d9567e2932e0450d08565bc64f2498e39336f`
- Docker Hub：`e8ce120ed943ee25aaa0d67218e4ce8e408dc42592251e9eec108daa1065d35d`

## 隔离验收证据

fresh 镜像 `modelmirror-mcp-public:wave16a-v1` 的 manifest list 为
`sha256:f0e28781f328480f403c279f50326dc54ec64d57d42f43a70196952727165554`。
构建阶段的无网络 contract smoke 对三项执行工具发现和 Schema 摘要校验。

随机前缀 `mm-wave16a-e6ac8656-*` 的真实 runtime 验收通过：

- Docker Hub 输入 `library/python`：搜索返回 3 项，仓库元数据返回公开 pull count，标签首项
  为 `3.13.15-alpine3.24`。
- shadcn/ui 输入 `button`：固定目录返回 61 个组件，`button` SHA 为
  `4d38506cee5430d95a59ec6a2a0cef2b79217e7a`，提交摘要匹配固定 commit。
- DuckDuckGo 输入 `Model Context Protocol`：返回 3 项，首项 Host 为
  `modelcontextprotocol.io`；第二次搜索等待 1859 ms，证明同会话节流有效。
- 三个已关闭工具在真实会话中均被拒绝；1 ms 客户端取消探针触发超时并关闭独立会话。
  该探针证明取消/断开/清理路径，不冒充提供商 15 秒网络超时的实测结果。
- sidecar 以 UID/GID 65532、只读根、`cap_drop: ALL`、
  `no-new-privileges`、512 MiB、128 PIDs 运行；调用后仅保留 PID1，`/workspaces` 为空。
- sidecar 重启后再次完成 Docker Hub 代表调用和取消探针；重启前后工作区均为空。
- 验收容器与卷已按精确前缀清理，残留为 0；未使用 Compose 项目，未启动或重建共享栈。

首次真实验收曾因固定 shadcn 目录中的非组件辅助文件 `_registry.ts` 被过严校验而停止；
修复只忽略非 `.tsx` 文件，未放宽组件元数据校验、Host、路径或工具面。失败验收资源已单独
精确清理，未复用为通过证据。

## 限制与回退

- DuckDuckGo 标题、摘要与链接属于不可信公网内容；适配器不会抓取结果页。
- shadcn/ui 只返回固定提交的 Git 元数据，不返回源码或安装命令。
- Docker Hub 只返回公开元数据，不验证镜像内容安全性，也不拉取或运行镜像。
- 公共提供商的限流、可用性和响应格式仍可能变化；任何 Schema、固定路径或身份漂移均
  fail-closed。

回退不涉及数据迁移：从 `MCP_PUBLIC_ALLOWED_ADAPTERS` 移除三个精确 ID、关闭对应项目
功能开关、断开目录会话，并把三项 manifest 恢复为 `planned`。无需删除凭据或外部数据，
因为本批不采集凭据也不写入上游。
