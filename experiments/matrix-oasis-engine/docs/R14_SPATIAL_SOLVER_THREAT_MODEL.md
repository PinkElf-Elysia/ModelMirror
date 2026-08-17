# R14空间求解威胁模型

## 信任边界

Spatial Intent、Environment Facts、Runtime Pack、Receipt、Asset Bundle、旧Spatial Assembly及其文件都不受信任。公开入口必须先复验canonical bytes、闭合合同、身份、哈希和计数上限。R13 facts是候选事实输入，Godot最终导航、碰撞与物理query是发布前权威。

## 主要威胁

1. identity或文件换身使Solution绑定不同输入；
2. 随机、时钟、哈希遍历或浮点顺序破坏确定性；
3. 无界DFS导致资源耗尽，或到上限后偷偷部分成功；
4. 旧AABB网格、案例坐标、文案猜测或隐藏翻转掩盖无解；
5. zone domain断裂、terminal footprint不足、spawn被截断或视线穿墙；
6. 资产接地、环境穿透、资产重叠或node可见性组合只在Node近似中通过；
7. 普通transition无条件传送，或失败后Runtime、世界、current部分更新；
8. solved overlay、一次性Godot工程或current指针被junction、symlink或同名对象换身；
9. 诊断泄漏ID值、绝对路径、供应商数据或底层异常；
10. R14.6自动门被误报为R14.7人工验收和初版完成。

## 控制

- 闭合Schema、canonical JSON、静态diagnostics、严格identity/hash及深冻结结果；
- 固定候选排序、整数毫米、Euler YXZ、100000状态硬上限，无随机和时间退出；
- support、footprint、near、separate、facing、clearance、non-overlap均为硬约束；
- Godot等待NavigationServer与physics同步，验证query_path、capsule collide/cast、接地、穿透、重叠、terminal approach和3 m视线；
- solved overlay与current独立事务发布，输入与目标使用FileHandle、realpath和bigint身份复验；
- 失败不发布Solution、不替换current、不改变Runtime或世界；
- 一方源码扫描拒绝网络、供应商、题材ID、绝对路径和隐藏坐标；
- R14.7前`pending-spatial-solver / claimAllowed=false`保持机器锁定。

## 不承诺

R14不增加生成模型、资产质量、NPC、任务、动画、音频、存档、导出或父产品接入。人工验收前只证明求解与复验链可运行，不证明初版体验已经完成。
