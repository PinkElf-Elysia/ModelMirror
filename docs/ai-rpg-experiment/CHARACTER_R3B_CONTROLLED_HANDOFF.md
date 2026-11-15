# R3b 受控生成、共享预算与候选交互交接

本补齐基于 `f31bf50ba99686abfb18431413fddd2da16e5648` 与上一轮 74 文件精确副本。工作树 `C:/tmp/mm-cr3b2`，分支 `codex/rpg-character-r3b-controlled`；原 `C:/tmp/mm-cr3b` 不变。本轮没有重新组合远端最新主线；这不是发布门禁报告。正式素材、会话和原验收数据未导入代码库。

## 已接通的边界

- `application.mjs` / `composition.mjs`：可信宿主组合入口。复用原 CharacterSessionHost、GroupHost、ControlPlaneModelControl 和两种 ControlledProvider；原单聊、群聊和 R3b 请求使用同一个 SharedBudgetGuard。无需修改旧 HTTP、消息、回执、版本或运行指纹合同。
- `controlled.mjs`：把修订请求映射到原 `character-messages/1` 或 `character-group-messages/1`，支持 authoring-task V1/V2。上下文、成员、前文哈希、来源 ID、模型参数在准备时固定；恢复不重新选择当前模型。完整 raw/SSE/receipt 留在本地修订证据内，length 或失败不能成为已完成候选。
- `shared-budget.mjs`：持久化任务预算护栏，作用域 `character-shared-budget-v1`。仅转交既有 loopback authoring 协议，不路由提供商、不持有模型密钥、不新增产品模型网关。不开放浏览器端预算写入或凭据参数。
- `context.mjs` / `sources.mjs` / `store.mjs`：可信固定模型注册表；原单聊从核验过的真实操作记录取得精确模型，而非从公开 DTO 推测。单次覆盖模型不改变成员默认模型，也不改原提示词。
- `browser.mjs` / `page.html` / `page.css` / `server.mjs`：按回复位置并列原回复与候选、单次模型选择、历史采用另开分支、恢复原回复、原始回执查看、TXT/Markdown 导出、任务共用余量、正文与分支标题草稿保护。查看回执后保持候选展开。没有原回复的新位置从“候选 1”编号。

## 可信配置接口

`startControlledWorkspace({directory, upstream, serviceToken, models, authorization, port, recoverDeadOwner, executionLabel})` 由宿主调用；浏览器不能构造这些参数。没有读取真实令牌的环境变量后门，也没有自动开启真实调用的 CLI。

- `upstream`：固定的 `http://127.0.0.1:<port>` 既有受控服务。
- `models`：冻结的 model choice（V1 或 V2）、上下文和输出上界、可信价格版本/输入输出单价/额外 framing 上界。缺价拒绝建立此金额硬上限上下文。
- `authorization`：`{id,calls,microUsd,initial:{calls,microUsd}}`，默认总量零。`initial` 是宿主核对后的旧消耗及全部预留，不能由网页更改。单个来源另有不可变上限，原群聊自身预算继续有效。
- 单聊模型列表只展示已核价选择；参数若不匹配冻结清单，保存前拒绝。R3b 可为本次请求选择其他已核验模型，默认沿用原模型。
- 本地私有鉴权只用于既有宿主传输。Host、Origin、CSRF、字段及回执验证仍执行；原 gateway 验证器未放宽。

真实服务启用前须人工核对调用上界、隐藏费用、当前路由授权及已经消耗/结果未知预留。不能把合成测试价格或 mock 的 40 次上限沿用于真实服务。

## 预算与恢复

1. 修订宿主落盘意图和族预算；原宿主仍按原合同落盘请求。
2. 共享护栏在同一任务账本原子占用一次调用和成本上界，然后仅派发一次 POST。原入口与候选入口争用同一任务额度。
3. 已知未派发失败只释放本次占用；此前预留不变。已派发但没有可信实际美元账单时，完成后仍保留保守成本占用，UI 不称为实际费用。
4. 超时/断连/未知保留次数和金额，阻止同源新请求。重复 intent、页面刷新、进程重启均不再次 POST。恢复只 GET 原 sessionId/requestId/sourceRequestHash。
5. 回执必须匹配源请求、模型、参数、原文/SSE 哈希及 V2 参数解析。完成回执重复采用只新增一条候选，不自动选中。
6. 准备阶段关闭：必须通过共享护栏原子写入“未派发取消”终态，证明不存在已占用/已派发记录；旧请求随后到达也只返回该拒绝结果。网关查无记录不能证明未派发，不能退款或重放。
7. 护栏重启复用已保存监听端口，维持旧 transport originHash。旧端口被占用时不能换端口后机械修改存档哈希。

任务范围内所有原入口必须使用这个组合入口；不能同时保留另一个绕过护栏、却声称共用本任务预算的宿主。其他任务、共享全局路由和既有授权不是由本模块自动接管；上游额度限制仍独立生效。

## 存档及证据

新增修订记录使用 `modelmirror.rpg.character-revisions/2`、`revision-ledger/2` 与 `character-revisions-v2` scope，独立登记运行源码和模型清单哈希。旧单角色、群聊、世界、R1/R3a 合同未因本次改动变更。

旧 R3b fixture v1 的树、记录和原报告完整保留；此版不迁移、不改写旧 revision 哈希，也不声称能在新指纹下直接打开旧修订数据。最终预览使用新建中性数据。完整群聊来源历史仍由原宿主核验，原群网关历史完整 transport evidence 仍依赖原目录；本轮没有交付脱离来源的通用独立证据包。

## 离线启动与回退

```powershell
node experiments/ai-rpg-engine/character/revisions/launch-controlled-mock.mjs C:/tmp/mm-cr3b2-data/new-preview 43180
```

该命令硬连本地 mock，采用合成 token 和价格，只种入原创中性夹具；不读取正式材料或真实授权。另一个预览仍运行时需使用新目录与其他端口。已有目录在源码漂移时必须拒绝，不能覆盖绑定。

回退停止本轮专用预览，使用旧树/旧入口；保留所有新数据与证据。不清理共享服务、旧树、历史材料或其他任务资源。

## 真实验收与下一轮前置条件

- 用户确认模型精确选择、调用次数/总美元预算、允许发送材料和停止条件；本轮真实调用为零。
- 只读核对现有路由、冻结价格及参数；把旧剩余与所有预留记入明确授权，不额外认证或补跑未知请求。
- 全部本任务原入口和 R3b 入口共用此护栏；用独立正式素材副本验证绑定，保留原会话与证据。
- 先核验未知/失败只查询、修改模型不会改变原请求，再进行另行授权的少量模型测试。模型叙事表现与系统合同验收分别报告。
- 若需共享上线，另做最新主线组合、审查与发布授权。当前无 Commit、Push、部署或调用授权。
- 原生文件选择器取消、真机软键盘/触控/系统返回仍延后待验；桌面/390 CSS px Playwright 不是设备验收。
