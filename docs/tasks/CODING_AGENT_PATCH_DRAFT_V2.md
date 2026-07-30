# 任务卡：CODING-PATCH-DRAFT-V2

> 第二轮只建立可审阅的临时修改草稿，不把 Agent 变成宿主仓库写入器。本任务风险等级为 L4；任何路径、权限、回滚或秘密门禁失败时立即停止。

## 1. 单一目标

- 在 `/coding` 中让 OpenCode 修改容器内一次性 ModelMirror 副本。
- 用户可以查看新增/修改文件、统一 Diff、轻量检查结果，下载 `.patch` 或放弃草稿。
- 真实仓库始终不挂载给 Worker；不自动应用、提交、推送或创建 PR。
- 实施基线：PR #69 后的 `1d83cd119ec398bbf7040d88161b63c82d87809f`。
- 实施分支：`codex/coding-patch-draft-v2`。
- 独立 worktree：`C:\tmp\modelmirror-coding-v2`。

## 2. 范围与限额

- 保留 `CODING_AGENT_MODE=readonly` 默认行为；只有显式设置 `draft` 才开放临时编辑。
- 只允许新增或修改 UTF-8 文本文件。
- 禁止删除、重命名、二进制、符号链接、绝对路径、`..`、环境文件、密钥、Git、依赖、缓存和运行数据。
- 最多 20 个变化文件；单文件最终大小不超过 512 KiB；统一 Patch 不超过 1 MiB。
- 不开放 Shell、Git、项目测试、联网工具、Task、MCP、Skill、插件或外部目录。
- 不新增前端依赖、项目完整测试依赖、数据库或持久化会话。

允许修改的主要路径：

- `server/coding_runtime/`
- `server/tests/test_coding_runtime_*.py`
- `server/coding_worker/Dockerfile`
- `docker-compose.yml`
- `server/.env.example`
- `client/src/pages/CodingPage.tsx`
- `client/src/components/CodingChangesPanel.tsx`
- `client/src/types/coding.ts`
- `client/src/utils/codingApi.ts`
- Coding 相关架构、部署、接入说明和本任务卡

禁止侵入 `/api/chat`、ChatPage、RAG、工作流和多模态主链路。

## 3. 事务与权限契约

- 镜像内基准快照固定在 `/opt/modelmirror-source`；`/workspace` 是 256 MiB 的 `nosuid,noexec` tmpfs。
- 会话创建时复制基准快照；关闭、过期或容器重启后全部清除。
- 每轮开始前建立检查点。
- 取消、模型失败、协议失败或硬性安全违规必须回滚本轮，不影响之前的合法草稿。
- 合法完成后 revision 单调递增；放弃草稿恢复基准并使旧 revision 失效。
- Readonly 模式继续拒绝全部权限请求。
- Draft 模式只处理 OpenCode `edit` 请求：
  - session 必须与当前 ACP session 一致；
  - `rawInput.filepath` 和 `rawInput.diff` 必须存在并通过限额与路径校验；
  - 只选择 `allow_once`；永不选择 `allow_always`；
  - 其他权限请求统一拒绝。

## 4. 公共接口

现有接口保持兼容。`GET /api/coding/capabilities` 的 `mode` 扩展为
`readonly | draft`，Draft 模式返回草稿限额和 `host_apply=false`。

新增：

- `GET /api/coding/sessions/{id}/changes`
- `GET /api/coding/sessions/{id}/diff?path=<relative>&revision=<n>`
- `GET /api/coding/sessions/{id}/patch?revision=<n>`
- `POST /api/coding/sessions/{id}/validate`
- `POST /api/coding/sessions/{id}/discard`

规则：

- Diff/Patch 使用 `Cache-Control: no-store`。
- Patch 只有在草稿非空且轻量检查通过时才允许下载。
- 路径与 revision 不匹配时失败关闭。
- API 不接收命令、工作目录、provider、任意检查器或宿主应用参数。
- 不返回绝对路径、原始 ACP 帧、完整工具输入或秘密。

## 5. 轻量检查

自动检查：

- UTF-8、NUL、符号链接、禁止路径和秘密模式；
- 文件数、文件大小和 Patch 大小；
- Python AST；
- JSON 解析；
- 冲突标记和新增尾随空白；
- 统一 Diff 可稳定生成。

硬性安全失败回滚本轮。语法或质量失败保留草稿供继续修正，但禁止下载 Patch。
轻量检查不等价于 pytest、TypeScript 构建或项目测试。

## 6. 用户体验

- Draft 模式明确显示“修改草稿，不会直接改变项目”。
- 回答完成后自动刷新变化并检查。
- 页面显示文件状态、增删行、按文件 Diff 和检查结果；revision 仅用于接口一致性，
  不要求非技术用户理解。
- 提供“检查修改”“下载 Diff”“放弃修改”；放弃操作需要清晰二次确认。
- 取消或失败后说明“本轮修改已撤销”。
- 页面不出现 ACP、进程、权限协议等非必要术语，不新增大型依赖。

## 7. 验收与停止条件

必须验证：

- 新增/修改文本、跨轮累积、稳定 revision、Diff 和 Patch。
- 取消/失败回滚本轮，放弃恢复基准。
- Python/JSON 错误阻止下载。
- 删除、Shell、`.env`、外部路径、二进制、符号链接和超限修改被拒绝。
- 真实仓库验收前后 `git status` 完全一致。
- Worker 无宿主端口和公网出口；根文件系统与基准快照不可写。
- Readonly 模式行为与首轮保持兼容。

