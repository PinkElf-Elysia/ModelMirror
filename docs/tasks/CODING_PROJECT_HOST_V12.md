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

## 8. 批次执行记录

| 批次 | 本地提交 | 结果 |
| --- | --- | --- |
| 0 | `325663c` | 固定 PR #98 基线、L4 数据边界、停止点和回退。 |
| 1 | `718316a` | 完成路径无关的配对、主机与项目领域契约。 |
| 2 | `ef29e1d` | 完成 Windows DPAPI、系统文件夹选择和 Git 资格检查。 |
| 3 | `996863d` | 完成 HEAD blob 快照、分块传输和 Broker 双重校验。 |
| 4 | `76be434` | 完成主机项目 API、动态租约及项目化恢复。 |
| 5 | `fd633a6` | 完成连接、添加、重命名、移除授权和项目选择体验。 |
| 6 | 本提交 | 完成独立部署 overlay、协议加固、文档与全量门禁。 |

批次 5 生产构建中 Coding 懒加载块为 33.78 KiB gzip；相对任务卡记录的第十一轮
30.94 KiB 增加约 2.84 KiB，低于 8 KiB 门禁，且未增加前端依赖。助手状态不保存
配对码、明文令牌、令牌前缀或宿主路径；便携包和构建产物均不纳入 Git。

## 9. 交付状态与下一轮边界

第十二轮完成自动验证后必须停在容器重建和人工验收之前。本任务没有占用或迁移共享
栈；不得在当前分支提前实现第十三轮。第十三轮必须以第十二轮合并后的最新
`origin/main` 为基线，另建 `codex/coding-direct-writeback-v13` 分支，避免把用户
项目写入能力与首次接入同时交付。

## 10. 部署与失败经验

便携助手由 `scripts/build-coding-project-host.ps1` 生成，固定依赖
`websockets==16.0` 与 `pyinstaller==6.14.1`，构建产物不提交。主机项目不要求旧版
`CODING_PROJECTS_ROOT`，部署时加载 `docker-compose.coding-project-host.yml`：

```powershell
$env:CODING_PROJECT_HOST_ENABLED='true'
docker compose -f docker-compose.yml -f docker-compose.coding-project-host.yml `
  -f docker-compose.coding-commands.yml -p modelmirror `
  --profile coding --profile coding-verify --profile coding-project-host config --quiet
```

需要恢复时再按原有预检门禁加载 `docker-compose.coding-recovery.yml`。独立 overlay
只增加路径无关状态卷、短时上传 tmpfs、单槽快照 tmpfs 与无网络 Project Source；
Server 和浏览器仍看不到物理路径。省略 overlay 或设置
`CODING_PROJECT_HOST_ENABLED=false` 即回到 PR #98。

本轮确认的失败经验：v1 协议不能只校验语义版本格式，必须拒绝主版本不兼容的助手；
令牌前缀也属于不必要的认证材料，不落盘；助手离线时保留摘要用于恢复判断，但显式
撤销时必须同步移除项目摘要。Project Source 新增导入模块后，Dockerfile必须同步复制
模块并预建只读根目录中的上传挂载点，否则 Compose 配置虽通过、容器仍会启动失败。
前端只能低频轮询单个配对或选择请求，不能借此刷新整页或重建活动会话。

便携包门禁首次执行还暴露出构建脚本把绝对输出路径再次拼接到仓库根目录的问题；脚本
现在区分空值、绝对路径和相对路径，默认写入 Windows 临时目录。最终压缩包为
12.90 MiB，SHA-256 为
`5482BC66874D939ECA6CEAADE37DE74A105F3F013C5D94D41C80CF4C39B63D1D`。该修复使用
独立提交追加，没有压缩或重写已完成的 7 个批次。

## 11. PR #102 定向集成与共享栈重建入口

- 最新基底固定为 PR #102 合并提交
  `151e79b57b34d2c81e65caa66182a44113c047e5`；该提交已线性包含 PR #99、#100 和
  #101 的 Skill、MCP 与多模态增量。
- 集成工作树固定为 `C:\tmp\modelmirror-coding-v12-102`，分支为
  `codex/coding-project-host-v12-102`。旧工作树
  `C:\tmp\modelmirror-coding-v12` 仅保留原始 #98 批次历史，不得再用于共享栈重建。
