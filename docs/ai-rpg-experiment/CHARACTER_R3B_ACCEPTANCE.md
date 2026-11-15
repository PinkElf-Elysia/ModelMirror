# R3b 离线验收报告

日期：2026-11-14。本轮完成独立离线实现、最小导航、帮助登记和专用浏览器验收；**不等同于真实受控生成接入或发布完成**。未调用产品模型、未提交/推送/部署。实现边界见 [交接](CHARACTER_R3B_HANDOFF.md)。

## 基线与原证据

开工拉取主线 f31bf50ba99686abfb18431413fddd2da16e5648，与 R3a 2a7dfea1 的 50 文件白名单组合，路径冲突 0。独立树 `C:/tmp/mm-cr3b`，数据 `C:/tmp/mm-cr3b-data`。核对来源 50/50 文件哈希一致、原数据副本 29/29 哈希一致；证据 `baseline.json`、`r3a-copy.json`、`original-data-hashes.json`、`scope-audit.json`。这些计数不冒充整个用户文件系统的哈希清单；正式素材和其他原证据未作为写入目标。

原 R3a 文档逐字保留，R3b 另记结果。旧 authoring/plugins/character 在 2a7dfea1 到 f31bf50b 的上游差异为空；没有通过扩大旧指纹接受范围修复旧失败。

## 逐项结果

| 项目 | 结果与证据边界 |
| --- | --- |
| 玩家、角色、开场编辑与历史截断 | 通过；新分支人工修订，原记录不覆盖、无自动生成 |
| 候选生成、预览和采用 | 离线通过；完成不自动采用；历史采用分支，末尾采用保留事件 |
| 原请求、响应和回执 | 原记录不改写；规范化证据封存；完整群网关 evidence 仍依赖原传输文件，非独立证据包 |
| 精确重生成 | 夹具通过；取生成前上下文，玩家输入不重复，接话无伪玩家消息；不支持推测缺失的旧模型描述 |
| 成员、可见范围与身份 | 通过；历史参与边界、固定身份/版本、同名区分、私有资料过滤；采用私有候选不扩大接收范围 |
| 记忆/摘要失效 | 有依赖资料失效、无依赖资料待确认、分支内人工确认通过；未进行模型抽取或总结 |
| 幂等/CAS/篡改 | 通过；重复操作不多派发，同 ID 异参数、过期修订、跨分支候选及重封装上下文篡改拒绝 |
| 故障与恢复 | 通过；提交前后故障、回执落盘中断、未知冻结、查询无记录不重派；关闭并重新打开存储/提供方后仅查询原结果 |
| 预算 | 夹具族额度及总授权、原入口协调、未知保留和无价格阻断通过；真实共享账本未接入 |
| 页面编辑保护与刷新 | 通过；confirm/beforeunload 取消、正文草稿、实际重载、分支选择、导出、键盘焦点 |
| 群聊/移动端/导航 | 通过；390 CSS px 无横向溢出、单次发送与接话、跨页入口；不代表真机操作 |
| 旧存档 | af3beb63、0cb019df 角色，3fffd5b4 世界，2a7dfea1 精确基线补验通过；a6563a9b 三项既有世界失败仍保留 |
| 原合同及导出边界 | R3b 未改旧宿主合同/指纹；卡片导出未接入修订；普通分支导出无候选/私有资料/凭据 |
| 帮助与构建 | 两个构建通过；新两张中性 PNG 合规；旧帮助 3 项失败与旧截图 24 处诊断保留 |
| 真实执行/设备 | 未实现开放真实 R3b 生成；真实模型未调用；原生选择器取消及真机键盘/触控/系统返回延期待验 |

## 实际测试与日志

下列日志全部在 `C:/tmp/mm-cr3b-data`，不进入仓库。

| 检查 | 结果 | 日志 |
| --- | --- | --- |
| 新 revisions Windows | 30/30 | revisions-private-fixed.log |
| 新 revisions 禁网 Linux、只读源码、独立 /tmp | 30/30 | revisions-linux-private-fixed.log |
| 角色/R1/R2/R3a/R3b 聚合 | 163 项、160 通过、3 旧世界失败；该次含当时的 27 项新测试，后补 3 项由上两行覆盖 | final-character.log |
| 历史存档定向补验 | 4/4 | combination-recovery-retest.log |
| authoring/studio 广域串行检查 | 中断，记录 339 条通过但无结束报告，不计全套通过 | shared-hosts.log |
| 直接依赖的五文件存储回归 | 72/73；旧 studio 指纹断言在纯主线复现 | shared-storage-final.log |
| 广域停止位置 C2-B 定向检查 | 1/1；未复现停滞 | shared-c2b-bounded.log |
| R3a 相同相关回归集合 | 232/232：补验 196，加本轮已完成 comparison-host 8、store 28；分日志核对，不冒充广域全绿 | shared-r3a-remainder.log、shared-hosts.log、shared-storage-final.log、r3a-shared-matrix.json |
| Windows 网关重点 mock | 70/70 | gateway-windows.log |
| 禁网 Linux 网关重点 mock | 70/70 | gateway-linux-retest.log |
| Windows 全部 RPG 网关 mock | 295/296；父应用导入缺 duckdb，环境失败 | gateway-windows-full.log |
| 禁网 Linux 全部 RPG 网关 mock | 295/296；父应用默认持久化目录位于只读挂载 | gateway-linux-full.log |
| 上述 Linux 单项隔离源码副本补验 | 1/1，4 个既有 on_event 弃用警告；未冒充全套首次通过 | gateway-linux-isolated-copy.log |
| 相关前端四文件 | 61/64；页面三文件通过，帮助 3 项既有失败 | frontend-tests-final.log |
| 父前端 build | 通过，保留大 chunk 提示 | client-build-final.log |
| card-replica 聊天前端 build | 通过 | chat-build-retest.log |
| 专用 Chromium 桌面/窄屏流程 | 9/9 | browser-run8/results.json |
| 帮助截图/取消编辑焦点 | 通过；截图过程新生成 0 | help-browser-release/result.json |
| 独立预览进程中断后的恢复 | 20/20 实际记录哈希不变；只更新死进程所有权记录；三页面 HTTP 200 | preview-restart-before/after.json、preview-health-final.json |
| 帮助图片检查 | 24 处旧诊断，新截图 0 新增 | help-images.log |

