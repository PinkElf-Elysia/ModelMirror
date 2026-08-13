# R12验收记录

状态：R12实施中；初版声明门关闭

固定基线：`6a88c648f3db2afc39574a57066a14c341c161f9`

## 批次

- [x] R12.1 治理与初版声明门（`a0085fc7`）
- [x] R12.2 通用候选验收与修复（`21a45323`）
- [x] R12.3 六资产组装与宿主扩容（已验证，等待本地提交；SHA在R12.4记录）
- [ ] R12.4 Marble SPZ自动空间化
- [ ] R12.5 离线端到端与泛化复验
- [ ] R12.6 末班地铁真实资格链
- [ ] R12.7 验收与初版收口

## R12.1证据

- PR #194已合并，`origin/main=6a88c648f3db2afc39574a57066a14c341c161f9`；已确认R11最终提交是该主线祖先且模块树零差异。从该BASE创建独立`codex/matrix-oasis-r12-last-train-mvp`和`C:\tmp\modelmirror-matrix-oasis-r12`，未从R11功能分支stack。
- 本批23个模块内路径；schema v12、active R12、固定BASE、五个兼容扩展package前缀及精确R12文件allowlist已同步。R0–R11验收、ADR、examples、既有Runtime/Creator/Godot/vendor与父仓继续fail-closed。
- 模块版本迁移为`0.12.0-r12`，lock只同步根版本两处；离线、禁脚本`npm.cmd ci --offline --ignore-scripts --no-audit --no-fund`安装120个锁定包，`npm.cmd prefix`与`npm.cmd ls --all --depth=0`退出0。
- `node --test tests/round-scope.test.mjs`为77/77；boundary正负fixture和新增MVP声明门4/4通过。`check:round-scope`与`check:parent-scope -- --base 6a88c648...`均为checked=23/changed=23，`check:boundary`为checked=1078/tracked=1070，`git diff --check`通过。
- 新增`docs/MVP_STATUS.json`及`check:mvp-claim`，当前固定`pending-r12-qualification / claimAllowed=false`；测试证明旧R10完成叙事、状态漂移和缺失R12验收证据均会fail closed。公开状态文档已改为只有R12全部硬门通过后才能声明初版完成。
- 最新树完整`npm.cmd run verify`为20/20步骤，Node 691/691；Godot 4.6.3的R4–R11 import/runtime/parity/3D/Scene/splat、Creator 248 modules build与HTTP smoke全部通过。
- 本批未调用模型、Marble或Meshy，未读取供应商凭据，未生成或提交真实资产，未启动父服务、Docker或共享栈。回退为单独revert R12.1提交，不影响仓外缓存或远程任务。

## R12.2证据

- `generatePrototype(request, provider, { acceptanceProfile })`新增可选、闭合的`Prototype Acceptance Profile 0.1.0`；旧两参数调用和包根三个运行时导出保持不变。Profile仅包含node、ending、action、zone、prop、character-placeholder数量范围，以及结构可达循环、全部ending结构可达和非环境brief实体/placement绑定，不含案例ID、题材文案或条件执行特判。
- 合法Generation Proposal若不满足Profile，会进入既有定向修复协议；修复请求仍只含上一候选及静态code/path，初始加修复总请求严格不超过3。Profile自身畸形在Provider访问前以`PROTOTYPE_ACCEPTANCE_PROFILE_INVALID`拒绝。
- 图检查改为显式栈，避免4096 node上递归耗尽；诊断顺序固定，结果与诊断深冻结，生成过程不修改Profile、Proposal或Provider输入。
- `npm.cmd run test:prototype-generator`为15/15，覆盖一次接受、Profile修复、三次耗尽、六类数量诊断、循环、ending可达、实体绑定、输入不变、冻结结果、无题材分支和公开面不扩张；显式`GODOT_BIN`环境下完整`npm.cmd test`为697/697。
- `check:round-scope`与`check:parent-scope -- --base 6a88c648...`均通过，`check:boundary`通过，`git diff --check`通过；R1–R11冻结路径和父仓未修改。
- 最终树完整`npm.cmd run verify`为20/20步骤，Node 697/697、Godot 4.6.3全回归、Creator 248 modules build及HTTP smoke均通过。首次沙箱内全量测试仅因既有测试无法在`C:\tmp`建立夹具而失败；授权同命令后仅缺`GODOT_BIN`的doctor失败，显式使用已核验Godot路径后7/7及最终697/697通过，未以替代测试掩盖失败。
- 本批仍未调用外部模型、Marble或Meshy，未读取任何供应商凭据，也未进入R12.6资格调用。回退为单独revert R12.2提交，R8原两参数生成行为继续可用。

## R12.3证据

- `assemblePrototypeScene(request, options?)`保留默认`matrix-oasis.prototype-assembly/1`及原公开常量不变，并新增闭合选择`{ profile: "matrix-oasis.prototype-assembly/2" }`。v1继续限制最多两个非环境brief；v2允许prop与character-placeholder任意组合、合计最多六个，zone、placement和每zone预算仍分别为4、32和8，七个brief固定返回`PROTOTYPE_ASSEMBLY_PROFILE_UNSUPPORTED`。
- v2已用0/2/6/7个非环境brief边界和三prop加三character混合案例验证；六资产声明顺序、Scene Pack、assembly report及20次canonical字节结果一致。默认v1仍拒绝六资产，旧调用与导出面不变。
- Prototype cache只对v2把profile加入cache key，旧v1 key字节保持不变；发布的assembly report记录所选profile，恢复时只接受v1/v2并按记录重新组装复验。新增事务发布与恢复回归证明v2 run可重启恢复，未知profile fail closed。
- 宿主brief上限由2提升至6，审批摘要按实际brief动态计算；六资产固定显示最多12个Meshy任务和180 credits，七资产在acquire前拒绝。Creator解析同步允许六项，审批列表有固定高度、纵向滚动和稳定scrollbar，窄屏不依赖横向溢出。live preview明确选择v2，既有缓存仍按其记录profile复验。
- 定向`node --test tests/prototype-assembly.test.mjs tests/prototype-host.test.mjs tests/prototype-builder.test.mjs`为37/37，Creator TypeScript/Vite build为248 modules。最终完整`npm.cmd test`为701/701；首次完整运行仅冻结R8 loopback超时用例在全套负载下计数0/1而失败，该用例单独复跑1/1且未改冻结代码，第二次同一完整命令稳定701/701。
- 最终树完整`npm.cmd run verify`为20/20步骤：Node 701/701，Godot 4.6.3的R4–R11 source/import/runtime/parity/3D/Scene/splat全回归，Creator 248 modules build及HTTP smoke通过；round scope为checked=43/changed=39、parent scope相同、boundary为1079/1079、`git diff --check`通过。
- 本批未修改R1–R11冻结实现、Godot、examples或父仓，未调用模型、Marble或Meshy，也未读取供应商凭据。回退为单独revert R12.3提交；v1默认路径和既有缓存继续可用。

## 最终硬门

只有模型与环境/资产两批真实调用分别获批，并完成三结局、循环、重置、300帧性能、正式人物/道具/碰撞、窄屏和中性泛化人工验收后，才能将状态改为`R12验收通过`、把机器状态改为`r12-qualified`并记录`MATRIX_OASIS_R12_MVP_READY`。

最终HEAD、split tree、source archive和真实资格artifact hash只记录在仓外交付清单，避免仓内自引用。
