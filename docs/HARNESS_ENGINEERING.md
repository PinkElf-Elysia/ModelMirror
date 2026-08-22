# Harness Engineering 开发规范

Harness Engineering 是模镜的工程交付系统。它由三部分组成：

- **轨道**：仓库事实、需求边界、架构与接口契约、任务卡。
- **护栏**：安全红线、受保护路径、依赖策略、兼容规则和停止条件。
- **仪表盘与刹车**：测试、构建、日志、Diff Review、回退与人工验收。

目标不是增加文档数量，而是让每次变更都可证实、可审查、可验证、可恢复。

最后更新日期：2026-08-07
维护人：模镜团队

## 1. 事实优先

所有任务先区分四类信息：

| 标签 | 定义 | 可以用于实施 |
| --- | --- | --- |
| 已证实事实 | 有当前代码、配置、测试、命令输出或用户确认作为证据 | 是 |
| 合理推断 | 由事实推导，但缺少直接运行或产品证据 | 需显式标注 |
| 建议方案 | 尚未实施的技术或产品选择 | 经确认后 |
| 待确认 | 缺少负责人决定或可靠证据 | 否 |

禁止虚构功能、接口、数据表、测试结果、部署方式或产品需求。目标客户、用户故事、商业目标、SLA、组织权限与合规要求在没有明确输入时必须标记为“待确认”。

当前仓库事实入口见 [REPOSITORY_FACTS.md](./REPOSITORY_FACTS.md)。发现文档与代码冲突时，以当前代码和可重复命令为事实，并登记文档债务。

## 2. 开工前任务契约

每次任务使用 [task-card.md](./templates/task-card.md) 声明：

1. 单一可验收目标。
2. 事实证据和未知项。
3. 允许修改与禁止修改的路径。
4. 公共 API、持久化数据、依赖和安全影响。
5. Given / When / Then 验收标准。
6. 最小验证、完整回归和人工验收。
7. 失败回退与停止条件。

默认单批最多修改 5 个文件。超过时必须说明无法安全拆分的原因。文件数不是绩效指标；目的是让每一批能独立审查和回退。

## 3. 标准执行门

### Gate 0：保护现场

执行并记录：

```bash
git status --short --branch
git diff --stat
git ls-files --others --exclude-standard
```

不得清理、覆盖或回滚不属于本任务的改动。需要隔离时使用独立分支或 worktree。

### Gate 1：调查仓库

至少确认：

- 前端路由、后端路由和真实执行入口。
- 相关模型、Store、配置、测试和持久化目录。
- 依赖锁定位置、Docker 服务和环境变量来源。
- 相邻能力的兼容契约。
- 文档是否与代码一致。

调查结论必须附路径或命令证据，不以记忆代替检查。

### Gate 2：差距与优先级

把差距分成：

- **P0**：泄密、数据损坏、权限绕过、主路径不可用、不可恢复执行。
- **P1**：核心功能闭环缺失、兼容回归、关键测试或回退缺失。
- **P2**：可维护性、文档、体验与效率改进。

P0/P1 未处理时，不用纯目录化、样式打磨或观测增强替代核心修复。

### Gate 3：小批实施

- 保持公共协议兼容，除非任务明确批准破坏性变化。
- 先修改模型和校验，再接执行，再接 UI，最后更新文档。
- 持久化写入使用原子替换、revision 或不可变版本。
- 外部请求、工具、文件与子进程必须有作用域、超时、限额和失败策略。
- 任何安全中间件异常不得 fail-open 执行敏感动作。

### Gate 4：验证

验证结果只允许四种状态：

- `通过`：命令实际执行并成功。
- `失败`：命令实际执行但失败，必须附错误摘要。
- `未运行`：没有执行，必须说明原因和剩余风险。
- `不适用`：与本次变更无关，必须说明判断依据。

不得写“应该通过”“预计正常”或伪造命令输出。

### Gate 5：Diff Review

提交前检查：

```bash
git diff --check
git diff --stat
git diff
git status --short
git diff --cached --name-only
```

Review 必须回答：

- 是否只改了任务声明范围？
- 是否覆盖用户已有改动？
- 是否出现真实密钥、路径、正文、运行存储或构建产物？
- 是否新增未说明的接口、依赖、数据迁移或行为变化？
- 错误、空态、取消、超时、重试和重启恢复是否有定义？
- 测试是否覆盖正常路径和至少一个失败路径？

