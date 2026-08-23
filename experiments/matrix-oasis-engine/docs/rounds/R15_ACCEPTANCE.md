# R15运行证据验收记录

状态：R15验收通过；等待R16 Creator迁移

## 固定基线

- `R15_BASE_SHA=4be3e9483e57f792769c079d3c985a357e99a558`
- 分支：`codex/matrix-oasis-r15-runtime-evidence`
- 版本：`0.15.0-r15`

## 批次记录

| 批次 | 本地提交 | 结果 |
|---|---|---|
| R15.1 治理与声明门 | `e61f0fb3` | 完成 |
| R15.2 Replay/Evidence合同 | `7893c1df` | 完成 |
| R15.3 Godot真实输入重放 | `928f0a60` | 完成 |
| R15.4 媒体与性能证据 | `b30f31aa` | 完成 |
| R15.5 候选级证据修复 | `b8cb11ee` | 完成 |
| R15.6 双缓存真实资格 | `73f29615` | 自动与人工验收通过 |
| R15.7 验收收口 | 本批提交 | 人工通过；提交后执行clean HEAD拆分复验 |

## R15.6真实运行证据

- 中性缓存重新通过冻结R14求解/物理复验后，R15 run为`04359c15960e8904f866e0f34ea7983ed1299a86867d5f6840ca52d0ca09ad20`，绑定Solution `sha256:842617274fa6ba4efa0c9d4b01c9bae348a7e933da5bae904e893b1f36576088`；首候选直接通过、无修复，3条重放、8张960×540截图、548帧30 FPS录像，300帧中位约67.0 FPS。
- 末班地铁使用R14人工通过的v22 Solution重新资格，R15 run为`21880e50f24ab81f5e0674284d138024c8eeceb2276dfdd740975c0a6fee1d67`，绑定Solution `sha256:15ea379be2c0492ee3175992a0f62a1e8381a8f31ca16a792f0b71cd0b2f199e`；首候选直接通过、无修复，9条重放覆盖三ending、循环、节点、ending后reset与active reset，产出27张960×540截图、1228帧30 FPS录像，300帧中位约91.8 FPS。
- 两条链均由实际InputMap、生产控制器、相机、射线和Action terminal完成；checkpoint逐项证明导航完成、capsule净空、floor距离和交互距离。资格与捕获严格复验canonical、身份、哈希和媒体字节，零网络、零凭据读取、零供应商费用。
- 历史无效/stale solved root没有被冒充为成功缓存；资格先精确识别其物理不兼容，再改用并正式发布已人工通过的R14 Solution。没有增加案例坐标、题材分支、阈值放宽或隐藏Provider调用。

## 人工验收

用户在R15正式预览中确认“全部正常，保持了上一轮的所有修复，可以判定通过”。据此，实际交互、边界、画面稳定性和R14修复保留通过本轮人工硬门；R15窗口随后关闭。

## 最终自动验收

- 模块`npm.cmd ci`、严格Godot 4.6.3 doctor、`verify:r15`和根`verify`通过；根验证共25步，Node测试813/813通过，Creator构建与smoke通过。
- `check:round-scope`通过（57个检查来源、53个变更路径），`check:parent-scope -- --base 4be3e9483e57f792769c079d3c985a357e99a558`通过，`git diff --check`通过。
- 父`client`完成clean `npm.cmd ci`，96个测试文件、506项测试通过，生产构建通过；依赖审计报告既有5项漏洞和大chunk警告，本轮未升级依赖或执行`audit fix`。
- clean standalone首次完整运行暴露冻结R9超时测试的20 ms调度竞态：安全实现可在请求抵达loopback前超时，因此请求计数允许为0或1；R15仅将旧断言收窄为“至多一次、不得重试”，没有修改Meshy Provider或产品行为，并以20次定向重复及完整门禁复验。
- `verify:extraction`要求clean HEAD，因此在本批提交后执行；source、split与archive哈希仅记录于仓外交付摘要，避免验收提交自引用。

## 声明门

R15人工通过后机器状态转为`pending-creator-migration / blockingRound=R16 / claimAllowed=false`。R15只证明离线双缓存的真实运行证据闭环；Creator尚未接入同一profile，因此仍不允许宣称初版闭环完成。
