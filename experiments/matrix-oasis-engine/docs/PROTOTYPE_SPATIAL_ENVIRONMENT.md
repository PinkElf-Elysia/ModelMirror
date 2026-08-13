# Prototype Spatial Environment 0.1.0

R11私有中间产物绑定Marble SPZ来源、deterministic compressed PLY、collider GLB和米制校准。它不扩展Scene Pack、Runtime Pack或存档格式。

稳定约束：

- `format = matrix-oasis.prototype-spatial-environment-bundle`
- `formatVersion = 0.1.0`
- `canonicalization = matrix-oasis.canonical-json/1`
- 完整源SPZ身份与全量转换统计必须保留；Godot使用的deterministic runtime LOD另行记录算法、目标/实际点数、相对路径、byteLength和SHA-256；collider独立记录相对路径、byteLength和SHA-256；
- report记录splat count、bounds、metric scale、ground offset、坐标变换与gdgs中心补偿；
- 不保存prompt、operation/world ID、下载URL、密钥、原始响应或本机路径。

Bundle只用于R11/R10宿主的私有组合流程；跨版本兼容、签名、信任与正式发布均未定义。

运行时LOD不是新的正式资产格式，也不替换源身份。首个批准profile使用`@playcanvas/splat-transform@3.3.0`的确定性CPU MPMM合并，不允许随机抽样；目标固定为640,000点。普通验证用小型fixture锁20次字节一致，真实资格资产重复转换核对hash。完整SPZ、全量compressed PLY和运行LOD的点数、字节数、SHA-256及派生profile同时进入Bundle。

物化入口先调用冻结的R10 Environment Bundle验证器，因此collider仍由既有R7/R10 GLB安全门负责；独立Spatial Bundle验证器只重新核对输出文件身份、哈希、长度和转换统计，不复制Scene Pack或GLB合同。Godot集成前还必须再次执行冻结Scene Pack与GLB门禁。

## Spatial Assembly

R11.4新增私有matrix-oasis.prototype-spatial-assembly/0.1.0，它不修改Scene Pack。组合器重新验证R10 assembly report、Scene Pack/Runtime身份和Spatial Environment Bundle，并要求Scene Pack中唯一环境placement绑定同一collider且在每个node可见。`entry-player-xz-v1`把空间源原点确定性对齐Runtime入口节点的Scene Pack玩家出生点X/Z，Y轴仅应用签署的Godot平移与地面偏移；最终root变换和入口锚点证据写入canonical assembly/report，Godot wrapper不追加未报告的坐标常量。

组合结果显式记录root平移/旋转、固定`YXZ` Euler顺序、splat局部中心补偿与`[0,0,0]`毫度局部旋转、独立的splat/collider尺度及panoramaVisible=false。资格SPZ是v2且没有坐标扩展，按`@adobe/spz`规范使用默认RUB（Y-up）坐标；Godot wrapper必须在节点进入树后重新应用canonical零旋转，覆盖gdgs为常规Y-down PLY设置的隐式`-180° Z`方向修正。root的X/Z以`entry-player-xz-v1`把环境稳健中心映射到入口出生点；splat按源位置1%/99%分位边界执行`splat-robust-fit-30m-v1`，collider按真实GLB边界执行`collider-fit-30m-v1`并以中心地面样本校准。三者不得共享未经证明的尺度或增加未记录试摆常量。

## Spatial Preview Overlay

R11.5的空间缓存不是R10 run的新版本。它以原R10 run ID为键，独立保存Spatial Environment Bundle/report、Spatial Assembly/report、绑定这些文件的run report和compressed PLY。每次recover/find/load都重新验证R10 run及其Scene GLB，再验证overlay和collider交叉身份；任一漂移即不再暴露缓存。

启动时只把复验后的Runtime、Receipt、Scene Pack、Scene GLB、Spatial Assembly和compressed PLY复制到一次性Godot工程。工程副本启用固定gdgs Compute设置并先运行editor import；正式模块工程、R10 run、R10 current和panorama均不修改或读取。
