# 任务卡：CODING-VERIFICATION-V3

> 第三轮只为 Coding 草稿增加隔离项目验证，不写回宿主仓库，也不把测试命令交给
> 浏览器或 Agent。本任务风险等级为 L4；路径、进程、网络、快照或输出边界失败时
> 立即停止。

## 1. 单一目标

- 用户在 `/coding` 中对当前 Draft revision 手动运行真实后端测试或前端构建。
- 验证运行在独立、无网络的 `coding-verifier` 中；结果按固定步骤聚合并脱敏。
- Verifier 未启动或故障时，现有 Draft、Diff 和 Patch 下载继续可用。
- 实施基线：PR #70 合并提交
  `53bd7afb9ff0c921c963f7d94f1440da7d65674e`。
- 实施分支：`codex/coding-verification-v3`。
- 独立 worktree：`C:\tmp\modelmirror-coding-v3`。

## 2. 范围与资源预算

- `coding-verifier` 使用独立 `coding-verify` Compose profile。
- 容器固定为非 root、无网络、只读根文件系统、无特权、无宿主端口和 Docker socket。
- 资源上限：2 CPU、3 GiB 内存、256 PIDs；`/workspace` 使用 1 GiB
  `nosuid,noexec` tmpfs。
- 镜像内固定安装基线 Python 和前端锁定依赖；运行时不得联网安装依赖。
- 镜像目标不超过 2.5 GiB；超过时停止并重新拆分依赖层。
- 后端步骤最多 300 秒，前端步骤最多 240 秒，整次验证最多 600 秒。
- Verifier、Worker 和浏览器均不得接收自定义命令、argv、工作目录或验证脚本。

允许修改的主要路径：

- `server/coding_runtime/`
- `server/coding_verifier/`
- `server/tests/test_coding_*.py`
- `docker-compose.yml`
- `client/src/pages/CodingPage.tsx`
- `client/src/components/CodingChangesPanel.tsx`
- `client/src/components/CodingVerificationPanel.tsx`
- `client/src/types/coding.ts`
- `client/src/utils/codingApi.ts`
- Coding 相关架构、部署、接入说明和本任务卡

禁止侵入 ChatPage、`/api/chat`、RAG、工作流、多模态和现有通用 Sandbox 主链路。

## 3. 验证选择与可信边界

- 仅变化 `server/**`：运行后端全量测试。
- 仅变化 `client/**`：运行前端生产构建。
- 同时变化两侧或包含根目录、未知代码路径：运行两项。
- 纯文档变化：结果为“不适用”，不启动重型验证。
- 变化 `server/tests/**` 时，先使用不可修改的基准测试验证草稿代码，再运行包含
  草稿测试变化的测试集。
- 变化 `server/requirements.txt`、`client/package.json` 或
  `client/package-lock.json` 时不下载依赖，结果为“未运行”。
- 固定命令：
  - `python -m pytest -p no:cacheprovider server/tests/ -q`
  - `npm --prefix client run build`
- Coding Worker 只发送当前 revision 的内部生成 Patch、变化路径和快照指纹。
- Verifier 必须独立复核 Patch 限额、路径和文件状态，再应用到临时副本。
- Worker 与 Verifier 的净化基准快照指纹不一致时不得运行验证。

## 4. 公共接口

`GET /api/coding/capabilities` 新增：

```json
{
  "verification": {
    "available": true,
    "strategy": "adaptive",
    "required_for_patch": false,
    "max_duration_seconds": 600
  }
}
```

新增：

- `POST /api/coding/sessions/{id}/verification`
- `GET /api/coding/sessions/{id}/verification?revision=<n>`
- `POST /api/coding/sessions/{id}/verification/cancel`

POST 请求体只允许 `revision`。响应分离运行状态和验证结论：

- `state`：`not_started | running | completed | cancelled`
- `result`：`not_run | passed | failed | not_applicable`

响应包含 revision、stale、固定步骤、起止时间、摘要和有限技术详情。API 不返回
绝对路径、环境变量、完整日志、密钥、原始协议帧或 Verifier argv。

