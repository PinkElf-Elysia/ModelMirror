# B2 开工契约

用户已确认 B1 界面验收通过，授权进入 B2。基线 e5a808a60b024635a1e13965558f15775c5af86a，分支 codex/rpg-god-card。
已有 B0/B1 两个未跟踪目录保留；无其他修改。原18505预览继续保留。

范围：experiments/ai-rpg-engine 与 docs/ai-rpg-experiment 内必要共享宿主接缝及新卡适配。
不改作者 system、Earth 文本、旧RPG01–05合同、父仓client/server、共享栈、现有验收数据。无真实预算、提交或发布权限。
B2目标：同一宿主提供 god 会话创建/发送/取消/历史恢复，服务器生成开局文本；保留原文与安全展示分离；捕获受控假 Provider 的实际请求，验证 Earth 对照路径。
B3才验证并开放分支/模型/M1/M2/M3；本批插件目录继续明确不兼容 god，不伪造通过。

只对新数据目录运行。持久数据风险：共享 store 文件进入 Earth 运行指纹，新宿主旧指纹会话可能只读；不自动追认或迁移。本轮不升级现有服务。
接口增量：独立 /rpg-app/god/api/*，现有 origin 检查前置；创建仅收setup+offline模式，发送仅收input/requestId/revision。首轮输入必须与服务器生成的天书逐字相同，续轮原输入；不接受客户端system/history/provider配置。
真实模型与参数尚未冻结，本批实际入口仅offline，假控制面模型/参数仅为测试夹具。

小批步骤：
1. 可选服务端卡片适配器接缝、god运行绑定、存储测试（最多5文件）。
2. 同宿主路由与受控传输卡片命名空间、HTTP双卡线证据（最多5文件）。
3. B2说明、状态、当前hash（最多5文件）。

基线测试：node --test card-replica/tests/*.test.mjs studio/studio.test.mjs studio/branches.test.mjs studio/controlled-provider.test.mjs（仓库相应绝对路径），52项通过。
验收：新卡测试；上述回归；受影响plugins与studio全体离线测试。
回退：停止本轮自有服务；撤回适配器接缝和新卡路由，保留新卡会话、原件及证据；旧服务和共享栈不变。

Final adjustment: legacy base store edits were reverted after a verified v1 hash regression. The final CardSessionStore derives from the unchanged VersionedSessionStore; legacy constants and assertions are unchanged. See B2-HOST-ACCEPTANCE.md for failure and recovery evidence.