### Gate 6：交付与回退

PR 必须包含变更摘要、事实依据、影响范围、真实验证证据、未完成项、风险和回退步骤。高风险变更在人工验收前不得描述为生产就绪。

## 4. 风险分级与最低验证

| 级别 | 示例 | 最低要求 |
| --- | --- | --- |
| L0 | 纯文档、无行为变化 | 链接/命令核对、`git diff --check`、敏感扫描 |
| L1 | 前端页面与交互 | `npm.cmd run typecheck`、`npm.cmd run test:run`、`npm.cmd run build`、错误/空态/禁用态、目标页面人工检查 |
| L2 | 后端 API、Store、校验 | `py_compile`、目标测试、错误路径、重启或持久化检查 |
| L3 | Chat、Workflow、RAG、Xpert、Toolset、公开 App | 重点测试、全量后端测试、前端 typecheck/test/build、Docker 重建与跨入口人工验收 |
| L4 | 密钥、迁移、公开访问、隔离边界 | L3 + 威胁检查、失败关闭、回滚演练、明确人工批准 |

## 5. 仓库基线命令

前端：

```bash
cd client
npm.cmd run typecheck
npm.cmd run test:run
npm.cmd run build
```

后端语法：

```bash
python -m py_compile server/main.py server/rag/*.py server/xpert_runtime/*.py server/workflow_native/*.py server/xperts/*.py
```

全量后端测试：

```bash
python -m pytest server/tests/ -q
```

Docker：

```bash
docker compose -p modelmirror config
docker compose -p modelmirror up -d --build --force-recreate
docker ps
curl http://localhost:8000/api/health
curl http://localhost:5173/models
```

目标测试由任务卡按改动模块补充。本治理 PR 保留 multimodal readiness workflow，并新增 repository quality workflow；quality workflow 执行前端 typecheck/test/build、后端测试和 Compose 配置检查。`main` 尚未启用 branch protection/ruleset required checks，因此这些结果是自动化验证证据而非强制合并门。不得把未实际运行的配置表述为“CI 已通过”。

## 6. 安全与受保护数据

漏洞与疑似凭据泄漏必须按仓库根目录的 [`SECURITY.md`](../SECURITY.md) 使用 GitHub private vulnerability reporting 私密报告。禁止在公开 Issue、Pull Request、Discussion、提交信息或 CI 日志中粘贴漏洞细节或真实 secret。

禁止提交或输出：

- `.env`、API key、token、credential 主密钥和原始分享密钥。
- `server/*/storage/`、RAG 上传、索引、DuckDB、浏览器状态、Sandbox 工作区。
- prompt 全文、工具完整输出、附件正文、Cookie、表单值和本地绝对路径。
- `node_modules/`、`client/dist/`、扩展 ZIP、日志、截图产物和 APK。

新增依赖必须记录固定版本、用途、许可证、供应链与镜像影响。优先使用现有依赖和标准库。

## 7. 停止条件

出现以下情况立即停止扩大改动：

- 需要猜测目标客户、用户故事、权限模型、SLA 或数据保留策略。
- 发现与用户改动冲突，无法安全合并。
- 需要明文密钥、破坏性删除、Git 历史重写或未批准迁移。
- 关键验证失败且无法归因。
- 任务范围、公共接口或风险等级发生实质变化。
- 无法提供独立回退方式。

停止不等于放弃：保留现场，记录已证实事实、阻塞条件和用户需要决定的最小问题。

## 8. 产品与需求文档边界

可以从代码反向整理现状，但必须注明“现状事实”，不能把现状包装成产品战略。以下板块当前由产品负责人补充：

- 目标客户与用户角色。
- 用户故事、业务流程和优先级。
- 商业目标与成功指标。
- 组织权限、租户、审计和合规要求。
- SLA、数据保留与灾备目标。

工程团队可以提供技术选项、风险和成本，不代替产品决策。

## 9. 回退模板

```markdown
## 回退方案

1. 回滚本 PR 的提交，保留用户持久化目录。
2. 恢复上一版环境变量和镜像，不打印密钥。
3. 如有新版本数据，只回切活动指针，不原地修改不可变快照。
4. 重新构建受影响服务。
5. 验证 `/api/health`、主入口和任务卡列出的回归路径。
6. 记录回退原因、数据影响和后续修复条件。
```

