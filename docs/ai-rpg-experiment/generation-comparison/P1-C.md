# P1-C：正式会话参数接线与请求冻结

状态：P1-C 已完成离线验收，等待用户确认本批。P1-A、P1-B 已确认；本批不是多模型对比 C1。

## 开工合同

- 工作区 C:/tmp/modelmirror-rpg-generation-comparison，codex/rpg-generation-comparison；HEAD d9f20fe691e8ddef8623d779a25a9359717f2e8d。保留全部 P0、P1-A、P1-B 未提交成果。
- 单一目标：通过正式创作会话 API 显式应用三用途 v2 参数，在会话锁和 revision 下原子保存，再以原参数配置及解析回执冻结受控请求；离线证明重启、未知结果和旧 v1 协议兼容。
- 六文件范围：authoring/model-control.mjs、authoring/controlled-receipt.mjs、authoring/joint-session.mjs、authoring/server.mjs、authoring/generation-session.test.mjs（新增）、本文。authoring 位于 experiments/ai-rpg-engine/。
- 超过五文件的原因：正式 HTTP 接口、原子会话、控制面参数适配、执行回执分别已有模块；本目标必须贯通这四处，独立测试与交付合同另占两文件。不改通用聊天、六卡 UI 或比较协调器。
- 配置意图采用新的显式版本；旧配置和旧请求保持原格式，不迁移旧会话。P1-B 预设是可移植的意图来源，应用时复制其 profile 并重新预检；不把独立参数存储作为正式会话的第二个写入真源。
- 正式会话 models 记录同时冻结参数 profile、选择／能力 revision、解析回执、有效值和供应商字段。正式 session revision 与 model revision 同时 CAS；忙碌、未知及已有授权守卫保留。
- 剧情／摘要／记忆分开选择；压缩任务使用摘要配置。参数界面与聊天新插件权限属于 P2，本批不能声明六卡已接线。
- 保存、目录、预检、恢复不派发生成；未知结果仅用原请求 ID 核对，能力变化不重新解释已派发请求。
- 不新增依赖／数据库迁移；不认证、不真实调用、不变更共享栈／旧入口／额度；不 Commit、Push、PR 或部署。

## 验收

1. 本地假控制面调用实际 Python 参数解析器，捕获三用途及压缩的 v2 请求、解析与回执；保留默认／显式差异、停止词和思考字段。
2. 正式 HTTP 配置与预检零生成；CAS 冲突、幂等操作、能力漂移、忙碌／未知、写入未确认、重启恢复及错误回执验证。
3. v1 控制面、旧会话、M2/M3、原子存储和分支相关回归；最终语法与差异检查。离线成功不代表真实模型能力或真实参数效果。
4. 证据写入独立 C:/tmp/modelmirror-rpg-p1c-evidence，完成后追加实际命令、失败和结果。

## 回退

不开放 v2 接口的前端入口即可停止新配置。已保存的新版本配置和请求保留，不改写为 v1；已派发请求仍按原 ID 核对。旧验收服务未重启，原会话和账本保持原目录。

## 正式 API 与冻结格式

- 原 `/rpg-app/api/authoring/model-settings/catalog` 及 `/sessions/:id/models` 默认仍返回 v1；显式 `?parameterVersion=2` 才读取受控 v2 能力。重复或未知查询参数拒绝。
- `/model-settings/check` 接收 `{choice}`；v2 choice 是 `modelmirror.rpg.parameter-selection/2`，仅有 catalogRevision、selectionId、selectionRevision、capabilityRevision 和可移植 profile。profile 为 P1-B 的用途、modelId、default/explicit 参数意图，不接收有效值、供应商字段或凭据。
- `/model-settings/configure` 沿用 operationId、sessionId、expectedRevision、expectedModelRevision、purpose、choice。新格式必须与外层用途一致。清除一个用途仍显式提交 choice=null，不修改其他用途。
- 宿主再次读取目录、执行零生成参数预检后，将选择、profile、有效参数、wireParameters 与 parameterResolution 原子保存为 `modelmirror.rpg.control-model-choice/2`。不再在发送时读取可变的预设文件；后续编辑预设不影响已应用会话。
- `/model-settings/reconcile` 沿用原操作 ID 核对原子存储结果；重复 configure 先返回已保存回执，不请求新目录、不追加配置版本。
- `modelmirror.rpg.authoring-task/2` 携带原配置及 capabilityRevision；服务端实际映射后的回执需与冻结的 parameterResolution、用途和参数一致。压缩使用 summary 配置，但仍是独立 compression 请求。
- 发送前若原选择的能力、资格或参数解析发生变化，阻断并保留旧配置；目录其他行变化不自动作废仍相同的已选行。相同目录的并发只读查询不应误报配置失效。
- 未知请求恢复只使用原请求 ID 与 sourceRequestHash，不读取当前能力、不新预检，也不重新映射旧请求。v1 请求与回执仍走原协议。
- 创作宿主原生配置无需模型选择插件；若原模型选择插件已启用，仍保留其会话授权提交守卫。本批不新增聊天参数插件，也不把原生创作 API 当作其授权。

