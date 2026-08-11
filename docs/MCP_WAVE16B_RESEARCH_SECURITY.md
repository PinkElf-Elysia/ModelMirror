# MCP 批次 16B：研究与安全公共读取适配

最后更新日期：2026-08-10

## 结论

批次 16B 将 BioMCP 与 SafeDep Vet 收敛为两个固定、匿名、只读的
`mcp-public` 原生兼容契约。两项 manifest 为 `ready`，并已在隔离真实验收通过、人工批准后
进入生产 Compose 的精确默认 allowlist；项目功能开关仍需显式开启。

目录总状态为 **55 ready / 46 planned / 99 blocked**；第二阶段扩充的 100 项为
**10 ready / 32 planned / 58 blocked**。

## 固定契约

| 目录 ID | 上游身份 | 固定 Host | 开放工具 | 明确关闭 |
| --- | --- | --- | --- | --- |
| `genomoncology-biomcp` | `genomoncology/biomcp` v0.8.25，commit `b5337826dbf06db6d6409f36ead7a4d6a70c710e`，MIT | `www.ebi.ac.uk`、`clinicaltrials.gov`、`myvariant.info` | `search`、`get` | 原始 `biomcp` 查询、研究文件、本地数据库、诊断上传、任意 URL/Header |
| `safedep-vet` | `safedep/vet` v1.18.1，commit `67abab1b0ec915713edb50e5e5b36687fd4cd86a`，Apache-2.0 | `community-api.safedep.io`、`registry.npmjs.org`、`pypi.org` | 漏洞、流行度、许可证、恶意软件报告、最新版本、版本列表共 6 项 | 包下载/解包/执行、扫描或上传、SQL、认证租户、任意 Registry/Endpoint/Header |

BioMCP 只返回公共文章、临床试验与变异元数据，并明确标注不构成医疗建议。
SafeDep 只接受规范化 `pkg:npm/...` 或 `pkg:pypi/...` PURL；版本级洞察必须携带版本，
公共 Registry 查询不能携带 qualifier、fragment、URL 或其他生态类型。两项均不运行上游
进程或二进制，不接受 Token、命令、环境变量、工作目录或宿主路径。

## 冻结 Schema

- BioMCP：`24c2ca66ce7643bdb91323912a73956c1adbd93c82c246c55fe773afa95f1c31`
- SafeDep Vet：`52be50ad2e6b7c53e2b6e76799a9083f3892ae49e2b0f2bfccee4ca8262be652`

## 隔离验收证据

fresh 镜像 `modelmirror-mcp-public:wave16b-v1` 的 manifest list 为
`sha256:1739fc1aadaea5ea6e6d89917a8967cf6e722d5e3d36c6ab2115f5a2e79a4c44`。
构建阶段的 contract smoke 对 Wave 16 的五项公共扩充适配器执行工具发现和 Schema 摘要
校验。

随机前缀 `mm-wave16b-f2a5d467-*` 的最终真实 runtime 验收通过，并在 sidecar 重启后完整
重复一次：

- BioMCP 输入文章查询 `BRAF melanoma`，返回 3 项；读取固定公共试验
  `NCT02576665` 成功。
- SafeDep 输入 `pkg:npm/lodash@4.17.20`，公共 Registry 返回最新版本 `4.18.1`，
  版本列表返回 5 项，社区漏洞服务返回 5 项；固定测试包
  `pkg:npm/safedep-test-pkg@1.0.0` 返回预期恶意软件标记。
- BioMCP 的原始 `biomcp` 工具和 Vet 的 SQL 工具均在真实会话中被拒绝；工具集合与
  冻结 Schema 完全一致。
- 1 ms 客户端取消探针触发超时并关闭独立会话。该探针证明取消/断开/清理路径，不冒充
  提供商网络超时实测。
- sidecar 以 UID/GID 65532、只读根、`cap_drop: ALL`、
  `no-new-privileges`、512 MiB、128 PIDs 运行；调用前后 `/workspaces` 为空。
- sidecar 重启被 `StartedAt` 变化确认，重启后再次完成两项代表调用、禁用工具和取消探针。
- 验收使用无端口的 Docker 默认 bridge，没有加入任何 Compose 项目或共享网络；临时
  helper 为 `network none`，仅通过只读 Unix socket 卷连接。容器与卷按精确随机前缀清理，
  残留为 0；未启动或重建共享栈。

首次编排因 Docker 自定义地址池耗尽，尚未创建容器即停止并清理为 0/0/0；随后改用无端口
默认 bridge。第一次真实调用已证明两项代表调用成功，但验收脚本的取消探针仍硬编码 16A
ID，被 16B-only allowlist 正确拒绝；修复仅让探针从当前已选适配器中选择固定工具参数，
未改变生产运行时、Host、工具或权限边界。该失败批次同样精确清理为 0/0。

## 限制与回退

- 生物医学结果可能不完整或过时，仅用于研究发现，不用于诊断、治疗或临床决策。
- SafeDep 返回的是公共社区/Registry 已有元数据，不下载包，也不保证未报告的版本安全。
- 公共提供商的限流、可用性和响应格式仍可能变化；任何 Schema、Host、PURL 或身份漂移
  均 fail-closed。
- 16B 两项已在验收批准后只增加 `genomoncology-biomcp` 与 `safedep-vet` 两个精确 ID；
  未放宽其他适配器或任意 endpoint。

回退不涉及数据迁移：从 `MCP_PUBLIC_ALLOWED_ADAPTERS` 移除两个精确 ID、关闭对应项目
功能开关、断开目录会话，并把两项 manifest 恢复为 `planned`。无需删除凭据或外部数据，
因为本批不采集凭据也不写入上游。