## 10. 完成报告

交付报告固定包含：

1. 目标与结论。
2. 已证实事实。
3. 变更文件。
4. 公共接口与数据影响。
5. 验证状态表。
6. 安全与敏感信息检查。
7. 人工验收结果。
8. 未运行项与剩余风险。
9. 回退方式。
10. 待确认的产品问题。

## 11. Coding 高风险闭环的失败经验

以下规则来自只读问答、草稿、验证、应用、提交、恢复、多轮和远端发布的实际失败，
适用于今后所有具有外部副作用的 Agent 功能：

1. **健康不等于可用。** 容器 `healthy` 只证明自身探针成功；Server 必须经真实私有
   socket 调用执行面 health，并让 capabilities 独立降级。一个可选执行面故障不得
   让已有草稿、Diff、下载或本地提交链消失。
2. **超时不是失败结论。** 写文件、更新 Git 引用、push 或创建 PR 可能已经成功但回执
   丢失。副作用前先持久化不透明操作 ID，重试前查询目标精确状态；不得因前端超时
   生成新的写入、提交、分支或 PR。
3. **恢复记录是安全状态，不是对话缓存。** 只保存最后一个完整 revision、脱敏结论和
   必要回执；半轮内容丢弃。基准、目标或外部状态不能精确证明时转只读冲突态，绝不
   改写指纹、重复执行或覆盖人工修改来“恢复成功”。
4. **状态轮询必须有明确终态。** 前端只在 `running/applying/committing/publishing`
   等有限活动状态查询，终态立即停止；不得用整页刷新、依赖对象抖动或无条件 effect
   重新创建请求。请求异常时保留最后可信状态和可见操作区。
5. **绑定目录性能不能靠增加超时掩盖。** 完整基准扫描放在启动或显式预检，运行时
   只复核清单、元数据和变化文件。耗时异常先定位重复扫描、Windows bind mount 和
   快照字节差异，再决定是否修改预算。
6. **累计状态与当前增量必须分开。** 多轮任务允许当前 Patch 为空、累计 Patch 非空；
   API、恢复、页面和验证必须分别绑定 revision 与范围，不能因当前轮没有文件就清空
   以前的修改、检查、应用或提交状态。
7. **外部发布前重复核对信任边界。** 固定仓库、基础 SHA、系统分支、本地提交链和
   恢复回执必须独立验证；远端分支只允许不存在或精确相同，禁止 force push。凭据只
   驻留最小执行面内存，不进入 URL、Git 配置、日志、响应或恢复存储。
8. **随机故障注入必须覆盖回执窗口。** 对“调用前、执行后、响应前、落盘前、重启后”
   分别测试；随机文件名、内容和操作 ID 可揭示缓存、硬编码路径和错误幂等键。不能只
   验证一次顺利的 happy path。
9. **产品不自动清理不可逆外部结果。** 删除工作树、提交、远端分支或 PR 需要新的
   明确授权；功能回退只停止后续能力，并向用户说明已有外部内容仍需人工处理。
10. **本地预检失败不能伪装成远端冲突。** 非 root 容器读取 Windows bind mount 时，
    Git 可能因目录属主不同触发 `dubious ownership`，状态扫描也可能明显变慢。固定目标
    必须显式配置精确 `safe.directory` 并保留有界超时；`repository_not_ready` 属于可重试
    的本地失败，只有基础分支、远端分支或 PR 精确对账不一致才进入不可写冲突态。
11. **多轮恢复的验证范围必须绑定累计草稿。** 恢复记录会把此前已完成轮次放入基准
    Patch，把当前轮放入活动 Patch；项目验证使用两者的累计路径选择步骤。恢复时若只用
    当前轮路径复核，会把合法的混合验证结果误判为损坏。恢复验证必须使用基准与活动
    路径的去重并集，同时仍分别复核两段 Patch 的路径和 revision。
12. **安全边界与质量建议必须分层。** 路径越界、秘密、符号链接、未经项目授权和逐次
    确认的真实仓库写入、目标指纹和远端基线属于不可覆盖的硬门禁；语法检查、项目验证和依赖可运行性属于质量
    结论。质量结论未通过时应清楚告知风险并允许用户再次确认，不能用同一种“不可用”
    状态永久锁死下载或专用副本应用。
