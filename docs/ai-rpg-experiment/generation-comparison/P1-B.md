# P1-B：三用途参数配置与预设存储

状态：P1-B 已完成离线验收，等待用户确认本批；P1 整体尚未完成发送接线。P1-A 已经用户确认。

## 开工合同

- 工作区 C:/tmp/modelmirror-rpg-generation-comparison，codex/rpg-generation-comparison，基线 d9f20fe691e8ddef8623d779a25a9359717f2e8d。
- 开工保留 P0 五文件与 P1-A 六文件；未暂存。原主工作区、共享栈和验收入口不动。
- 证据：model-control.mjs 的目录与会话选择仅为 v1 三参数；contracts.mjs 的作品 model 仅含一套 selection/parameters；SessionRecords 已有独占锁、CAS、写入未确认核对，可复用。
- 单一交付目标：v2 零调用预检 → 三用途参数记录／预设 → 重启恢复，以及显式作品推荐的保存、发布、导出导入闭环。
- 允许七文件：authoring/generation-settings.mjs（新增）、authoring/model-control.mjs、authoring/contracts.mjs、authoring/session-storage.mjs、authoring/runtime-binding.mjs、authoring/generation-settings.test.mjs（新增）、本文。authoring 位于 experiments/ai-rpg-engine/。
- 增加第六文件原因：原 SessionRecords.reconcile 会核对任意待确认记录；为避免预设恢复清除另一会话的待确认写入，增加可选 expectedId 守卫，旧调用保持兼容。该原子存储守卫与本批恢复验收不能分离。
- 增加第七文件原因：回归实际发现两个隔离源码副本缺少新模块，原运行绑定清单也未覆盖它。runtime-binding 的完整产物清单必须同步加入新模块及其宿主依赖；不修改或弱化旧源码漂移测试。
- 风险中：新增独立文件记录，不迁移旧会话，不改变现有发送行为。无生产依赖、数据库迁移、真实调用、认证、共享部署或 Git 发布。
- 本批提供宿主内部配置 API；旧会话发送器与双端 HTTP/UI 接线在后续批次完成。不能把配置已保存表述为现有聊天已经生效。
- 不改：原 system、作品资源、旧模型记录、账本、八插件、六卡 UI、模型执行与比较协调器。

## 本地合同

- profile 只含用途、modelId 与 v2 default/explicit 参数；不得携带路由、资格、凭据、插件授权或额度。
- 剧情、摘要、记忆分别有配置 revision，另有会话参数记录 revision；摘要的压缩任务后续读取摘要配置。保存一个用途不覆盖其余用途。
- 参数保存必须由宿主固定的控制面预检接口校验。读取、预设预览、作品保存和发布不派发模型。预设或能力改变后应用必须再次校验，不能凭曾经的回执跳过。
- 预设独立于会话，以用途与模型标记；跨模型不偷偷转换。预览保留原值并给出不兼容项，明确修改后才能应用。
- 会话记录和预设库独立 CAS、操作 ID、容量；重复操作返回原回执，碰撞拒绝。未知写入通过原记录核对，不重新校验或创建另一操作。
- 作品新增可选 model.recommendations，包含三用途 portable profile。不能与旧非空 selection/parameters 混存；旧文档不自动加字段。推荐仅供新会话明确采用，不自动覆盖会话。
- 最大 100 个预设、每记录 1000 次操作；容量满则明确拒绝，不自动删历史。

## 验收与回退

- node --test authoring/generation-settings.test.mjs：固定 loopback 假控制面；验证三用途隔离、能力漂移、跨模型、不支持字段、默认／显式、重复操作、冲突、写入失败、未知写入核对、重启、损坏，以及实际 AuthoringStore 的发布／导入导出。
- 回归：model-control、store、offline-session、controlled-session 相关测试；必要时增加宿主边界测试。
- 语法、git diff --check、敏感内容与最终范围检查。
- 回退不启用新配置 API 即可；保留新记录，不回写旧记录、不删除未知操作。Windows 沿用现有原子文件方案，不宣称断电持久保证。

