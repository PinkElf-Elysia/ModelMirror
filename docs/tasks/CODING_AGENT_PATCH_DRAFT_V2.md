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
- 页面显示文件状态、增删行、按文件 Diff、检查结果和 revision。
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
