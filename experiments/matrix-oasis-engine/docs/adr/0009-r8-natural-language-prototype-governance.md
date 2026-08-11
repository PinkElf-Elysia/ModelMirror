# ADR-0009：R8自然语言原型生成治理

状态：接受

R8在冻结R1–R7的前提下新增模块独立生成层。采用无SDK的OpenAI兼容HTTP适配器、私有版本化Scene Blueprint和安全CLI；真实模型仅作逐次批准的资格验证。

R8不修改正式Authoring、Runtime或Scene Pack合同，不更新Creator和Godot，不生成资产。这样可以单独拆分、回退，并让R9只消费经过验证的Blueprint，而不依赖模型响应格式。
