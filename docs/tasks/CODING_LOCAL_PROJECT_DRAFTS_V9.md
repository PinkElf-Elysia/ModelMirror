# 任务卡：CODING-LOCAL-PROJECT-DRAFTS-V9

> 第九轮把 Coding 的只读问答与修改草稿能力扩展到部署者登记的本地独立 Git 克隆。
> 本任务风险等级为 L4；宿主项目写入、项目串读、路径越界、错误恢复或 ModelMirror
> 既有闭环回归都必须停止。

## 1. 基线与单一目标

- 基线：PR #81 合并提交 `4544b75060d01599c70cd486a167a5ef787e37cc`。
- 分支：`codex/coding-local-project-drafts-v9`。
- 工作树：`C:\tmp\modelmirror-coding-v9`。
- 单一目标：用户可从部署者登记的干净独立 Git 克隆中选择一个项目，完成问答、
  新增或修改 UTF-8 文本草稿、Diff 审阅、下载以及重启恢复。
- ModelMirror 仍是默认内置项目，验证、应用、本地提交、恢复和 GitHub 发布完整保留。
- 自定义项目本轮只开放查看、草稿、Diff 和下载；不开放项目验证、应用、提交或发布。

本轮不包含 Agent 命令、删除、重命名、直接写宿主项目、任意绝对路径、多任务、
对话保存、第二 ACP、多 Agent、远程项目或分布式 Worker。

## 2. 受控项目与数据边界

- 部署者只配置一个绝对 `CODING_PROJECTS_ROOT`；清单固定为该根目录下的
  `.modelmirror-coding-projects.json`，版本为 1，最多登记 50 个项目。
- 清单项目路径必须是根目录内规范化相对路径；拒绝绝对路径、`..`、重复路径、
  大小写冲突和任何符号链接链。
- 项目必须是独立 `.git` 目录、有效分支与 HEAD，且工作区、索引和未跟踪文件均为空。
  拒绝 Git worktree 指针、alternates、子模块和 Git 树中的符号链接。
- 允许项目存在 remote，但不得读取、返回或连接 remote；API 不返回项目根目录、
  相对路径、Git 配置或其他物理位置。
- 单项目限制为 20,000 个文件、192 MiB 快照、单文件 32 MiB；敏感路径不进入快照，
  只返回隐藏数量。根目录 `AGENTS.md` 必须是 UTF-8 且不超过 64 KiB。
- 自定义项目草稿继续限制为 20 个变化文件、单文件最终大小 512 KiB、Patch 1 MiB，
  只允许新增和修改 UTF-8 文本。

## 3. 隔离与执行契约

- 只有可选的 `coding-project-source` 只读挂载整个受控项目根目录；Server、Runtime、
  Verifier、Applier、Committer 和 Publisher 都不得看到该根目录。
- Broker 无网络、非 root、只读根目录、无宿主端口、无 Docker socket、无模型或 Git
  凭据；通过私有 Unix socket 只维护一个当前项目快照租约。
- 快照从 Git HEAD blob 生成，只使用固定 `git ls-tree` 与 `git cat-file --batch`，不得运行
  Hook、过滤器、凭据助手、仓库自定义命令或联网操作。
- 快照槽在启动、释放和失败时清空。Runtime 仅只读读取当前租约，必须复核项目 ID、
  HEAD、租约 ID 和指纹后才复制到 `/workspace`。
- OpenCode 固定为 `1.18.9`，ACP v1、Shell/task/web/插件/MCP/外部目录禁令保持不变；
  仓库内 OpenCode、provider、插件、MCP 和可执行配置不得生效。
- 选择一个项目后，其他登记项目的文件、名称之外的元数据和随机标记不得被读取。

## 4. 恢复与兼容契约

- 现有 recovery schema v3 与 SQLite `user_version` 不变；新增独立加密项目上下文表，
  保存项目 ID、类型、显示名和基准 HEAD，不保存宿主路径。
- 旧恢复记录没有项目上下文时自动解释为 ModelMirror，现有恢复、验证、应用、提交和
  发布回执不得改变。
- 项目仍存在、干净且 HEAD 相同时，可从不可变快照和已保存 Patch 精确恢复；项目被
  删除、变脏或 HEAD 变化时进入只读冲突态，只允许查看与下载原始 Diff。
- 继续不保存提问、回答、工具日志或原始协议内容；恢复界面明确说明此前对话未保存。
- 活动会话或待恢复草稿存在时锁定项目选择，必须先结束、下载或放弃当前任务。

## 5. 公共接口与体验边界

- `GET /api/coding/capabilities` 增加 `projects` 能力，不改变旧字段语义。
- `GET /api/coding/projects` 只返回不透明 ID、名称、来源类型、状态、安全原因、分支、
  短 HEAD 和功能矩阵。
- `POST /api/coding/sessions` 可接受可选 `project_id`；无请求体时仍创建 ModelMirror 会话。
- 会话、`session_started` 事件和恢复状态都返回同一份脱敏项目摘要。
- 自定义项目直接调用验证、应用、提交或发布接口时统一返回
  `project_operation_unavailable`，不得执行可选服务探测或产生副作用。
- `/coding` 使用日常语言展示项目选择和功能范围；不显示绝对路径、ACP、容器、租约
  或内部协议术语。390px 视口不得横向溢出，不新增前端依赖。

## 6. 七个批次与验证

1. 任务契约。
2. 项目领域、清单解析与资格规则。
3. 隔离项目快照 Broker、固定 Git plumbing 和容器。
4. Runtime 动态项目来源与受限 `AGENTS.md`。
5. 项目 API、会话编排和加密恢复上下文。
6. 前端项目选择、范围提示和恢复绑定。
7. 架构、部署、接入说明、第三方声明和 Harness 失败经验。

每批最多修改 5 个文件；固定执行文件范围检查、专项测试、`git diff --check`、完整
Diff Review、敏感信息和禁止产物扫描，通过后形成一个独立本地提交。前一批失败时保留
现场，不进入下一批。

整轮自动验证至少包括：项目清单、Git 资格、Broker、租约、快照字节、Runtime、API、
恢复和安全专项测试；`python -m py_compile`；全量 `server/tests/`；前端生产构建；基础、
项目 overlay 和全部既有 Coding overlay 的 Compose 配置检查；ModelMirror 完整闭环回归。

## 7. 验收、停止与回退

人工验收前不得推送或创建 PR；共享栈重建必须取得独占窗口。验收使用随机项目名、文件名
和正文，覆盖项目 A/B 串读、`AGENTS.md`、脏仓库、worktree、alternates、子模块、符号链接、
限额、宿主仓库不变、Server/Runtime/Broker 重启恢复和项目 HEAD 漂移后的只读下载。

以下任一条件出现时停止：Runtime 能看到未选择项目；宿主仓库发生变化；快照可能执行
Hook、过滤器或联网；失败租约未清理；恢复出不同 Diff；项目变化后仍允许继续修改；
Broker 故障影响 ModelMirror；公共响应泄露路径、remote、配置、凭据或原始工具内容。

回退时设置 `CODING_PROJECTS_ENABLED=false` 并省略项目 overlay，恢复第八轮固定
ModelMirror 行为。新增加密项目上下文表由旧逻辑忽略，不修改 recovery schema v3，
不删除恢复记录，也不触碰任何宿主项目。
