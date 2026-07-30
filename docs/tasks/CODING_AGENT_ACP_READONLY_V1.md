# 任务卡：CODING-ACP-READONLY-V1

> 首轮只建立 OpenCode 最小 ACP 只读闭环；本任务风险等级为 L4，任何安全边界或取消清理门禁失败时立即停止。

## 1. 单一目标

- 本次要完成：在独立 `/coding` 页面中，对服务端固定的 ModelMirror 工作区进行真实、流式、可取消的只读代码问答。
- 本次明确不做：文件修改、Diff、Shell、测试执行、Git 操作、多 Agent、远程仓库、完整 ACP、自动 push/PR、分布式 Worker、重启恢复和生产级多租户。
- 实施基线：PR #67 合并提交 `69b3cf470cea049c5aed24a1a64cc0771f7802c0`。
- 实施分支：`codex/coding-opencode-readonly-v1`。
- 独立 worktree：`C:\tmp\modelmirror-coding-v1`。

## 2. 证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| 基线提交存在且 worktree 从该提交创建 | 已证实事实 | `git rev-parse HEAD` |
| 原工作树包含其他进行中的修改，本任务必须隔离 | 已证实事实 | 原工作树 `git status --short --branch` |
| OpenCode 可通过 stdio 的 newline-delimited JSON-RPC 提供 ACP 服务 | 已证实事实 | OpenCode 官方文档：`opencode acp` |
| 固定依赖为 `opencode-ai@1.18.9`，许可证为 MIT | 已证实事实 | `npm view opencode-ai@1.18.9 version license dist.integrity` |
| npm integrity | 已证实事实 | `sha512-tqvu/hJ26c2dBj/V/uTHaQI3bMSpLck0hIgGXO2z7b11s5mYfnaq+K1CBjsg8Pp6EirfzwUYGzi85K/SvOgkKg==` |
| 只读必须由协议拒绝、OpenCode 权限和容器只读挂载共同保证 | 建议方案 | 本任务安全边界与批次 2、3 验收 |

## 3. 影响范围

- 允许修改路径：
  - `server/coding_runtime/`
  - `server/coding_worker/`
  - `server/tests/test_coding_runtime_*.py`
  - `server/tests/fake_acp_agent.py`
  - `server/main.py` 中 Coding Router 的最小挂载
  - `server/Dockerfile` 中 Coding 包复制
  - `server/.env.example` 中 Coding 开关和非秘密配置
  - `client/src/pages/CodingPage.tsx`
  - `client/src/utils/codingApi.ts`
  - `client/src/types/coding.ts`
  - `client/src/App.tsx` 和 `client/src/pages/StudioHomePage.tsx` 的最小入口变更
  - `docker-compose.yml`
  - Coding 相关架构、部署、第三方声明和本任务卡
- 禁止修改路径：
  - `/api/chat` 与 `ChatPage`
  - 多模态、RAG、经典工作流和 workflow-native 主链路
  - 其他工作树中的用户修改
  - `.env`、运行日志、持久化目录、`node_modules/`、`client/dist/`
- 每批预计文件数：不超过 5；整轮拆为 7 个可独立回退的本地提交。
- 影响路由/API：
  - `GET /api/coding/capabilities`
  - `POST /api/coding/sessions`
  - `POST /api/coding/sessions/{id}/turns`
  - `GET /api/coding/sessions/{id}/events?after=<seq>`
  - `POST /api/coding/sessions/{id}/cancel`
  - 前端路由 `/coding`
- 影响持久化数据：无。会话和有限事件缓冲只驻留内存，重启即丢失。
- 新增或升级依赖：仅在隔离镜像内固定安装 `opencode-ai@1.18.9`；前端不新增依赖。
- 涉及密钥/网络/文件/子进程/公开访问：涉及模型网关密钥、内部网络、只读源码挂载和 ACP 子进程；不新增宿主端口，不允许公网暴露。

## 4. 公共契约与安全边界

- 浏览器不能提交工作目录、命令或 provider；工作区固定为容器内 `/workspace`。
- 前端只接收供应商无关的内部 `CodingEvent`，不得接收原始 ACP 帧、绝对路径、密钥或完整工具输出。
- 内部事件只允许：会话开始、轮次开始、计划、回答增量、工具状态、完成、失败、取消和心跳。
- 所有 ACP 权限请求统一拒绝，协议异常、超时或进程退出必须失败关闭并清理。
- OpenCode 仅允许 `read/list/glob/grep/lsp`；禁止 edit、bash、task、web、skill、外部目录、插件、MCP、分享和自动更新。
- 子进程只继承必要的 `PATH`、`HOME`、模型和 gateway URL/key，不继承 FastAPI 全部环境。
- 源码以只读方式挂载；容器使用非 root、只读根文件系统、无特权、资源限额和 tmpfs 状态。
- `CODING_AGENT_ENABLED=false` 为默认值；Coding Worker 不可用时不得影响核心健康检查。
- 并发上限为 1，Prompt 上限为 20,000 字符，空闲 TTL 为 30 分钟。
- `/coding` 面向没有代码基础的用户，页面不得混入不必要的协议、进程或供应商
  术语；输入区优先显示，服务状态、停止、错误和只读范围必须直接可理解。

## 5. 验收标准

### 正常场景

- Given：管理员显式启用 Coding Agent，隔离 Worker 可用且模型网关配置有效。
- When：用户在 `/coding` 提交针对固定 ModelMirror 工作区的问题。
- Then：页面按序显示计划、聚合工具状态和流式回答；可取消；页面不暴露真实绝对路径或原始协议日志。

### 禁用或故障场景