## 已实现接口及存储边界

新增 `ParameterSettingsStore` 是宿主内部服务；它只访问受信 `ControlPlaneModelControl` 的固定目录、参数预检端点，不提供供应商生成、认证、路由配置或账本写入方法。

| 接口 | 行为与版本要求 |
| --- | --- |
| `readSession(sessionId)` | 读取三用途参数及修订；新记录为空配置，不自动采用作品推荐 |
| `configure(input)` | 指定用途保存或清除覆盖；带 operationId、参数记录 expectedRevision、expectedPurposeRevision |
| `listPresets()` / `savePreset(input)` | 独立预设库 revision；按 preset ID、用途、modelId 保存，修改需 expectedPresetRevision |
| `inspect(profile, selection)` | 保留全部原始配置，返回不兼容字段；没有生成调用 |
| `applyPreset(input)` | 校验预设 revision、用途、模型和当前能力，再存入指定会话用途；不复用旧资格 |
| `recommendations(sessionId)` | 明确导出三用途可移植意图；不会自行保存作品或改变其他会话 |
| `recover(kind, targetId, operationId)` | 核对指定存储目标，不重做预检；不会清除其他会话或预设库的待确认写入 |

`ControlPlaneModelControl.parameterCatalog()` 显式请求 v2；原 v1 缓存、选择和验证方法保持原协议。`checkParameters()` 先刷新正式目录，匹配模型／选择／能力 revision，展示本地字段诊断，再调用控制面权威预检。新的纯读取方法不会认证或配置控制面。

- parameter profile 格式为 `modelmirror.rpg.parameter-profile/1`，只含 `purpose`、`modelId`、`parameters`，其中 parameters 沿用 P1-A 的 `modelmirror.rpg.parameter-config/2`。
- 会话和预设的本地记录保留控制面解析快照、明确/默认来源、供应商映射和不透明回执 hash；作品推荐仅保留 profile，排除这些执行依据。
- 推荐格式为 `modelmirror.rpg.parameter-recommendations/1`；story/summary/memory 均为 profile 或 null。作品的 `model.selection`、`model.parameters` 同时保持 null，避免新旧两套推荐争夺优先级。
- 配置 JSON 与完整资源包均走原 AuthoringStore 校验、操作 ID、revision、原子写入及不可变发布版本；不另建绕过导入校验的入口。
- 停止词保留空白及 Unicode。空列表是否允许由当前能力决定；请求以完整 UTF-8 JSON 的 16 KiB 限额预检。合法的较大受信默认值不会因错误套用请求限额而被拒绝。
- 独立目录 `generation-parameters-v1` 保存参数记录；每个记录有 CAS、不可覆盖的操作回执和哈希。沿用 SessionRecords 的独占 owner 与明确死进程恢复，不触碰旧运行目录。
- 原绑定的 artifactHashes 同时记录新模块及其宿主依赖；源码变化会要求重新启动，不能把旧绑定静默当成新实现。

### 后续接线门禁

本批尚未把新配置接到既有会话发送器、双端 HTTP/UI 或六卡聊天。参数记录 revision **不等同于正式剧情上下文 revision**。下一批必须在会话锁下同时校验正式 revision、busy/unknown、用途授权和参数记录，再冻结引用与有效配置；不能仅因参数存储写入成功就宣布下一轮已生效。

既有会话继续读取原模型记录；本批无静默迁移。新目录的缺失记录表示尚未配置，写入时非零 expectedRevision 必须拒绝缺失或陈旧记录；后续发送器应以会话内冻结引用校验所需参数修订，不能把缺失记录回退为空配置。停用后的实际配置显示、插件权限以及三个用途的真实发送证据属于后续接线验收。

## 复核发现与修复

