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

物化入口先调用冻结的R10 Environment Bundle验证器，因此collider仍由既有R7/R10 GLB安全门负责；独立Spatial Bundle验证器只重新核对输出文件身份、哈希、长度和转换统计，不复制Scene Pack或GLB合同。Godot集成前还必须再次执行冻结Scene Pack与GLB门禁。

## Spatial Assembly

R11.4新增私有matrix-oasis.prototype-spatial-assembly/0.1.0，它不修改Scene Pack。组合器重新验证R10 assembly report、Scene Pack/Runtime身份和Spatial Environment Bundle，并要求Scene Pack中唯一环境placement绑定同一collider且在每个node可见。

组合结果显式记录共同root平移/旋转、固定`YXZ` Euler顺序、splat局部中心补偿与`[0,0,-180000]`毫度局部旋转、splat/collider相同米制scale及panoramaVisible=false。root的Y平移等于声明的Godot平移加ground offset；Godot wrapper必须在节点进入树后重新应用canonical splat旋转，以覆盖gdgs节点的隐式默认方向修正，不得增加未记录的试摆常量。

## Spatial Preview Overlay

R11.5的空间缓存不是R10 run的新版本。它以原R10 run ID为键，独立保存Spatial Environment Bundle/report、Spatial Assembly/report、绑定这些文件的run report和compressed PLY。每次recover/find/load都重新验证R10 run及其Scene GLB，再验证overlay和collider交叉身份；任一漂移即不再暴露缓存。

启动时只把复验后的Runtime、Receipt、Scene Pack、Scene GLB、Spatial Assembly和compressed PLY复制到一次性Godot工程。工程副本启用固定gdgs Compute设置并先运行editor import；正式模块工程、R10 run、R10 current和panorama均不修改或读取。
