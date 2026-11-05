# 变量面板 A 组真实验收与未派发状态修复

日期：2026-11-05。工作树 `codex/variable-panel-livefix`，基线 `6aa5924dd64b706a684ab2fd0dcc6da1d9feeb08`。

## 结论与范围

原 4 次生成授权内完成 V1–V4，均为 JP 控制面的 `google/gemini-3.8-flash`，temperature=0.70、top_p=0.80、max_tokens=8192；4 次剧情、0 次认证、0 次独立面板整理、0 次自动重试。M1=1，分支存档启用；M2/M3 关闭。

**状态更新、计划不生效、窗口裁剪后状态延续、分支隔离及重启回放均有真实结果。** 本轮只完成 A 组，不声明可选 B 组变量面板与记忆宫殿真实联动已通过。待用户人工审阅，不把协议校验等同于完整内容质量验收。

入口：`http://127.0.0.1:18650/?work=work-35c8164205b3dde8124320d98918eee91f7709bb13ad9f04d9bafdcc89dd63ea`。
18642、18644 原验收入口及 18648 原失败记录均保留；结尾 HTTP 检查均为 200。旧工作树继承的 41 个文件 hash 未变。

## 阻断修复及共享更新

原 V1 被共享 JP 的 `RPG_AUTHORING_EXECUTION_ENABLED=false` 拒绝，供应商调用为 0。旧接口直接返回 HTTP 503，未保存可查询的任务结果，本地因此只能判作未知并占用 1 次。

修复包含：

- 鉴权的只读 `/api/rpg/v1/authoring/readiness`，在本地预估和新任务落盘/预留之前检查执行开关。预检关闭或不可确认时不生成新执行记录、不占用额度。
- 若开关在预检后关闭，服务端仍以原操作/请求 ID 持久化 `failed + dispatched:false` 回执；同 ID 后续查询或重复请求只返回原失败，不在重新开启后派发。
- 本地仅凭校验通过的受控失败回执确认未派发，预算结算为 `not_dispatched` 并释放该槽位；请求、身份与回执均保留。真正未知、已派发失败仍计数，不以普通 HTTP 错误作为退款依据。
- UI 和帮助区分“执行入口未开启”“未派发”“结果仍未知”。保存失败与结果丢失仍保持保守恢复。

经用户明确授权，`modelmirror-server` 已更新为 `modelmirror-server:variable-livefix-20261105`，镜像 ID `sha256:392a7a674885444af5f6f89fc1e9549836735c7a1836734d489776caf4eda174`。服务器补丁文件 SHA-256：`b68c07326a9f399e7f2cb7a4a8ab95051d636014f9db8950acac4cd9862447a9`。

共享更新只复制该 Python 文件并开启执行开关；凭据及其他环境值、35 个挂载、端口和网络均逐项核对保留。先在开关关闭时收束原操作，再开启，合计两次本服务重建。其他服务没有重建。

完整 Compose 配置不落盘。自动审批拒绝过复制完整配置到新文件的方案（可能额外保存凭据）；该写入未执行，最终以进程内配置和 stdin 完成部署。

原始配置来源保留：`C:/tmp/modelmirror-shared-441-ops/server-catalog-compat-compose.json`。它含环境变量占位符，不能脱离现有运行环境直接重建。实际部署脚本与配置 hash 在下述 evidence 的 `deploy.py` / `deployment-before.json` / `deployment-after.json`；Compose 标签因 stdin 显示配置来源为 `-`，不要据此推断没有配置。

## 原请求与额度的处理

原请求 `request-b080136c4b1f3d22673c65cfced1e31d8d581d8053cbc9330fab4019ca3623b8` 的原 503 记录保留。补丁部署且执行开关仍关闭时，以完全相同 ID 和原始请求建立新的持久“未派发拒绝”结果，再由旧界面按原 ID 恢复到终态；此过程供应商调用为 0，原生 spent=6 不变。该回执明确标注为新建立的关闭开关拒绝，不伪称原 503 已自带回执。

18648 保留旧宿主及历史账本（旧计数仍占 1/4），其新派发守卫关闭。没有手工改写旧账本，也没有静默迁移会话。18650 使用独立新目录及修复源码，沿用同一四次授权；共享 JP 路由从既有 spent=6 到 spent=10，limit=10。本批新本地账本 used=4/4，剩余 0。两层限制约束同一批调用，不相加。

四份请求完成后，18650 的本批中继也写入 `closed.json`，禁止新生成；原结果查询仍可用。

## 实际结果

| 步骤 | 结果 | 状态 revision | tokens（输入/输出） |
| --- | --- | ---: | ---: |
| V1 前往灯塔并拾钥匙 | 北岸灯塔；体力70；铜币20；背包含钥匙；发现true、交付false；备注null | 1 | 394 / 554 |
| V2 只计划明天交付 | 数据及 hash 完全不变，宿主标记 unchanged | 1 | 714 / 534 |
| V3 前往档案馆交付 | 档案馆；体力65；铜币25；移除钥匙；交付true；已登记 | 2 | 689 / 525 |
| V4 从V1分支返港 | 雾港码头；体力67；铜币20；仍有钥匙；交付false；备注null | 2 | 713 / 889 |

每份回执 HTTP200、completed、finishReason=stop、retries=0、actualModel 与冻结模型一致；总计 5012 token，不推断美元费用。

V3 的实际消息为单条 system、V2 用户原文、V2 助手完整原文、本轮带变量协议的 user；V1 原文已退出发送窗口。当前状态仍随本轮 user 携带。模板 HTML/CSS 不发给模型，保存的模型原文未被展示清理修改。

