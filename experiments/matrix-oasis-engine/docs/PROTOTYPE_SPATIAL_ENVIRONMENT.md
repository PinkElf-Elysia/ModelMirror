# Prototype Spatial Environment 0.1.0

R11私有中间产物绑定Marble SPZ来源、deterministic compressed PLY、collider GLB和米制校准。它不扩展Scene Pack、Runtime Pack或存档格式。

稳定约束：

- `format = matrix-oasis.prototype-spatial-environment-bundle`
- `formatVersion = 0.1.0`
- `canonicalization = matrix-oasis.canonical-json/1`
- full-resolution splat与collider各有相对路径、byteLength和SHA-256；
- report记录splat count、bounds、metric scale、ground offset、坐标变换与gdgs中心补偿；
- 不保存prompt、operation/world ID、下载URL、密钥、原始响应或本机路径。

Bundle只用于R11/R10宿主的私有组合流程；跨版本兼容、签名、信任与正式发布均未定义。
