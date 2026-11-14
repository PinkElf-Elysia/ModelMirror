# 原生工具独立环境：路由修复与完整模拟验证

## 结论与根因

本次只完成环境修复与模拟派发，没有真实模型调用，费用 USD 0；没有修改共享配置、降低安全校验或部署。

回归测试已复现直接原因：旧隔离脚本删除了排在 Gemini 前面的路由，保留 Gemini 的数据库 position=1。运行时对剩余路由重新枚举得到 position=0，原子派发校验因此拒绝。目录 available=true 无法发现这一不一致。此前“缺少其他模型资格导致失败”的诊断只是初步推断，现由本次证据更正。

修复保留原有完整路由顺序和引用。未选连接只保留公开描述、禁用并清空凭据；仅选定 Gemini 连接保留已授权的加密凭据及其资格、认证和目录依赖。没有补造认证或绕过版本校验。模拟账本证明两条本次请求均以原 position=1 成功派发到模拟供应商。

DNS 沿用任务进程 resolver：容器只读解析，原 ProviderEgressPolicy 校验并固定公网地址，保留 Host/SNI/TLS 和空内网白名单。不修改系统 DNS、hosts、共享容器或路由配置。

## 完整链路证据

实际执行 Node ToolsHost → NativeBridgeProvider → 本地 HTTP → RPG 原生工具桥接 → 原路由选择及派发校验 → MockTransport → 原生查询 → 续写 → 原子状态提交。

- 第一次 auto 返回分片函数参数；查询已登记丹药及功法。
- 第二次 none 保留对应调用编号、查询结果及不透明签名，返回正文和增量。
- 丹药 5→3，只有一个完成回合；正式后续历史不带原始工具协议。
- 重复提交与恢复不再次生成或结算；第三个请求在预算检查处被阻止。
- 两条回执 complete/dispatched=true，此处 dispatched 仅指派发到模拟传输层。
- 模拟阶段完成时间约 1.984 秒、1.873 秒；每阶段 100 输入/20 输出 token 是固定模拟值，不能作为 Gemini 性能、用量或计费证据。正文首字未测得。
- 冻结首请求指纹：b05a79f54058f226a9ef38a16dc374b5c6200c118a370a88e1d3b7fef2c6bf82。

证据目录（非跟踪）：`experiments/ai-rpg-engine/.rpg03-work/tools-isolation-mock/`。
`verification.json` 保存逐项结果；`dispatch-audit.json` 只列本次两条请求及无密钥的连接摘要；`mock-outbound.json` 保存两阶段消息；`session.json`、`transport-evidence.json` 保存状态与阶段记录；`cleanup.json` 保存凭据清理结果。

模拟桥接已停止，加密 router 目录与 local-auth.json 已删除，主密钥仅在已退出进程的内存中使用，没有落盘。回复、工具调用和统计保留。

## 代码与验证

- `local-state/tools/isolated_dns.py`：任务级 DNS 适配。
- `local-state/tools/isolated_mock_bridge.py`：完整隔离依赖快照及强制模拟传输；必须显式 --mock-only，不提供真实调用模式。
- `local-state/tools/verify-isolation.mjs`：同一冻结输入的跨进程全链路验收。
- `server/tests/test_rpg_isolated_route.py`：先复现序号缺口，再验证完整顺序及无其他连接凭据的修复。

上述工具是本任务验收脚本，依赖本机只读来源及历史冻结的公开端点能力证据，不是通用部署命令。现有运行目录保留证据，不覆盖旧记录。真实准备时须重新确认公开报价和能力，不能把历史缓存视为实时证明。

运行命令（仓库根；pytest 的 PYTHONPATH 使用本任务已有 .rpg03-work/pydeps）：

```text
python -m pytest server/tests/test_rpg_isolated_route.py server/tests/test_rpg_local_state_tools.py server/tests/test_rpg_bridge.py -q --tb=short --basetemp C:/tmp/lsiso-final-7
node experiments/ai-rpg-engine/local-state/tools/verify-isolation.mjs
```

验证结果：上述 Python 回归 **75 项通过**（215.99 秒，4 条既有 FastAPI 生命周期弃用警告）；跨进程模拟验收通过。新增脚本语法检查、`git diff --check` 和凭据字面量检查通过；暂存区为空。未运行全项目测试，未修改正式前端。

## 最小真实补测方案：待确认

仅一个玩家回合，Gemini `google/gemini-3.8-flash`，沿用选定 OpenRouter 连接并限定 Google AI Studio 标准端点，不允许供应商回退。

| 请求 | 输入与目的 | 上限 |
| --- | --- | --- |
| 1 | 原成年中性开局；核对全部已登记丹药与功法，然后服用两份现有丹药；auto，验证自主查询 | 1 次，USD 0.60 |
| 2 | 仅在首个响应为合法查询时，回传实际查询结果及原签名；none，验证正文、增量与丹药 5→3 | 1 次，USD 0.60 |

合计最多 2 次、USD 1.20，不自动重试、不为达到预期补发。直接回复、非法查询、失败或未知均保留原结果；未知先核对，不继续派发。沿用 temperature=0.7、top_p=0.8、max_tokens=32768；实际执行前用新报价和完整请求核算保守上限，不能满足则不调用。

正式调用前用同一快照准备规则核对 DNS、选定路由/资格/端点和预算，并先在模拟传输下检查完整派发。新授权只用于真实两阶段，不增加认证调用。保存原始 SSE、工具消息、状态前后、逐阶段 token/费用/时间，完成后清理临时凭据，提供独立只读验收页。不部署正式前端。

已排除本次已知 DNS 和路由序号阻塞；模拟不能保证将来供应商网络、资格时效和 Gemini 协议行为。仍待真实验证的重点是模型主动查询、真实签名续传和正文/状态一致性。
