# Prototype Asset Bundle 0.1.0

状态：R9.2 已实现并冻结的模块私有中间合同。

Prototype Asset Bundle 把冻结的 R8 Scene Blueprint 资产需求映射到固定环境模板和离线规范化 GLB。它只在 R9/R10 之间使用，不扩展 Authoring Pack、Runtime Pack、Receipt、Scene Pack 或存档格式。

## 固定结构

- 根身份：`matrix-oasis.prototype-asset-bundle` / `0.1.0` / `matrix-oasis.canonical-json/1`。
- `scene`：与 Runtime identity 一致的 `id`、`contentVersion` 和标题。
- `blueprint`：Scene Blueprint format/version、canonical SHA-256，以及不含 prompt 的资产 brief 摘要。
- `runtimeIdentity`：Runtime format/version、Pack identity、Authoring canonical SHA-256 和 Runtime artifact SHA-256。
- `environmentTemplate`：精确固定为 `kenney-prototype-room-v1`。
- `materializations[]`：按 brief 声明顺序一一对应；environment 使用 `builtin-template`，prop 与 character-placeholder 使用 `meshy-text-to-3d`。
- `assets[]`：仅 `assets/*.glb` 相对路径、visual/collider roles、规范化 profile、byteLength、SHA-256 和结构/纹理/bounds 元数据。

Bundle 不包含供应商任务 ID、下载 URL、API key、原始 HTTP 响应、用户 prompt、Scene Pack 坐标或未发布中间文件。

## 限制

- manifest 最大 256 KiB，JSON 深度最大 256。
- 最多 16 个 brief、16 个 materialization 和全局 16 个 GLB。
- 单 GLB 最大 32 MiB，总资产最大 128 MiB。
- visual 最多 100,000 triangles；承担 collider role 的文件最多 10,000 triangles。
- 纹理每轴最大 2048；毫米 bounds 每轴限制在 ±1,000,000。
- 恰好一个 environment brief，必须同时覆盖 visual/collider；非 environment brief 必须绑定 entity。
- roles、materializations 和文件数组均保持确定性声明顺序。

## 验证边界

`validatePrototypeAssetBundleJson(text)` 按 `parse → schema → semantic → integrity` fail-closed：

- 拒绝非字符串、超限、深层、无效 JSON、注释、尾逗号、重复键和孤立代理项；
- 使用 JSON Schema 2020-12 严格关闭未知字段，不做强转、默认值注入或属性删除；
- 所有数值字段均有远低于 JavaScript safe integer 的闭合 Schema 上限；随后校验身份、brief/materialization 一一对应、来源类型、roles、文件唯一性、预算、bounds 与 collider triangle 限制；
- 最后要求输入字节精确符合 `matrix-oasis.canonical-json/1`。

报告固定为 `{reportVersion, valid, diagnostics}`，diagnostic 只含静态 code/message 和 RFC 6901 path，不回显未知键名、输入值、ID 值、路径或底层异常。内部故障抛出 `PrototypeAssetContractOperationalError`，固定 code 为 `PROTOTYPE_ASSET_CONTRACT_INTERNAL_ERROR`。

本合同只验证 manifest 自洽性。R9.4 Pipeline 才核对实际文件字节、GLB 结构、纹理/triangle 统计、Blueprint 外部原文 hash 和 Runtime artifact；Bundle receipt 不是签名，也不证明供应商或作者真实性。

## 公共 API

私有 workspace `@matrix-oasis/prototype-asset-contracts@0.1.0-r9` 只公开冻结常量、Schema/limits、验证报告类型、操作错误类和 `validatePrototypeAssetBundleJson`。它复用既有 `@matrix-oasis/runtime-pack-contracts@0.1.0-r3`、Ajv `8.20.0` 与 jsonc-parser `3.3.1`，不新增 registry 依赖或网络能力。
