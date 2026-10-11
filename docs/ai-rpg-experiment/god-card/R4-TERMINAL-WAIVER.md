# R4 本次终止豁免与额度提示审计

2026-10-11。用户明确授权本次供应商截断不消耗本卡账本，并由用户手动重新派发。

slot-10：会话fef7a176-90d8-47bf-978f-ab5f58d5b995，原SSE重复报告两个同值content_filter终止字段及一个DONE。临时校验曾因要求恰好一个finish字段而失败，修正为所有finish均为content_filter且一个DONE后复核通过。任务已收束failed/PROVIDER_CONTENT_FILTERED；未派发M3，未完成剧情未入历史。并非length终止，不把供应商实际尝试或费用抹除。

绑定单次授权god-r4-terminal-exemption-20261011：request b46a1847cc3ceec5ca23b0b149879a8f106e3a17e52f2ae8098bd3718900a942，result.json SHA256 780710919f057ee061a7c569b69348878cd205ab2c138ab0625559b64d3b1ebe。原文件hash复核未变，不是通用自动退款。

核对归属后仅重启本轮18511/18512预览，PID57692。原会话与运行hash0422509148fca01c8c44ec17bf0157e33192df897b7b6aa545916d6e975495f7保持兼容；system、后置词和参数均未变。GET状态limit9/used7/reserved0/remaining2/attempts10/exempted3；M3 enabled=true、busy=false、maxCalls2。浏览器刷新后剩2次、发送可用。Agent派发0次。

预算豁免测试2项通过，预览脚本语法检查通过。原历史delivery不改，另存本批hash。无Commit/Push/PR或共享服务变更。

## 后续用户报告的“额度已用完”提示

只读定位确认：memory-budget.mjs 的remaining=limit-used-reserved，代表尚未预留的可用额度。chat.tsx loadBudget仅保存remaining，丢弃used/limit/reserved；渲染在remaining===0时直接显示“本卡额度已用完”，未区分预留。插件任务状态刷新会触发loadBudget，因此请求进行中就能出现误提示。

例如本卡7/9，剩2；发送时为剧情+记忆预留2，remaining立即0，used尚未9。剧情已派发时used8/reserved1/remaining0，也不等于全部额度已经消耗。相邻memoryBudgetBlocked也会把这时的0用于“当前额度不足”文字，混淆当前在途任务和下一次发送。

影响：误导提示，不是额外扣费，也不会凭此条件取消已开始的任务。并非有任何剩余额度时都必现，准确条件是剩余额度全部被本次任务预留或消费。

建议修复：保存完整预算快照，区分已计数、预留、未预留可用；只有used>=limit才称耗尽，reserved>0解释在途预留，执行中不显示针对下一次任务的不足警告。覆盖预留2、派发1保留1、失败释放未派发部分、真正耗尽和未知状态。当前用户要求检查原因，此UI修复尚未实施，未以真实调用做探针。
