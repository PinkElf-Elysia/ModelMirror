# ADR 0023：R22有界认知治理

## 决策

R22采用模块内有界认知适配器、Godot原生`Control`表现层与R19权威裁决，不引入通用Agent框架、对话脚本运行时或外部记忆服务。模型只可返回纯文本对白和当前Turn披露的一个不透明Action选择；任何Action必须重新映射为R19 Intent，并经R20到达证明和R19单写裁决后才能改变Runtime。

每个Turn只允许一次、内容绑定、逐次人工批准的OpenAI Responses请求。普通验证、假Provider资格和拒绝审批均禁止网络及凭据读取。`store:false`不等于ZDR；批准界面必须披露实际外发JSON、价格上限、可能的prompt caching及官方默认abuse monitoring最长30天保留边界。

R22不扩展R19 Ledger去保存对白，也不允许模型写Persona、Memory、Relationship、任务或世界事件。原始玩家文本、上下文、完整payload和模型对白只驻内存；仓外持久证据仅保存身份、哈希、计数、预算和裁决结果。

## 回退

停用R22 profile并逆序revert R22提交即可回到R20固定策略与R21派生状态。R16默认预览、R19 Ledger、R20时间线及R21 Bundle保持独立可用。Git回退不删除仓外脱敏证据，也不能撤销已发生的供应商费用。
