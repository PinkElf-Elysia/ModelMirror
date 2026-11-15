# R3a/R3b 组合门禁与 Draft PR 范围

日期：2026-11-14（America/Phoenix）。这是当前提交准备说明；历史阶段报告保留原貌，不用后续结果覆盖首次失败。仅整理代码、文档和离线验收，未提交、推送、创建远端 PR、合并、部署或调用模型。

## 基线与隔离

原工作树 `C:/tmp/mm-cr3b2`、分支 `codex/rpg-character-r3b-controlled`、HEAD `f31bf50b`，91 个未提交范围文件保持字节不变。独立组合树 `C:/tmp/mm-cr3-pr`，最终分支 `codex/rpg-character-r3-pr-286`、HEAD `286de1ee`，数据和日志 `C:/tmp/mm-cr3-pr-data`；干净主线对照树 `C:/tmp/mm-cr3-base`。不复制正式卡片、会话、令牌或真实请求数据进入仓库。共享回归的旧 helper 还会在本树被忽略的 `.rpg04-work` 和系统临时目录中新建随机中性记录；不读取或清理原存档。

初次获取主线为 `d61dbd84a32793b454420bad715cc2c4e59410f8`；验收期间推进至 `af81d10ee3a8b8243a729fa115771e7c32b637f0`。后一次增量 82 个路径仅在独立 E5 实现和文档中。本轮 91 文件无同路径冲突；client、server、character、authoring、card-replica、plugins、studio、runtime、context、content 及 AGENTS 均与初次组合基线一致。不据此接入多 Agent、E5 或 Harness。最终远端复核又取得 `286de1eeb1066f1f82d265596e286373d872a49d`（R8，45 个路径），增量仅在 agent-core 与其文档中；上述受测输入和运行依赖再次确认不变。此次锁定 286de1ee 作为最终组合基线，重跑其新生成存档与两个构建；主线继续前移须在实际提交前复核，不无限追逐其他工作树。R8 已合入主线不等于本轮角色分支已与其对接。

复制完整白名单是一次组合验收快照，不是重新修改 91 个文件。按逐文件 SHA-256 验证后再验收；依赖从本机相同 package/lock 的已安装目录复制，无安装、升级或锁文件修改。实际第二个构建目标是 `card-replica`，不是早期 `ui` 目录。

## 拟提交范围：92 文件

| 类别 | 文件数 | 内容 |
| --- | ---: | --- |
| character/daily | 21 | 玩家档案与静态头像、身份快照、会话目录/检索/归档/导出、精确兼容及测试 |
| character/revisions | 27 | 消息修订、候选、分支、受控适配、共用预算、恢复、页面与测试 |
| 既有 character 入口 | 16 | 创作/群聊导航、身份接入、v1/v2 分支及精确运行描述符；无 R1/世界源码改动 |
| 帮助/父前端 | 10 | 两篇文章、注册与测试、6 张中性截图 |
| 文档 | 18 | 原 17 篇阶段文档及本篇组合门禁 |

本轮不更改既有 91 个范围文件；仅新增本文。精确清单和哈希保存在本地 `submission-scope.json` / `submission-scope.md`。不把目录通配符当作将来 Git add 授权。正式素材、头像素材原文件、会话、回执、模型文本、本地截图/日志/验收 JSON、构建产物和 node_modules 均排除。

6 张帮助图均已逐张目视复核并核对原登记哈希：`2a7dfea1/character-daily-sessions.png`、`character-player-profiles.png`；`f31bf50b/character-revision-candidate.png`、`character-revision-mobile.png`、`character-revision-controlled.png`、`character-revision-controlled-mobile.png`。它们只包含原创中性测试数据；不新增正式角色或本轮验收截图。

## 当前产品边界与接口交接