13. **进程内对账必须优先复用已知操作。** Applier 已保存相同操作 ID 与回执时，应先
    验证当前文件是否匹配该回执并返回 `applied`；不能再用进程启动时的通用基准清单
    推断 `not_applied`，否则仅重启 Server 就会把已完成应用误判成恢复冲突。
14. **可选执行面的探测不能串行阻塞核心路径。** 创建或恢复 Agent 会话只探测 Worker；
    Applier、Committer 与 Publisher 健康检查并行执行，只用于能力展示和对应操作的最终
    复核。前端仅在确有 pending 记录时请求恢复详情，避免重复 socket 探测放大延迟。
15. **重启恢复必须处理跨进程孤儿会话。** Server 内存状态丢失后，Runtime 可能仍占用
    单会话槽位；恢复请求已经通过加密记录、指纹和 Patch 复核时，可在 Runtime 内先关闭
    该临时会话再重建。普通创建请求仍不得抢占活动会话，畸形或指纹不匹配的恢复请求也
    不得触发清理。
16. **多轮已提交任务由提交链证明累计状态。** Applier 重启后只持有镜像基准，最后一轮
    回执无法解释前几轮已合法写入的文件，若再次做全目录基准比较会产生恢复误报。存在
    受控 Committer 日志时，应先复核固定分支、线性提交链、索引和工作区的精确状态；
    对账成功即可证明累计应用结果，未提交草稿仍由 Applier 按文件与基准严格复核。
17. **真实发布验收必须先解决实现分支与远端基线的时序。** 从未合并实现 HEAD 创建的
    专用仓库，其任务提交父节点不会等于远端 `main`，严格基线检查必然拒绝。不得把
    “远端必须精确匹配”放宽为祖先或可快进判断；应先合并实现，或由部署者临时固定一个
    精确指向任务基线的验收分支。验收结束后恢复 `main` 并明确处理临时远端内容。
18. **项目根目录只能由最小宿主执行面看见。** 清单 `local_clone` 不得为了项目选择把整个
    根目录挂载给 Server 或 Runtime；只有无网络 Project Source 可只读扫描清单，Runtime
    只消费当前租约的单槽快照。`host_git` 的物理路径只保存在 Windows Helper 的 DPAPI
    registry 中，Server、浏览器、Runtime 和模型仍只能看到不透明项目 ID。任一来源故障
    必须独立降级，内置 ModelMirror 不受影响。
19. **项目快照必须来自 Git HEAD blob。** 复制工作区会把 Windows CRLF、过滤器结果和
    未跟踪内容混入基准，导致指纹误报甚至泄露。固定 `ls-tree`/`cat-file` 只读取对象，
    不运行 Hook、clean/smudge、凭据助手或仓库命令；宿主仓库验收前后必须无变化。
20. **健康探针不能扫描全部仓库。** 高频 health 只解析并校验清单结构；项目干净状态、
    HEAD 与对象限制在列表刷新或显式租约时检查。否则 10 秒探针会在 Windows bind mount
    上形成持续高延迟，并把可选项目能力拖成核心服务不可用。
21. **项目身份要逐层复核且按序释放。** API、Broker 与 Worker 分别绑定不透明项目 ID、
    HEAD、租约和指纹；必须先关闭 Worker 会话，再释放并清空快照。反序释放或只校验名称
    会造成跨项目串读、恢复错绑或正在使用的文件突然消失。
22. **仓库说明是显式输入，不是配置发现。** 禁用 OpenCode 项目配置会同时禁用自动
    AGENTS 发现，因此只把经过大小和编码校验的根 `AGENTS.md` 显式注入；嵌套说明、
    OpenCode、MCP、插件和 provider 配置保持隐藏，不能让仓库自行扩大 Agent 权限。
23. **恢复项目上下文要向后兼容并保持加密。** 项目 ID、类型、显示名和 HEAD 进入独立
    认证加密表，不保存宿主路径、不改 recovery schema v3 `user_version`；旧记录视为
    ModelMirror。项目变化只降级为下载，不改写基线或借旧 Patch 恢复到新 HEAD。
24. **前端先按项目功能矩阵裁剪请求。** `local_clone`、`host_git` 和内置项目必须分别按
    自身 features 与执行面可用性请求验证、应用、提交或发布，不能让一个全局 capability
    覆盖项目级拒绝。状态查询仅用于 applying、reverting、committing、undoing 等活动态，
    终态立即停止；活动会话或 pending 恢复存在时锁定项目选择，切换不能成为清空草稿的
    隐式操作。
