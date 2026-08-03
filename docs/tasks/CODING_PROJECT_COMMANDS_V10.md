# 任务卡：CODING-PROJECT-COMMANDS-V10

> 第十轮为 Coding 自定义项目增加隔离验证与逐次确认命令。风险等级为 L4；任何命令
> 绕过用户确认、继承模型密钥、访问网络或其他项目、写入宿主目录、留下残留进程，
> 都必须立即停止。

## 1. 基线与单一目标

- 基线：PR #82 合并提交 `0f4599e4fb56ecdfd32977b717928b478d6212a2`。
- 分支：`codex/coding-project-commands-v10`。
- 工作树：`C:\tmp\modelmirror-coding-v10`。
- 单一目标：自定义项目可以运行经用户明确确认的真实 Python/Node 项目验证；代码助手
  可以通过系统内置 MCP 提出结构化 argv，用户逐条允许一次后在无网络临时副本执行。
- ModelMirror 现有验证、应用、提交、多轮恢复和 GitHub 发布不得降级。

本轮不包含命令文件变化导入、运行时联网安装、直接 Shell、环境变量或 stdin、自定义
项目应用/提交/发布、删除、重命名、多任务、对话保存、第二 ACP、多 Agent 或分布式 Worker。

## 2. 数据、执行与权限边界

- 项目清单兼容 version 1；version 2 可配置自动识别、最多 8 条固定检查和一个部署者
  准备的离线 Runner Pack。浏览器和 Agent 都不能修改项目验证配置。
- Agent 命令只接受 `argv`、项目内相对 `cwd`、日常语言用途和 1–300 秒超时；最多
  64 个参数、8 KiB、每轮 20 条、累计执行不超过 600 秒。
- Runtime 仅在 Draft 模式注入固定 `modelmirror-runner` stdio MCP。仓库 MCP、插件、
  `bash`、`task`、web 和外部目录继续禁用；系统 MCP 内部实施一次性产品确认。
- 允许决定 300 秒后失效。拒绝或超时作为结构化工具结果返回 Agent，不结束本轮；
  整体取消必须同时拒绝待确认请求并终止运行进程组。
- 复用 `coding-verifier` 执行已确认 argv。它保持无网络、非 root、只读根目录、无端口、
  无 Docker socket、无模型或 Git 凭据，只能读取当前项目租约快照和可选只读依赖包。
- 命令在当前草稿的一次性 tmpfs 副本中执行；运行产生的所有文件变化、缓存和构建产物
  在结束后清除，不得回灌草稿或宿主项目。
- 输出合计最多 64 KiB，移除控制字符并脱敏路径、密钥模式和环境信息；命令、输出、
  决定和待确认状态不落盘。恢复只保留最后一个完整草稿及可验证的脱敏验证结论。

## 3. 离线依赖包契约

- 可选 `CODING_RUNNER_PACKS_ROOT` 只读挂载给 Verifier，Server、Runtime 与 Agent 不可见。
- 每个 Pack 使用安全 ID 和固定 `pack.json`，声明 Linux 平台、Python 3.12/Node 22、
  依赖输入 Git blob 哈希、Python 路径、Node `node_modules` 映射和附加 bin 路径。
- Pack 路径必须规范化且位于自身根目录；内部符号链接只允许解析到同一 Pack。Pack
  不得包含项目源码、密钥、环境文件或可写目录。
- Pack 缺失、版本或依赖哈希不匹配只使自动项目验证显示“运行环境未就绪”；问答、
  草稿、Diff、下载、恢复和基础 Python/Node 命令继续可用。
- 运行时禁止下载、更新或写入 Pack；依赖环境指纹变化后旧验证结果必须标记过期。

## 4. 公共接口与用户体验

- capabilities 新增 `commands`；项目功能矩阵新增 `commands`，自定义项目在执行面可用时
  开放 `verification`，但 `apply/commit/publish` 继续为 false。
- 新增 pending 查询、一次性 decision、项目验证确认接口；浏览器只提交不透明请求 ID、
  revision、confirmation ID 与 `allow_once | reject`，不回传 argv。
- 事件新增 `command_requested` 与 `command_resolved`；自定义项目验证状态增加
  `awaiting_confirmation`。SSE 重连必须恢复待确认卡片，不刷新整个页面。
- 页面使用“代码助手希望运行一项检查”“允许本次运行”“暂不运行”等日常语言；精确
  argv 默认折叠。等待确认不是错误或持续旋转状态，整体停止始终可用。
- 多条项目验证命令先完整列出，再由一次操作分别授予本批每条命令一次执行权；revision、
  HEAD、计划或依赖环境变化后确认失效。

## 5. 七个批次

1. 现场保护与本任务契约。
2. 清单 v2、命令领域、自动识别、Pack 和计划指纹。
3. 固定系统 MCP、确认桥、超时、拒绝与取消。
4. Verifier 动态快照、Pack、批准 argv、输出限制和进程清理。
5. Worker、FastAPI、恢复、能力与 Compose 编排。
6. 前端命令确认、验证预览、结果与重连体验。
7. 安全加固、完整回归、架构/部署/Coding/Harness 文档。

每批最多修改 5 个文件；固定执行文件范围检查、目标测试、`git diff --check`、完整 Diff
Review、敏感信息和禁止产物扫描，通过后形成一个独立本地提交。前一批失败不得进入下一批。

## 6. 验证与人工验收

- 自动验证覆盖清单 v1/v2、命令规范化、自动识别、Pack 失配、MCP 工具发现、批准/拒绝/
  超时/重连、Shell 与越界拒绝、无网络、无密钥、跨项目隔离、输出脱敏和进程组清理。
- 对命令前、运行中、结果返回前及 Server/Runtime/Verifier 重启注入故障，确认不会导入
  文件变化、留下占用或破坏最后一份完整恢复记录。
- 执行 Coding 专项测试、`python -m py_compile`、全量 `server/tests/`、前端生产构建以及
  基础和全部 Coding Compose overlay 配置检查。
- 人工验收使用随机 Python/Node 项目、文件名、断言和正文，覆盖失败后修复、整组验证、
  拒绝、超时、写文件产物丢弃、Pack 失配、项目 A/B 串读、重启恢复和宿主 Git 状态不变。
- 自动验证完成后停止；取得共享栈独占窗口并由用户明确验收通过前不推送、不创建 PR。

## 7. 停止条件与回退

以下任一条件出现时停止：OpenCode 1.18.9 无法只启用固定系统 MCP；命令可绕过确认；
命令进程继承模型/宿主凭据；可联网、访问其他项目或写快照/Pack/宿主；危险 cwd/argv
可越界；取消后进程残留；SSE 重连丢失待确认状态；旧能力回归；公共响应泄露路径、
密钥、环境或未截断输出。

回退时设置 `CODING_PROJECT_COMMANDS_ENABLED=false` 并省略 Runner Pack overlay，恢复
第九轮自定义项目草稿能力。停止 Verifier 不得影响问答、草稿、Diff、下载或恢复；
本轮不升级恢复数据库版本，也不修改任何宿主项目。
