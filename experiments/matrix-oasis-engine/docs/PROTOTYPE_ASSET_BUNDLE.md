# Prototype Asset Bundle 0.1.0

状态：R9 私有中间合同；R9.2 实现前的治理约束。

Asset Bundle 把冻结的 R8 Scene Blueprint 资产需求映射到固定环境模板和离线规范化 GLB。它只在 R9/R10 之间使用，不扩展 Authoring Pack、Runtime Pack、Receipt、Scene Pack 或存档格式。

固定内容：

- Scene Blueprint canonical SHA-256、Runtime artifact SHA-256 与 scene 身份；
- `kenney-prototype-room-v1` 环境模板；
- 每个 asset brief 恰好一个 materialization；
- `builtin-template` 或 `meshy-text-to-3d` 来源类型；
- `assets/*.glb` 相对路径、visual/collider roles、规范化 profile、字节/hash/结构/纹理/bounds 元数据；
- 脱敏 generation report。

明确禁止供应商任务 ID、下载 URL、API key、原始 HTTP 响应、用户提示、Scene Pack placement坐标和未发布的中间文件。最终闭合Schema、语义码和公开API在R9.2冻结。