25. **不要用 Windows junction 共享可删除依赖目录。** 临时 worktree 若把 `node_modules`
    junction 到实现目录，清理 worktree 时可能遍历并删除真实依赖。体积基线应使用独立
    依赖安装、既有构建产物或只读统计，清理前必须确认链接目标不会被递归处理。
26. **写回资格必须逐项目显式授权。** “位于受控根目录”只证明 `local_clone` 可被 Broker
    读取，不等于可以写入；它仍需要清单 v3、逐项目开关、无 remote、固定分支和独立
    `.git`。`host_git` 则必须同时满足 v2 协议、服务端独立开关、助手在线、系统选择授权、
    当前分支和项目资格。任一条件缺失只降级该项目的写入/提交能力，不得禁用问答、草稿、
    Diff、验证或下载。
27. **恢复基准必须锚定不可变 `H0`，当前轮写入绑定 `Hk`。** 用户确认写入或提交后，目标
    按设计不再等于初始干净工作区；恢复以首轮操作日志授权读取保存的 `H0` 快照，再由线性
    CommitReceipt 推导当前父提交 `Hk` 并对账活动操作。不能用当前工作区重写基准，也不能
    把合法脏树、HEAD 前进或历史轮次当作未授权改动。
28. **公开回执与执行面日志不能混为一种凭据。** API ApplyReceipt 面向会话、恢复和前端，
    Writer/Committer 日志还包含内部基准、父提交和操作阶段；两者时间或指纹字段不必相同。
    对账应分别校验各自 schema 并以不透明 ID 关联，不能拿公开回执直接伪造内部提交上下文。
29. **安全策略新增合法状态时要同步删除旧的绝对禁令。** 删除文本从“永远非法”变为受控
    能力后，Draft、共享 Patch 校验、Verifier、Applier、Committer、API 类型、前端状态和
    测试必须同步演进。遗留的 `deleted file mode` 拒绝测试会制造误报，迫使用户绕过正确
    门禁；迁移时应增加“临时副本允许、宿主仍需确认”的成对测试。
30. **能力矩阵与执行面健康必须共同决定前端入口。** 项目清单声明 `apply/commit=true`
    只表示该项目符合静态资格；Writer 未配置或不可达时仍应隐藏写入入口并显示日常语言
    原因。反过来，Writer 健康也不能给未授权项目开放按钮；不能复用 ModelMirror 的 Applier
    capability，否则会把自定义项目错误路由到专用副本并造成状态区消失或重复请求。
31. **Helper v1 与 v2 必须按协议能力降级。** 旧助手、写回开关关闭或瞬时离线都要保留
    项目摘要、草稿、Diff、下载和最后可信操作状态，只关闭新写入动作。paired 与 available
    不是同一状态；过期凭据要停止旧码重试并始终提供重置/重新配对入口。
32. **副作用结果未知只能按原 ID 对账。** apply、revert、commit 和 undo 都要在副作用前
    持久化 operation ID；超时、断线、畸形回执或 catalog 落盘失败统一进入待核对状态。
    前端不得显示红色确定失败、生成新 revision 或重新发送新 ID，只能触发原方向 reconcile。
33. **Windows 文件事务必须绑定对象而不是路径字符串。** `lstat/hash → replace/unlink` 存在
    TOCTOU，会覆盖同内容人工替换或穿透 junction。生产写回要以 no-follow 句柄、file ID、
    no-replace 移动、持久事务清单和可重入隔离完成；无法提供等价删除语义的平台失败关闭，
    POSIX 领域测试不能被宣称为产品支持。
34. **Git 安全边界是完整命名空间。** 只禁 remote 或 Hook 不够；objects、fanout、refs、
    reflog、HEAD、index/index.lock、config、commondir、alternates、partial clone、promisor、
    replace refs、grafts、refStorage/reftable、外部 excludes、filter、credential、签名与 URL
    rewrite 都可能造成越界读写或联网。对象应在私有目录生成，reflog/索引按身份停放恢复，
    ref 使用固定参数 CAS；不支持的 refs 后端在任何事务产物前拒绝，remote 配置可存在但不得
    读取值。
