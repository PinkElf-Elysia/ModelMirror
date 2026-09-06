# RPG-03 最小受信插件宿主

03G1/G2 已实现并通过离线门禁：宿主 9/9，运行合同、存储与核心 53/53。统计以 `RPG03_STATUS.json`、`RPG03_ACCEPTANCE.md` 为准；本文不是插件市场、外部代码执行隔离或真实服务资格证明。

## 身份、授权与启用

`createPluginHost({hash})` 使用同步 SHA-256 端口，默认注册表为空。`register({manifest,manifestSha256,artifactSha256,adapter})` 只接受调用方直接提供的受信适配器；核对原 0.1.0 manifest、精确版本、规范 manifest hash 及声明的 artifact hash，不读取或加载卡片入口。这里的 artifact hash 是受信交付绑定，不声称通过函数源码重建了该制品。重复注册 ID 被拒绝，不支持隐式替换或升级。

`checkAuthorization(record)` 核对显式授权与 manifest 的权限、数据范围及设置。`enable(record)` 是独立的操作意图，不能由注册或卡片需求隐含触发；依赖需逐个明确启用。`disable({sessionId,pluginId})` 使已有调用和结果失效。模型、长期记忆、网络和 UI 服务门面均返回 unavailable，声明某项权限不能提供尚未接入的服务。

授权记录绑定会话、卡包和玩家 hash、精确插件版本、manifest/artifact hash、revision 与真实/模拟类别。会话内授权历史按 revision 非降序排列，同一插件的 revision 严格递增；唯一最新记录决定授权是否仍有效。创建会话时允许调用方提供显式 revision 0 初始授权，每个插件最多一条。后续授权和撤销通过运行实例的 CAS 接口持久化；保存失败不能授予新权限，保存成功也不会自动启用。

`setPluginAuthorization({sessionId,expectedRevision,authorization})` 在该会话 active 时拒绝；授权记录类别必须匹配运行实例。CAS 已成功而宿主 disable 失败时，记录保留、实例 faulted。此时 read 可返回磁盘证据和警告，不能解除故障；需修复宿主并显式 resume。无注册信息时仍可撤销已有同版本/hash 的授权，不能借撤销授予新能力。

必需插件的首次启动顺序为：准备明确授权记录 → 注册受信适配器 → 显式 enable → 携带同一记录创建会话。重启后先 `readSession` 读取和核对持久证据，再注册并显式启用仍有效的授权，最后 `resumeSession`。读取本身不执行插件 readiness，避免无法取出授权记录的恢复死锁；恢复和生成仍执行就绪门禁。仅有 manifest 或授权记录不等于插件已启用。

## 数据与候选结果

`invoke({pluginId,capability,session,cardPackage,playerSetup,turnInput?,turnProposal?,timeoutMs?})` 校验资源绑定、当前授权、启用记录和依赖，只将已授权 read 范围的副本交给适配器。sessionMetadata 仅含逻辑引用、hash 与 revision，不包含整个历史。设置也使用独立副本。

适配器输出仅允许 `{proposals:[{scope,content}]}`：scope 为已授权的 context、state 或 informationModule，content 是不可信短文本。最多 64 项、单项 65536 字符、总计 262144 字符。这些候选不会自动改状态、组装提示词或渲染 UI；文本中的 root、HTML 或指令不会成为运行权限。

宿主为候选附上会话 revision、资源 hash、插件版本、能力、授权 hash 和启用 epoch。调用结束时重新核对启用及依赖，消费者还必须用 `validateResult(value,session)` 对当前会话复核。撤权、停用、重新启用或会话推进后，旧结果不能继续使用。

## 故障与限制

同一会话同一插件最多一个在途调用；超时默认 1000 ms，可明确设为 1～5000 ms。异常、超时、依赖停用和迟到结果使用稳定诊断，取消信号仅是协作通知。无响应适配器不能将迟到结果写回核心。

适配器属于同进程受信代码：本宿主不是安全沙箱，无法撤回已经发生的外部副作用，也不能抢占同步阻塞的 JavaScript。不得将卡片或 UGC 源码放进这些受信绑定。外部插件执行隔离和市场属于后续路线。

零插件卡直接运行；必需插件不可用时阻断；推荐插件按原声明的 core、omit 或 readOnly 降级。降级只描述可用能力，不自动实现缺失的记忆、检索、规则或 UI 服务。
