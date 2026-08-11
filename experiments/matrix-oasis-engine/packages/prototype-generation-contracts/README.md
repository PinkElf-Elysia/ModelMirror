# Prototype Generation Contracts

R8 的私有合同包，定义严格 Generation Proposal 与实验性 Scene Blueprint `0.1.0`。

- Proposal 直接嵌入冻结的 Authoring Game Pack Schema。
- Scene Blueprint 只描述资产需求、逻辑分区、摆放意图与 node 绑定。
- 不包含供应商任务、文件路径、哈希、3D 坐标、密钥或原始用户提示。
- 仅提供文本入口；所有报告、准备结果和解析值均深冻结。

Scene Blueprint 是 R8/R9 生成流水线中间产物，不是 Runtime Pack、Scene Pack 或存档格式。

## 稳定入口

- `validateGenerationProposalJson(text)`：只返回冻结、排序稳定的验证报告。
- `prepareGenerationProposalJson(text)`：合法时返回深冻结值及 Proposal、Authoring、Blueprint 三份 canonical JSON；非法时只返回验证报告。

验证顺序固定为严格 JSON 解析、闭合 Schema、冻结 Authoring Validator、跨合同语义。Schema 失败时不会运行后续语义检查；未知属性名和输入值不会进入公开诊断。