35. **路径授权必须绑定项目对象身份。** 仅用规范路径 HMAC 无法区分同一路径整体换仓或
    `.git` 被替换。Helper 应在 DPAPI 本地状态保存 root/`.git` identity、让 project ID 随身份
    变化，并在 inspect→archive→reinspect 及 apply/revert/commit/undo/reconcile 全程持有
    no-follow guard；旧 path-only 授权只能要求重选，不能由 inventory 或首次操作静默补绑。
36. **公开回执、Helper 日志和 catalog 各有独立职责。** 回执证明会话操作，DPAPI 日志证明
    宿主阶段和对象身份，catalog 只提供脱敏可见性。只有严格回执与日志同时通过才推进
    catalog HEAD/state；离线 catalog 隐去 branch/head 时可用当前会话可信身份补显示，但
    不能用旧 catalog feature 重新开放动作。
37. **Helper registry 落盘必须先成功再接受内存状态。** DPAPI、原子写盘或留存清理失败时
    要回滚候选内存值，不能让本进程报告 HEAD 已推进、重启后又回到旧值。多 Helper 进程还
    必须以单实例锁或跨进程 CAS 防止 last-writer-wins 丢失操作日志。
38. **连接代际和心跳要独立于长操作。** Helper 必须主动心跳，并用 generation 或
    connection_id 防止旧连接的 finally 把新连接标离线；snapshot、apply、commit 等长操作
    不能饿死心跳。前端能力签名变化可静默刷新，但不得形成无延迟请求循环或整页闪烁。
39. **容器配置通过不等于执行面可启动。** 新 Python 模块要同步 Dockerfile/PyInstaller
    收集和只读挂载点；Compose overlay 合并要验证实际顺序，尤其两个 Project Source 来源
    并存时需要 full compatibility overlay。容器重建还必须同步打包和启动同一 HEAD 的 v2
    便携助手，不能用旧包或 Mock 冒烟替代。
40. **未知结果 UI 必须保留唯一安全出口。** apply/revert/commit/undo 四种未知方向的 receipt
    形态不同；界面要分别显示对应“核对本次操作”，锁住新修改和放弃，但保留 Diff、下载和
    原操作核对。活动态轮询终止后应刷新 history/catalog，不能让成功状态因旧摘要退回只读。
41. **共享栈验收必须晚于自动门禁且再次核对基线。** 多 worktree 共用 `-p modelmirror` 时，
    任何 build/up/recreate 前都要取得用户确认的独占窗口；若 `origin/main` 前进，应在新验收
    集成工作树定向引入批次并 range-diff。人工验收后还要再次核对主线、全 Diff、敏感信息
    和禁止产物，才允许 push 或 PR。
42. **通用 Coding Worker 只接受供应商中立任务。** 模块只能提交目标、opaque 来源、上下文引用、
    冻结验收 ID 和低于系统上限的预算；`origin` 由 Server 写入。物理路径、环境变量、remote URL、
    凭据、供应商名和原始执行端点不得进入 `TaskSpec`。领域适配放在调用模块，不进入 Worker 内核。
43. **Provider、Executor 与网络出口必须分层隔离。** Provider 可以访问受控模型网络，但不能直接执行
    Workspace 命令；Executor 只能加入内部工具网络，不能访问 Server/newAPI。网络动作必须经绑定任务、
    用途、域名和 TTL 的租约以及独立 egress proxy；IP 字面量、私网和重定向越界保持拒绝。
44. **任务完成由冻结验收和当前树证据决定。** 模型停止调用工具只进入 `testing`。必需检查的 argv、
    timeout 和交付物由后端注册，Agent 与调用模块不能删除、替换或降级；Workspace tree hash 改变后旧
    Evidence 必须失效，未全部通过不得进入 `completed`。
45. **双槽任务和进程必须按任务归属。** 两个任务可并行，第三个持久排队；取消、输入、输出、服务、
    审批和 Artifact 都要绑定 `task_id` 与 slot。取消一个任务不得终止另一个任务的进程，也不得把其
    Workspace、事件或 Artifact 读给另一个调用方。
46. **恢复只重放控制意图，不重放未知副作用。** Server 或 Worker 重启后，运行中进程停止并把任务
    标记 `interrupted`。只有 provider checkpoint 与当前 tree hash 精确匹配才可由显式 resume 恢复；
    副作用工具使用稳定 operation ID，未知结果先 reconcile，禁止换 ID 盲重放。