## 5. 生命周期与失败行为

- 只有 Draft 空闲、revision 匹配且轻量检查通过时才能启动验证。
- 验证运行期间禁止开始新 Agent 轮次或放弃草稿；Diff 和 Patch 仍可只读查看。
- 取消必须幂等并终止整个进程组；会话关闭、过期或 Worker 关闭时必须取消并清理。
- revision 更新后旧结果标记 stale，不能作为当前草稿的通过证据。
- Verifier 不可用、指纹不匹配或依赖清单变化只使验证“未运行”，不得把 Draft
  会话标记失败。
- Patch 下载不强制项目验证通过；未验证或失败时由前端明确警告并原位二次确认。

## 6. 用户体验

- 在“本轮修改”中新增“项目验证”，由用户手动触发。
- 步骤使用“检查服务代码”“检查页面构建”等日常语言，不显示容器、pytest、
  npm 或协议术语。
- 运行时逐步显示状态并提供“停止验证”；失败原因默认简述，技术详情折叠。
- “让代码助手修复”只把脱敏、截断的问题摘要填入输入框，不自动提交。
- 未启动服务时显示“项目验证服务未启动，仍可查看和下载修改。”
- 下载警告使用原位确认，不使用模态框。
- 不新增前端依赖；Coding 懒加载块新增 gzip 体积不超过 8 KiB；390 px 页面无横向
  溢出，键盘焦点、ARIA live 和 reduced motion 必须有效。

## 7. 验收与停止条件

必须验证：

- 后端、前端、混合、文档和依赖变化选择规则。
- 基准测试不能被草稿测试变化绕过。
- 正常完成、失败、超时、取消、重复取消、stale 和重跑。
- Verifier 缺失与指纹不匹配时 Draft、Diff 和下载正常。
- 输出截断、路径脱敏、秘密扫描和固定 argv。
- Verifier 无网络、无宿主端口、无密钥、无 Docker socket，根文件系统只读。
- 验收前后真实仓库 `git status` 完全一致。

立即停止：

- Patch 能越界、删除、重命名或写入基准快照；
- 浏览器或 Agent 能改变命令、argv、cwd、环境或测试范围；
- 取消后仍有验证子进程或临时副本；
- Verifier 故障影响 Draft 或核心服务健康；
- API 泄露绝对路径、秘密或完整日志；
- 容器具备公网、宿主仓库或 Docker daemon 访问能力；
- 前端必须侵入 ChatPage、引入新依赖或新增 gzip 超过 8 KiB。

## 8. 交付门禁与回退

- 全轮拆为 7 个批次，每批最多 5 个文件、一个本地 commit。
- 每批执行范围检查、目标测试、`git diff --check`、完整 Diff Review、秘密与禁止
  产物扫描。
- 全轮执行 Coding 专项、后端全量、前端构建、双 profile Compose 配置和容器隔离
  检查。
- 活动栈固定使用 `-p modelmirror`；取得共享栈独占窗口后才可重建。
- 用户完成真实容器验收前不推送、不创建 PR。

回退：

1. 停止并省略 `coding-verify` profile，恢复第二轮 Draft 能力。
2. 设置 `CODING_AGENT_MODE=readonly`，关闭草稿编辑。
3. 设置 `CODING_AGENT_ENABLED=false`，完全关闭 Coding Agent。
4. 按独立 commit 逆序回退；本轮没有数据库迁移或持久化验证结果。

## 9. 实施记录

| 批次 | 本地提交 | 结果 |
| --- | --- | --- |
| 0 任务契约 | 待提交 | 待验证 |
| 1 验证领域契约 | 待提交 | 待验证 |
| 2 隔离验证引擎 | 待提交 | 待验证 |
| 3 专用 Verifier 容器 | 待提交 | 待验证 |
| 4 Worker 与 API | 待提交 | 待验证 |
| 5 前端验证切片 | 待提交 | 待验证 |
| 6 整轮加固与文档 | 待提交 | 待验证 |
