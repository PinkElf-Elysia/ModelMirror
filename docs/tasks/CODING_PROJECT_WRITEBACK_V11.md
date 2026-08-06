# 任务卡：CODING-PROJECT-WRITEBACK-V11

> 第十一轮为 Coding 增加可回滚的文本文件删除/移动能力，并把自定义项目草稿受控应用、提交到部署者显式授权的专用本地克隆。风险等级为 L4；若出现跨项目访问、部分写入、未经授权的宿主修改、Git 元数据破坏或无法精确回滚，必须立即停止。

## 1. 基线与单一目标

- 基线：PR #89 合并提交 `e1c67bed4806459fb4775f57719bb004c9302bcc`。
- 分支：`codex/coding-project-writeback-v11`。
- 工作树：`C:\tmp\modelmirror-coding-v11`。
- 单一目标：代码助手可在临时草稿中删除或移动 UTF-8 文本文件；验证并经用户确认后，可应用并提交到所选、显式授权的专用本地克隆。
- 自定义项目本轮仍不支持 GitHub 发布、同一任务多轮提交、任意分支选择或真实开发工作树写入；ModelMirror 已有完整闭环不得降级。

## 2. 现场保护与允许范围

- 只允许修改 Coding Runtime、Project Source、新增 Project Writer、Coding API/前端、Coding 专项测试、Coding Compose overlay 与 Coding/Harness 文档。
- 禁止修改 PR #87/#89 引入的 MCP/Skill 目录、目录数据、匹配器、审计脚本和页面；禁止修改其他并行 Xpert、RAG、工作流或多模态区块。
- 每批最多修改 5 个文件，前一批验证失败不得进入下一批；已通过批次形成一个独立本地提交，不压缩历史。
- 不提交 `.env`、密钥、恢复数据库、项目副本、构建产物、日志、缓存或测试生成文件。

## 3. 数据与权限边界

- 项目清单 version 3 通过 `writeback.enabled=true` 逐项目授权；v1/v2 和未声明项目默认没有宿主写权限。
- 可写项目必须为受控根目录内的独立 `.git` 克隆、无 remote、固定分支 `coding/local-draft`，且应用前 HEAD、索引、工作区和未跟踪文件均符合预期。
- 产品内置文件工具只操作 Runtime 临时草稿；删除/移动无需单独确认，但真正写入本地项目仍必须由用户确认。
- 文件操作只允许 UTF-8 普通文本；拒绝目录、符号链接、二进制、敏感路径、越界路径和覆盖已有目标。移动规范化为删除旧路径与新增新路径。
- 只有可选 `coding-project-writer` 读写挂载受控项目根目录。它无网络、无端口、无 Docker socket、无模型/Git 凭据；浏览器和 Agent 不能提交物理路径、分支或 Git 参数。
- 应用和提交必须先持久化操作意图，再以项目 ID、基准 HEAD、Patch、文件哈希和不透明操作 ID执行；重复请求幂等，结果不明确时只读失败关闭。
- 恢复只保存加密 Patch、项目上下文、脱敏验证结果和操作回执，不保存宿主路径、问题、回答、工具日志、命令输出或凭据。

## 4. 用户体验与接口边界

- 页面使用“删除文件”“移动文件”“应用到所选本地项目”“保存本地版本”等日常语言，不展示 MCP、进程、socket 或内部协议。
- 应用确认显示项目名称及新增、修改、删除数量，明确不会上传；提交成功后只提供撤销和结束任务。
- `changes` 文件状态扩展为 `added | modified | deleted`；现有 apply/revert/commit/undo 接口复用于合格自定义项目，不新增浏览器路径参数。
- 未授权、Writer 未启动、存在 remote、错误分支或项目变脏时，只禁用应用/提交；问答、草稿、Diff、验证、命令、下载和 ModelMirror 能力继续可用。
- 自定义项目调用 continue 或 publish 继续返回 `project_operation_unavailable`。

## 5. 九个批次与提交

1. 现场保护与任务契约：`docs: 定义 Coding 自定义项目本地落地契约`。
2. 删除与移动草稿领域：`feature: 添加 Coding 文本文件删除与移动契约`。
3. 结构化文件工具：`feature: 添加 Coding 结构化文件操作工具`。
4. 项目写入资格：`feature: 添加 Coding 项目写入资格契约`。
5. 原子应用与撤销：`feature: 添加 Coding 自定义项目原子应用`。
6. 本地提交与 Writer 容器：`feature: 添加 Coding 自定义项目隔离提交服务`。
7. FastAPI 与恢复：`feature: 添加 Coding 自定义项目本地落地接口`。
8. 前端体验：`feature: 添加 Coding 自定义项目本地落地体验`。
9. 加固与文档：`docs: 完成 Coding 自定义项目本地落地 harness`。

每批固定执行文件范围检查、目标测试、`git diff --check`、完整 Diff Review、敏感信息及禁止产物扫描。

## 6. 停止条件与回退

- 删除/移动在取消、模型失败或协议失败后不能精确恢复时停止。
- Writer 能访问未选项目、覆盖已有目标、执行 Hook/过滤器/凭据助手、连接网络或留下部分写入/半提交时停止。
- 应用、提交、撤销或回执落盘中断后不能通过 HEAD、索引和文件哈希唯一判定结果时停止。
- 自定义项目恢复需要绕过项目授权、读取不同基准或覆盖人工内容时停止。
- 全量回归失败、出现非任务文件、敏感信息或不可独立回退时不得交付重建验收。

功能回退设置 `CODING_PROJECT_WRITEBACK_ENABLED=false` 并省略 Writer overlay；文件操作可通过 `CODING_FILE_OPERATIONS_ENABLED=false` 单独关闭。回退不会自动撤销已产生的本地提交或人工内容。

## 7. 交付门禁

- 自动验证覆盖 Coding 专项测试、`py_compile`、全量 `server/tests/`、前端生产构建和全部 Coding Compose overlay 配置。
- 最终本地提交和验证报告完成后停止，不重建共享栈、不推送、不创建 PR。
- 只有用户明确确认共享栈空闲后才重建容器；人工验收通过后再检查基线漂移并推送 ready PR。