47. **来源租约应尽可能晚取得并及时释放。** `host_snapshot` 只在任务真正出队时请求 Windows Helper，
    导入并校验 project/head/fingerprint 后立即释放 Project Source 租约；排队任务不能占用单槽快照。
    Server、Provider、Executor 和模型始终只看 opaque ID 或隔离 Workspace，不得获得宿主物理路径。
48. **宿主写回继续由 v13 执行。** Worker 在合成 Git H0 内生成 Diff，不在用户仓库原地运行。只有
    completed、必需检查通过且仍绑定原 Host Snapshot 的文本变更，才可由用户显式交给 v13
    apply/commit/undo/publish 链；Worker 不新增第二套宿主写事务。
49. **共享 Console 必须区分展示与能力。** `/coding` 和 `/agents/workbench` 可复用任务、对话、文件、
    Diff、审批、Evidence、Artifact 与终端组件；只有 Coding 上下文展示 v13 领域动作。开关关闭、Provider
    不可用或 legacy 活动会话存在时必须清晰降级，不能把 Mock/静态状态误报为真实引擎就绪。
50. **专业文件修改必须以 preimage 与 tree CAS 整批发布。** unified patch、移动、删除和批量修改的每个
    输入都要绑定旧内容；任一冲突时全部保持旧状态。Shell `mutate` 还必须满足 exit 0 与真实 Workspace
    tree 未变化，`inspect` 永不发布文件变化；超限或策略外产物只进入 Artifact。
51. **Shell 没有任务级永久批准。** 每次批准必须精确绑定 operation ID、脚本摘要、相对 cwd、模式、
    timeout 和网络范围。修改脚本、重复批准、过期批准或把服务/检查租约复用于 Shell 都必须拒绝；未知
    结果按原 operation ID 对账，不能换 ID 重跑。
52. **代码智能结果是树版本证据，不是长期事实。** symbols、definition、references、hover 和 diagnostics
    必须绑定 task、entry ID 与 tree hash。任何 Workspace 变化立即使旧结果失效；LSP 重启只能重新索引，
    不能恢复旧进程、旧诊断或读取另一个任务的 Workspace。
53. **Provider 可移植不等于 Provider 可见。** 公共 route 只表达平台质量档；供应商、版本、端口、原始帧、
    session ID 与凭据只存在于加密私有绑定。任务只允许原 Provider、兼容版本和相同 tree 恢复；不得自动
    跨引擎迁移或把缺少 Claude secret 扩大为 OpenCode/legacy 故障。
54. **真实 Provider 内建工具必须保持关闭。** OpenCode 与 Claude 都只能经 ModelMirror Tool Broker 读写、
    执行或联网。Claude secret 只进入独立 Provider 子进程，Provider 不挂 Workspace，Executor 不接收模型
    凭据；conformance Mock 或 CLI `--version` 不能替代真实双引擎任务验收。
55. **模块控制必须持续复核 origin。** SDK 的查询、事件、message、pause、resume 与 cancel 都要匹配
    `origin(module, business_object)`，不能只在创建时校验。模块可登记来源、上下文和验收适配器，但不能
    注册 Provider、Shell/LSP 进程、secret 或任意 MCP Server。
56. **Console 展示的输出必须可补发且有界。** operation output 按序号重连，完整内容从受限 Artifact 获取；
    大输出、长路径和 diagnostics 不得阻塞主线程或破坏移动端。精确 Shell 批准要可键盘操作、带明确焦点
    与状态反馈，Coding 领域动作仍只在 Coding 上下文出现。
57. **子任务是平台所有的一级隔离任务。** 只允许 `explore`、`implement`、`review`，深度一、每父任务最多四个；
    父任务 checkpoint 后停车释放槽位。子任务不得继承审批、网络租约、operation、预算、Artifact、Evidence、
    Provider session 或隐藏上下文，也不得创建子任务。
58. **Fork 合并必须保守失败。** `implement` 结果同时绑定 fork H0、结果 tree 与 changed paths；父任务按文件
    preimage 和当前 tree CAS 原子合并。同文件冲突不得自动覆盖，子 Fork 保留；子 Evidence 不能替代父任务
    必需检查，合并后必须在父 Workspace 重跑。
