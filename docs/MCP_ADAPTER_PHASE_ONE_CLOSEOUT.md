# MCP 适配第一阶段收口

最后更新日期：2026-08-09
维护人：模镜团队

## 1. 收口结论

MCP 目录第一阶段已完成“固定目录、分批威胁建模、可执行适配器收窄和默认拒绝”目标。本结论不表示 100 个条目全部可运行，而是表示每个条目都已有明确批次、状态与准入边界：

- 目录固定为 100 个条目、18 个分类。
- `ready` 45 项：已具备固定运行契约、工具策略和对应批次验收。
- `planned` 14 项：全部属于批次 10，等待不可伪造的多租户主体、OAuth 生命周期与凭据作用域完善。
- `blocked` 41 项：上游归档、任意执行、动态控制面、宿主/设备高权限、凭据或资源作用域无法安全冻结等原因均有明确记录。
- `planned` 与 `blocked` 项没有运行镜像、服务命令、端点、配置/凭据字段或工具策略；环境功能开关不能把它们变为可执行。

本文件冻结的是第一阶段 100 项基线。第二阶段另行批准并集成的 100 项见
[MCP 双源目录扩充批准清单](./MCP_CATALOG_EXPANSION_REVIEW.md)；它们全部为不可执行的
`planned`，不改变本表第一阶段的 Ready/Planned/Blocked 结论。

各批次状态冻结如下：

| 批次 | Ready | Planned | Blocked | 本阶段结论 |
| --- | ---: | ---: | ---: | --- |
| 0 | 7 | 0 | 0 | 兼容基线 |
| 1 | 3 | 0 | 0 | 断网计算 |
| 2 | 3 | 0 | 2 | 固定公网读取 |
| 3 | 4 | 0 | 1 | 受控文件工作区 |
| 4 | 15 | 0 | 1 | 固定 Token 只读访问 |
| 5 | 6 | 0 | 5 | 结构化数据库只读访问 |
| 6 | 4 | 0 | 2 | 固定 SaaS 与资源审批 |
| 7 | 2 | 0 | 2 | 临时浏览器与独立出口 |
| 8 | 0 | 0 | 2 | 任意 Python 执行阻断 |
| 9 | 1 | 0 | 13 | Terraform 公共 Registry 子集 |
| 10 | 0 | 14 | 0 | 延后到多租户完善后 |
| 11 | 0 | 0 | 13 | 桌面、IDE、Docker 与设备宿主阻断 |

## 2. 已交付边界

第一阶段只交付固定、可审查的能力，不提供通用 MCP 执行器：

- 后端以项目 manifest 冻结批次、可用性、连接方式、网络/文件系统策略、资源限制、配置/凭据字段和工具读写效果。
- 前端只从公开 manifest 展示状态；后端未返回 `executable=true` 时不提供连接入口。
- 已交付的 sidecar 按能力拆分为断网计算、固定公网、文件、Token、数据库、SaaS、浏览器及 Terraform Registry 边界，不接受任意命令、镜像、URL、Header、环境变量或宿主路径。
- 有状态写入要求服务端生成的一次性审批、冻结参数与状态摘要；歧义结果不自动重试。
- 目录会话与通用 MCP 会话隔离，日志不记录 Secret、完整参数或返回正文。

批次 11 没有新增桌面代理或运行时。小红书、OpenTabs、IDE/DAW/逆向工具、Obsidian/Zotero、本机对话历史、Docker daemon、移动设备和 Xcode 等能力必须经过新的宿主桥接路线，不能复用服务端 sidecar 或任意 localhost 输入绕过。

## 3. 阶段二准入条件

以下基础能力全部完成前，批次 10 不恢复，第 11 批也不拆分为可执行适配器：

1. 每个请求使用不可由客户端伪造的认证主体，并将租户、所有者、项目、会话和审批纳入同一作用域。
2. OAuth 授权、刷新、撤销、账号切换和凭据轮换均具备租户隔离与可审计生命周期。
3. 桌面桥使用签名安装包、双向配对、版本证明、宿主实例证明、逐应用/目录/项目授权和即时撤销。
4. 外部写入具备目标预览、终止操作审批、幂等账本、未知结果处置和服务商侧核对指引。
5. 设备、IDE、Docker 或 macOS runner 使用专用资源租约和 allowlist，不把宿主 daemon、USB、LAN 或任意文件路径直接暴露给服务端。
6. 新适配器仍需独立完成初始化、Schema 锁定、代表性调用、超时、重连、断开、清理和越权负向测试；不能用健康检查或 Mock 代替真实验收。

## 4. 收口验收

本阶段最终目录变更使用以下命令复核：

```powershell
docker run --rm --network none `
  -e PYTHONDONTWRITEBYTECODE=1 `
  -e WORLD_STORAGE_DIR=/tmp/modelmirror-wave11-world.json `
  -v "${PWD}:/workspace" -w /workspace `
  modelmirror-server python -m pytest server/tests/test_mcp_catalog.py -q -p no:cacheprovider

Set-Location client
npm.cmd ci
npm.cmd run build
```

发布前还必须检查：

```powershell
git status --short
git diff --check
git diff --cached --name-only
```

本收口分支只更新目录状态、项目说明、测试和文档，不重建共享栈，也不启用任何第 10/11 批运行能力。

## 5. 后续维护

- 上游版本变化只触发重新审核，不自动改变 `planned` 或 `blocked`。
- 新的桌面桥、多租户或 OAuth 工作应使用独立路线和验收记录，不在本阶段分支中悄然放宽 manifest。
- 状态统计必须由后端公开 manifest 生成，并与前端及路线文档保持一致。
- 发生安全回归时优先关闭对应项目开关、断开目录会话和停止所属 sidecar；不得通过开放通用调用端点临时绕过。
