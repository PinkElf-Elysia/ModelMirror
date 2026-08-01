# 任务卡：CODING-TASK-RECOVERY-V6

> 第六轮只恢复 Coding 最近一次完整草稿及其安全操作状态，不保存提问、回答、
> 工具日志或原始命令输出。本任务风险等级为 L4；出现明文敏感内容、恢复越界、
> 状态不明时继续写入或错误数据根目录时必须立即停止。

## 1. 单一目标

- 用户重启 Server、Runtime、Verifier、Applier 或 Committer 后，可以继续最近一份
  未完成的修改草稿，并查看此前的轻量检查、项目验证、应用和本地提交状态。
- 恢复使用不可变基准和经过复核的统一 Diff 重建工作区；处理中断的半轮修改不恢复。
- 版本或外部状态不再一致时只允许查看和下载 Diff，不允许继续写入、撤销或提交。

本轮明确不做：保存对话、保存原始工具过程、多任务历史、远程仓库、push、产品内
PR、多 Agent、跨项目恢复或生产级多租户。

实施基线：PR #74 合并提交
`40785208e68d6ca4a26474196fb7c3a2661b4919`。

实施分支：`codex/coding-recovery-v6`。

实施工作树：`C:\tmp\modelmirror-coding-v6`。

## 2. 已证实事实与设计约束

| 结论 | 等级 | 证据 |
| --- | --- | --- |
| FastAPI 会话、事件、ApplyReceipt 和 CommitReceipt 当前只保存在进程内存 | 已证实事实 | `server/coding_runtime/api.py` 的 `CodingApiSession` 与 `_sessions` |
| Worker 草稿和检查点位于容器 tmpfs，进程关闭后清理 | 已证实事实 | `server/coding_runtime/worker.py`、`docker-compose.yml` |
| ApplyReceipt 和 CommitReceipt 已包含 revision、文件哈希及 Git 对账所需标识 | 已证实事实 | `server/coding_runtime/apply_models.py`、`commit_models.py` |
| 项目已有 SQLite WAL 与 Fernet 本地密钥模式，无需新增第三方依赖 | 已证实事实 | `server/model_router/repository.py`、`server/requirements.txt` |
| 只持久化安全草稿与结果可以恢复工作，同时避免保存用户对话 | 用户确认 | 第六轮计划确认 |

## 3. 不可变安全边界

- 最多保存一份恢复记录，默认保留 7 天；到期或用户放弃时只删除恢复数据，不修改
  专用项目副本或本地 Git 提交。
- SQLite 只保存非敏感索引字段和 Fernet 密文。Patch、文件列表、验证详情、应用回执、
  提交回执和提交说明均置于认证加密负载中。
- 密钥文件已存在而不可读、数据库存在但密钥缺失、密文被篡改或 schema 不兼容时
  必须失败关闭；不得生成新密钥覆盖旧数据，不得返回损坏负载。
- 不持久化 Prompt、回答、事件增量、计划、工具输入、原始 ACP 帧、完整验证日志、
  环境变量、绝对路径、模型密钥或 Git 凭据。
- 恢复 Patch 必须重新执行现有路径、文件类型、编码、文件数、单文件和 1 MiB Patch
  限额检查；不得调用 Shell、Git、Hook 或仓库命令来重建草稿。
- 只有基准指纹和验证策略指纹完全一致时才保留验证结论；否则结论标记过期。
- `applying/reverting/committing/undoing` 等中间状态只能通过目标内容和 Git 元数据的
  精确只读对账转为确定状态；无法确定时进入只读冲突态。
- 恢复能力不得扩大 Runtime、Verifier、Applier、Committer 的 socket、网络、挂载、
  环境变量或宿主路径访问范围。

## 4. 公共行为与数据生命周期

- `GET /api/coding/capabilities` 增加 `recovery`，公开启用状态、可用性、是否存在记录、
  保留秒数及 `restores_conversation=false`。
- 新增 `GET /api/coding/recovery`、`POST /api/coding/recovery/resume`、
  `POST /api/coding/recovery/discard`、`GET /api/coding/recovery/patch`。
- 恢复接口只操作唯一恢复记录，不接受路径、revision、命令、分支或任意扩展字段；
  响应统一 `Cache-Control: no-store`。
- Agent 一轮完成时，先取得并加密保存安全快照，再向页面公开完成事件。取消、失败或
  Server 崩溃时只保留上一份完整 revision。
- 活跃会话空闲 30 分钟后关闭 Worker，但保留恢复记录。存在恢复记录时，新建会话返回
  `recovery_pending`，必须继续或放弃后才能新建。
- 同基准恢复时创建新的内部 Agent 会话，不恢复对话；页面提示“已恢复上次修改，
  此前对话未保存”。版本不匹配时允许下载保存的 Diff，但禁止恢复执行面。
