# Meta Planner Headless Authoring

最后更新日期：2026-08-30
状态：V3 Round 2 实现契约

## 目标

Headless Authoring 是 Meta Planner、管理端编辑器和后续 Optimizer 共用的候选编排协议。
它只修改 pending `AuthoringProposal`，不写 Xpert 草稿、不发布版本，也不启动 Workflow。

固定链路：

```text
Proposal state -> editor diff / typed patch -> side-effect-free preview
-> checksum-bound apply -> existing proposal validation -> human approval
```

## Patch 边界

`GraphPatchEnvelopeV1` 最多包含 64 个有序操作，并绑定 Proposal revision、当前 Graph
authoring checksum 和当前 compiled candidate checksum。

四个 Headless API 的 JSON 正文最多 2 MiB、嵌套最多 32 层；超限请求在进入
Pydantic、Adapter 或 Proposal 服务前失败关闭。

Patch 只接受 Planner ref、命名端口和 Adapter config。原生节点 ID、Handle、资源版本、
NodeContract Schema、执行策略与 checksum 均由服务端推导，客户端或模型不能注入。

支持 Xpert 元数据、Workflow Agent、控制/data 边、输出变量、四类资源、中间件、
Prompt Profile、最终输出和布局。`input/output` 由编译器管理；布局不会改变 Graph
checksum，但会改变 candidate checksum。

## 预览与应用

Preview 从实际 Proposal 反编译 GraphIntent，重新解析 Graph IR、编译候选，并执行
Workflow、资源和发布预检。Preview 不持久化、不调用模型，也不创建运行。

Apply 必须携带 Preview checksum。服务端重新读取 Proposal、能力快照和目标 Xpert，
再完整重算 Preview。以下任一变化均拒绝应用：

- Proposal revision 或候选 checksum 漂移。
- 更新目标的 draft revision 漂移。
- 相关 NodeContract、Adapter、资源版本或动态 Schema 失效。
- Patch 端口、类型、基数、控制图、授权或最终输出不合法。

无关 Capability Snapshot 变化只产生 warning。Apply 成功只增加一次 Proposal revision，
随后复用现有 Authoring validation。安全 receipt 最多保留 20 条，只记录操作类型、前后
checksum、诊断计数和时间。历史 receipt 会按安全字段重新规范化，未知字段和无法验证的
记录不会继续进入 typed Apply。Proposal 写盘失败时，内存 revision 与 payload 同步回滚。

## 编辑器与兼容

管理端画布先调用 `editor-diff` 将差异转换成 Graph Patch，再执行 Preview 和用户确认。
原始 `WorkflowDefinition` 从不直接进入 Headless 持久化。Proposal 授权范围之外的节点、
未授权中间件和 compiler-managed 节点修改均 fail-closed；无法表达的编辑返回逐项诊断。
Meta Planner Proposal 创建时的 `authorized_scope` 是不可扩大的授权上界；旧整包 PATCH
可以编辑候选内容，但不能修改该上界，运行时仍只使用它与当前能力快照的交集。

- Graph IR V3 使用 Headless Authoring。
- 可唯一恢复变量来源的 V2 候选可 Preview，并在首次 Apply 时升级为 V3。
- `lossy_conversion` 的 V2 候选保持旧兼容读取、校验和审批路径，禁止 Headless Apply。
- 旧整包 Proposal PATCH 继续可用，但会将 Graph IR 标记为 `stale`。

模型首次生成仍输出 GraphIntent V3。若输出可解析但门禁失败，唯一修复调用必须返回
Graph Patch；完全无法解析时才允许一次完整 GraphIntent V3 修复。总模型调用上限仍为 3。

## API

```text
GET  /api/meta-agent/authoring/proposals/{proposal_id}
POST /api/meta-agent/authoring/proposals/{proposal_id}/editor-diff
POST /api/meta-agent/authoring/proposals/{proposal_id}/patch/preview
POST /api/meta-agent/authoring/proposals/{proposal_id}/patch/apply
```

Capability Snapshot V5 暴露 authoring protocol、操作 JSON Schema、Adapter authoring
checksum 和限制，但节点范围仍严格保持原有七类。

## 回退

本轮没有数据迁移。回退时可移除 Headless API 和管理端入口，现有 Proposal、V2/V3 IR、
Xpert 草稿与发布版本仍可读取。不得通过回退清理 Runtime Store 或覆盖用户 Proposal。
