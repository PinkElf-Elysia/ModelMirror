# Xpert Sandbox 与 Skill Runtime

最后更新日期：2026-07-16

## 能力边界

私有 Workflow、Xpert Chat、Goal 和 Handoff 可以为 `workflow_agent` 绑定 `sandbox_files`、`sandbox_shell` 与 `skills_runtime`。这些中间件提供隔离文件工作区、受控命令、本地 Skill staging 和产物发布；公开 Xpert App/API 在部署预检与运行时均拒绝这些能力。

Sandbox 不提供公网、Docker Socket、主服务源码、Runtime Store 或环境密钥。浏览器和联网客户端工具不属于本轮能力，后续必须使用独立联网服务和 SSRF/域名策略实现。

## Sidecar 架构

Docker Compose 中的 `sandbox` 是独立 sidecar：

- `network_mode: none`，没有宿主机公开端口。
- 主服务只通过共享 Unix Domain Socket 发送 JSON 请求。
- 根文件系统只读，使用非特权 UID、`cap_drop: ALL` 与 `no-new-privileges`。
- 配置 CPU、内存、PID 与临时目录限制。
- 主服务只读挂载工作区卷；sidecar 不挂载仓库、`.env` 或主服务持久化目录。
- 子进程通过 Linux Landlock 将文件访问限制在当前工作区及必要的系统运行库。

sidecar 镜像内预装 Python、Node/npm/npx、git 与 ripgrep。由于容器完全断网，npm、npx 与 git 只能处理镜像或工作区中已有的本地内容。

## 工作区与作用域

目录固定为：

- `inputs/`：从 Xpert 会话显式选择的附件副本。
- `work/`：Agent 可编辑文件。
- `skills/`：由 `skill_stage` 安全复制的已安装 Skill。
- `artifacts/`：用户可下载的已发布产物。
- `.modelmirror/operations/`：sidecar 操作幂等记录。

作用域隔离规则：

- Xpert Chat：`conversation`，可跨同一会话运行持久化。
- Goal：`goal`，按 Goal/Step 隔离。
- Handoff：`handoff`，按 Handoff 隔离。
- 普通 Workflow：`workflow`，按 task/node 隔离并由清理策略回收。
- Xpert App：禁止创建工作区。

默认单工作区配额为 256 MB。所有路径必须是工作区内相对路径；绝对路径、`..`、symlink 穿越、二进制文本读取与超限操作均被拒绝。

## Runtime 工具

文件与命令工具：

- `sandbox_list_files`
- `sandbox_read_file`
- `sandbox_write_file`
- `sandbox_search_files`
- `sandbox_shell`
- `sandbox_publish_artifact`

Skill 工具：

- `skill_list`
- `skill_read`
- `skill_stage`

`sandbox_shell` 只接受 argv 数组，不解析 shell 字符串、管道、重定向或命令替换。允许命令由中间件配置与服务端固定上限共同约束，超时会终止整个进程组，输出最多保留 64 KB。每次调用携带稳定 operation ID；已经完成的副作用操作在 HITL 恢复或请求重放时返回原结果，不重复执行。

Skill 必须先由用户安装。`skills_runtime` 固定 1 至 10 个 Skill ID，发布 Xpert 时验证其存在；`skill_stage` 排除 `.git`、symlink、超限文件和路径逃逸。Skill 不会自动执行，脚本仍须通过 `sandbox_shell` 并经过权限、HITL 与审计链路。

## HITL 与安全顺序

Sandbox 工具统一执行顺序为：

1. Agent 工具 allowlist 与 `ToolPermissionPolicy`。
2. `human_in_the_loop` 审批。
3. Tool audit started。
4. sidecar 调用。
5. Tool audit finished/failed。

`sandbox_shell.require_approval=true` 时，静态校验和运行时编译都要求同一 Agent 绑定覆盖 `sandbox_shell` 或 `*` 的 HITL 中间件。人工编辑 argv 后会重新执行路径、命令、超时、schema 与 policy 校验。拒绝不会调用 sidecar，拒绝原因作为工具结果返回 Agent。

checkpoint 与 SSE 只保存 workspace、operation、artifact ID、状态、耗时、长度和安全错误摘要，不保存文件正文、命令完整输出、附件内容、prompt 或密钥。

## 管理接口

```text
GET /api/runtime/sandbox/capabilities
GET /api/runtime/sandbox-workspaces?scope_type=&scope_id=&limit=
GET /api/runtime/sandbox-workspaces/{workspace_id}
GET /api/runtime/sandbox-workspaces/{workspace_id}/files?path=
GET /api/runtime/sandbox-workspaces/{workspace_id}/files/content?path=
GET /api/runtime/sandbox-artifacts/{artifact_id}
GET /api/runtime/sandbox-artifacts/{artifact_id}/download
```

内部 sidecar 协议不暴露 TCP 端口。管理接口返回逻辑相对路径和安全元数据，不返回工作区物理路径。

## 验收护栏

任何 Sandbox/Skill 变更至少验证：路径与 symlink 逃逸、配额、argv 白名单、超时进程组终止、环境隔离、操作幂等、HITL 恢复、Skill staging、产物下载、作用域隔离、容器重启恢复以及 Xpert App 部署阻断。

Xpert 的 AGPL 实现仅作行为参考；本模块为 ModelMirror 独立实现。浏览器、联网工具、远程依赖安装、Docker Socket 和多租户权限不在当前范围。
