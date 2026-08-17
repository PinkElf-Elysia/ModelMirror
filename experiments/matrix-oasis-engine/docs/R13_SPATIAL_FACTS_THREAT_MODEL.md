# R13空间事实威胁模型

## 资产与信任边界

输入的Spatial Environment Bundle、collider GLB和校准参数均不受信任。Node必须先用冻结验证器复验canonical manifest、文件长度、SHA-256和身份，再把只读副本交给一次性Godot分析工程。Godot只写Node创建并明确传入的临时输出文件。

Godot碰撞、NavigationMesh和物理space query是Environment Facts的事实来源；SPZ视觉、panorama、题材文案、旧布局结果和人工坐标不是事实来源。Spatial Intent只表达引用与语义约束，不能携带坐标、文件路径、供应商字段或秘密。

## 主要威胁

1. 非canonical、未知字段、悬空引用或身份漂移绕过合同检查；
2. GLB、manifest或输出路径在校验后被symlink、junction或同名对象换身；
3. collider校准、坐标系或Euler顺序错误导致整体翻转和偏移；
4. 导航几何在非主线程解析，或在物理同步之外执行ray/capsule query；
5. 空导航、断裂区域、窄通道、低顶、超坡度、台阶和门洞被错误归类；
6. 浮点遍历顺序、异步烘焙完成顺序或哈希表迭代导致facts字节不确定；
7. 固定案例坐标、150 mm落地常量或题材分支伪装成通用分析；
8. 诊断、报告或调试捕获泄漏绝对路径、输入ID、供应商信息或底层异常；
9. 参考项目源码被错误作为运行依赖引入；
10. 事实提取成功被误报为布局、出生点或初版体验已经修复。

## 控制

- 全部对象闭合、阶段门控、静态诊断、canonical JSON和16 MiB facts上限；
- FileHandle、realpath和bigint dev/ino身份复验，同父staging后单次rename，目标存在即拒绝；
- 固定Godot右手Y-up、毫米、Euler YXZ和玩家分析profile；
- GLTF场景与导航source geometry在主线程解析，异步烘焙完成后再进入物理同步query；
- 所有输出量化为整数，并按量化坐标与拓扑稳定排序；
- 一方源码扫描拒绝网络、供应商调用、案例文案、绝对路径和隐藏落地常量；
- 参考只保存固定commit的非执行摘录、许可证、哈希与适配说明；
- `MVP_STATUS`保持`pending-spatial-solver`且`claimAllowed=false`。

## 不承诺

R13不证明NavMesh适合最终玩法，不选择placement/spawn/terminal，不改善Creator预览，不保证跨平台浮点一致，也不认证输入资产来源。R14必须把Intent与Facts求解后再由Godot做最终物理复验。
