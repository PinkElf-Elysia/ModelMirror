# 任务卡：CODING-GITHUB-PUBLISH-V8

> 第八轮把已验证并保存的线性本地提交受控发布为 GitHub Draft PR。本任务风险等级
> 为 L4；任何基线漂移、远端分支冲突、重复 PR、凭据泄露或结果不明确都必须停止。

## 1. 基线与交付范围

- 基线：PR #79 合并提交 `97420f3226b2f76110cad7ce1787d65bd8eae1c0`。
- 分支：`codex/coding-github-publish-v8`。
- 工作树：`C:\tmp\modelmirror-coding-v8`。
- 固定发布目标为部署者配置的 GitHub.com 单仓库和 `main` 基础分支。
- 只发布当前任务已经验证、应用并形成的线性本地提交；一次发布冻结整个任务。
- 首次远端结果固定为 Draft PR，用户再次明确确认后才能标记为 Ready。

本轮不包含 GitHub Enterprise、fork、仓库或分支选择、force push、merge/rebase、
合并或关闭 PR、删除远端分支、评论、标签、Reviewer、CI 状态监控和自动修复。

## 2. 不可变安全边界

- Runtime 和浏览器继续不能使用 Shell 或 Git，也不能提交仓库、基础分支、远端分支、
  URL、命令或 Git 参数。
- Publisher 只读挂载现有无 remote 独立仓库；不得写工作区或 `.git`，不得修改本地
  分支、索引、配置和操作日志。
- GitHub App 安装令牌必须限制到配置的 repository ID，只申请 `contents:write`、
  `pull_requests:write` 和 metadata 读取；私钥、App JWT 和安装令牌不得进入响应、日志、
  Git 配置或恢复数据库。
- Publisher 只能通过无凭据 allowlist 代理连接 `github.com:443` 和
  `api.github.com:443`，不得直连公网、宿主服务或 Docker socket。
- 远端 `main` 必须精确等于任务基线；出现新提交时不 push、不创建 PR。
- 本地 HEAD、提交父子链、回执和恢复记录必须一致；远端目标分支只允许不存在或已
  精确指向本次 HEAD，禁止覆盖、删除或非快进更新。
- 发布路径不得包含 `.github/workflows/**`；首版不申请 workflows 写权限。

## 3. 外部操作与恢复契约

- 系统生成稳定分支名 `codex/modelmirror-<task-id>-<head-sha>`，同一发布意图始终复用。
- push 前必须持久化加密操作意图；push、PR 创建和转 Ready 后均先对账再返回终态。
- push 成功而 PR 回执丢失时，通过固定分支、HEAD 和 open PR 查询恢复，不得重复创建。
- 请求失败若可证明尚无远端副作用，可恢复本地撤销能力；远端结果存在或不明确时进入
  冻结或只读冲突态，绝不自动清理远端内容。
- 恢复 schema v3 只加密保存发布意图、标题、说明和回执；不保存令牌、对话、回答、
  工具日志或完整 Git 输出。
- Publisher、App 或 GitHub 不可用时，第七轮草稿、验证、应用、本地提交和恢复保持可用。

## 4. GitHub 官方接口约束

- 安装令牌通过 `POST /app/installations/{installation_id}/access_tokens` 创建，限制到单个
  repository ID 和所需权限；令牌最长约一小时，只驻留 Publisher 内存。
- REST 请求固定发送 `Accept: application/vnd.github+json` 和
  `X-GitHub-Api-Version: 2026-03-10`。
- Draft PR 使用 `POST /repos/{owner}/{repo}/pulls`；重试前按固定 head/base 查询 open PR。
- 转 Ready 使用 GraphQL `markPullRequestReadyForReview`；重复确认若已 Ready 应返回同一结果。

## 5. 批次、验证与停止条件

1. 任务契约。
2. 发布领域与恢复契约。
3. 受控发布与对账引擎。
4. Publisher 与受限出口容器。
5. FastAPI 与恢复编排。
6. 前端发布体验。
7. 安全加固与文档。

每批最多修改 5 个文件；固定执行文件范围检查、专项测试、`git diff --check`、完整
Diff Review、敏感信息和禁止产物扫描，通过后形成一个独立本地提交。

以下任一条件出现时停止：可能执行 Hook、force push、写错仓库、越权联网、泄露凭据、
覆盖远端分支、在基线漂移后写入、产生重复 PR、重启后结果无法精确对账，或 Publisher
故障影响第七轮本地功能。

## 6. 交付、人工验收与回退

- 自动门禁完成后停止；用户取得共享栈独占窗口、配置 GitHub App 并重建后人工验收。
- 人工验收产生的远端测试分支和 Draft PR 不由产品自动清理，只能由用户在 GitHub 明确处理。
- 验收通过前不推送实现分支、不创建实现 PR；失败只追加对应修复提交，不重写历史。
- 设置 `CODING_GITHUB_PUBLISH_ENABLED=false` 或不加载发布 overlay 即恢复第七轮能力。
- 回退不会删除任何已创建的远端分支或 PR，也不会改写本地提交和恢复记录。
