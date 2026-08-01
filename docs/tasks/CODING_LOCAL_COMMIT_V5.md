# 任务卡：CODING-LOCAL-COMMIT-V5

> 第五轮只把已经验证并应用到专用副本的修改保存为隔离本地 Git 提交。
> 本任务风险等级为 L4。任何共享 Git 元数据可写、远程操作、Hook/过滤器执行、
> 半提交状态或撤销覆盖文件的情况都必须立即停止。

## 1. 单一目标

- 用户在 `/coding` 明确确认后，把当前 revision 对应的已应用文件保存为一个真实
  本地提交。
- 提交只存在于无远程、独立 `.git` 的专用克隆；不接触当前主工作树、不推送、
  不创建 PR。
- 系统给出中文提交说明建议，用户可编辑；撤销提交只恢复 Git 元数据并保留文件，
  之后可重新提交或撤销第四轮应用。

实施基线：PR #72 合并提交
`eb747a1edfb4c8d267b5a3a8efdb31a2b4577dc0`。

实施分支：`codex/coding-local-commit-v5`。

实施工作树：`C:\tmp\modelmirror-coding-v5`。

人工验收目标：`C:\tmp\modelmirror-coding-repository-v5`，只在整轮本地提交完成后
从最终 HEAD 创建独立克隆。

## 2. 不可变安全边界

- `coding-committer` 使用独立 Unix socket，只有 Server 挂载；Coding Runtime、
  Verifier 和 Applier 不得访问。
- `/target` 只读，只有 `/target/.git` 单独可写；目标必须是独立 Git 目录，拒绝
  worktree gitfile、`commondir`、alternates、远程地址、错误分支和共享对象库。
- 固定分支为 `coding/local-draft`。浏览器和 Agent 不得提交路径、分支、作者、
  Git 参数或命令。
- Committer 无网络、非 root、只读根文件系统、无特权、无宿主端口、无 Docker
  socket、无模型密钥和 Git 凭据。
- 只使用 ApplyReceipt 中已通过路径和哈希检查的新增/修改文本文件；继续遵守
  20 文件、单文件 512 KiB、总 Patch 1 MiB 限额。
- 使用临时索引、固定 Git plumbing 和 compare-and-swap 引用更新；不得执行
  Hook、签名、clean/smudge filter、凭据助手或仓库提供的命令。
- 提交失败必须恢复索引和分支引用。撤销只移动本次提交引用，文件内容不得改变；
  任何外部变化都必须拒绝撤销。

## 3. 公共行为

- capabilities 增加本地提交的配置、可用性、固定目标、消息限额、撤销能力和
  `remote_operations=false`；不返回真实路径或 Git 配置。
- 新增 commit、commit status 和 commit undo 接口；请求体禁止额外字段，响应
  全部使用 `Cache-Control: no-store`。
- 一个会话只允许一个有效提交；重复请求返回同一结果。撤销后可修改说明重新提交。
- 有效提交存在时禁止撤销应用；先撤销提交并保留文件，才可继续撤销应用。
- Committer 缺失或目标不合格时，Draft、Diff、验证、Patch、应用和尚未提交时的
  应用撤销继续工作。
- 页面使用“本地提交”“保存一个可找回的本地版本”等日常语言，明确不会上传；
  技术原因默认折叠。

## 4. 允许与禁止范围

主要允许修改：

- `server/coding_runtime/`
- `server/coding_committer/`
- `server/tests/test_coding_*.py`
- `docker-compose.coding-commit.yml`
- Coding 页面、组件、类型和 API 工具
- Coding 架构、部署、接入说明和本任务卡

禁止侵入：

- `/api/chat`、ChatPage、多模态、RAG、工作流和通用 Sandbox 主链路
- 当前主工作树、现有专用 worktree 和其他工作树源码
- 用户 `.env`、密钥、运行日志、持久化业务数据和共享 Git 历史

每批最多修改 5 个文件，完成目标测试、范围检查、`git diff --check`、完整 Diff
Review、敏感信息和禁止产物扫描后形成一个本地 commit。

## 5. 停止条件

- Committer 能写工作区文件、共享 `.git`、当前主工作树或配置外目录。
- Git worktree、远程地址、alternates、共享对象库或错误分支仍可提交。
- 浏览器或 Agent 能控制路径、分支、作者、Git 参数、环境或命令。
- Hook、过滤器、签名器、凭据助手或外部程序能够执行。
- 任一故障留下半提交、脏索引、错误引用，或撤销改变/覆盖文件。
- 有效提交存在时仍可直接撤销应用。
- Committer 故障影响 Draft、Diff、Verifier、Patch、应用或核心服务健康。
- 前端侵入 ChatPage、引入新依赖，或 Coding 懒加载增量超过 8 KiB gzip。
- 出现非任务改动、敏感信息、全量回归失败或无法独立回退。

## 6. 批次与交付门禁

1. 任务契约。
2. 提交领域契约。
3. 原子提交与撤销引擎。
4. 隔离 Committer 容器。
5. Worker 与 FastAPI 接口。
6. 前端本地提交体验。
7. 整轮加固与文档。

- [x] 前 6 个小批次均有独立本地提交和验证记录；本任务卡更新将形成第 7 个提交。
- [x] 自动验证、最终 Diff Review、敏感信息扫描和回退演练完成。
- [ ] 从最终 HEAD 创建无远程、固定分支的独立验收克隆。
- [ ] 用户完成共享栈重建与真实提交、撤销、故障和隔离验收。
- [ ] 用户明确验收通过前不推送、不创建 PR。

已完成的批次证据：

| 批次 | 本地提交 | 关键验证 |
| --- | --- | --- |
| 0 | `e0967fa` | 基线、范围、停止条件与回退契约审阅。 |
| 1 | `0391791` | 提交状态、说明建议/校验和幂等领域测试 11 项通过。 |
| 2 | `52257fb` | 独立仓库、原子提交/撤销、Hook/过滤器与故障测试 11 项通过。 |
| 3 | `fb538a5` | socket 服务测试 3 项、Compose 隔离检查及镜像构建通过。 |
| 4 | `c91e5d5` | Committer socket 与 FastAPI 提交/互锁测试合计 26 项通过。 |
| 5 | `080c3a8` | API 测试 22 项、生产构建和桌面 DOM 预览通过；懒加载 gzip 增量约 2.23 KiB。 |

批次 6 完成后只运行自动验证、创建独立验收克隆并构建四个 Coding 执行镜像；不
启动共享栈。容器人工验收、推送和 ready PR 仍由用户门禁控制。

整轮自动门禁记录（2026-07-31）：Coding 专项 `177 passed`；全量后端
`846 passed, 4 warnings`；Coding Python 语法检查与前端生产构建通过；基础、
Apply overlay 和 Commit overlay 的 Compose 配置均通过。Committer 渲染结果为
非 root、`network_mode: none`、只读根目录、无端口、`cap_drop: ALL`，环境变量仅
含作者和 socket 配置。省略 Commit overlay 的配置演练通过，第四轮能力可独立回退。

## 7. 回退

1. 不加载 `docker-compose.coding-commit.yml` 或停止 `coding-commit` profile，恢复
   第四轮能力。
2. 提交中失败由引擎恢复索引与引用；成功后会话有效期间可安全撤销提交。
3. 会话失效后保留本地提交，由开发者人工检查或删除并重建独立克隆。
4. 按独立提交逆序回退；本轮没有数据库迁移，也不持久化 Prompt、回答或 Patch。