## 验收过程记录

- 首次语法检查发现新增测试中非 async 箭头使用 await，已修正；另一次测试命令因工作目录重复拼接路径在运行前停止，没有产生调用。
- 首轮新增验收：23/25 通过。两项失败是测试误写统一的 OFFLINE_OPERATION_PENDING；实际宿主分别返回既有 OFFLINE_RESULT_PENDING 和 MEMORY_RESULT_PENDING。已按源码核对并修正精确断言，不改宿主既有状态语义。
- 补充同目录并发只读、目录变化、分支、撤权及额度不足用例；开始运行新增与受影响旧回归，最终结果待追加。

- 综合离线回归（4 个测试文件并行）：253/254 通过，0 跳过，749.07 秒。唯一失败为新增 v2 分支用例在参数配置预检时 MODEL_CONTROL_TIMEOUT；堆栈定位于本地假控制面 parameters/validate。单独按名称重跑该用例 1/1 通过（18.55 秒）。不能将这次综合命令称为全绿，原日志完整保留。
- 没有增加生产超时、弱化断言或跳过用例。补充较短测试超时下的无写入／无生成／无额度消耗检查；随后使用单文件并发复测全部新增验收。运行代码在该综合回归之后未修改。

## 最终结果与边界

- 旧回归 223/223 通过；覆盖 v1 参数控制、正式 HTTP、受控请求、M2/M3、模型选择插件、分支、预算、运行绑定和保存恢复。
- 本批新增验收 32/32 通过，197.58 秒，0 跳过；包含三用途与压缩任务、默认／显式值、HTTP 接线、参数／正式版本冲突、重启未知恢复、分支隔离、撤权、额度不足、未确认写入、异步篡改及预检超时零副作用。
- 综合并发命令保留原结果 253/254（1 次本地 MODEL_CONTROL_TIMEOUT），不改写为整组全绿。其后全部新增用例以单文件并发复测通过，没有修改生产超时、重试策略或既有测试。
- JavaScript 五文件语法、UTF-8、空白检查及 git diff --check 通过。P1-A 的 12 份冻结文件未变化；P1-B 除本批明确扩展的 model-control.mjs 外，其余 manifest 文件保持一致。
- 32 项新增用例均在独立临时目录中使用本地假控制面；参数解析调用真实 Python rpg_parameters.py。供应商回执为明确的测试回执，不能宣称真实生成质量／资格／费用已验证。
- 原验收入口未重启、共享栈和真实账本未修改；没有 Commit、Push、PR、认证或真实模型调用。未改前端，本批不运行或宣称 UI／六卡接线及前端构建通过。

执行命令（除最后一条外工作目录为 experiments/ai-rpg-engine）：

```powershell
$env:RPG_TEST_PYTHON='C:/tmp/modelmirror-gate-q-venv-4764406c/Scripts/python.exe'
$env:RPG_P1C_EVIDENCE='C:/tmp/modelmirror-rpg-p1c-evidence'
node --test --test-concurrency=4 --test-reporter=spec --test-reporter-destination=C:/tmp/modelmirror-rpg-p1c-evidence/regression.log --test-reporter=junit --test-reporter-destination=C:/tmp/modelmirror-rpg-p1c-evidence/regression.xml authoring/generation-session.test.mjs authoring/generation-settings.test.mjs authoring/model-control.test.mjs authoring/model-plugin.test.mjs authoring/controlled-provider.test.mjs authoring/controlled-state.test.mjs authoring/controlled-session.test.mjs authoring/controlled-workbench.test.mjs authoring/joint-session.test.mjs authoring/joint-api.test.mjs authoring/branch-session.test.mjs authoring/runtime-binding.test.mjs authoring/server.test.mjs authoring/joint-budget.test.mjs
node --test --test-name-pattern='v2 branch seals' --test-reporter=spec authoring/generation-session.test.mjs
node --test --test-concurrency=1 --test-reporter=spec --test-reporter-destination=C:/tmp/modelmirror-rpg-p1c-evidence/targeted-final.log --test-reporter=junit --test-reporter-destination=C:/tmp/modelmirror-rpg-p1c-evidence/targeted-final.xml authoring/generation-session.test.mjs
# 仓库根目录执行
 git diff --check
```

证据位于 C:/tmp/modelmirror-rpg-p1c-evidence：first.log/xml 保留初次断言失败；regression.log/xml 保留综合结果；targeted-final.log/xml 为最终新增验收；typed-session-capture.json 包含正式配置、四种任务原始参数／解析／请求 hash 与测试回执；static-checks.json、verification-summary.json、P1-C-HASHES.json 记录静态检查、保护检查和源码／证据 hash。

## 下一批

P2：创作原生参数界面、聊天生成参数插件、六卡及通用作品的真实宿主接线，补齐停用后的有效配置显示、预设交互和权限校验；二级交互继续按用户门禁先观察原站。当前 P1-C 不代表这些 UI 或聊天宿主已完成。先验收参数阶段，之后才进入 C1 多模型对比。真实调用和共享部署仍须对应授权。