- 第十二轮 9 个提交按原顺序 cherry-pick 到 #102，`git range-diff` 九项均为 `=`，
  没有冲突、漏提交或内容改写。共享栈只能从本节指定的新工作树执行 Compose。
- 集成后 Coding 专项 414 项、Project Host 专项 36 项、前端生产构建和 9 组 Coding
  Compose 配置全部通过；Coding 懒加载块仍为 33.78 KiB gzip。
- 全量后端得到 1197 项通过。剩余 7 项 Agent Workspace 失败可在纯 #102 归档中
  独立复现，原因为基线内置 Skillset 摘要不一致；另一个 Node matcher 用例在同时具备
  Python/Node 的 Verifier 镜像中通过。本轮不得跨范围修改这些并行模块。

完成共享栈独占窗口确认前仍不得重建。重建前必须再次确认当前工作树 HEAD、
`origin/main` 和 Compose 配置；若 `origin/main` 已继续推进，应重新执行同样的定向
集成审计，不能从旧镜像或旧工作树覆盖共享栈。

共享栈若要同时保留第十一轮清单项目和第十二轮 Windows 助手项目，必须在
`docker-compose.coding-projects.yml` 与 `docker-compose.coding-project-host.yml`
之后继续加载 `docker-compose.coding-project-host-full.yml`。该兼容 overlay 显式
重建 Project Source 的隔离列表与挂载集合，同时保留只读 `CODING_PROJECTS_ROOT`、
助手短时上传 tmpfs 和单槽快照；缺少它时两个来源 overlay 会产生重复
`security_opt`，Compose 将拒绝解析。仅使用其中一种项目来源时不要加载兼容 overlay。

## 12. 后续规划：开放文件夹与任意文件模式

本轮“干净、独立且已有提交的 Git 项目”只是验证 Windows 助手、快照隔离和恢复链路的
首个受控切片，不代表“真正的自定义项目”的最终产品边界。后续目标应接近 OpenCode 的
本地使用方式：用户可以选择任意本地文件或文件夹，不以 Git、分支、HEAD 或干净状态
作为问答、阅读、草稿和 Diff 的前置条件。本节只记录路线，不在第十二轮实现。

资格检查应从“整项拒绝”改为“能力识别与降级”：

- 非 Git 文件夹、空仓库、detached HEAD、普通 worktree、存在未提交或未跟踪内容的项目
  均可进入问答和草稿模式；只有确实依赖 Git 的提交、撤销提交和发布能力才按条件关闭。
- 用户明确选择的文件应可进入项目上下文，不因扩展名或是否被 Git 跟踪而直接拒绝。
  文本文件支持草稿和 Diff；暂不能安全改写的二进制或超大文件仍可展示、引用和下载，
  页面明确说明能力差异，不把整个项目标记为不可用。
- 快照基准优先使用“用户选择时的文件系统状态”，Git HEAD 只作为可选的版本信息和后续
  提交能力来源，不能继续成为通用项目的唯一基准。

安全策略改为两级：只有越过用户所选范围、未经确认写入或删除宿主文件、静默访问
remote/公网、泄露凭据以及可能留下部分写入等完整性风险继续硬性阻断；脏工作区、无
Git、detached HEAD、worktree、敏感文件候选、验证失败或未运行、规模超过推荐值等改为
清楚展示风险，并允许用户对当前项目快照或当前 revision 选择“仍然继续”。风险确认在
项目内容变化后失效，不使用一次确认永久放开所有项目。

建议按以下小步补齐：

1. 增加任意文件/文件夹只读快照，支持非 Git 和脏目录的问答、草稿与下载。
2. 增加可选择的包含/排除清单及敏感文件逐项确认，避免只能“全部允许或全部拒绝”。
3. 为任意文件夹增加带备份回执和原文件哈希保护的原子写入；验证结论只作为风险提示。
4. 检测到合格 Git 能力时再渐进开启本地提交和发布，不让 Git 门禁阻塞基础 Coding。

体验与性能门禁同步调整：项目接入先快速返回基础可用状态，文件索引和可选 Git 能力在
后台渐进完成；不得用高频全量扫描阻塞问答。助手离线、模型不可用、项目风险和上传失败
必须分别呈现准确原因，不能继续合并为“代码助手暂时无法回答”。