命令基于计划实际执行：`node --test --test-concurrency=1` 的 revisions、character/conversation/group/daily、authoring/studio 测试；Python `server/tests/test_rpg_*.py`；四个相关前端文件；`npm.cmd --prefix client run build` 和 `npm.cmd --prefix experiments/ai-rpg-engine/card-replica run build`。计划中的 ui 路径首次缺 tsc，按实际项目改为 card-replica，未安装项目依赖。Linux 容器使用已有本地镜像、`--network none`，不拉取镜像、不连接共享模型。

## 失败、修复与归因

- 既有 a6563a9b 世界三项：AUTHORING_RUNTIME_BINDING_MISMATCH、OFFLINE_SESSION_CORRUPT、JOINT_SESSION_CORRUPT。R3a 原报告已有同基线重现；此次相同。未删除断言或机械更新旧哈希。
- 既有帮助三项涉及语音指南基线、旧语音文档结构、voice-library 图片基线；旧图片检查 24 处。未把这些标为修复。
- 本轮新观察到的主线既有失败：studio/model-session-store.test.mjs 的 legacy verified runtime 断言，实际 88c10e8e…，期望 05d0d34f…。从 f31bf50b git archive 建立的独立纯主线副本复现完全相同差异（main-studio-legacy-reproduction.log），不是 R3b 代码导致；未修改期望或接受清单。
- 广域 authoring/studio 进程在记录 339 条通过后结束，工具会话不可恢复，没有结束统计或可确认终止原因；不能称为通过或确定为产品挂起。日志停止附近的 C2-B future transfer 有界定向检查 1/1 通过。按 R3a 原日志定位 12 个相关测试文件；已完整执行的 comparison-host/store 不重复，余下 10 文件单独补验。
- combination-tests 首次 136 项中 6 失败：上述 3 项加 Windows 文件复制 junction 导致 EPERM 3 项。新树 engine 依赖改为已有依赖的独立实目录副本后，相关 4 项通过；不改变项目依赖清单。
- full-character 首次 158 项中 4 失败：旧 3 项加实现与聚合测试同时进行触发 RUNTIME_CHANGED。冻结源代码后聚合只剩旧 3 项；原漂移拒绝证据保留。
- 前端首次 59/64：两个新增帮助登记错误已修复（精确文章列表、指南索引）；剩下旧 3 项。没有削弱旧期望。
- 浏览器 run1 为脚本 ESM Windows 路径错误；run2 定位把玩家名当作群标题，6/7；run4 确认角色库导航在隐藏容器内，8/9，移至可见 header 后通过；run5 启动未就绪连接失败。run3、run6、run7 和最终 run8 均保留，最终 9/9。原失败截图/脚本未覆盖。
- 隔离补验首次 Linux 命令未传 R2 测试根目录，28/29；补齐环境后 29/29，后新增私有候选回归最终 30/30。前两次父应用补验分别暴露 agent/MCP 默认目录只读限制；最终复制源码到容器临时目录运行该单项，1/1。Windows duckdb 环境未安装/未伪称解决。
- 最终自查发现采用历史候选默认 public 且成员边界可能停留在前一条消息。先新增失败回归 `private-candidate-before.log`，最小修复保留原 visibility/addressedTo 并固定请求时边界；Windows/Linux 最终新增测试通过，页面重新验证。原预览数据保留，使用新的 preview-final-review，不重写旧运行哈希。
- 构建首次使用错误 ui 路径、父构建缺 card-replica 的已安装依赖链接；修正环境后两个构建通过。首次日志均保留。
- 最初部分既有测试 helper 使用默认 mkdtemp 根目录，创建了新测试目录，没有覆盖旧证据；后续运行统一传入本任务短数据目录。最终 git diff --check 仍报告组合副本的 CRLF 行尾诊断（diff-check-final.log），未进行无关全文件换行重写，此项不能记为通过。

## 预览、剩余门禁与回退

当前独立预览：`http://127.0.0.1:43162/revisions`，数据 `C:/tmp/mm-cr3b-data/preview-final-review`。进入“续写体验员（中性夹具）”单聊或“河边茶亭”群聊体验；页面链接连通角色库/创作、群聊和会话目录。所有生成是明确标记的离线回复。

未完成：真实受控发送/模型切换/跨进程真实预算；脱离原来源的完整证据快照；原回复统一候选展示；用户人工页面验收；设备延期项。原生文件选择器取消与真机键盘/触控/系统返回继续是待验，不是永久豁免。完整 R3b 发布或真实模型验收前应先补齐对应门禁并另行授权，不能仅增加调用额度就启用。

回退停止本任务预览并移除可选入口，保留所有新数据；不回滚 R3a、不迁移旧存档、不清理其他任务资源。暂存区为空；素材、会话、日志与本地验收文件不入库，仅中性帮助图片属于代码交付范围。

## 最终范围核对

74 个文件：R3a 原样副本 45、在 R3a 上增加 R3b 接入 5、R3b 新文件 24。包括 R3a 两张与 R3b 两张中性帮助 PNG；没有正式素材、会话、日志或本地数据。暂存区为空。最终清单与哈希在 scope-audit.json；运行实现未写入旧工作树。