1. 新模块加入 contracts 后，旧隔离源码副本的两项回归报 ERR_MODULE_NOT_FOUND。已修复运行绑定产物清单，带入 generation-settings、model-control、model-plugin 与 session-storage；旧测试保持原样，相关 78 项重跑通过。
2. 初次新增验收为 28/30：测试中的 Python stdin 在 Windows 未指定 UTF-8，以及“旧能力”样例误取了新 revision。已固定 UTF-8 并保留原选择快照；没有放宽断言。联查同时对齐空停止词列表、固定参数、依赖规则和完整请求大小校验。
3. 恢复接口增加 expectedId 守卫，核对预设时不会清除另一会话的 pending 记录。旧无参 reconcile 调用保持原行为。
4. 原 model-control 的 CRLF 已保留；未混入整文件行尾改写。


## 最终验收结果

- 首轮旧回归：160 通过 / 2 失败，已保留 regression-first-failures.log/xml；缺失依赖清单已修复，旧断言不变。
- 集中回归：198 通过 / 0 失败 / 0 跳过，331.50 秒；包含当时新增 36 项与旧回归 162 项。
- 随后针对通用会话 ID 补充实际兼容修正：读取 studio/card-session-store.mjs 证实旧卡使用 UUID，参数存储改为接收有界宿主 ID，并映射为独立哈希文件名，避免与 presets 键冲突。没有改旧卡数据或会话宿主。
- 该最终修正后重跑全部新增验收、离线会话和运行绑定：**79 通过 / 0 失败 / 0 跳过，20.69 秒**（37 项新增 + 42 项受影响旧回归）。未对与此 ID 校验无关的其他旧测试重复执行。
- JavaScript 语法检查、git diff --check、七文件空白及常见凭据模式扫描：通过。P1-A 的 12 份源码／证据 hash 全部保持一致；暂存区为空。
- P0 的 18660/prototype.html 只读检查 HTTP 200。没有重启旧入口或共享栈。
- 前端构建／浏览器新 UI 验收：不适用，本批不改前端。真实认证／生成／共享部署／Commit／Push／PR：未运行。

执行命令（工作目录 experiments/ai-rpg-engine）：

```powershell
$env:RPG_TEST_PYTHON='C:/tmp/modelmirror-gate-q-venv-4764406c/Scripts/python.exe'
$env:RPG_P1B_EVIDENCE='C:/tmp/modelmirror-rpg-p1b-evidence'
node --test --test-reporter=spec --test-reporter-destination=C:/tmp/modelmirror-rpg-p1b-evidence/all-final.log --test-reporter=junit --test-reporter-destination=C:/tmp/modelmirror-rpg-p1b-evidence/all-final.xml authoring/generation-settings.test.mjs authoring/model-control.test.mjs authoring/store.test.mjs authoring/offline-session.test.mjs authoring/controlled-session.test.mjs authoring/runtime-binding.test.mjs authoring/joint-session.test.mjs
node --test --test-reporter=spec --test-reporter-destination=C:/tmp/modelmirror-rpg-p1b-evidence/id-compat-final.log --test-reporter=junit --test-reporter-destination=C:/tmp/modelmirror-rpg-p1b-evidence/id-compat-final.xml authoring/generation-settings.test.mjs authoring/offline-session.test.mjs authoring/runtime-binding.test.mjs
```

证据目录 C:/tmp/modelmirror-rpg-p1b-evidence：含首轮失败、修复重跑、集中回归、最终 ID 兼容回归、preflight-capture.json 和 P1-B-HASHES.json。preflight-capture 通过 loopback 假控制面调用实际 Python 参数解析器，记录 v2 请求、解析值和回执；没有模拟成真实供应商调用。

下一批 P1-C：在正式会话锁／revision／授权边界下接入配置 API 与发送冻结，再进入 P2 双端 UI 和六卡联动；不能跳过未知恢复、版本引用与旧记录兼容。