V4 分支只继承 V1 的封存状态和回合。新子路线创建后剧情模型为空、插件未授权；从 UI 单独选定模型与参数并启用 M1、分支。实际请求历史仅含 V1，主线 V3 结果未进入分支。V4 完成后主线仍保持档案馆/65/25/已登记。

## 浏览器、恢复与质量边界

真实调用通过创作 UI 发送，作品由冻结包在 UI 导入、校验、发布。逐轮检查面板显示及原文。桌面和 390×844 手机验证：数据展开/收起、复制地点（实际剪贴板“雾港码头”）、路线切换、刷新、真实回放。390px 页面 scrollWidth=390，没有页面横向溢出。

四次完成后仅重启本轮 18650/18651 独立服务。9 份核心持久记录在重启、刷新和路线切换前后逐字节 hash 相同；请求 claim/returned 始终各4份，JP spent 始终10。18642/18644/18648 未重启。

仍需保留的观察：

- 数值和任务语义符合冻结输入；正文会补充场景与动作细节。V4 自行加入“锁门”，不属于用户明确指示的动作；本轮没有因此增加任何已建模的消耗或奖励。这是正文忠实性限制，不能称为完全遵从。
- 手机预览窗口保持打开并切回桌面尺寸后，预览弹窗顶部插件/路线按钮出现重叠。关闭预览后嵌入式桌面视图恢复正常。本轮冻结运行源码未改此布局，列为后续 UI 修复项，不宣称所有布局门禁通过。
- A 组没有制造付费未知故障，没有运行 B 组或 M2/M3 联合真实验证；这些边界仅有既有离线覆盖，不能替代真实结论。

## 离线验证记录

- Python 受控创作执行、路由与 RPG bridge 目标测试初次 117通过/1失败。失败涉及隔离运行主应用的可写数据目录及新增 readiness 路由清单；在同一镜像临时目录中修正预期后，原失败测试单独复跑1通过。保留4项既有 FastAPI deprecation warning，不称为完整后端全绿。
- `node --test authoring/controlled-provider.test.mjs`：13/13。
- controlled-session + joint-budget：34/34。
- workbench、client、UI、variable panel/runtime/http 相关回归：51/51。
- `npm run test:authoring` 对应 890 项：首次873通过/17失败，17项均为运行中源码换行变化触发 `OFFLINE_FILES_CHANGED_RESTART_REQUIRED`。冻结源码后按原失败文件复跑：joint-session 33/33、joint-api 19/19。没有跳过失败项；这不是一次890项全绿的执行记录。
- `git diff --check` 通过；修改的3份 Python 文件语法编译通过。没有新增依赖或数据库迁移。
- 本轮未修改 React/Vite 打包代码，未重新执行两处前端构建；不引用此前构建结果充作本轮证据。

## 文件与证据

新增修复范围12个源/测试/帮助文件：

- `server/model_router/rpg_authoring_execution.py`、`server/tests/test_rpg_authoring_execution.py`、`server/tests/test_rpg_bridge.py`。
- `experiments/ai-rpg-engine/authoring/controlled-provider.mjs`、`controlled-provider.test.mjs`、`controlled-test-support.mjs`、`controlled-session.test.mjs`。
- 同目录 `joint-budget.mjs`、`joint-budget.test.mjs`、`joint-session.mjs`、`joint-ui.mjs`、`creator-guide.html`。

相对于继承的变量面板批次进行复核；Git 中另外39个已验收 CG/变量面板源文件和2份原验收文档属于既有内容，不冒充本修复新增。`docs/audits/provider-coverage.json` 为继承差异，保持原样。部分已触及文件的混合换行统一为LF，不能把整文件行尾差异当成同等规模逻辑重写。

本地未跟踪证据根（不提交原始模型日志、运行数据或账本）：

- `experiments/ai-rpg-engine/.rpg04-work/variable-livefix-20261105/`：baseline、测试日志、共享部署与原请求收束证据。
- `experiments/ai-rpg-engine/.rpg04-work/variable-panel-live-resume-20261105/`：冻结 plan/package/authorization、V1–V4 request/claim/returned/review、branch-before-authorization、两份 acceptance-before/after-restart、captures 三张PNG及完整运行数据。

`acceptance-after-restart.json` 保存12个修复文件及3张截图的 SHA-256，`acceptance-before-restart.json` 保存模型回执身份、消息检查、两层额度和9份持久记录 hash。

冻结包 SHA-256：`4c05aba8ad02117ad333f42cfb6152aec994051b92ec15530bccc3c1cf544c11`；冻结方案 SHA-256：`589a3b543a5c3b2f760a6528d8973443cfbba0a0b8026b6d91f35743b7860d52`。原准备方案中的 awaiting 状态作为历史保留，实际授权与本报告共同解释，未改写冻结原件。

## 回退与交付状态

首选停止本轮独立18650/18651服务，保留数据，继续使用18642/18644原入口。旧宿主不得直接读取新写入 `not_dispatched` 的账本；不向旧目录复制新状态。

共享原镜像保留为 `modelmirror-server:vp-before-20261105`，原 image ID `sha256:82444f501502373dc196b7ae46bbfb019542d9bd8d31b095a101e0958d639ee5`。如需共享回退，先核对没有活跃任务，再用原配置和运行时占位变量恢复原镜像/开关，原挂载及路由 spent=10 保留；不得重置额度、删除回执或对原失败 ID 重发。部署脚本的镜像身份保护有特定前置条件，不能将其当作不经复核的通用回退命令。

本轮发生了用户明确授权的共享后端更新。没有 Commit、Push、PR、Merge 或其他发布。
