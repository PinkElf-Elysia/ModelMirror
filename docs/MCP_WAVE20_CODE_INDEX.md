# MCP 第 20 批：封存仓库代码索引

## 当前结论

本批按 Codebase Memory → CodeGraphContext → GoGraph 顺序复审，只保留一个候选运行时。
`ozgurcd-gograph` 已作为现有 `mcp-files` sidecar 内的固定 facade 完成真实隔离验收与用户验收，
现已晋级 `ready` 并加入文件 sidecar 的精确默认 allowlist。另两个实现转为
`blocked/superseded`，不进入镜像。

| 目录 ID | 固定上游 | 状态 | 结论 |
|---|---|---|---|
| `deusdata-codebase-memory-mcp` | v0.10.1 / `564d32cc87d520afd1b007babdbe71a89d3ea119` / MIT | blocked | 一次性 CLI 仍强制在 `/tmp` 建立进程协调树；在不授予 Landlock 根目录遍历的前提下无法启动 |
| `shashankss1205-codegraphcontext` | v0.5.7 / `0ae10a15885038aec4413769c1a283e8fb4642da` / MIT | blocked | 必装多种图数据库、Watcher 与宽管理面，无法在本批保持小型固定产品身份 |
| `ozgurcd-gograph` | v1.5.6 / `aa4d6d549e64f35c492664263630ba1350c66920` / MIT | ready | Go 专用、一次性内存索引、六个固定分析工具；真实隔离与用户验收通过 |

Codebase Memory 的直接断网原型能够解析固定 Go fixture，但在真实 UDS + Landlock
边界下因上游不可关闭的 `/tmp/cbm-daemon-UID` 协调机制失败。放行 `/` 的递归目录读取会
暴露同一挂载中的其他工作区名称，因此没有以削弱隔离换取兼容。

## 固定供应链与工具

GoGraph 使用官方 v1.5.6 原生发布包：

- linux-amd64 SHA-256：
  `1ef375a88cc8825ca7879b1170720352702e59723d1e3b06d33101a50a6f7030`；
- linux-arm64 SHA-256：
  `c8b6d8a42326264858f14c7819200f47d00d0fcd58520b6c6d1e1b16b022a6b5`。

镜像同时固定 Go 1.26.5 工具链，官方多架构镜像摘要为
`sha256:53eeac89074db483fdf0ab3be1df32bf6e47562263d2d0d6baa7f26acb4957dd`。
GoGraph 六个上游子集 Schema digest 为
`a2c8f2fcf028067f2e080d018e482a52bd7ba8c3546ac92ba254b6b8b3fca25f`；
ModelMirror 公开六工具 Schema digest 为
`b2f18ca952f7d555b29a460af5261e3f9ab1b81d187d884af778ca3360fae981`。

公开工具只有：

- `index_repository`：建立当前封存 Go 工作区的一次性内存索引；标记为 state-write、非幂等；
- `search_symbols`：按一个有界关键词搜索符号、包、文件与 import；
- `get_symbol_context`：读取一个精确符号的源码、调用者、被调用者和测试；
- `get_source`：读取一个精确 Go 符号的仓库内源码；
- `get_callers`：读取最多三层且排除测试边的调用者；
- `get_repository_summary`：返回固定的结构概览。

上游 `--persist-refresh`、Git baseline、`gograph_doc`、boundary 创建、会话遥测、Wiki、
配置路径、任意工具名和其余 59 个工具均不可发现、不可调用。

## 隔离边界

- 输入只来自服务端选择并封存的上传工作区；只接受 `.go`、`.mod`、`.sum`、`.work`。
- 浏览器不能提交仓库路径、宿主路径、命令、环境变量、Git ref、配置或输出目录。
- sidecar 为 `network_mode: none`、UID 65532、只读根、`cap_drop: ALL`、NNP、128 PID、
  1 GiB、1.5 CPU；Landlock 只读当前 input root，只写 session `/tmp`。
- 固定 `GOPROXY=off`、`GOSUMDB=off`、`GOTOOLCHAIN=local`、`GOFLAGS=-mod=readonly`；
  不下载模块或工具链，不运行目标仓库二进制、测试或生成器。
- GoGraph 以 `gograph mcp <server-selected-root>` 启动且永不传 `--persist-refresh`；索引与刷新
  只在该一次性上游进程内存中存在，`/outputs`、`/memory` 与源工作区不写入任何索引。
- `index_repository` 继续使用现有一次性写审批；管理器收到 `retry_on_failure=false`，
  超时或断线不自动重发。
- 上游身份、六个子集 Schema、输出大小、绝对路径和未知响应全部 fail closed；公开结果
  上限 240 KiB。

## 真实验收证据

构建镜像：`modelmirror-mcp-files:wave20-gograph-staged`；本次 manifest list 为
`sha256:44a118617ef8ee6fa26dfd01afb09d857a96a659c7628e3a30df2cd47c25668a`，
config 为 `sha256:318df554685e7f8841cd6e3230fcc5b4a45557011d3bbbf071dbde8ae57fe737`。

实际命令在 production-equivalent 容器边界中完成两轮独立 UDS 会话，验证：

- initialize、tools/list、公开 Schema 与上游六工具 Schema；
- 未索引先查询拒绝、一次性索引、搜索、上下文、源码、调用者、仓库摘要；
- 任意 path / Git ref 参数拒绝；
- 真实上游请求超时后进程终止；
- 两轮结果确定、源目录摘要不变、无输出或 memory 持久文件、无 GoGraph/Go 子进程残留；
- 临时根最终精确清理。

运行结果：

```text
wave20_code_index_runtime_smoke=ok network=none source_immutable=true rounds=2 timeout=verified cleanup=verified
```

定向后端回归为 `24 passed, 2 skipped`；跳过项仅为当前容器内不可创建的链接场景，
不替代真实 runtime 的只读源与清理证据。

## 晋级与回退

当前目录为 `68 ready / 31 planned / 101 blocked`；扩充 100 项为
`23 ready / 17 planned / 60 blocked`。Compose 默认文件 allowlist 只增加 `ozgurcd-gograph`。

Codebase Memory 与 CodeGraphContext 保持 blocked。回退只需从默认 allowlist 移除
`ozgurcd-gograph`、恢复 planned 并断开相关目录会话；本批没有数据迁移或持久索引。
