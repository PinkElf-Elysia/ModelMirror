# RPG 公共运行核心 · R3 原型

状态：默认关闭的独立原型。离线入口仍仅用模拟供应商；已获授权的有限真实接入位于 real/，不会由下方离线启动命令启用，不进入正式故事馆。

真实补测已完成且不得重跑执行脚本；只读验收页 http://127.0.0.1:18844/ 。详情见 [真实验证记录](../../../docs/ai-rpg-experiment/harness/R3-REAL-VALIDATION.md)。

从仓库根运行：

```powershell
node experiments/ai-rpg-engine/agent-core/server.mjs --offline-preview --port 18841
```

打开 http://127.0.0.1:18841/ 。数据保存在本树
`experiments/ai-rpg-engine/.rpg03-work/agent-core-r3/`。端口占用会失败，不停止其他服务。
不传 `--offline-preview` 时启动在创建存储前拒绝。

## 实际复用边界

`composition.mjs` 静态登记两个可信适配器，注入相同的 `CoreHost` 和 `CoreStore`。
核心不依赖具体卡片、角色字段或修仙 schema。存储直接使用已有 `SessionRecords`；
旧 local-state Host/Store 不参与新链路。

- `contracts.mjs`：有限 JSON、绑定和端口验证。
- `arithmetic.mjs`：安全整数、未知初值、拒绝或显式钳位。两适配器都实际调用。
- `store.mjs`：新格式和 scope；正文、状态、事件、回执一次提交；改写、分支、幂等、CAS。
- `host.mjs`：同一受限回合与恢复函数；普通回复一次，原生查询后至多一次续写。
- `query-protocol.mjs`：可信查询定义注入，保留 assistant/tool ID 及不透明 metadata。
- `simulator.mjs`：唯一预览供应商，固定样例和故障注入；无网络派发实现。
- `adapters/*`：schema、语义规则、上下文、只读查询、表现及中性样例。
- `manifest.mjs`：运行入口静态依赖与声明资源的内容摘要；存档绑定启动时 manifest。
  锁文件记录依赖版本，不声称这是第三方代码沙箱或整个 node_modules 的完整性审计。

适配端口：validateState/validateConfig、initial、assemble、candidate、query、render/display、
memory、ownership；候选返回 body/state/changes，不直接落盘。初始化默认确认样例，禁止旧存档自动导入。
供应商端口由离线实现提供 generate/lookup/close、options 和离线 preview；real/provider.mjs 经显式 controlledReal 准入使用共享预算、冻结槽位和既有路由，已完成有限真实验证。旧桥接类不能直接替入便宣称可用。

## 卡片差异

修仙沿用原 schema、有效提示词、世界书、Mod、自定义配置、记忆来源及安全面板。
迁接语义操作，数量／账户／关系计算使用公共助手；新旧规则通过差分检查。
strictRelations、quantityOnly 仍是修仙适配策略；上下文选项放在
`options.contextOptions`，例如 `{"quantityOnly":{"id":"medicine","delta":-2}}`。
无新成长曲线、兑换或恢复公式，定性判断仍属模型。

灯塔为原创成年测试配置，根字段只有 keeper/supplies/duty。消耗灯油、更新值守说明、
查询登记物资；绷带初值 null，不当作零。它不是第二张正式作品卡。

## 页面验收

1. 选择配置，展开初值并确认，开局。
2. 选择“先查询…两份”，执行后观察数量 5→3、两阶段、只读查询依据。
3. 刷新；状态不再扣减。普通交流只需一次模拟生成。
4. 设置“最终回执丢失”，执行后状态不提交；核对原请求才发布保存的结果。
5. 候选预览不入账；采用一次。明确失败可保留记录结束；未知不能跳过。
6. 在高级面板填写改写原因，审阅差异再确认；按检查点创建独立分支。
7. 停用保留资产和读恢复能力。可下载当前会话的离线原始请求、结果和记忆投影。

模拟器只匹配列出的动作，不承担自然语言理解。存档绑定漂移会拒绝加载，不删数据或放松校验。
UI 投影失败不重新生成或计算。恢复验证覆盖进程中断，未承诺任意断电持久性。

## 验证与后续

```powershell
$tests = Get-ChildItem experiments/ai-rpg-engine/agent-core -Recurse -Filter '*.test.mjs' | ForEach-Object FullName
node --test --test-concurrency=4 @tests
node experiments/ai-rpg-engine/agent-core/measure.mjs
```

详见仓库文档 `docs/ai-rpg-experiment/harness/R3-ACCEPTANCE.md`。
此轮不实现角色私有认知、工具注册平台、联网、地图、备用模型、多 Agent 或小说编辑。
第四轮继续上下文与生成职责分配；第三轮不承诺 token／费用降低。
