# 任务卡：CODING-PROJECT-HOST-V12

> 第十二轮为 Coding 增加 Windows 本地项目助手。用户可在页面发起一次明确的文件夹选择，在任意本地磁盘上登记干净的独立 Git 项目，并继续使用问答、修改草稿、Diff、验证、命令确认和恢复能力。本轮不写入所选项目。风险等级为 L4；出现物理路径泄露、跨项目读取、未授权宿主写入、助手令牌泄露或快照越界时必须立即停止。

## 1. 基线与单一目标

- 基线：PR #98 合并提交 `dfabc46e7236b1892dfa78a784a8f1787a78eacb`。
- 分支：`codex/coding-project-host-v12`。
- 工作树：`C:\tmp\modelmirror-coding-v12`。
- 单一目标：通过可配对的 Windows 本地项目助手选择、登记和读取宿主机上的干净独立 Git 项目，并把 HEAD 快照安全交给现有 Coding Runtime 与 Verifier。
- `origin/main` 已在实施时推进到 PR #100；后续 Skill/MCP 文件禁止修改，交付前单独审计基线漂移。

## 2. 允许与禁止范围

- 允许修改 Coding Project Host、Project Source、Coding Runtime/API、Coding 前端、Coding 专项测试、可选配置和 Coding/Harness 文档。
- 禁止修改 Skill、MCP、Chat、RAG、Xpert、工作流、多模态、模型目录及其测试和数据。
- 本轮不得由助手写入所选项目，不得运行项目命令、Hook、过滤器、凭据助手、remote、Shell 或网络请求。
- 不提交 `.env`、令牌、宿主路径、恢复数据库、项目快照、便携包、构建产物、日志或缓存。

## 3. 数据与权限边界

- 助手协议固定为 `modelmirror-coding-project-host-v1`，只允许连接 `127.0.0.1` 的 ModelMirror Server。
- 配对码单次使用且最多存活 5 分钟；Server 只保存令牌哈希，助手使用 Windows DPAPI 保存令牌、设备密钥和授权路径。
- 浏览器和 Server 只接收不透明项目 ID、显示名、分支、HEAD、能力和安全原因，不接收物理路径、remote 或 Git 配置。
- 只允许本地磁盘中的干净独立 Git 仓库；拒绝 UNC、远程盘、`.git` 指针、alternates、子模块、符号链接/目录联接、detached HEAD、脏索引和未跟踪文件。
- 已有 remote 可以存在，但助手不得读取、返回或连接 remote；所有 Git 调用使用固定 argv 和净化环境。
- 快照只读取 HEAD blob，路径与内容经助手和 Broker 两次校验；单槽租约结束、失败或服务重启时必须清理。
- 限额保持 50 个登记项目、20,000 个文件、192 MiB 快照、单文件 32 MiB；草稿限额继续为 20 个文件、单文件 512 KiB、Patch 1 MiB。

## 4. 公共行为与恢复

- `/coding` 提供连接助手、输入配对码、添加项目、重命名和移除授权；不显示 DPAPI、socket、租约、绝对路径或协议帧。
- 新项目类型为 `host_git`；活动任务或待恢复草稿存在时锁定项目切换和移除。
- 助手离线、版本不兼容或项目变化时，ModelMirror 内置项目保持完整可用；已有恢复记录仍可查看并下载 Diff。
- 恢复记录只保存不透明项目上下文。项目 HEAD、分支或授权变化时进入只读冲突态，不重新读取其他版本。
- 本轮 `project_host.direct_writeback=false`，直接应用和本地提交留到第十三轮。

## 5. 七个批次与提交

1. 任务契约：`docs: 定义 Coding 本地项目助手任务契约`
2. 配对与领域：`feature: 添加 Coding 项目助手配对与领域契约`
3. Windows 助手：`feature: 添加 Windows 本地项目助手`
4. 快照桥：`feature: 添加 Coding 主机项目安全快照桥`
5. API 与恢复：`feature: 添加 Coding 项目自助接入与恢复接口`
6. 前端体验：`feature: 添加 Coding 本地文件夹选择体验`
7. 加固文档：`docs: 完成 Coding 本地项目助手 harness`

每批最多修改 5 个文件；固定执行范围检查、目标测试、`git diff --check`、完整 Diff Review、敏感信息和禁止产物扫描，通过后形成一个本地提交。

## 6. 停止条件与回退

- 助手能连接非回环地址、伪造项目 ID、绕过配对、越过授权项目或把路径传给浏览器/Server时停止。
- 快照可包含未跟踪文件、工作区内容、敏感路径、符号链接、子模块、越界路径或其他项目内容时停止。
- 断线、取消、超时或重启后租约/单槽未清理，或 ModelMirror 原有闭环回归时停止。
- 设置 `CODING_PROJECT_HOST_ENABLED=false` 即恢复 PR #98 行为；移除助手只撤销授权，不触碰宿主项目。

## 7. 交付门禁

- 自动验证覆盖配对、撤销、DPAPI边界、Git资格、CRLF快照、篡改/超限、断线清理、API、恢复和前端构建。
- 执行 Coding 专项、`py_compile`、全量 `server/tests/`、前端生产构建和全部 Coding Compose配置检查。
- 完成 7 个本地提交后停止；用户确认共享栈窗口前不重建，人工验收通过前不推送、不创建 PR。