59. **会话控制只发生在完整安全边界。** 问题只能结算一次；compaction 只在完整工具调用边界；undo/redo/fork
    只在没有命令、服务、审批、问题或子任务运行时开放。它们只改变 Workspace 与公开会话，不声称撤销外部
    服务副作用，也不保存隐藏思维链。
60. **能力等效只能由冻结真实对照证明。** Fake、确定性 Harness、Compose、CLI 版本探针和单次人工任务均不
    能支持“接近 OpenCode”结论。必须满足任务卡中的 144 次、连续两轮、成功率/时长/token、安全与人工 UX
    全部门禁；任一失败时只能以 Experimental、默认关闭交付。
61. **交互屏障必须是 Turn Transaction。** approval、input、subtask、compaction 或未知结果出现后，Provider
    turn 先进入 `parking`，停止活动请求并写入精确 checkpoint；只有 durable `parked` 才能结算。屏障后的同批
    工具调用不得创建新 operation。恢复只能使用原 turn、operation、tree 与 checkpoint。
62. **平台数据优先于 Provider 提示。** Plan、Todo、Question、Turn、capability 和 Evidence 由 Store/API 权威
    提供。Provider 原生 plan/question/compaction 帧只可作为公开提示，不能改变任务状态、开放动作或满足验收。
63. **任务能力必须实时失败关闭。** `enabled`、`supported`、`available`、`reason` 绑定固定路由、sidecar generation
    与健康窗口。离线、旧版本、缺工具、开关关闭、绑定改变或旧任务缺快照时不能用全局布尔值误报高级能力。
64. **认证角色必须隔离。** Controller 不读取隐藏检查、模型 key 或 Workspace；runner 不读取隐藏 bundle；checker
    无网络且是唯一读取隐藏正文的进程。任何 fixture、bundle、image、route、candidate 或 tree digest 不一致都必须
    在隐藏检查前拒绝。runner 自报 passed 不能替代 checker receipt。
65. **两轮认证必须拥有不同运行身份。** 每个 round 的 run ID 纳入 round ID、engine、task 与 attempt；跨 round
    复用幂等任务是无效认证。两轮必须绑定同一候选、manifest、route、bundle 与 runner image，并连续完成共 288 次
    真实运行。未运行、部分运行或人工修复过的矩阵不得写成绿色。
66. **真实任务先证明任务有效。** Harbor 任务必须分离 H0、Oracle solution 与独立 verifier；Oracle 重复通过、Nop
    与 near-miss 重复失败后才可进入真实模型校准。公开任务只能保存 verifier 包装与策略检查，隐藏正文必须位于仓库外
    只读密封目录；CLI 核对逐任务及整体哈希后才可注入一次性 task 副本。Provider 不得读取 solution、verifier 或密封
    checker，checker 无网络且只消费绑定终态 Workspace Artifact。结构性微型夹具不得冒充能力认证集。
67. **协调指标必须由原始证据派生。** 平台协调失败、重复副作用、未结算 operation 与孤立 interaction 必须从
    Harbor ATIF 和 Worker ledger 重建并携带 evidence ID。调用方填写的零值、缺失轨迹、摘要不符或不可证明的
    “无异常”一律不能通过报告门禁。
68. **任务准入先于持久化和排队。** 新任务先验证 opaque source 注册、精确 revision、当前可用与适配器支持，
    再原子写入任务、加密 receipt 与 `source_admitted`。精确幂等查询必须更早执行，已有任务不因来源临时离线消失；
    Scheduler acquire 仍复核来源，receipt 不能绕过 TOCTOU 检查。
69. **故障注入必须绑定一个已持久意图。** 评测 Controller 不得取得 Docker socket 或任意执行端点。Harness profile 的
    故障只可绑定当前任务唯一待批准的 mutate shell operation；副作用后的回执丢失进入 unknown，关闭 Executor task
    binding 后仅按原 operation ID reconcile。任务或 operation 不唯一、开关关闭、token 缺失或重复 arm 均失败关闭。
70. **对照 runner 的能力缺口不能伪装成模型失败。** 原生 Agent 必须使用默认拒绝权限，并只运行任务冻结的精确命令；
    inspect 命令允许失败后复测，mutate 命令必须保持唯一。若 runner 不能在相同条件下完成 question、steering、compaction
    或故障恢复，完整矩阵必须在启动前失败关闭，不能删除任务、事后补答案或把适配器失败计入成功率差值。
