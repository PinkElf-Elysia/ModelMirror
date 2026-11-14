# E4 对接边界与离线交接
2026-11-14，基线 f2d4ccbc21e3da42ef20e94c2d0c27659b197e8b；独立工作树 C:/tmp/modelmirror-rpg-context-actions-e4，分支 codex/rpg-context-actions-e4。离线与最终真实原型均已获用户验收，正在按授权整理提交 PR。当前结论与历史阶段记录分开，优先阅读 [E4-CLOSEOUT.md](E4-CLOSEOUT.md)。

## 所有权
探索线：情境入口、玩家意图、候选/明确选择来源、连续修订、预检展示与只读交接。
主线：规则登记、世界与分支绑定、真实任务派发、执行确认、写入/事件、恢复、角色观察与权限。
角色线：模板、人物装配、个人/群聊与玩家档案。没有修改两条工作树或公共核心。

## 实际复用
- source.mjs使用R5 actionAdapters、registry中的prepare_action声明；不自建动作编号或重复计算规则。页面文案为E4适配元数据。
- 读取R5完整记录用CoreStore验证，再按任务投影登记资源和人物ID/名字。任意人物出现于登记册不代表在场或同意；不是角色观察授权投影。
- 仅初始化独立示例时使用ActionStore.create，之后只读快照；不调用propose、execute、override或剧情写入。
- draft.mjs对规则支持动作调用prepare与verifyProposal纯函数；赠送的引文、槽位、未知条件复用E3 applyChanges。未修改E3格式或将赠送映射为服药。
- provider.mjs复用公共assemblePrepared及E3 JSON变更校验，明确只组装提案所需能力/状态/当前意图，不是世界叙事请求，不宣称完整卡片/世界书运行兼容。
- store.mjs复用SessionRecords保存独立草稿、CAS、请求与回执。没有世界账本或第二份预算；常规交互页提供者只有固定离线模拟器；real/目录新增独立受限实测适配，不注册正式服务。
- server.mjs仅GET view/inspect/export与POST draft。浏览器无来源路径、供应商或执行配置入口。18886必须显式--offline-preview，端口占用即失败。

## 交接格式
modelmirror.rpg.e4-handoff/1包含：
- executionStatus=not_executed、requiresExecutionValidation=true。
- 有来源版本、bindingHash/stateHash/recordHash和候选引文的intent。
- capability=rule-supported或intent-only。
- rule-supported可带原生rpg-action-proposal/5纯预检结果；没有在R5记录中登记该提案，更没有执行。
- accepted只代表这版意图接受；数量/对象/行动修订使接受失效。来源变化会阻止导出，仍允许草稿编辑；显式refresh才更新来源并重算。
- 礼物condition是玩家陈述，不能变为已确认世界事件、人物观察或私有记忆。条件余量不是可写账本补丁。

本轮不修改R5 inspectHandoff。E4文件不能直接伪装成E2/E3文件或执行授权；主线后续接入须独立设计接收器，重新读取目标世界/分支、核对当前规则与能力、验证意图语义和执行权限，并单独确认执行。不能直接信任本地导出的预计值或hash作为安全授权。
E3旧赠送流程继续保持；E4新记录不是自动迁移旧E3草稿。没有改R6任务/角色观察接口，R6未合并不作为运行依赖。

## 启动与回退
仓库根：
```text
node experiments/ai-rpg-engine/feature-exploration/context-actions-e4/server.mjs --offline-preview
```
默认独立目录.rpg03-work/e4-preview，端口18886。--data与--port只由可信本地启动指定，不来自浏览器。
正常停止本任务服务会关闭草稿所有权；仅在核对原进程退出后可加--recover-dead-owner恢复本任务目录。未知模拟请求保留pending，不自动重发；reconcile只核对本地写入，不调用供应商。
回退只需停止经身份核对的E4服务，保留草稿和快照，不清理其他工作树或共享数据。

## 历史阶段记录（后续状态以收尾文档为准）
离线人工验收通过。用户后续授权的E4实测最多2次/$0.40，实际1次后停止；结果和清理见E4-REAL-RESULT.md。没有自动重试或追加补测。正式执行桥接、公共接口修改、部署、Commit/Push/PR均未授权。模型主动追问仍为后续议题，不把本轮固定模拟问题当作其改进证据。
主线已在R6推进公共任务与角色观察；本轮只补玩家侧能力感知提案，未抢做人物主动行为或模型调度。


## 单围栏兼容修复（后续授权）
用户授权仅修复 E4 parse 对单一完整 JSON 围栏的兼容，已完成保存回复离线回放及18项相关检查；原实测失败证据未改，新增真实调用0次。范围、证据和单请求补测建议见 E4-FENCE-FIX.md。第二次赠送修订仍待真实补测授权，不接公共核心或执行。


## 单次赠送补测（最新）
用户授权后已新增1次Gemini调用，费用$0.002595，无重试。围栏成功解析；模型gift/zhou/medicine/数量1正确，但condition.value改写玩家原文，触发既有INTENT_CONDITION_EVIDENCE，整批修订未采用。旧接受失效，旧服药草稿保留，世界状态完全不变。原始材料和临时凭据清理记录齐备，18903独立只读页待验收。见E4-GIFT-RETEST.md。不继续补丁或调用，不改公共核心或正式执行。


## 条件原文合同补充（最新离线进度）
用户授权后仅在E4 CONTRACT中明确condition.value及quote逐字摘录当前输入，不改写指代。校验保持原样，旧错误回复仍拒绝；21项测试和9项离线证据核对通过，源状态及历史材料未变，新增真实调用0。下一次补测建议1次Gemini/$0.20，待用户确认，见E4-CONDITION-CONTRACT.md。旧实测脚本的冻结指纹不应被直接放宽；新合同需独立运行声明及证据目录。


## 新合同单次真实补测（最新）
已完成1次Gemini/$0.0037035，无重试。模型保留“他已经答应了。”原文含句号，赠送数量1/对象周禾正确，业务修订通过，旧确认与服药预检清除，源状态不变。原验收脚本两项期望遗漏句号而误报，保留原结果并通过独立合同核对；未改业务校验。临时凭据清理完成，18913只读页待用户验收。见E4-CONDITION-REAL-RESULT.md；不追加调用、不接公共核心/执行/部署。

## 最终交接状态
用户已接受本轮原型并授权提交 PR；不再增加补丁或真实调用。最终条件含原文句号，业务校验通过，两个冻结脚本期望值误报与独立核对同时保留。
2026-11-14 只读核对主会话：R7 公共任务执行与受控调度已完成离线原型，尚不据此声明 E4 已接入。E4 没有修改 tasks、actions、actor 接口或两条并行工作树。未来对接由主线接收器重新验证 intent、版本、规则和执行权限；E4 的 accepted、预检与条件估算均不是执行授权。详见 E4-CLOSEOUT.md。