- R3a 使用独立 daily 存储；档案修订与会话创建快照分离。旧 `{id,name}` 身份及 v1 会话保留原语义；头像不进入模型。会话搜索/导出仅返回已验证、玩家可见的完成历史。
- R3b 使用 `character-revisions-v2`；编辑和历史候选采用创建分支，原文/回执不改。重生成固定原前文、角色版本与身份；未选候选不进入有效历史。派生资料随来源失效，无法证明依赖的旧记忆待确认，不能自动注入。
- `startControlledWorkspace` 同时组合原单聊、群聊和修订入口；三者必须使用同一 `SharedBudgetGuard`。保留来源上限、任务总上限和上游授权；未知请求继续占用，不因刷新或切换入口释放。
- 浏览器不能提交可信模型对象、完整上下文或回执；宿主核验旧 character/group 消息合同和 V1/V2 参数回执。恢复只查询原请求，不补跑、不自动采纳。没有修改 server 网关合同或新增生产依赖。
- 精确兼容清单和旧源码字节不变；未知版本、篡改与源码漂移继续拒绝。v1 修订夹具不自动迁移至 v2，旧程序不得回写新记录降级。
- 分支仍依赖可信原来源及完整群网关证据目录，不是便携证据包；没有多 Agent、自主发言、世界写入、Harness 接入、分支合并、分支换身份/模板、自动摘要/记忆作业。

详细接口以 [R3a 交接](CHARACTER_R3A_HANDOFF.md)、[R3b 受控交接](CHARACTER_R3B_CONTROLLED_HANDOFF.md) 为准。前期 R3b_HANDOFF 中“未接受控”等属于初始阶段；后续实现以受控交接为准，真实验收与系统归因以 [收口记录](CHARACTER_R3B_CLOSEOUT.md) 为准。原报告不覆盖重写。

## 离线验收

| 检查 | 结果 | 证据边界 |
| --- | --- | --- |
| 角色/R1/R2/R3a/R3b 聚合 | 181/184；失败 3 | 旧 a6563a9b 世界失败；包含旧角色、群聊、参与隔离、来源漂移拒绝 |
| 最新 286de1ee 原生存档 | 4/4 | 新生成中性单聊/群聊与 Offline/Summary/Joint 世界存档；恢复、续聊、原绑定不变及拒绝篡改 |
| 禁外网 Linux 修订合同 | 48/48 | 现有 Node24 镜像、只读源码/参数解析器挂载、临时数据；无拉取、部署或外网 |
| 父前端相关四文件 | 53/56；失败 3 | 帮助中心既有语音基线、步骤数和图片基线断言 |
| 聊天组件 | 94/94 | card-replica 的本地组件/呈现测试 |
| 父前端构建 | 通过 | 最终 286de1ee 分支 tsc + Vite；保留 chunk 大小警告 |
| 聊天前端构建 | 通过 | 最终 286de1ee 分支 card-replica tsc + Vite |
| 帮助图全库检查 | 失败：24 处 | 干净主线相同诊断，新增 6 张无诊断 |
| 共享五文件 | 144/144（完整分批覆盖） | 三个短文件 60，加受控会话/参数生成 13 批共 84；未修改原断言 |
| 网关 mock | 187/187 | 独立 Windows basetemp，沿用原 character/group/V1/V2 合同 |

干净 d61dbd84 对照复现旧世界 3 项、帮助测试 3 项及截图 24 项；af81d10e 与 286de1ee 这些路径及依赖无变化；本地 main-advance / main-advance-r8 记录增量路径。历史 `af3beb63`、`0cb019df` 角色和 `3fffd5b4` 世界，以及 `2a7dfea1` 单聊/群聊恢复通过；原 a6563a9b 的 `AUTHORING_RUNTIME_BINDING_MISMATCH`、`OFFLINE_SESSION_CORRUPT`、`JOINT_SESSION_CORRUPT` 不通过，不扩大指纹范围去消除它们。

