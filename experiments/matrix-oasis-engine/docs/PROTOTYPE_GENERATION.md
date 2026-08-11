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

## R8.3 OpenAI兼容Provider

`@matrix-oasis/prototype-generator@0.1.0-r8` 的 Provider 适配器使用Node 24原生Web API，固定向配置的精确 `/v1/chat/completions` 发出一次非streaming请求。响应格式为strict Generation Proposal JSON Schema；请求不含tools、函数调用或自动重试，redirect固定拒绝，单次超时120秒，响应上限1 MiB。

Provider包不读取环境变量。endpoint、模型和凭据只由未来R8.4 CLI宿主传入；HTTP仅允许loopback，外部主机必须HTTPS。公开Provider对象不暴露凭据，HTTP正文、底层异常和动态响应字段不进入错误。正常响应必须只有一个choice、文本content与`stop`结束状态。

首轮请求只携带纯文本prompt；修复请求只携带上一候选、静态code/JSON Pointer和原始Schema，不再次发送原始prompt。普通测试使用本机loopback假服务，不调用外部模型。

## R8.4 生成编排与CLI

`generatePrototype(request, provider)` 固定执行：请求候选、严格准备Generation Proposal、调用冻结Compiler、canonical化Receipt、准备冻结Runtime并创建入口会话。至少必须声明一个Action，且入口会话必须处于active状态。内容诊断最多触发两次定向修复；Provider、Compiler、Runtime或哈希故障统一抛出静态`PROTOTYPE_GENERATOR_INTERNAL_ERROR`。

成功值只含五个canonical字符串：Authoring Game Pack、Scene Blueprint、Runtime Pack、Runtime Receipt和generation report。report只记录模型标识、请求次数、可用时的聚合usage、四个业务artifact的SHA-256/UTF-8字节数及初始Runtime检查，不包含prompt、endpoint、凭据、原始响应或异常。

```powershell
npm.cmd run plan:prototype-call -- --prompt-file <C:\tmp文件>
npm.cmd run generate:prototype -- --prompt-file <C:\tmp文件> --output <C:\tmp新目录> --acknowledge-external-upload
npm.cmd run verify:prototype-generation
```

`plan:prototype-call`不发网络请求，只披露将使用的主机、模型、最多三次请求及prompt字节数。`generate:prototype`只有在显式上传确认后才创建Provider；校验失败不写候选文件，成功时五个固定文件在同父临时目录完成独占写入、同步和回读后通过一次目录rename发布。输出必须是尚不存在的`C:\tmp`一级子目录。
