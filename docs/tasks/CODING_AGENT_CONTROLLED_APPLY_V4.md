# 任务卡：CODING-CONTROLLED-APPLY-V4

> 第四轮只把已经审阅并满足验证门禁的 Coding 草稿应用到固定的专用工作树。
> 本任务风险等级为 L4。任何越界写入、部分写入、错误覆盖、Git 元数据可写或绕过
> 验证门禁的情况都必须立即停止。

## 1. 单一目标

- 用户在 `/coding` 明确确认后，将当前 revision 的合法 Patch 写入开发者预先创建的
  专用工作树。
- 应用后保留只读审阅结果，允许一次冲突安全的撤销，并由用户显式结束会话。
- 本轮不写当前主工作树，不提交、推送或创建 PR，不开放 Agent Shell、Git、路径选择
  或多次增量应用。

实施基线：PR #71 合并提交
`5f0502c483af6153fa051875a15c77d6c175f554`。

实施分支：`codex/coding-controlled-apply-v4`。

实施工作树：`C:\tmp\modelmirror-coding-v4`。

人工验收目标：`C:\tmp\modelmirror-coding-apply-target-v4`，只在整轮本地提交完成后
从最终 HEAD 创建。

## 2. 不可变安全边界

- `coding-applier` 使用独立 Unix socket，只有 Server 挂载；Coding Runtime 和
  Verifier 不得访问。
- 目标路径只来自部署时的 `CODING_APPLY_WORKTREE`，浏览器和 Agent 不得提交路径、
  命令、分支名或 Git 参数。
- 目标除 `.git` 外必须与 Applier 内置基准快照完全一致；`.git` 必须被独立只读挂载。
- Applier 无网络、非 root、只读根文件系统、无特权、无宿主端口、无 Docker socket、
  无模型或网关密钥。
- 继续执行 Draft 的 20 文件、单文件 512 KiB、总 Patch 1 MiB 限额；只允许新增和
  修改 UTF-8 文本，不允许普通操作删除、重命名、二进制、符号链接或禁止路径。
- 应用前轻量检查必须通过；项目验证必须是当前 revision、非 stale，且结果为
  `passed`，纯文档允许 `not_applicable`。`failed`、`not_run`、`cancelled`、
  `running`、依赖变化和 stale 一律拒绝。
- 任一步失败必须恢复全部已写文件。撤销只恢复本次应用，且目标出现任何外部变化时
  拒绝执行，不覆盖人工修改。

## 3. 公共行为

- capabilities 增加受控应用的配置、可用性、固定目标类型、验证要求和撤销能力；
  不返回真实路径。
- 新增 apply、apply status、revert 和 close 接口；请求体禁止额外字段，响应全部
  `Cache-Control: no-store`。
- 相同会话和 revision 的重复应用返回原结果，不再次写入。
- 成功应用后会话冻结；Diff、Patch、变化和验证结果继续可读，修改、放弃、重新验证
  和重复应用被拒绝。
- Applier 缺失或目标不就绪时，Draft、Diff、Verifier 和 Patch 下载继续工作。
- 页面使用“本地项目副本”“应用修改”“撤销本次应用”等日常语言，不展示容器、
  socket、协议、绝对路径或命令。

## 4. 允许与禁止路径

主要允许修改：

- `server/coding_runtime/`
- `server/coding_applier/`
- `server/coding_verifier/`
- `server/tests/test_coding_*.py`
- `docker-compose.coding-apply.yml`
- Coding 页面、组件、类型和 API 工具
- Coding 架构、部署、接入说明和本任务卡

禁止侵入：

- `/api/chat`、ChatPage、多模态、RAG、工作流和现有通用 Sandbox 主链路
- 当前主工作树和其他工作树的源码
- 用户 `.env`、密钥、运行日志、持久化业务数据和 Git 历史

每批最多修改 5 个文件，完成目标测试、范围检查、`git diff --check`、完整 Diff
Review、敏感信息和禁止产物扫描后形成一个本地 commit。

## 5. 停止条件

- Applier 能写入 `.git`、当前主工作树、基准快照或配置外目录。
- Patch 可越界、跟随符号链接、删除或重命名普通文件。
- 多文件失败后目标留下部分修改，或撤销覆盖外部变化。
- 未通过验证、stale revision 或依赖变化能够应用。
- 浏览器或 Agent 能控制路径、命令、cwd、环境、Git 参数或测试范围。
- Runtime/Verifier 能访问应用 socket，或 Applier 获得公网、密钥、端口或 Docker
  daemon。
- Applier 故障影响 Draft、Diff、Verifier、下载或核心服务健康。
- 前端必须侵入 ChatPage、引入新依赖，或 Coding 懒加载增量超过 8 KiB gzip。
- 出现非任务改动、敏感信息、全量回归失败或无法独立回退。

## 6. 验证矩阵

| 检查 | 预期 | 状态 |
| --- | --- | --- |
| Patch 与领域测试 | 路径、限额、状态和错误码稳定 | 未运行 |
| Applier 引擎 | 原子应用、幂等、自动恢复和冲突撤销 | 未运行 |
| API 与 Worker | 精确 revision 门禁、冻结、降级和 close | 未运行 |
| 容器安全 | 无网、非 root、`.git` 只读、独立 socket、无密钥 | 未运行 |
| 前端生产构建 | 无新依赖，交互和体积门禁通过 | 未运行 |
| 全量后端回归 | `server/tests/` 全部通过 | 未运行 |
| Compose | 基础配置与显式 apply overlay 均可解析 | 未运行 |
| 人工验收 | 只有专用目标产生预期变化，撤销恢复基准 | 未运行 |

## 7. 回退

1. 不加载 `docker-compose.coding-apply.yml` 或停止 `coding-apply` profile，恢复第三轮
   能力。
2. 应用中失败由引擎自动回滚；成功后会话有效期间可安全撤销。
3. 会话失效后删除并重建专用目标工作树；不得自动清理用户工作树。
4. 按独立提交逆序回退；本轮没有数据库迁移，也不持久化 Prompt、回答、Patch 或
   应用凭据。

## 8. 交付门禁

- [ ] 7 个小批次均有独立本地提交和验证记录。
- [ ] 自动验证、最终 Diff Review、敏感信息扫描和回退演练完成。
- [ ] 最终本地 HEAD 的专用目标工作树已建立，但不接触当前主工作树。
- [ ] 用户完成共享栈重建与真实应用、撤销、故障和隔离验收。
- [ ] 用户明确验收通过前不推送、不创建 PR。
