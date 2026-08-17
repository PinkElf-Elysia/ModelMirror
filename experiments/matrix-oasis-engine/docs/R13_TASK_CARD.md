# R13任务卡

## Objective

建立案例无关的Spatial Intent和Godot Environment Facts事实层，为R14布局求解器提供可验证输入。

## Scope

- 两个私有workspace：空间规划合同、离线环境分析器；
- 隔离的`apps/runtime-godot/spatial_analysis/`；
- 固定commit和许可证的非执行参考摘录；
- Node harness、合成环境、仓外R11/R12缓存资格与调试捕获；
- R13治理、测试和验收记录。

R1–R12、Creator、现有Godot产品场景、examples、vendor和历史验收均冻结。R13不实现求解、不接入产品预览、不调用供应商。

## Acceptance

每批先运行定向测试、范围与边界检查，再提交。最终运行`verify:r13`、根`verify`、standalone extraction、父范围检查和`git diff --check`；四个环境的navmesh与anchors只在仓外调试视图人工验收。

## Risks

- 导航烘焙线程和物理query时序错误会产生不稳定或不真实的facts；
- collider校准或Euler顺序漂移会造成整体翻转/偏移；
- 隐藏采样常量会让分析器对单一案例过拟合；
- 文件系统换身可能发布不完整或不属于已验证输入的facts。

## Rollback

按R13.6到R13.1逆序`git revert`六个提交。Git回退不删除仓外分析输出；仓外目录由验收清单单独处理。
