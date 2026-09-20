# R22有界认知威胁模型

- **提示注入**：玩家文本、Runtime文案、Action文案、memory及历史对白全部是不可信数据。安全性来自无危险sink、动态闭合Schema、本地重新验证和R19裁决，不依赖模型识别攻击。
- **权限扩张**：AI候选必须是R19 grant、R20 rule、当前可用Action及当前可见绑定actor的交集。外部Policy只能缩窄，模型只看到opaque choice ID。
- **陈旧响应**：响应后必须复验timeline、revision、head、snapshot、R21 Bundle和候选集；任一漂移即丢弃，不rebase、不重试。
- **重复收费**：每Turn最多一次fetch。`reserved`先持久化；`dispatching`后崩溃视为费用不确定并扣完整预留，恢复不得再次调用。
- **审批换身**：approval hash绑定完整payload、Schema、endpoint、model、价格和保留披露；任何字节变化使批准失效。
- **隐私泄漏**：API key只在已批准且即将dispatch时读取。原始玩家/模型文本、完整payload、response ID、拒绝正文和异常正文不得写Receipt、Ledger、R21、日志或诊断。
- **结构化输出误信**：Structured Outputs只约束形状；refusal、incomplete、工具项、错误model、非法usage、超限、未知choice和语义越权均进入固定本地fallback。
- **富文本执行**：Godot只使用原生Control渲染纯文本；禁用BBCode、链接、资源加载、表达式和脚本。
- **权威倒置**：模型不能创建Action、任务或事件，不能写Runtime、Ledger、Persona、Memory或Relationship。只有R19接受的Intent可以改变Runtime并进入后续R21重建。
- **预算绕过**：预算使用safe integer microusd并在host级跨reset累计；零预算、溢出、价格/模型/端点锁漂移均在网络前fail closed。
- **并发分叉**：每timeline只有一个认知lease和一个in-flight调用；取得lease前暂停R20新调度并等待当前command结束。
- **虚假重放**：重放只证明审批、预算、映射、裁决与Ledger结果，不重调Provider，不声称模型文本可重现。
- **范围漂移**：R23任务/事件、Creator接入、动画、语音、多Agent模型循环及外部记忆服务保持冻结。
