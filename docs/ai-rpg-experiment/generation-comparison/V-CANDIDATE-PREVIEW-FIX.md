# V 候选预览变量协议修复

日期：2026-11-07。用户已接受 A 组，授权先修复再按原方案执行 B 组，剩余总额度9次；本修复不消耗生成额度。

## 原因与范围

正式剧情展示已经通过变量协议校验后生成显示副本；候选预览漏用了该投影步骤。现在使用批次冻结的变量定义与初始状态做只读校验，通过后仅从显示副本隐藏协议块。完整原文、请求 hash、回执及正式变量状态均不修改；不触发 CG、摘要或记忆更新。非法、缺失、不完整、重复或类型不符的协议继续留在原文及可诊断显示中，不以正则无条件抹除。

主要显示修复涉及 turn-playback.mjs、comparison-host.test.mjs 和本文档。随后为遵守冻结 B 方案，补齐零额度初始化及六卡通用持续要求；新增文件与完整差异见外部 source-manifest.json，不把准备阶段缺口隐藏在显示修复中。修复在独立 codex/rpg-comparison-v-preview-fix 工作区，继承原 V 候选的120份文件；18692 及原入口不替换。回退只需使用原显示实现，保留已生成原文及账本。

## 验证

- 新回归测试在修复前正确失败，捕获候选正文暴露 variable_panel。
- `node --test authoring/comparison-host.test.mjs authoring/turn-playback.test.mjs authoring/variable-panel.test.mjs`：39/39通过。
- 同一批次的两份 A 组完整真实原文，经新 TurnPlayback.candidate 回放；正文不暴露协议，完整原文可展开核对。初始会话、批次、正式变量和 CG 不推进，候选渲染不新增模型任务。
- 两份原始文本 hash 分别为 c20b82f672355fe9859bee6a1375dc7e1fbfdddcad017fa8216ac6c90c21cf8d、78c65dff03e22e5150914af6342694aea271e8734e9899e82ccc83bda37a24fd。
- 桌面、390px 通过浏览器实际回放，截图归档。独立只读入口 http://127.0.0.1:18694/ ，无供应商客户端或写入路由。
- 证据：C:/tmp/modelmirror-rpg-v-live-20261107/preview-fix/ 下的 regression.txt、full-raw-replay.json、candidate-*-frame.html、三张截图及 baseline.json。

A 的 txt 导出使用 Windows CRLF；本次回放真源是原批次持久化的原文，按原 rawHash 校验。未重新生成、改写或覆盖任何既有证据。

此修复验证不替代 B 组真实生成、独立参数和后台顺序验收；B 组结果另行记录。

## B 组准备阶段的零额度修复

独立六卡宿主初始化时发现参数传输层要求 limit>=1，而既有共享预算组件已经支持 limit=0，导致无法只向 God 分配已批准的8次、其余卡保持0。将该入参下限对齐为0；负数、非整数和超过100仍拒绝。零额度初始化不发起请求，任何额度预留都会明确拒绝。新增回归先复现，再跑完整 card-generation.test.mjs，18/18通过（包括六卡、三用途独立、重启恢复、分支与容量）。证据 zero-budget-red.txt、zero-budget-regression.txt。

准备时两次启动在依赖加载/入参检查阶段退出，未生成、未认证、未预留供应商额度；保留日志。复用原已安装依赖后启动独立18696。该轻量宿主不提供主站/rpg/plugins页面，四个基础插件使用既有安装API准备，逐会话授权与设置通过聊天UI完成；不把独立宿主的市场页面记为浏览器通过。

## B 组准备发现的持续要求缺口

原生六卡宿主缺少创作通用宿主已经具备的“持续要求”，无法履行冻结方案的逐轮明确保存。现补齐同一通用能力，不改作者 system、开局资料或 M3 整理指令：

- 新增 card-requirements.mjs，宿主保存严格类型校验的独立版本及操作回执。最多8000个Unicode字符、1000个版本；操作ID、会话revision、要求revision均校验。
- 当前 user 消息末尾只附加一次；原玩家输入与历史原文保持。历史按该回合生效的版本重建，分支继承分支点快照与前缀，不读取父路线将来的编辑，不继承可执行操作。
- 六卡共用配置/原操作查询接口与聊天浮层。取消、Esc未保存放弃、390px、刷新保持通过浏览器核验；失败/结果未确认阻断发送，核对不自动POST。
- 参数与对比使用包含持续要求版本的上下文revision；批次冻结后不能编辑它。
- `node --test studio/card-generation.test.mjs studio/card-comparison.test.mjs studio/card-context.test.mjs`：23/23通过；覆盖六卡请求、保存冲突、落盘失败、重启、旧回合重建及分支隔离。
- card-replica `npm.cmd run build` 通过 TypeScript/Vite；存在原有动态/静态导入重复警告。
- 新源与产物只用于18698新建会话。先前零调用准备会话与18692均保留，不静默迁移。

真实 B2 后来出现旧对比轮询与采用的锁竞争，尚未修复；23项串行离线回归不能替代这条浏览器并发路径。详情见 V-B-LIVE-RESULT.md。
