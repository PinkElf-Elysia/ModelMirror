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
- 完成专项测试、`py_compile`、全量后端测试、前端生产构建、全部 Coding Compose 配置和 Windows 助手独立打包/启动冒烟；真实 v2 配对与项目写入单列为人工验收。
- 重建前重新确认 `origin/main`、实现 HEAD、绝对 `MODELMIRROR_DATA_ROOT`、对应 `server/.env` 和 Compose overlay 拓扑；不得打印密钥。
- 若主线前进，在新验收集成工作树中按下表定向引入本轮全部已记录提交（7 个计划批次及所有后置安全纠正），逐项核对 SHA 并执行 range-diff，不从旧工作树覆盖共享栈。
- 设置 `CODING_PROJECT_HOST_WRITEBACK_ENABLED=false` 回到第十二轮；设置 `CODING_PROJECT_HOST_ENABLED=false` 完全关闭本地项目助手。
- 人工验收失败时只新增对应修复提交，不压缩或重写已完成批次历史。

共享栈之外的实现和专项验证可以在本工作树、无网络测试容器及临时输出目录中完成；
`docker compose up/build/recreate`、替换或启动正式便携助手、真实项目人工写入及 PR 均不在
自动验证授权内。进入这些步骤前必须由用户再次确认独占时间窗口和最新 `origin/main`。

## 8. 已实现边界与验证状态

- `host_git` 与清单 `local_clone` 使用不同执行面。前者只由 Windows Project Host v2
  在已选项目本地执行，后者继续由无网络容器 Writer 执行；任一执行面离线都不得串扰
  另一类项目或内置 ModelMirror。
- 写入请求帧只携带 request、project、operation、action 及 payload ID、摘要、大小、到期时间。
  revision、分支、HEAD、Patch 和提交说明由助手使用 host token 经 90 秒、单次消费、
  `no-store` 的绑定 envelope 拉取，并再次核对 host、project、operation、action、长度和摘要。
- 助手以 DPAPI 加密日志、原子文件事务和固定 Git plumbing 执行 apply、revert、commit、
  undo 与 reconcile；Windows 生产路径失败关闭，测试用 POSIX 后端不构成产品支持声明。
- 本地提交保留选择时的安全当前分支；允许仓库配置 remote，但实现不读取 URL、不调用
  remote/fetch/push/ls-remote，也不创建 GitHub PR。内置和清单项目的固定分支规则未放宽。
- 多轮恢复把初始快照 `H0` 与当前轮父提交 `Hk` 分开；历史链、当前操作和助手日志逐项
  精确匹配后才能继续。超时、断线或畸形回执统一显示“结果未知”，只能按原 ID 对账。
- 已完成的分批专项验证包含 Project Host 文件事务、提交/撤销、命名空间隔离、一次性负载、
  三类项目路由、重启恢复、多轮 `H0/Hk`、Windows 原生门禁、前端生产构建和 18 项前端测试。
  最终自动门禁与基线同现失败见第 11 节；共享栈、真实 v2 配对和用户项目写入仍按第 12 节
  明确标为未运行，不得从 Mock、单测、打包或 `--help` 冒烟推断完成。

实现过程中已验证并固化的失败经验包括：健康不代表执行面可用；结果未知不能盲重放；
写入后项目变脏不能再套初始资格；Windows 路径删除必须绑定对象身份；Git 安全边界还包含
objects、refs、reflog、index、commondir、alternates 与 partial clone；catalog 的可见状态
不能代替 `H0/Hk` 谱系和助手本地日志。

## 9. 批次执行记录

| 批次 | 本地提交 | 结果 |
| --- | --- | --- |
| 0 | `d946749` | 固定基线、L4 边界、停止条件与回退 |
| 1 | `983a227` | Project Host v2、动态回执与 DPAPI 操作日志 |
| 2 | `903cbf6` | 原子应用、故障恢复与安全撤销 |
| 3 | `07b92b7` | 当前分支提交、撤销提交与线性多轮 |
| 4 | `5d90900` | 一次性负载、API 路由与恢复对账 |
| 5 | `fc068f8` | 项目写入确认、活动态、离线与未知结果体验 |
| 6 | 本提交（待创建） | 文档、加固与整轮自动验证入口 |

## 10. 后置安全纠正

完整 Diff Review 触发以下停止条件纠正。它们均为独立提交，没有压缩、改写或伪装成新的
计划批次；验收集成时必须与 0–6 批一起按顺序引入。