立即停止：

- 无法在编辑前可靠识别目标路径和 Diff；
- 权限 fail-open 或选择了 `allow_always`；
- 取消/失败不能可靠回滚；
- Worker 可以修改宿主仓库、基准快照或访问公网；
- Diff/Patch 泄露绝对路径、秘密或未变化的完整文件；
- 前端必须侵入 ChatPage 或引入大型依赖。

## 8. 交付门禁与回退

- 全轮拆为 7 个批次，每批最多 5 个文件、一个本地 commit。
- 每批执行范围检查、目标测试、`git diff --check`、完整 Diff Review、秘密与禁止产物扫描。
- 全轮执行 Coding 专项、后端全量、前端构建、Compose 静态验证。
- 活动栈使用固定 `-p modelmirror`；只有取得共享栈独占窗口后才能重建。
- 用户完成容器重建和真实 Draft 验收前不推送、不创建 PR。

回退：

1. 设置 `CODING_AGENT_MODE=readonly`，立即恢复首轮能力。
2. 设置 `CODING_AGENT_ENABLED=false` 并停止 `coding` profile。
3. 按独立 commit 逆序回退；本轮没有持久化数据迁移。

## 9. 实施记录

已按小批次形成独立本地提交：

| 批次 | 本地提交 | 结果 |
| --- | --- | --- |
| 0 任务契约 | `f3797a4` | 通过 |
| 1 草稿工作区域模型 | `0aaafc7` | 通过 |
| 2 ACP 编辑权限代理 | `75e8daa` | 通过 |
| 3 临时可写 Worker | `df33c27` | 通过 |
| 4 草稿审阅 API | `f530bc8` | 通过 |
| 5 前端审阅切片 | `0eefc94` | 构建、桌面和 390 px 移动端预览通过 |
| 6 整轮加固与文档 | `dc2ff02` | 通过 |
| 验收修复 | `b9b62a9`、`0d27f12`、`6f6132b` | 修复取消回滚、上下文残留和长轮次误超时 |
| 搜索运行时修复 | 本提交 | 补齐 `ripgrep`、启动预检和单轮步骤预算 |

整轮自动门禁：

- Coding Runtime `py_compile`：通过。
- Coding 领域、ACP、Worker、API 与安全专项：容器环境 `71 passed`。
- `server/tests/` 全量回归：`740 passed`，4 条既有 FastAPI `on_event`
  弃用警告。
- 前端生产构建：通过；`CodingPage` 懒加载块 30.97 kB，gzip 9.78 kB。
- `docker compose -p modelmirror --profile coding config --quiet`：通过。
- 最终范围、Diff、秘密与禁止产物检查：通过。

活动栈已在独占窗口内重建，`server` 与 `coding-runtime` 健康。真实 Draft 已验证
新增与修改、Diff/Patch、轻量检查下载门禁、取消回滚、取消后不续做旧任务、禁止
`.env`、放弃草稿、公网阻断、基准快照不可写，以及重建和验收前后宿主仓库状态
指纹一致。仍待用户完成最终页面人工验收并明确通过；通过前不得推送或创建 PR。

最终页面验收期间发现两类失败，门禁保持关闭：

- 首次失败由通用 120 秒请求超时误杀持续活动的长轮次；`6f6132b` 已拆分控制请求、
  Prompt 绝对超时和空闲超时，并补齐失败回滚与会话恢复测试。
- 再次失败的结构化记录显示，Worker 缺少 OpenCode `glob` / `grep` 所需的
  `ripgrep`，造成 13 次搜索失败、6 次无效 Shell 尝试和 16 次逐段读取，最终因
  上下文膨胀触发模型网关额度拒绝。修复批次固定安装并启动预检 `ripgrep`，同时
  将单轮 Agent 步骤限制为 12；达到上限后只输出当前结果，草稿仍由既有检查和
  回滚边界保护。

修复后专项、全量后端、前端构建、Compose 配置、无网络 OpenCode 配置解析和镜像
构建均通过。共享栈重建和再次页面人工验收仍未执行，门禁继续关闭。

再次验收在首次模型请求时立即失败。结构化错误确认 newAPI 剩余额度约 1.57 美元，
而未设输出上限的请求需要预扣约 2.71 美元；本次尚未调用工具或产生草稿。为限制
单请求成本并避免网关按过大的默认值预扣，自定义模型显式设置 131,072 token 上下文
和 8,192 token 输出上限。外部额度不足仍会失败关闭，不得转换为成功结果。

页面刷新后曾因前端丢失会话编号、后端仍保留单实例草稿而误报并发占用。TTL 继续
保持 30 分钟以保护待审阅草稿；同一浏览器标签现在使用 `sessionStorage` 保存会话
编号和最后事件序号，刷新后从下一条事件继续，服务重启导致编号失效时则安全清除。

修复提交 `b5a5d65` 后，在共享栈独占窗口内仅重建 `server`、`coding-runtime` 和
`client`。三项服务启动正常，Coding 能力返回 `enabled=true`、`available=true`、
`mode=draft`；已部署 131,072/8,192 token 请求预算。Worker 继续满足非 root、只读
根文件系统、无特权、无宿主端口、基准快照不可写、内部网关可达且公网阻断。重建
前后独立工作树保持干净，主工作树状态指纹一致。旧内存会话已随服务重启清除。
用户从刷新后的 `/coding` 完成真实 Draft 测试并于 2026-07-30 明确验收通过，
人工门禁现已开放，可以执行最终基线漂移检查、推送分支并创建 ready PR。