命令均在独立树运行：`node --test --test-concurrency=1` 指定全部 character 测试；共享五文件 controlled-provider/controlled-session/controlled-state/model-control/generation-session；pytest 指定 test_rpg_character_contract/group_contract/authoring_route/authoring_execution/parameters_v2，带独立 basetemp；Vitest 指定 helpContent/RpgLibrary/RpgAuthoringEntry/RpgChatState；两个前端各 `npm.cmd run build`。本地 `*.result.json` 记录精确 cwd、参数、退出码与耗时。最新主线存档用原回归的独立脚本副本，仅改变来源提交并补全依赖，不改产品测试断言。

## 失败记录和证据保全

- 保留历史并发测试首次失败、广域回归中断、旧依赖 junction EPERM、V2 参数/缺价/页面首次失败及修复后结果，引用原阶段报告，不称为首次全绿。
- 本轮父构建首次因 card-replica 依赖尚未就绪报 TS2307；相同源码在依赖齐备后通过。最终分支上的首次重复构建也随终端中断，另存 final 日志的两个构建均正常完成。网关首次系统临时目录权限错误，改用独立 basetemp；Linux 首次漏 R2 临时根、随后发现纯 Python 参数解析器挂载缺失，补齐后 48/48。
- 最新主线世界存档脚本首次漏 character 依赖，3 项 ERR_MODULE_NOT_FOUND；完整打包后新目录 4/4。失败脚本和日志保持原样。
- 长测试曾丢失终端会话，后续独立进程出现退出码 1073807364；保留中断日志，没有正常结束的输出不计通过。最终重跑另存文件。原聚合运行另观察到 2 条参数/记忆用例失败；短路径定向 2/2、随后完整分批覆盖中的同用例均通过。首轮根因未完全定论，不宣称仅为并发，不以分批通过覆盖失败记录。
- 原始 `git diff --check` 失败（继承 CRLF 行尾）；`cr-at-eol` 仅作为只读对照，未变更仓库配置/属性或降低产品测试断言。没有行尾之外的空白诊断。源码参与运行哈希，因此不在本次整理中规范化源码。
- 原工作树 91 文件、真实验收清单 108 项、离线刷新清单 18 项逐项哈希一致；秘钥模式扫描无匹配，仍只按白名单控制范围。未读取、复制或改写原素材/会话，未使用共享额度。

## 真实结果、延期与未验证

历史真实验收为 8 次（Gemini / DeepSeek 各 4），系统 7 项通过、1 项部分通过；本轮新增真实调用为零。模型在可见事实上的叙事偏差直接存在于原响应，用户接受为原型误差，不再追加提示词补丁。报告费用仅为既有回执证据，不代表最终账单复核。

第 8 项完成后恢复/进程重开通过，但真实处理中刷新采样早于派发，继续待验。后续桌面/390 CSS px 离线 Chromium 刷新 2/2 已证明本地受控链路不重发，不能替代供应商真实处理中刷新。R3a 浏览器 16/16、R3b 页面 11/11 和定向 3/3 均保留原证据，本次无 UI 源码变动，未声称重新做了整套页面人工验收。

Windows 原生文件选择器取消、真机软键盘/触控/系统返回继续延期，非通过、取消或永久豁免。人工最终页面签收、共享部署、线上账单、长期人物/记忆效果、远端 CI 未在本次验证。旧 studio legacy fingerprint 的历史主线失败继续保留，本次未扩展重跑该非直接依赖文件。

## 发布与回退

本地 Draft PR 标题/正文位于独立数据目录；没有创建远端 PR。提交前按最终 92 路径白名单复核暂存区，另行取得 Commit、Push 与 Draft PR 授权；不能以本报告推定发布许可。

回退只停止本轮独立入口并使用旧工作树/入口，保留新数据、来源、候选和证据。不得修改共享存档、关闭指纹校验或批量降级。后续发布时若主线再次变化，先做受影响路径比较与必要验证。


