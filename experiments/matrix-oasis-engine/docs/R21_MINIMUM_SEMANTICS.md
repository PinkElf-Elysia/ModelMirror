# R21最小语义

本文只冻结R21可实现的最小语义，不定义最终Schema字段或扩大到R22。

## 权威边界

- Runtime是游戏状态转换的唯一权威。
- R19 World Event Ledger是NPC裁决历史、因果关系和来源证明的唯一权威。
- R21的persona、memory和relationship产物均为派生读模型；删除或损坏后必须能从可信seed、policy和同一Ledger重新生成，不得修改Runtime、Ledger或R20调度。
- R19 `Derived Projection Manifest`只用于绑定reducer、Ledger head、entity scope与R21内部生成的artifact身份；任意调用者提供的artifact字节不能单独证明投影正确。

## Persona

- Persona是可信、版本化、闭合字段的静态seed，并精确绑定Runtime中的actor实体。
- Persona不从Ledger、文案或模型输出推断；R21不学习、修正或演化persona。
- Persona只作为后续投影或R22认知的受限输入，不授予Action权限，也不改变R19 Authority Policy。

## Memory

- Memory只记录actor自身已被R19接受的Action，形成结构化episode。
- Episode只能携带Ledger中已有的确定性身份、revision、node/action、transition和snapshot hash等结构化事实；不复制自由文本，不生成摘要，不使用embedding。
- 拒绝、损坏、陈旧或属于其他actor的Intent不得成为memory episode。

## Relationship

- Relationship只能由可信、版本化、闭合policy中的精确Action映射产生。
- 映射输出为“source actor → target actor”的定向、有界、安全整数delta；不得从Action文案、entityIds、persona或模型文本猜测关系。
- 默认只有已接受Action贡献delta；拒绝事件贡献为零。
- 聚合顺序固定为Ledger revision顺序；整数越界必须fail closed，不得截断或环绕。

## 明确不支持

- 只支持单一timeline。跨timeline、跨reset合并、迁移和比较均不支持、不宣称。
- “删除”只指删除该timeline的全部派生artifact/index后，从原始Ledger字节级重建。R21不实现选择性forget、单条删除或correction。
- R18路线中的“correction/isolation”在R21被收窄为：不接受跨timeline输入，以及整库删除后确定性重建；更强语义必须进入新版本和后续单独审批。
- 不引入外部索引、数据库、第三方生产依赖、模型调用、embedding、自由文本解析、Creator接线、Godot行为变化、动态任务或世界事件生成。
