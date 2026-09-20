# M1 历史窗口 B3：正式 UI 与封装候选

状态：B2 已获用户确认；B3 已完成本地离线/UI 验证，等待用户验收。B4 真实调用未授权、未运行，不能据此宣告记忆竖切闭环完成。

## 基线与范围

工作区 C:/tmp/modelmirror-rpg-history-window，分支 codex/rpg-history-window，基线 61b6eea80152904eb8fb3cc0ebba5e7647594828。主工作区、18453 原型、原会话与旧账本保留。未 Commit、Push、PR、共享部署或读取凭据。
本批按设置组件/会话接线、市场/帮助、验证/证据小批推进。前端变动为 card-replica/src/{session-client.ts,history-window.tsx,chat.tsx,styles.css,platform.tsx}，父仓仅 RPG 市场、对应测试和帮助。没有改 Studio 布局、旧 RPG05、作者文本、原 assembly、模型参数或世界书策略；B2 后端运行绑定不变。

## 交付行为

- 市场第三项“历史窗口”；安装不启用。新版本会话的插件浮层内显式启用并进入设置，没有新常驻面板。
- 默认3个完整回合、开局资料关闭；1–50整数校验。保存仅影响后续发送，草稿保留，取消或 Esc 不保存。
- 每次发送携带服务器策略 revision。设置操作先在浏览器保存原操作 ID；未知结果恢复原操作，模型切换/分支/发送不能绕过未确认设置。
- 停用恢复完整历史；卸载保留配置和历史并撤销授权，重装不自动启用。旧格式会话只提示不兼容，不提供静默升级。
- 设置失败提示与普通保存中的状态分开；恢复成功后重新读取策略。权限未知时不静默发送另一种装配。

## 验证命令与结果

日志均在 experiments/ai-rpg-engine/.rpg04-work/history-window-b3/，完整 hash 见 B3-DELIVERY.json。

| 检查 | 结果 | 日志 |
|---|---|---|
| node --test experiments/ai-rpg-engine/plugins/*.test.mjs experiments/ai-rpg-engine/studio/*.test.mjs | 106通过，1跳过，0失败 | studio-plugins.log |
| npm.cmd run verify --prefix experiments/ai-rpg-engine/card-replica | 26测试、TypeScript、生产构建通过 | earth-verify.log |
| client: npx.cmd vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false src/pages/RpgHistoryWindow.test.tsx src/pages/RpgPluginsPage.test.tsx src/pages/RpgChatState.test.ts src/pages/RpgModelSelector.test.tsx src/content/help-center/helpContent.test.ts src/components/StudioBetaPanels.test.tsx | 30非帮助测试通过；帮助当时14通过1失败 | frontend-help-final.log |
| client: 同一Vitest选项，单独 src/content/help-center/helpContent.test.ts | 修复登记基线后15通过；当前合计45项通过，非一次命令结果 | help-tests-final.log |
| npm.cmd run build --prefix experiments/ai-rpg-engine/card-replica -- --base=/rpg-app/earth/ | Studio路径构建通过 | earth-studio-final.log |
| client: npm.cmd run build | 最终生产构建通过，保留既有大chunk警告 | client-build-delivery.log |
| client: npm.cmd run verify:help-images | 3项既有失败，0新增 | help-images-final.log |

唯一测试跳过：未设置 RPG_B3_LEGACY_COPY，未对真实旧会话副本执行兼容验收；合成旧格式通过不替代此项。
帮助图片的3项失败为 b266b870/studio-beta-headings.jpg、eeb5bbd2/creator-cache.jpg、eeb5bbd2/studio-layout.jpg 未被引用。将 HEAD 的47份相关文件隔离复制后执行同一检查，得到完全相同失败（help-images-baseline.log）；没有删除旧图片或弱化检查。

## 可见 UI 与请求证据

正式候选 http://127.0.0.1:18455/rpg/earth；市场 /rpg/plugins；帮助 /help/set-rpg-history-window。18455代理/18456宿主均为本轮独立进程，Provider关闭、预算0。其余父仓API为本地空响应，只验证RPG入口与帮助，不代表完整父仓后端。
实机浏览器检查：安装、逐会话启用、默认值、51拒绝、Esc取消、键盘Space勾选/Enter保存、草稿与刷新、三轮离线样例、停用、卸载重装不恢复授权、旧格式不兼容、帮助导航。390px无横向溢出。最后按帮助进入RPG/市场/地球卡/历史/插件/设置，并确认取消后仍为原1回合配置。
虚构许澄会话由宿主种子创建；本批没有重跑整个角色创建流程。未声称完整焦点循环通过；浏览器未知网络结果依赖前端/HTTP故障测试，不冒充真实浏览器断网实测。
UI实际保存3回合/6条历史；第三轮策略 totalTurns=2、selectedTurns=[2]、initialSnapshotAdded=true。完整原文、请求hash、策略记录保留，详见 B3-UI-EVIDENCE.json。固定离线样例不能证明卡内记忆质量。
5张原始JPEG实拍保存在 b3-screenshots。帮助PNG来自其中900×800原始截图，只转换编码、未重绘或缩放。最终帮助文字/图片节点已可见；读取图片DOM自然尺寸的工具操作超时，不据此宣称像素解码检查通过。

## 原始失败与修复后验证

保留全部失败日志：设置失败文案先被状态刷新清除，修复后发现浮层与底层重复提示，再修复并通过27项RPG前端测试。一次相对路径修改脚本从client目录执行失败，未写入文件，随后正确定位修复。
初次父卡iframe空白由构建默认 /assets 路径造成；按已有Docker约定显式 --base=/rpg-app/earth/ 重新构建后通过。verify脚本的嵌套构建未转发该参数，最终再次显式构建。
帮助初次存在重复标题、标准章节缺失、截图格式/目录不合规；修复后剩登记基线断言失败，补准确当前基线并重跑15项通过。截图检查最终只剩上述原基线3项问题。
首次手机市场抓图仍为桌面viewport，移入忽略目录，设置390后重拍归档；不将前次计作手机成功。最后两次浏览器只读属性查询超时，页面操作状态通过新快照核对，不将超时算通过。收尾时 python 不在PATH导致一次只读汇总命令未运行，改用Node/PowerShell完成。

## 数据保护与回退

修改前34份B2文件已复制并逐hash验证；B2-DELIVERY保持原值，原STATUS另存B2-STATUS.json。当前源码/产物/日志登记B3-DELIVERY.json，不重写历史通过证据。
首选停用插件，保留历史、配置、分支、派发记录。撤回代码仅针对M1增量；旧宿主降级必须另用数据副本验证，不直接复用新登记数据。默认服务开关保持关闭；此候选实例显式开启M1且关闭Provider。仅停止核对归属后的本轮实例，不动旧预览或共享服务。

## 下一门禁

请用户验收B3界面与帮助。B4需要单独真实额度并实际越过窗口，再逐份人工审阅；目前真实派发0，未占用任何历史额度。Commit/Push/PR/共享部署仍未授权。
