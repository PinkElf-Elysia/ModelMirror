# R12验收记录

状态：R12实施中；初版声明门关闭

固定基线：`6a88c648f3db2afc39574a57066a14c341c161f9`

## 批次

- [x] R12.1 治理与初版声明门（`a0085fc7`）
- [x] R12.2 通用候选验收与修复（`21a45323`）
- [x] R12.3 六资产组装与宿主扩容（`4e8fee93`）
- [x] R12.4 Marble SPZ自动空间化（`52c56075`）
- [x] R12.5 离线端到端与泛化复验（已验证，等待本地提交；SHA在R12.6记录）
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

## R12.4证据

- 环境Pipeline保留原R10 panorama+collider双下载API和字节行为，并新增显式`materializePrototypeEnvironmentWithSpatialSource`。新路径只从同一次`marble-1.1` world取得panorama、collider及`spz_urls.full_res`各一次，并读取`semantics_metadata.metric_scale_factor`与`ground_plane_offset`；审批精确绑定一次create、最多180次poll、一次Get World、三次下载、1600 credits和1.50美元上限。缺失、畸形或未批准的尺度/URL在发布前静态拒绝。
- 新增私有canonical`Prototype Spatial Source Bundle 0.1.0`，将同一Environment Bundle、collider、full-res SPZ以及`metricScaleMicros=round(metric×1,000,000)`、`groundPlaneOffsetMm=round(offset×1,000)`绑定。Bundle和脱敏report不含prompt、world/operation ID、下载URL、凭据或原始响应；它只证明本地字节完整性，不宣称供应商真实性。R12专用loopback回归精确锁定一条7请求链、审批前零请求、URL/metadata fail closed和动态凭据不泄漏。
- `materializePrototypeSpatialEnvironmentFromSource`先复验Environment与Spatial Source两份bundle和文件身份，再调用既有R11转换器生成确定性640k compressed PLY；尺度仅取已量化官方字段，Godot平移/旋转固定为零，不存在人工常量回退。定向测试锁定同一source重复转换、bundle/file漂移及旧显式校准API兼容。
- `assemblePrototypeSpatialScene`保留默认v1不变，并新增闭合v2选择，只接受prototype assembly v2。v2从通用walkable envelope扣除墙体与1m安全余量，按Scene Pack声明顺序生成固定4×2槽位，最多六个非环境placement；窄于8×4m或七项固定返回`PROTOTYPE_SPATIAL_ASSEMBLY_SAFE_LAYOUT_UNAVAILABLE`。六槽位、0/7边界及20次canonical确定性已验证，不含案例坐标或题材分支。
- R11空间overlay恢复器现在从已复验的源assembly report精确选择v1/v2重算；未知profile fail closed。实际事务导入/恢复测试证明v1缓存语义不变、v2 overlay可重启恢复。Godot strict loader兼容可选layout；wrapper在空间root坐标内应用X/Z槽位，再继续以每个Mesh的全局AABB完成Y轴落地，环境visual仍隐藏、collider与panorama禁用策略不变。
- 定向门：prototype environment 9/9、spatial environment 10/10、spatial assembly 10/10、spatial builder 10/10、Godot 4.6.3 headless import通过。完整`npm.cmd test`最终为707/707；首次仅冻结R9 loopback超时计数用例在全套高并发负载下得到0/1，该原用例单独复现1/1，未改冻结代码，随后同一完整命令稳定707/707。
- 最终树完整`npm.cmd run verify`为20/20步骤，Node 707/707、Godot 4.6.3的R4–R11 source/import/runtime/parity/3D/Scene/splat回归、Creator build与HTTP smoke均通过；boundary为checked=1080/tracked=1079，round/parent scope均通过，`git diff --check`通过。
- 本批只使用官方文档确认字段形状并以loopback假服务验证；未调用真实Marble、模型或Meshy，未读取供应商凭据，未创建远程world或下载真实资产。回退为单独revert R12.4提交；原R10双资产API、R11显式校准和v1 spatial overlay继续可用。

## R12.5证据

- 新增题材无关的Runtime黑盒可达性证明器，只调用冻结R3公开入口准备、创建和单步执行；状态键仅含location与variables，不硬编码action/ending ID。它按声明顺序自动发现全部ending路径和至少一个可达循环，设有10,000步与16,384状态硬上限，失败仅返回静态诊断。
- 中性2-node/2-ending夹具证明两个ending和循环均可自动发现；同一Runtime/Receipt并发执行20次的冻结结果字节一致，非canonical输入fail closed。一方qualification源码扫描不含地铁、学生、护士、通勤者、车票或时钟分支。
- R10宿主状态机新增`spatializing`可观察阶段，R10调用仍按原`normalizing -> assembling`工作；R12操作组合器固定执行environment、assets、normalization、spatialization、prototype publish、spatial overlay publish，任一阶段失败均阻断后续发布且不回显底层异常。Creator只增加对应阶段标签，旧Runtime/Parity/Builder模式保持可用。
- 显式只读复验仓外中性真实缓存`C:\tmp\matrix-oasis-r10-runs`与`C:\tmp\matrix-oasis-r11-spatial-primary-density-v8-overlay`成功：交集run为`321ec351...bac488`，模型记录为`gpt-5.6-luna`，Runtime声明1个ending且通用BFS到达1个；复验逐字重算prototype assembly、spatial assembly、Scene/Runtime/Receipt、GLB和compressed PLY身份，不读取API key、不访问网络。
- 定向`npm.cmd run test:r12`为12/12，包含实际存在的profile、六资产、空间源、泛化和宿主入口；宿主与Creator回归20/20，Creator TypeScript/Vite build为248 modules。完整`npm.cmd test`最终为707/707；首轮明确失败仅为新增测试含本机绝对临时路径字面量触发boundary，改为卷根动态路径后`check:boundary`通过，同一完整命令复跑707/707。
- 最终树完整`npm.cmd run verify`为21/21步骤：doctor、R12 scope/boundary/MVP claim、Godot 4.6.3的R4-R11 source/import/runtime/parity/3D/Scene/splat、全部Pack/Generator/Asset/Spatial门、R12定向门、707项Node、Creator 248 modules build及HTTP smoke全部通过；round scope为checked=70/changed=64，MVP状态继续诚实保持`pending-r12-qualification / claimAllowed=false`。
- 本批未调用真实模型、Marble或Meshy，未读取供应商凭据，未创建远程任务或下载新资产，也未启动父服务、Docker或共享栈。回退为单独revert R12.5提交；已发布R10/R11缓存及R1-R11冻结行为不受影响。

## 最终硬门

只有模型与环境/资产两批真实调用分别获批，并完成三结局、循环、重置、300帧性能、正式人物/道具/碰撞、窄屏和中性泛化人工验收后，才能将状态改为`R12验收通过`、把机器状态改为`r12-qualified`并记录`MATRIX_OASIS_R12_MVP_READY`。

最终HEAD、split tree、source archive和真实资格artifact hash只记录在仓外交付清单，避免仓内自引用。