- 默认保留期为 604800 秒，可由部署环境缩短；取值必须受限，浏览器不得修改。

## 5. 允许与禁止范围

主要允许修改：

- `server/coding_runtime/`、`server/coding_applier/`、`server/coding_committer/`
- `server/tests/test_coding_*.py`
- Coding 页面、组件、类型和 API 工具
- 可选 Recovery Compose overlay、Coding 架构、部署、接入说明和本任务卡

禁止侵入：

- `/api/chat`、ChatPage、多模态、RAG、工作流、模型目录和通用 Sandbox 主链路
- 当前主工作树、V1 至 V5 工作树、其他工作树和共享 Git 历史
- 用户 `.env`、现有持久化业务数据、日志、构建产物和真实凭据

每批最多修改 5 个文件。前一批完成目标测试、文件范围检查、`git diff --check`、完整
Diff Review、敏感信息和禁止产物扫描并形成独立本地 commit 后，才进入下一批。

## 6. 验收场景

### 完整草稿恢复

- Given：随机新增或修改 1 至 3 个安全文本文件，并完成一轮处理。
- When：分别重启 Server、Runtime 和完整 Coding 容器组，然后选择继续上次修改。
- Then：revision、文件列表和 Diff 精确一致；页面不出现此前提问、回答或工具过程。

### 验证结果恢复

- Given：当前 revision 的项目验证完成。
- When：以相同基准和验证策略重启。
- Then：结论和脱敏步骤摘要恢复；任一指纹变化后结论变为过期并要求重新验证。

### 外部操作中断对账

- Given：应用、撤销、提交或撤销提交在外部动作执行前后发生故障。
- When：服务恢复并执行只读对账。
- Then：只在目标状态精确匹配时恢复确定结果；不重复写入或提交，不覆盖外部修改。

### 版本或数据损坏

- Given：基准变化、目标被人工修改、密钥缺失或密文被篡改。
- When：查询或继续恢复记录。
- Then：失败关闭并给出安全原因；版本变化仍可下载 Diff，密文损坏不得泄露明文。

### 数据根目录预检

- Given：从独立工作树准备重建共享栈。
- When：数据根目录为相对路径、缺少 `server/.env`、目标仓库状态不合规或源码指纹不同。
- Then：预检返回非零并在 Compose 重建前停止，不创建目录、不打印密钥。

## 7. 批次、验证与停止条件

1. 任务契约。
2. 加密恢复领域与存储。
3. 草稿工作区安全重建。
4. 应用与提交故障对账。
5. Worker 与 FastAPI 恢复编排及可选 Compose overlay。
6. 前端恢复体验。
7. 整轮加固、预检和文档。

固定自动门禁：Coding 专项测试、Python 语法检查、全量 `server/tests/`、前端生产构建、
基础及全部 Coding overlay 的 Compose config、持久化明文扫描和最终 Diff Review。

立即停止条件：

- 敏感负载明文落盘，旧数据库被静默覆盖，或损坏密文 fail-open。
- 恢复绕过路径/限额/编码校验，生成与保存记录不同的 Diff，或留下半轮修改。
- 不明确的外部状态可继续应用、撤销、提交或撤销提交。
- 恢复功能不可用时影响第五轮 Draft、Diff、验证、应用、提交或核心健康。
- 前端自动覆盖恢复记录、侵入 ChatPage、引入新依赖，或懒加载增量超过 8 KiB gzip。
- 共享栈预检允许相对/错误数据根目录，或出现非任务改动、敏感信息、回归失败。

## 8. 回退与交付门禁

1. 不加载 `docker-compose.coding-recovery.yml` 或设置
   `CODING_RECOVERY_ENABLED=false`，恢复第五轮内存会话行为。
2. 回退本轮提交不删除恢复目录；需要清理时由用户明确授权，且只清理 Coding 恢复数据。
3. 恢复记录删除不改变专用副本文件或本地提交；外部状态冲突由用户重建专用仓库处理。
4. 整轮本地提交和自动验证完成后停止。用户取得共享栈独占窗口、重建并明确验收通过前，
   不推送、不创建 PR。

## 9. 完成定义

- [ ] 7 个小批次均形成独立本地提交和验证记录。
- [ ] 正常恢复、过期、篡改、错误密钥、指纹失配和故障对账均有自动测试。
- [ ] 数据库扫描确认不含随机 Prompt、回答、模拟密钥和明文 Patch。
- [ ] 公共接口、状态、错误、空态、降级和回退均已记录。
- [ ] 实现工作树和主工作树保持无额外改动。
- [ ] 用户人工验收通过前未推送、未创建 PR。