- Given：功能默认关闭、Worker 缺失、协议帧畸形、请求超时或子进程退出。
- When：浏览器查询能力或发起会话。
- Then：Coding API 返回明确但已脱敏的不可用/失败状态；核心服务健康不受影响；子进程和会话资源被清理。

### 权限绕过场景

- Given：Agent 请求写文件、执行 Shell、访问外部目录/公网或其他未允许能力。
- When：权限请求到达 ACP 客户端，或 OpenCode 权限配置失效。
- Then：协议层拒绝请求，且容器只读挂载/网络边界继续阻止真实源码修改和公网访问。

## 6. 实施批次

1. 批次 0：现场保护与任务契约。
2. 批次 1：供应商无关的领域契约、状态机和 fake adapter。
3. 批次 2：最小 ACP stdio 客户端和 fake ACP 子进程测试。
4. 批次 3：隔离 `coding-runtime` Compose profile、Unix socket 执行面和固定 OpenCode 镜像。
5. 批次 4：FastAPI 只读会话 API、SSE 和运行门禁。
6. 批次 5：独立、懒加载的 `/coding` 前端垂直切片。
7. 批次 6：安全加固、架构/部署说明和整轮验证。

每批固定执行：范围检查、目标测试、`git diff --check`、`git diff --stat`、完整 Diff Review、敏感信息/禁止产物检查；通过后形成一个本地提交。

已形成的本地提交：

| 批次 | 提交 | 状态 |
| --- | --- | --- |
| 0 | `38a4bb1` `docs: 定义 Coding Agent 首轮任务契约` | 通过 |
| 1 | `d04e89f` `feature: 添加 Coding Runtime 领域契约` | 通过 |
| 2 | `42b64f1` `feature: 添加最小 ACP 会话客户端` | 通过 |
| 3 | `a341c50` `feature: 添加只读 ACP 执行容器` | 通过 |
| 4 | `4eafa76` `feature: 添加 Coding Agent 只读会话接口` | 通过 |
| 5 | `7ecdec0` `feature: 添加 Coding Agent 只读工作台` | 通过 |
| 6 | `docs: 完成 Coding Agent 首轮 harness` | 自动门禁通过，随本文件提交 |

## 7. 验证矩阵

| 检查 | 命令或步骤 | 预期 | 状态 |
| --- | --- | --- | --- |
| 基线与隔离 | `git rev-parse HEAD`、`git status --short --branch`、`git worktree list` | 基线正确，新 worktree 干净且独立 | 通过 |
| 后端语法 | `python -m py_compile server/main.py server/coding_runtime/*.py` | 无语法错误 | 通过 |
| Coding 专项测试 | `python -m pytest server/tests/test_coding_runtime_*.py -q` | 全部通过 | 22 passed |
| 后端回归 | `python -m pytest server/tests/ -q` | 全部通过 | 634 passed，4 条既有弃用警告 |
| 前端构建 | `cd client; npm.cmd run build` | 类型检查和构建通过 | 通过；Coding 独立 chunk gzip 5.79 kB |
| 前端体验 | 1440×900 与 390×844 页面检查 | 输入优先、状态易懂、无横向溢出或页面错误 | 通过 |
| Compose 静态验证 | `docker compose -p modelmirror --profile coding config` | 配置可解析且隔离属性存在 | 通过 |
| Docker/人工验收 | 用户执行完整重建和只读真实问答验收 | 流式、取消、拒绝写入/命令/公网均符合契约 | 未运行 |
| 敏感信息扫描 | 检查暂存文件中的密钥模式、`.env`、日志和运行存储 | 无秘密或禁止产物 | 通过；文档仅含明确占位符 |

## 8. 风险与停止条件

- 主要风险：ACP 事件与生命周期不兼容、子进程泄漏、取消竞态、SSE 断线续读错误。
- 兼容风险：新增功能必须保持独立，不修改 `/api/chat`、ChatPage、多模态和核心健康路径。
- 安全风险：权限 fail-open、源码可写、任意命令/路径注入、秘密或绝对路径泄露、Worker 获得公网出口。
- 触发停止的条件：
  - 内部统一事件无法覆盖只读交互。
  - 子进程泄漏、取消不可靠或权限 fail-open。
  - 无法同时满足源码只读、内部模型网关可达和无公网出口。
  - API 泄露绝对路径、密钥或原始 ACP 帧。
  - 前端必须侵入 Chat 主链路或引入大型依赖。
  - 全量回归失败、出现非任务改动、敏感信息或无法独立回退。
- 需要用户确认的问题：整轮自动门禁通过后，必须由用户重建 `coding` profile 并人工验收；验收前不推送、不创建 PR。

## 9. 回退

1. 功能级：设置 `CODING_AGENT_ENABLED=false` 并停止 `coding` profile。
2. 批次级：按 7 个独立 commit 逆序回退到最后一个通过批次。
3. 整轮级：撤销整轮变更并重建核心服务。
4. 持久化影响：无数据迁移，无需恢复数据或版本指针。
5. 回退后验证：核心后端语法/测试、前端构建和默认 Compose 配置继续通过。

## 10. 完成定义

- [x] 7 个批次各自通过门禁并形成 7 个本地 commit。
- [x] 实现只覆盖声明范围。
- [x] 正常、故障和安全拒绝路径均有自动验证。
- [x] 公共接口和无持久化影响已说明。
- [x] Diff 已审查，无用户改动被覆盖。
- [x] 无密钥、运行存储或构建产物进入提交。
- [x] 架构、部署、第三方声明和 Harness 已同步。
- [ ] 用户完成容器重建和真实链路人工验收。
