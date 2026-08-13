# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父仓 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先提交父项目变更申请并取得用户人工批准。
2. 禁止依赖父 `client/`、`server/`、根配置、环境变量、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot 缓存、测试报告或生成的空间资产。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R11 专属限制

- R11 只修复 R10 panorama 室内环境缺乏平移视差和空间体积感的问题；不新增 AI NPC、任务、世界事件、图片输入、编辑器、存档、正式导出或父项目接入。
- R1–R10 contracts、validator、compiler、runtime、Scene Pack、examples、Creator 既有模式、Godot R4–R10、vendor 与历史 ADR/验收记录全部字节冻结；仅本轮机器白名单列出的新包、Godot spatial wrapper、gdgs 原样 vendor、指定 Creator 文件和 R11 文档可变更。
- 固定输入为仓外 Marble SPZ 与 collider GLB；不创建、轮询或下载新的 Marble/Meshy/模型任务，也不读取其密钥、额度或远程状态。
- SPZ 只允许通过 `@playcanvas/splat-transform@3.3.0` 和 `@adobe/spz@0.2.2` 离线转换为确定性的 compressed PLY；不得把非确定性 SOG 作为权威缓存格式。
- Godot 只允许 vendored `ReconWorldLab/godot-gaussian-splatting` v3.3.0、commit `70996511607a886dac9fdd5fc59a0445308eb3db` 的 Compute 路径；不得静默回退 Raster 或 panorama。
- 全景 PNG 可以保留为来源证据，但 R11 成功预览不得可见渲染 panorama；环境视觉必须来自可追溯到完整 SPZ 身份的 deterministic compressed PLY，碰撞来自独立 collider GLB。
- 2026-08-13 用户已显式批准在 R11 内加入确定性 LOD/降采样，或更换、修改 Gaussian renderer。运行时 LOD 必须同时记录完整源 SPZ/全量转换统计和派生算法、目标点数、字节数、SHA-256；不得以随机丢点、隐藏质量降级或降低 30 FPS 门冒充通过。
- R11 在稳定不少于 30 FPS、连续画面无宏观闪动、视觉/碰撞/出生点对齐，并完成一个不同来源且非过拟合的第二样例全链验证前，不得提交收口批次、push、创建 PR 或宣称初版完成。
- R11通过后仍不得宣称初版闭环。R12必须以最初的末班地铁案例从自然语言开始实际贯通正式人物、环境/道具资产、全部Pack/Spatial组装和Godot游戏运行时，并以另一个非题材专用样例证明可泛化；R12通过前不得提前进入初版完成叙事。
- 所有 metric scale、ground offset、坐标变换与中心补偿必须显式进入 canonical bundle/report；不得以人工试摆或隐藏常量掩盖对齐误差。
- 普通 verify 只能使用合成夹具或已验证的仓外缓存，不产生费用、不联网、不写入正式工程资产。
- 每次验证使用固定 R11 基线 `da2a914a2ff131507750a0afb8d8881180530f62`；committed、staged、unstaged、untracked 一视同仁。
- 不 push、不创建 PR，直至用户明确回复“R11验收通过，可以创建PR”。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自 rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
