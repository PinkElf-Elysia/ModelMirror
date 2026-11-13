# C1-B 正式批次预留与宿主接线

状态：实现及本批离线验证完成，待用户确认。尚未进行 C2 界面验收或真实供应商验收。

## 范围与授权

本批接入正式控制面批次预留接口、创作与六卡宿主适配器，继承已验收 C1-A 协调器。工作树为 `C:/tmp/modelmirror-rpg-comparison-coordinator`，分支 `codex/rpg-comparison-coordinator`，基线 `d9f20fe691e8ddef8623d779a25a9359717f2e8d`。实施前保存了 80 个继承改动文件的 hash；本批增量为 34 个文件。

单一闭环横跨 Python HTTP、Node 额度、两类会话存储和插件授权，超过五文件建议范围。没有新增生产依赖、数据库表或迁移，没有修改作者提示词、共享配置或既有账本，没有认证、真实生成、部署、Commit、Push 或 PR。

18662、18664、18666、18668 原验收服务保留。对应有效页面只读检查均返回 200；这是入口存活检查，不替代浏览器功能验收。本批无新增前端控件，二级交互及桌面/390px 对比界面留到 C2，仍须遵守原站观察门禁。

## 已接通行为

1. 控制面复用现有 `router_metadata` SQLite 事务，整批预留与普通作者请求共享同一额度。预留包含 2–4 个候选、必要的前置摘要/压缩、采用后的摘要/压缩/记忆，最大 9 个阶段。普通请求不能占用已预留额度。
2. 请求必须绑定批次、阶段、源请求 hash 和模型选择；任务领取、实际派发标记与已用计数原子更新。已派发、失败和未知保留计数；只释放有证据未派发的阶段。重复领取不会产生第二次派发。
3. Node 受信传输通过本机 S2S HTTP 使用正式控制面路由，保存预留意图、冻结请求及原始回执。缺失确认时只按原 ID 查询，不重发、不自动换模型或认证。
4. 创作作品及六卡均由服务器组装原始上下文。前端只提交输入、候选选择和操作意图，不能注入 system、历史切片、供应商地址或任意参数。六卡为 Earth OL、God、行间、古代人生、超级修仙、诡秘之主。
5. 候选最多同时两路执行。允许提前采用完成候选，其他候选继续收束；下一轮仍被锁定。采用幂等，多标签冲突被拒绝；未采用候选只留在对比记录，不进入正式历史。放弃恢复输入草稿，不推进正式剧情。
6. 创作宿主调用既有正式回复提交链路，变量状态及 CG 只更新被采用内容，然后执行 M2 → M3。后台任务各自冻结模型与参数，不改变后续剧情默认配置。六卡接入各自原始装配、历史、M2/M3 与预算存储；它们未声明独立变量/CG 状态引擎时，相应提交步骤为空操作，不虚构该能力。
7. 聊天新增 `rpg.model-comparison` 安装与逐会话授权，创作原生对比不依赖该插件开关。已知插件数为十个；旧默认目录不自动增加插件，新宿主必须明确启用对比适配。撤权后迟到候选不能被采用，原请求与费用继续保留。
8. 崩溃恢复覆盖已领取但确认未写回、额度组已建立但宿主链接丢失、HTTP 完成确认丢失、采用提交中断。恢复不重复派发或创建额度组，不重复推进剧情、变量、CG、摘要、记忆。

## 接口与启用方式

控制面新增：

- `GET /api/rpg/v1/authoring/comparison-budget`
- `POST /api/rpg/v1/authoring/comparison-reservations`
- `GET /api/rpg/v1/authoring/comparison-reservations/{batch}`
- `POST /api/rpg/v1/authoring/comparison-reservations/{batch}/{claim|release|settle}`
- 作者任务协议新增 `modelmirror.rpg.authoring-task/3` 批次绑定；旧任务协议保持兼容。

宿主新增：

- 创作：`/rpg-app/api/authoring/joint/comparisons/{session}`。
- 六卡：`/rpg-app/{card}/api/sessions/{session}/comparisons`，兼容原有 `chat/` 路径形式。
- 集合 POST 接受 `preflight` / `create`；集合及批次 GET 查询；批次 POST 子路径为 `adopt`、`discard`、`reconcile`、`stop`。
- 沿用操作 ID、上下文/授权 revision；候选进度独立于正式上下文 revision。

`startControlledWorkbench` 的受信 `comparison` 选项（CLI 为 `--comparison`）与 `createGenerationCards({comparison:true})` 显式接线，默认关闭。服务端提供控制面地址及 S2S 凭据，浏览器不能配置。开关不赠送额度，不安装或授权聊天插件。源文件身份检查继续有效，既有冻结会话不能静默迁移到新源码。

## 验证证据

测试使用真实 Python 路由、SQLite、Node HTTP、实际作品/六卡存储；供应商网络由 `httpx.MockTransport` 替代。测试模型能力、资格和额度是隔离样例，不是新增真实认证或授权。六卡使用完整原始提示词；测试能力显式声明大上下文，没有截短来源来绕过容量检查。

| 检查 | 结果与证据 |
| --- | --- |
| 控制面作者路由、执行、参数与预留回归 | 155/155；新增边界后比较测试 13/13，其中新增 3 项，合计覆盖 158 个不同用例 |
| C1 核心、正式传输、创作/六卡与预算联合运行 | 54/54；随后并发实接线、公开 HTTP 各 1/1 |
| 原有联合会话、参数与插件回归 | 首次 179/180；唯一失败为已知插件数量断言仍写死 9。改为 10，并增加默认目录不自动开放新插件的断言 |
| 最终受影响复测 | 45/45，覆盖修正断言、旧卡参数、六卡对比和新增预算链接恢复用例 |
| 去重后的 Node 覆盖 | 237 个用例均有通过记录（57 个比较相关，180 个既有回归）；并非声称在同一次命令中运行 237 项 |
| 静态检查 | 本批 JS 语法、Python 编译及 `git diff --check` 通过 |
| 请求落盘摘要 | 创作 + 六卡共 7 组、16 份候选记录，同批 `messagesHash` 与 `contextHash` 一致，保留源请求 hash、参数、原始回执和文件 hash |
| 原入口 | 18662/18664/18666/18668 对应有效页面 GET 200，未重启或迁移 |

