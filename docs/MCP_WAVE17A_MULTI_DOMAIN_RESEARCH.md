# MCP 批次 17A：多域公共研究适配

最后更新：2026-08-10

## 当前状态

本批实现 open-webSearch、Idea Reality MCP 与 GitMCP 的固定、匿名、只读兼容层。
三项已经完成真实隔离验收并获得人工批准，目录状态为 `ready`，且只将三个精确 ID
加入 `MCP_PUBLIC_ALLOWED_ADAPTERS` 默认清单；项目功能开关仍需显式开启。

## 冻结契约

| 目录 ID | 上游身份 | 固定 Host | 开放工具 | 明确关闭 |
| --- | --- | --- | --- | --- |
| `aas-ee-open-websearch` | `Aas-ee/open-webSearch` v2.1.9，commit `84695b392ca03ffc68fbd406f1d7937b7151e4b6`，Apache-2.0 | `cn.bing.com`、`html.duckduckgo.com` | `search` | 网页抓取、Playwright、代理、任意 URL/Header/env、关闭 TLS 校验 |
| `mnemox-ai-idea-reality-mcp` | `mnemox-ai/idea-reality-mcp` v0.5.0，commit `755e1859c1f7d1d017c67f615c67ec595c8edb66`，MIT | `api.github.com`、`hn.algolia.com`、`registry.npmjs.org`、`pypi.org` | `idea_check` | Product Hunt Token、LLM、账号数据、上传、任意 endpoint |
| `idosal-git-mcp` | `idosal/git-mcp` commit `c487a29895dcfcb5b672247e646426a56e2051c1`，Apache-2.0 | `api.github.com` | `fetch_repository_documentation`、`search_repository_documentation`、`search_repository_code` | 动态 GitMCP endpoint、通用 URL 抓取、GitHub Token、clone、代码执行和仓库写入 |

open-webSearch 仅保留 `bing` 与 `duckduckgo` 两个固定 engine，最多 10 条结果，
且始终使用 request-only 与 Strict SafeSearch。Idea Reality 的 `quick` 模式只查询 GitHub
和 Hacker News，`deep` 追加 npm/PyPI 公共索引；输出只是相似性研究线索，不构成投资、
法律或产品建议。GitMCP 只接受规范 `owner/repository` slug，不接受 URL、`.git`、额外路径、
百分号编码或动态服务地址；代码搜索被明确收窄为最多 20 条的仓库路径索引，不克隆代码。

## Schema 摘要

- open-webSearch：`cf695f0f1d6a9fb3fe08ae454f3729367f28103bc85d1c893737f42ad706fe99`
- Idea Reality：`65b4b069bcb5faa961341576f452e72faa49b4deae214a6f840da2521a010c24`
- GitMCP：`56a8c84a969a4beaca16bf905be83899bb497d19a4e95cef5135ad4465ef4811`

## 验收门槛

晋级前必须使用 fresh `modelmirror-mcp-public:wave17a-v1` 镜像完成两轮真实代表调用、
禁用工具拒绝、Schema 校验、客户端取消/断开、sidecar 重启与精确资源清理，并确认共享栈
容器未启动、未重建、未变化。任何上游限流、响应漂移、固定域名或真实调用失败都会让对应
项目继续保持 `planned`，不会进入默认 allowlist。

## 隔离验收证据

fresh `modelmirror-mcp-public:wave17a-v1` 镜像 manifest list 为
`sha256:b4b771b76d85ab52900d9c8ebd7228a9f9e034ad6d5031de8b5af6690bffde79`。
构建期 contract smoke 对当前公共扩展八项逐一完成工具发现和 Schema digest 校验。

最终隔离运行使用固定前缀 `mm-wave17a-20260810c-*`，三个新 ID 仅通过该次显式
`MCP_PUBLIC_ALLOWED_ADAPTERS` 注入；默认清单没有变化。第一轮完成后重启 sidecar，
`StartedAt` 从 `2026-08-11T04:09:31.996435901Z` 变化为
`2026-08-11T04:09:58.272759928Z`，第二轮完整重复：

- open-webSearch 输入 `Model Context Protocol`，返回 4 条，结果同时来自 `bing` 与
  `duckduckgo`；通用网页抓取工具被拒绝。
- GitMCP 输入 `octocat/hello-world`，读取 README，并分别得到 1 条文档与代码路径结果；
  通用 URL 抓取工具被拒绝。
- Idea Reality 使用 `quick` 模式，固定读取 `github,hacker_news`，返回 1 条相似线索；
  Product Hunt 工具被拒绝。另以同一最终镜像独立验证 `deep`，只访问
  `github,hacker_news,npm,pypi`，返回 6 条线索。
- 1 ms 客户端取消探针触发并关闭独立会话；每轮结束 `/workspaces` 为空。
- sidecar 为 UID/GID 65532、只读根、`cap_drop: ALL`、`no-new-privileges`、128 PID、
  512 MiB；helper 为 `network none`，只挂只读 Unix socket 卷。
- finally 后精确复核为 0 个容器、0 个卷；未启动或重建共享栈。

首次真实组合测试因 `www.bing.com` 区域重定向被 `max_redirects=0` 正确拒绝，资源同样
清理为 0/0。修复没有放宽重定向或 host，而是直接使用上游审阅实现的固定
`cn.bing.com` RSS 入口；随后最终两轮在无重定向策略下通过。

## 回退

本批当前尚未启用，无数据迁移。回退只需删除三个 builder/proxy 测试入口并恢复镜像标签；
目录状态保持 `planned`，不需要撤销凭据、外部写入或持久化数据。
