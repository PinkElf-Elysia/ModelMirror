# E3 交接边界

当前更新：用户已通过离线验收；授权的2次Gemini补测已完成，实报$0.05499225，临时凭据已清理，用户已通过真实页面验收；模型未主动追问仅记录为后续优化项，不再增加补丁或真实调用。见 [E3-REAL-RESULT.md](E3-REAL-RESULT.md)。下文保留离线阶段记录，其中未实测/无额度描述仅指当时状态。
工作树 C:/tmp/modelmirror-rpg-free-action-e3，分支 codex/rpg-free-action-e3。
基线 ea4f1c9249430dff1d3cfaade26a3d8128cee48e（E2 PR477 合并）。本轮没有提交。
实施前核对：主线R5离线128测试/页面获验收，真实首请求正确产生prepare_action但结束帧解析失败，未执行，后经主线离线修复。主线当时仍在整理证据；此为会话报告，不是本分支重跑。R5-HANDOFF旧文档落后于该实测进度。

## 所有权
E3负责玩家意图澄清、候选解释、修订、接受与只读交接。主线负责工具登记、类型化动作、规则、执行授权、状态写入和叙事恢复。
未修改 agent-core/**、local-state/**、SessionRecords、E2、父项目、角色工作树或正式入口。
代码新增于 feature-exploration/free-action-e3/**；文档新增于本目录E3文件。

## 运行接口
- intent.mjs：纯变更应用、缺失项推导、来源和差异、条件预估、接受与导出。仅单项赠送实例，非通用动作执行器。
- context.mjs：复用R4 cultivationSource与assemblePrepared；只带上一有效意图/问题/世界条件和本次回复。不继承E2旧完整输出合同。
- provider.mjs：固定MockProvider和JSON协议解析。没有真实供应商实例或网关注册。
- store.mjs：独立SessionRecords，先保存冻结请求再生成；结果未知保留pending；新请求不能绕过。明确失败保留原回复及上一有效内容，无自动重试。
- server.mjs：loopback GET /api/view、GET /api/export；POST /api/draft仅reply/edit/accept/revoke/cancel/new/refresh/reconcile。无执行路由。
- public/*：连续澄清、明确值编辑、版本差异、查询依据、原始回复与导出。
- *.test.mjs、browser-check.mjs、replay-check.mjs：定向离线验证。

E3格式：modelmirror.rpg.e3-proposal/1、e3-draft/1、e3-handoff/1。
导出包含来源、有效槽位、来源引文、候选/明确标记、未决条件、当前版本接受记录。
executionStatus固定not_executed，requiresExecutionValidation固定true。
这是独立内部格式；R5现有inspectHandoff只接受E2格式，不能声称已兼容E3。
不为兼容伪装成e2-handoff，不把赠送转换use_medicine，不从conditionalAfter计算正式状态补丁。

## 使用与停止
从仓库根：
```text
node experiments/ai-rpg-engine/feature-exploration/free-action-e3/server.mjs
```
默认端口18881，占用启动失败，不停止其他进程。来源路径 .rpg03-work/e3-source/session-1.json 必须匹配既定指纹。
源可通过可信启动参数 --source-url / --source-session 配置为E2受限R4 loopback GET；本轮浏览器使用归档副本，未接R5新绑定。
--recover-dead-owner 只用于确认本任务旧进程已终止后按原SessionRecords恢复所有权，不是重发未知模型调用。
停用只关闭已核对身份的E3预览进程，保留草稿和来源；没有世界数据需要回滚。

## 尚未证明及后续
1. 真实连续自然语言理解尚未测试；有限数字引文和实名引用检查不等于完整语义校验。无法确定时仍需澄清或明确编辑。
2. 一个赠送目标、一个物品/数量、一个玩家条件字段；不支持多行动队列、多个物资分配或通用目标编辑。数量中文只验证一到十，复杂表达保守拒绝；界面正整数可直接输入。
3. 世界是否在场/同意固定保持unknown；玩家陈述只保存其原话。不是NPC认知隔离、授权裁定或角色记忆。
4. “同门/初识”等E2残余描述不复制到E3生成步骤；模型自由提问仍需后续真实验收，未增加全文事实审查。
5. 非空profiles/mods沿用E2绑定拒绝；未宣称任意插件组合兼容。
6. 存储未知可核对本地提交标记；供应商未知保持pending，本轮没有供应商查询或自动重发。未来接真实提供者需先设计明确结果核对。
7. 每份独立记录最多30次草稿交互，满额提示DRAFT_LIMIT；不删除旧证据或做迁移。历史界面操作也保留记录。
8. E2原记录不自动导入。后续真正对接需主线重读目标实例、映射受支持动作、重验版本/规则/权限和人物条件，单独确认执行。

下一步先由用户离线验收，再单独申请最小Gemini连续澄清补测。已有E2预算已结束，本轮没有可用真实额度；正式执行、公共接口变更、PR和部署须另行授权。

## 当前对接结论（2026-11-14 收尾）
主会话最新报告已进入R6独立原型：公共任务与角色观察桥接。本分支只读核对其会话进度，未重跑或修改其实现。R5已在origin/main 38266775合并；E3冻结基线与真实样本不变。
- E3输出是有来源和版本的意图建议，不是可执行命令、工具回执或世界事件。
- 主线未来若读取e3-handoff/1，须显式映射受支持动作，重读当时目标世界和状态版本，校验对象/数量/规则/授权与人物条件；本轮未实现此适配。
- accepted仅表示玩家接受当前意图版本；任何修订使旧接受失效，不能复用为执行许可。
- 玩家声称“对方答应”只留玩家引文；不能转换为角色观察、私有记忆、在场或同意事实。角色仅应获得主线允许投影的已确认事件。
- 不把赠送意图映射为服用丹药，不将conditionalAfter或草稿差异转换成账本补丁。
- E3独立SessionRecords只维护草稿；世界状态仍由主线唯一写入。后续执行结果回传是待立项边界。
- real/为本任务两次限额补测接入及只读证据页，不是生产路由，也不提供持续在线提案服务。旧额度已结束。
- 模型未主动追问列为后续优化项；本轮按用户要求不再补丁或调用。PR仅提交E3源码/测试/脱敏文档，原始回复与运行数据留本地忽略目录。
