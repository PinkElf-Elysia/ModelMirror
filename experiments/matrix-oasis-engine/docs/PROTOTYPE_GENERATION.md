# R8原型生成接口

R8接受纯文本，生成严格Generation Proposal。Proposal由冻结Authoring Game Pack和私有Scene Blueprint组成；成功后继续生成Runtime Pack、Receipt和脱敏generation report。

Scene Blueprint只表达环境提示、资产需求、逻辑区域、摆放意图和node可见关系。它不包含文件路径、哈希、供应商任务、3D坐标、凭据或原始用户提示，也不承诺成为长期公共格式。

普通验证只使用loopback假Provider。真实模型资格必须使用模型调用审批模板，并且不能自动延续到下一轮或资产供应商。

## R8.2 合同边界

`@matrix-oasis/prototype-generation-contracts@0.1.0-r8` 是私有、UNLICENSED workspace。Generation Proposal 固定包含冻结 Authoring Game Pack 与 Scene Blueprint `0.1.0`；Authoring Schema 直接从冻结包组合，不维护第二份字段定义。

Scene Blueprint 固定包含：

- scene 身份、标题、环境提示和视觉风格提示；
- 最多 16 个逻辑 zone；
- 最多 16 个 environment、prop 或 character-placeholder brief；
- 最多 128 个逻辑 placement；
- 每个 Authoring node 恰好一个 zone 与可见 placement 绑定。

恰好一个 environment brief，必须同时声明 visual 与 collider，并且恰好有一个 environment placement。其他 brief 必须绑定已有 Authoring entity。Blueprint declaration ID、entity、zone、asset、placement 和 node 引用全部机器验证。

公开文本入口先拒绝非严格 JSON、重复键和超过 256 层的文档，再执行闭合 Schema、冻结 Authoring Validator 和跨合同语义。孤立代理项、未知字段、超限数组及不匹配引用均被拒绝；报告只含静态 code 与 JSON Pointer，不回显输入值。
