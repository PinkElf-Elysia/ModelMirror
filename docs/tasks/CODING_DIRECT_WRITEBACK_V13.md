# 任务卡：CODING-DIRECT-WRITEBACK-V13

> 第十三轮为 Windows 本地项目助手增加受控直接写入与当前分支本地提交能力。风险等级为 L4；任何物理路径泄露、跨项目写入、部分写入、重复提交、未确认副作用或不明确结果被自动覆盖时必须立即停止。

## 1. 基线与单一目标

- 基线：PR #104 合并提交 `952f8094c38b29baffa5de3a5b0caa94e501f45f`。
- 分支：`codex/coding-direct-writeback-v13`。
- 工作树：`C:\tmp\modelmirror-coding-v13`。
- 单一目标：把当前完整草稿受控应用到 Windows 助手所选的干净独立 Git 项目，并在用户当前分支创建可安全撤销的本地提交。
- 自动验证完成后停止；共享栈重建、人工验收、推送和 PR 必须等待用户明确授权。

## 2. 允许与禁止范围

- 允许修改 Coding Project Host、宿主写入领域、Coding Runtime/API、Coding 前端、专项测试、可选配置及 Coding/Harness 文档。
- 禁止修改 MCP、Skill、Chat、RAG、Xpert、工作流、多模态、模型目录及其测试或数据。
- Server 和浏览器不得接收物理路径；Runtime、Verifier 和容器 Writer 不得获得宿主项目写权限。
- Agent 不得获得 Shell、Git、remote、环境变量、stdin 或宿主路径能力。
- 不提交 `.env`、令牌、宿主路径、恢复数据库、项目内容、便携包、构建产物、日志或缓存。

## 3. 协议、数据与授权边界

- Project Host v2 才能声明 direct writeback；旧 v1 助手严格保持只读兼容。
- 每次 apply、revert、commit 和 undo 都绑定 host、project、revision、operation ID、保存的分支与 HEAD。
- Patch 通过短时、单次、仅助手令牌可读取的负载通道传输；控制 WebSocket 不承载大 Patch。
- 助手使用独立 DPAPI 加密操作日志保存意图、阶段、Patch、哈希和回执，不保存对话、密钥、remote URL 或工具日志。
- 超时和断线只表示结果未知；必须先按原 operation ID 对账，禁止生成新的副作用 ID 重试。
- `CODING_PROJECT_HOST_WRITEBACK_ENABLED=false` 为默认，关闭时完整保留第十二轮只读项目能力。

## 4. 写入、提交与恢复规则

- 应用前复核项目 ID、当前分支、HEAD、索引、工作区、触及文件和 Patch；只允许已授权项目内的安全文本变化。
- 路径越界、秘密、二进制、符号链接、大小写冲突及完整性风险不可绕过；验证失败或环境未就绪可对当前 revision 明确确认后继续。
- 多文件写入必须先预演并原子替换；失败只能恢复系统已知状态，检测到人工修改时进入只读冲突。
- 提交使用临时索引、固定 Git plumbing 和 CAS 更新当前分支；禁止 Hook、签名、过滤器、凭据助手、配置 include 和 remote 操作。
- 撤销提交保留文件；撤销应用恢复精确初始内容。应用后项目变脏是合法状态，恢复不得重新套用初始干净资格检查。
- Helper 或 Server 重启后只有现场状态与加密日志精确一致才恢复；不明确时仅保留查看和下载 Diff。

## 5. 七个批次与提交

1. 任务契约：`docs: 定义 Coding 主机项目直接写入契约`
2. 协议与日志：`feature: 添加 Coding 主机写入回执与加密操作日志`
3. 原子应用：`feature: 添加 Coding 主机项目原子应用与撤销`
4. 本地提交：`feature: 添加 Coding 主机项目本地提交与撤销`
5. API 与恢复：`feature: 添加 Coding 主机写入恢复对账接口`
6. 前端体验：`feature: 添加 Coding 自定义项目写入体验`
7. 加固文档：`docs: 完成 Coding 主机项目闭环 harness`

每批最多修改 5 个文件；固定执行文件范围检查、专项测试、`git diff --check`、完整 Diff Review、敏感信息和禁止产物扫描，通过后形成一个本地提交。

## 6. 停止条件

- v1 助手可接收写入请求，或 v2 能力协商可被伪造、降级绕过。
- 浏览器或 Server 获得宿主路径，或任一执行面可访问未选项目。
- Patch 可绕过路径、秘密、二进制、符号链接或限额策略。
- 多文件应用可能留下部分修改，撤销可能覆盖人工内容，或 CRLF 产生无关整文件变化。
- Hook、过滤器、凭据助手、remote 或非固定 Git 参数可能执行。
- 超时、断线或重启可能导致重复应用、重复提交或结果未知时继续写入。
- Helper 故障导致草稿、Diff、下载或 ModelMirror 原有闭环不可用。

## 7. 验证、共享栈与回退

- 自动验证覆盖协议、一次性负载、DPAPI 日志、原子应用、提交、撤销、故障注入、恢复对账、API、前端和旧能力回归。
- 完成专项测试、`py_compile`、全量后端测试、前端生产构建、全部 Coding Compose 配置和 Windows 助手真实协议冒烟。
- 重建前重新确认 `origin/main`、实现 HEAD、绝对 `MODELMIRROR_DATA_ROOT`、对应 `server/.env` 和 Compose overlay 拓扑；不得打印密钥。
- 若主线前进，在新验收集成工作树中定向引入 7 个提交并执行 range-diff，不从旧工作树覆盖共享栈。
- 设置 `CODING_PROJECT_HOST_WRITEBACK_ENABLED=false` 回到第十二轮；设置 `CODING_PROJECT_HOST_ENABLED=false` 完全关闭本地项目助手。
- 人工验收失败时只新增对应修复提交，不压缩或重写已完成批次历史。

## 8. 批次执行记录

| 批次 | 本地提交 | 结果 |
| --- | --- | --- |
| 0 | 待提交 | 固定基线、L4 边界、停止条件与回退 |
| 1 | 待提交 | Project Host v2、动态回执与 DPAPI 操作日志 |
| 2 | 待提交 | 原子应用、故障恢复与安全撤销 |
| 3 | 待提交 | 当前分支提交、撤销提交与线性多轮 |
| 4 | 待提交 | 一次性负载、API 路由与恢复对账 |
| 5 | 待提交 | 项目写入确认、活动态和离线体验 |
| 6 | 待提交 | 文档、加固与整轮验证报告 |
