# R14任务卡

## Objective

将R13 Spatial Intent与Environment Facts接入确定性约束求解和Godot最终物理复验，产出可独立回退的solved overlay，并在人工验收后切换Creator默认预览。

## Scope

- 三个私有workspace：Solution合同、确定性求解器、Godot复验Node封装；
- 隔离的`spatial_solution_verification`与`solved_spatial_prototype` Godot目录；
- Intent离线合成、solved overlay、CLI、合成夹具与R11/R12缓存资格；
- R14治理、测试和验收记录。

R1–R13、历史Creator/Godot产品场景、examples、vendor和历史验收均冻结。R14不调用供应商，不增加题材分支或额外产品功能。

## Acceptance

每批先运行定向测试、范围与边界检查，再提交。R14.6后由用户人工检查中性与末班地铁两类案例；只有明确通过后才实施R14.7默认切换及声明门解除。

## Risks

- facts拓扑或资产bounds身份漂移导致错误候选；
- 无界搜索、随机或时间退出破坏确定性；
- Node求解与Godot真实物理结果不一致；
- node transition无条件传送或overlay换身破坏原子性；
- 自动测试通过被误报为人工体验与初版资格通过。

## Rollback

按R14.7到R14.1逆序`git revert`。Git回退不删除仓外facts、solution、overlay、capture或run；这些按验收清单单独处理。