| 本地提交 | 触发证据 | 文件范围 | 安全效果 | 专项验证 |
| --- | --- | ---: | --- | --- |
| `dd0ef4a` | Project Host 选择、快照与 Apply 可能在 partial clone/promisor 仓库触发 lazy fetch | 5 | 通用 Git 环境禁 lazy fetch；Helper/Apply 在对象命令前拒绝 partial clone、promisor、alternates/http-alternates | 新增 12 项定向全通过；Windows Helper 31 通过、1 跳过 |
| `f6c29bc` | 同一路径替换仓库、危险 config/include/filter、replace refs 与元数据 reparse 可绕过选择身份 | 5 | DPAPI 本地记录绑定 root/`.git` identity；旧授权要求重选；inspect→archive→reinspect 与五类操作持有 no-follow guards；拒绝危险配置与 replace objects | Windows Helper + Host Apply 125 通过、3 跳过 |
| `767522c` | 快照发布后首次采信 pathname identity；CommitEngine 漏拒 refStorage、grafts 和外部 excludes | 5 | 归档在发布前取得 identity并 no-replace 发布；上传/清理只认该 identity；commit/undo/reconcile 在创建事务产物前绑定 config/namespace 并拒绝不支持的 refs 后端与重定向 | 定向 12 通过；Windows Helper 81 通过、1 跳过；Snapshot 5 通过 |
| `66fdbdf` | 上一门禁把 symbolic HEAD 错按 ASCII 解码，合法中文分支被拒 | 3 | HEAD 严格按 UTF-8 解码，非法字节仍失败关闭；安全 config 预检与事务 Git 环境分别验证 | 定向 2 通过；Host Apply + Commit 142 通过、3 跳过 |

## 11. 最终自动门禁

| 门禁 | 实际范围或命令 | 实际结果 | 状态 |
| --- | --- | --- | --- |
| Python 语法 | 本轮全部 16 个变更 Python 文件，缓存写入 `C:\tmp` | 通过；未向仓库写入 pycache | 通过 |
| Project Host 专项 | 5 个 `test_coding_project_host*.py` 文件，无网络、源码只读容器 | 239 通过、9 个 Windows-only 跳过；对应 Windows Helper 原生 81 通过、1 个权限型跳过 | 通过 |
| 全部 Coding 后端 | 34 个 `test_coding_*.py` 文件 | 617 通过、9 个平台型跳过 | 通过 |
| 后端全量 | `server/tests/`，独立可写 detached 工作树 + 无网络容器 | 1413 通过、9 跳过、8 失败；8 项在 PR #104 基线逐项同现，均属于 Agent Workspace/Skill Finder 禁止范围 | 基线失败，非 v13 回归 |
| 前端测试 | `npm.cmd run test -- --run` | 7 个测试文件、18 项通过 | 通过 |
| 前端生产构建 | `npm.cmd run build`；CodingPage gzip 基线 33,780 bytes | 构建通过；37,721 bytes，增量 3,941 < 8,192 | 通过 |
| Compose 配置 | 临时数据根、占位 `.env`，11 组 base/legacy/projects/host/full 最大组合，仅 `config --quiet` | 11/11 通过；未执行 up/build/run | 通过 |
| Windows Helper 包 | 专用 venv；`websockets==16.0`、`pyinstaller==6.14.1`；解包后隐藏 `--help` | ZIP 13,782,607 bytes（13.144 MiB）；SHA256 `4E56D08C2E642C6E237A64E60B44BF3E7CBEC3DDB80A3F1B1C2AED8C8EFAFBEE`；退出码 0 | 通过 |
| 范围/敏感/产物 | 每提交文件数、`git diff --check`、禁止路径/扩展、依赖与 notices 不变、新增行凭据模式 | 本提交完成后共 11 个本地提交且每个 ≤5 文件；26 个变更路径，无禁止模块/产物/凭据模式命中；requirements、package/lock 与 notices 无漂移 | 通过 |
| 真实 v2 协议 | 正式便携包配对、选择项目、断线重连 | 未运行；需要用户窗口 | 未运行 |

## 12. 未执行的共享栈与人工验收

- 未执行任何共享项目的 `docker compose up/build/force-recreate/run`；本轮 Compose 仅做临时
  `config --quiet`，不代表正式数据根、正式 `.env` 或运行中容器已验收。
- 未替换或启动正式安装的 Windows Helper，未执行真实 v2 配对、正式 8000/5173 健康检查，
  未在真实用户项目中执行 apply/commit/undo/revert，也未完成 Helper/Server/Runtime 重启与网络
  观测。打包、单测和 `--help` 均不能替代这些人工证据。
- 未 fetch 后重新确认最新 `origin/main`，未创建最终验收集成工作树，未 push，未创建 PR。
- 用户确认独占窗口后，先记录最新 `origin/main`；若已前进，创建新集成工作树，按第 9、10 节
  顺序引入全部提交并 range-diff。随后只核对正式绝对 `MODELMIRROR_DATA_ROOT` 及对应
  `server/.env` 的键存在/非空（不打印值），再按实际同时启用的 manifest/host 拓扑加载 overlay、
  配置预检、重建和人工随机项目验收。验收后再次 fetch/核对基线与范围，才可 push/PR。
