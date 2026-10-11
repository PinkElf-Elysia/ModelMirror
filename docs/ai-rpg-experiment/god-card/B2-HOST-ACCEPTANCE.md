# B2 — 第二卡同宿主会话与受控线证据

日期2026-10-09；工作区 C:/tmp/modelmirror-rpg-god-card，分支 codex/rpg-god-card，基线 e5a808a60b024635a1e13965558f15775c5af86a。
用户已明确通过 B1。B2 当前完成离线/API 候选，待用户确认进入 B3；没有把后台接入视为第二卡完整竖切通过。

## 最终实现

- Studio start 增加默认关闭的 godCard 选项，CLI 对应 RPG_GOD_CARD_ENABLED=true。关闭时不加载 God 资源，原宿主无需新卡文件即可启动。
- 开启后同一 HTTP 宿主提供 /rpg-app/god/api/status、sessions、sessions/:id、send、cancel；复用原同源/JSON/请求大小检查。
- 新的 studio/card-session-store.mjs 继承原 VersionedSessionStore，复用会话读写/枚举、版本校验、锁、取消控制和分支存储能力；新的发送边界由服务端卡片适配器决定配置与装配。God 使用原子落盘。不是复制一套独立 Earth 宿主。
- god-card/lib/adapter.mjs 接受 setup+mode，只允许offline；服务器验证并序列化 B1 天书。world=null、params={}，不虚构表/里世界或继承 Earth 模型参数。天书长度最多20000 UTF-16代码单元，与现有发送边界一致；超限拒绝，不截断。
- 首轮必须发送服务器返回的完整 characterText，且只出现一次；后续保留原始历史与原输入。不加入 Earth 前后词、旧主持词或工程回执。
- 模型原文和 hash 保存；输出展示复用原 safeHtml，脚本及远端图片不会进入安全HTML。B2没有新建聊天前端或进行输出面板可见验收。
- 受控 Provider 增加服务端 cardId 命名空间（仅earth/god），默认仍为earth。God 假测试记录落入 god/slot-*，不借用或重置Earth预算。
- 插件实例生命周期仍复用现有服务。God 会话可被正确识别，但未通过 B3 的插件仍返回409 PLUGIN_CARD_INCOMPATIBLE；没有改任何插件 manifest 的兼容清单。
- 新运行描述绑定官方文本、开局序列化、派生宿主、HTTP入口及传输源码 hash。真实模型和参数未选定，运行描述明确offline-only。
- 所有请求字段由服务端校验；客户端传system、history、世界类型、params或real模式被拒绝。未知结果/最终落盘失败保留pending，重启不自动重放，换ID也被阻断；本批不提供人工收束未知状态的UI。

## 原始发现、失败与修复

1. 起初在 card-replica/lib/store.mjs 与 studio/versioned-store.mjs 增加可选适配器接缝，窄测试21项通过。
2. 双卡HTTP测试首次错误地期待插件返回 compatible=false；现有合同实际是409拒绝。依据源码修正为精确错误码后，15项受控测试通过。未放松产品拒绝检查。
3. 完整回归首次290通过、1失败、1跳过：基础文件变动使旧 Earth v1 运行指纹变化。用 Git HEAD 原字节在内存重建，证明基线指纹等于 LEGACY_RUNTIME_HASH，故这是本批引入的回归。
4. 已仅撤回上述两个基础文件的本轮修改，保持其与 HEAD 逐字节一致；新卡改用派生宿主。未修改旧指纹、测试常量或重签旧会话。失败的原测试重新通过。
5. 最终全面回归293项：292通过、0失败、1跳过。跳过项 model-legacy-copy.test.mjs 依赖未提供的 RPG_B3_LEGACY_COPY 外部真实副本；不能视为真实旧会话迁移通过。

## 可核对验证

命令（仓库根目录）：

```powershell
node --test experiments/ai-rpg-engine/god-card/tests/*.test.mjs experiments/ai-rpg-engine/card-replica/tests/*.test.mjs experiments/ai-rpg-engine/studio/*.test.mjs experiments/ai-rpg-engine/plugins/*.test.mjs
git diff --check
```

- 基线窄回归52项通过。
- 最终上述回归292通过，包含God16项、既有Earth与插件宿主测试；1项环境依赖跳过。
- 实际回环假控制面收到首轮和续轮；完整消息/回执/原文均已落盘。断言系统逐字一致、天书不重复、续轮无外加前后词；同宿主 Earth 对照仍使用原首轮角色资料和续轮官方前后词。
- 假控制面在返回前检查 request.json 已落盘。两个God测试槽不会消耗Earth模拟槽；同请求ID不重复派发；异卡会话ID读取404。
- God原输出中的脚本/远端图片在raw保留，在html投影移除。
- 新B2实际进程完成一份固定离线样例，恢复查询revision=1，真实used=0/limit=0；与假控制面证据分别记录。
- 本批无前端代码变化，未重做浏览器验收/截图或生产构建。B1界面人工通过记录保留；B3进行插件、聊天界面及构建验收。

本地忽略证据目录：
- .rpg04-work/god-card/b2-evidence/regression.txt：完整最终测试输出
- .rpg04-work/god-card/b2-evidence/live-offline-smoke.json：运行实例固定样例回执
- .rpg04-work/god-card/b2-http/wire-*/wire-assertions.json：通过的双卡实际请求断言及完整消息
- 同目录 test-dispatches/god/slot-*/：假Provider预留、请求、响应及回执
所有 synthetic/model、token、参数均为离线测试夹具，不是本卡获批的真实模型或参数，也不是付费账本。

## 状态分层和剩余门禁

| 层次 | 结果 |
|---|---|
| B1作者UI | 用户已通过；18505继续保留 |
| B2离线/API | 通过上述验收 |
| God跨卡插件 | 未运行；B3仍拒绝启用 |
| 真实Provider与内容质量 | 未运行；额度0；不得继承旧授权 |
| 新聊天前端/Studio入口/生产构建 | 待B3 |
| Commit/Push/PR/共享部署 | 未发生 |

## 操作、兼容和回退

B2独立API： http://127.0.0.1:18507/rpg-app/god/api/status ，仅回环监听，真实调用关闭。启动器/日志/PID位于 .rpg04-work/god-card/b2-preview/；会话在其data/god目录。
B1界面仍为 http://127.0.0.1:18505/ ，本批没有将其连接到聊天API，避免将未完成聊天UI伪装成已经验收。

旧 Earth v1 源文件指纹与续玩门禁已保留。较新宿主的源码绑定包含受控传输，升级此新版本时旧现代运行绑定可能被判为只读；不能直接升级旧数据目录。本批未碰任何原验收入口、旧会话或共享栈，后续发布须继续用副本验证。
回退优先关闭 godCard；只停止本轮18507自有实例，先核对PID命令行。保留会话、源码快照、原件与全部失败/成功证据，不删除预算或旧数据。
