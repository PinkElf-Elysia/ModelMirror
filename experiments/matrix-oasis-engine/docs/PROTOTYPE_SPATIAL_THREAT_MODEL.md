# R11空间环境威胁模型

## 资产入口

- SPZ最大64 MiB、最多2,500,000 splats；compressed PLY最大96 MiB；collider最大32 MiB；Bundle目录最大256 MiB。
- 输入只允许仓外普通文件；拒绝链接、穿越、换身、未知格式、越界计数和hash/byteLength不符。
- 转换只读，不执行输入中的脚本、URL或扩展行为；普通verify无网络、无供应商调用。

## 供应链

- `@playcanvas/splat-transform@3.3.0`（MIT）、`@adobe/spz@0.2.2`（ISC）固定lock。
- gdgs v3.3.0固定commit与逐文件tree hash，原样vendoring；任何字节漂移或未知addon文件都fail closed。
- 一方Godot代码继续禁止网络、环境变量、进程执行、文件写入和动态脚本加载；gdgs由供应链hash约束。

## 运行与隐私

- 诊断只发布静态code/path，不回显本机绝对路径、原始prompt、远程ID、URL、异常正文或凭据。
- 预览只绑定loopback；panorama不可见；Compute不可用时失败，不回退Raster。
- metric scale、ground offset、坐标变换、中心补偿和所有输入hash进入canonical report，以防隐藏校准漂移。