主要验收命令（仓库根目录；Windows pytest 使用短、可写临时路径）：

```powershell
python -m pytest server/tests/test_rpg_comparison.py server/tests/test_rpg_authoring_route.py server/tests/test_rpg_authoring_execution.py server/tests/test_rpg_parameters_v2.py -q --basetemp=C:/tmp/c1b-py-07b
python -m pytest server/tests/test_rpg_comparison.py -q
node --test --test-concurrency=3 --test-timeout=180000 experiments/ai-rpg-engine/authoring/comparison-coordinator.test.mjs experiments/ai-rpg-engine/authoring/comparison-host.test.mjs experiments/ai-rpg-engine/studio/card-comparison.test.mjs experiments/ai-rpg-engine/studio/comparison-budget.test.mjs
node --test --test-timeout=180000 experiments/ai-rpg-engine/authoring/comparison-http.test.mjs
node --test --test-concurrency=3 --test-timeout=240000 experiments/ai-rpg-engine/authoring/joint-budget.test.mjs experiments/ai-rpg-engine/authoring/joint-session.test.mjs experiments/ai-rpg-engine/authoring/generation-session.test.mjs experiments/ai-rpg-engine/studio/card-generation.test.mjs experiments/ai-rpg-engine/plugins/generation-parameters.test.mjs experiments/ai-rpg-engine/plugins/host.test.mjs experiments/ai-rpg-engine/plugins/multi-host.test.mjs
node --test --test-concurrency=3 --test-timeout=180000 experiments/ai-rpg-engine/studio/comparison-budget.test.mjs experiments/ai-rpg-engine/studio/card-comparison.test.mjs experiments/ai-rpg-engine/studio/card-generation.test.mjs experiments/ai-rpg-engine/plugins/generation-parameters.test.mjs
```

独立证据目录：`C:/tmp/modelmirror-rpg-c1b-evidence/`。保留 `control-regression.log`、`control-additional.log`、`comparison-final.log`、`concurrent-http.log`、`comparison-http.log`、`existing-regression.log`、`final-targeted.log`、`request-captures.json`、`preserved-entries.json`、`delta-manifest.json` 和实施前快照。

失败也保留：Windows 临时目录 ACL/长路径曾使旧用例失败；改用短可写路径后通过。测试服务中间件初始化时序曾错误，已改为独立测试应用挂载正式 router。旧六卡能力样例不足以容纳完整修仙提示词，已明确扩大假能力声明，没有删改提示词。派发后保存返回旧对象导致请求未冻结的问题已修复，并由完整宿主重启回归验证。源码变动期间的运行身份拒绝没有绕过，最终复测在源码稳定后进行。

## 文件清单

以下路径相对仓库根目录；精确 SHA-256 见证据目录的 `delta-manifest.json`。

| 范围 | 文件 |
| --- | --- |
| Python 控制面（4） | `server/model_router/rpg_comparison.py`、`rpg_authoring_route.py`、`rpg_authoring_execution.py`、`rpg_bridge.py` |
| 共用传输与创作宿主（12） | `experiments/ai-rpg-engine/authoring/comparison-control.mjs`、`comparison-host.mjs`、`comparison-ledger.mjs`、`controlled-receipt.mjs`、`controlled-workbench.mjs`、`joint-budget.mjs`、`joint-session.mjs`、`joint-session-state.mjs`、`chat-parameters.mjs`、`history-control.mjs`、`runtime-plugins.mjs`、`server.mjs` |
| 六卡宿主（5） | `experiments/ai-rpg-engine/studio/card-comparison.mjs`、`card-generation.mjs`、`generation-cards.mjs`、`memory-budget.mjs`、`memory-session-store.mjs` |
| 插件（4） | `experiments/ai-rpg-engine/plugins/model-comparison.mjs`、`model-comparison.manifest.json`、`catalog.mjs`、`host.mjs` |
| Python 测试（2） | `server/tests/test_rpg_comparison.py`、`rpg_comparison_fixture.py` |
| Node 测试与辅助（6） | `experiments/ai-rpg-engine/authoring/comparison-host.test.mjs`、`comparison-http.test.mjs`、`comparison-test-support.mjs`；`experiments/ai-rpg-engine/studio/card-comparison.test.mjs`、`comparison-budget.test.mjs`；`experiments/ai-rpg-engine/plugins/generation-parameters.test.mjs` |
| 文档（1） | `docs/ai-rpg-experiment/generation-comparison/C1-B.md` |

## 未覆盖与回退

C2 双端交互、桌面/手机视觉、多供应商真实取消能力及收费回执仍未验收。此次仅证明实际协议与宿主在离线供应商下的行为；未运行共享部署、真实生成或全仓所有测试。本批不改前端产物，未重新构建前端。Windows 原子文件存储沿用现有保障，不新增断电恢复保证。

首选回退是关闭新对比入口与授权，保留配置、批次、候选、正式剧情和账本。停止未派发任务；已派发和未知任务继续用原 ID 核对，不能通过切回旧二进制或删除记录假定请求未发生。既有验收服务维持原版本，不自动迁移、取消或重放。
