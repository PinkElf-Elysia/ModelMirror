# Gaussian Splat 仓外资格结论

状态：`defer`。R7 不接入 gdgs，不复制 addon，不支持 SPZ，也不调用 Marble/Meshy。

## 固定候选

- 仓库：`ReconWorldLab/godot-gaussian-splatting`
- 提交：`d9de8db86a63e8bf9067c869dcdbd0614922fd1e`
- Git tree：`af2ca6aae12b8203186341370d09e8a8e811e60d`
- 许可：MIT；许可证 SHA-256 `5f6105df7c9d6af2a32867c350781b500d378c9b3e8966bba900c1ed5d40f6cc`
- 计划预期版本：`3.2.0-beta`
- 固定提交实际 `plugin.cfg` 版本：`3.3.0`

版本元数据与批准计划不一致，因此即使动态资格检查通过，R7 仍不得把该候选接入正式工程。未来若要采用，必须单独审批新的版本/提交边界并重新验证。

## 仓外动态证据

使用 Godot `4.6.3-stable` 在 `C:\tmp` 一次性副本完成：

- editor import：通过；
- upstream smoke/backend/raster/collision/lighting 五组 headless tests：全部退出 0；
- 仓库自带 `samples/assets/demo.sog`：3,401,487 bytes，SHA-256 `acb3138ee1c218c2b499cbb923ab01c451f7b6b4775c186345f018b036c07b78`；
- Raster 固定帧：640×360、96,811 bytes，SHA-256 `df04c2cee61ba7f2b906ee9740d6ca16b7935397f87a7d5e4a41dbdb6aa1dbab`；
- 资格前后源 checkout 的 HEAD 与 `git status --porcelain -z` 一致，正式模块文件树未被修改。

详细日志、工作副本、图片和机器报告只位于仓外 `C:\tmp\matrix-oasis-r7-gdgs-qualification`，不提交。

## 格式边界

该候选声明支持 `.ply`、`.compressed.ply`、`.splat`、`.sog`，不支持 `.spz`。R7 不做 SPZ 转换，不将 Marble 输出静默改写为其他格式。Scene Pack 正式入口仍只接受本地 `.glb`。
