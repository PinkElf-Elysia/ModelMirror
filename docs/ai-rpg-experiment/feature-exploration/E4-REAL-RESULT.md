# E4 Gemini 最小实测结果

2026-11-14。离线原型已获用户验收；本次真实补测停在首请求格式校验，尚未完成连续两请求验收。没有自动重试、公共核心修改、共享配置修改、正式执行、部署或提交。

## 授权与派发

用户授权 E4 专用实测接入、最多 2 次请求、每次 $0.20、总计 $0.40，先完整模拟，必要凭据仅加密暂存于任务目录并于结束清理。

模型 google/gemini-3.8-flash，既有选定连接，Google AI Studio 标准端点，禁止路由 fallback。参数 temperature=0.7、top_p=0.8、max_tokens=8192。派发前读取端点报价，输入 $0.75/M token、输出/内部推理 $3.75/M token。预算使用既有 NativeBridgeProvider 保守字节上界与最大输出预留，并保存报价指纹；不是把估算当作实报。

真实目录 .rpg03-work/e4-context-real-v1；完整模拟目录 .rpg03-work/e4-context-mock-v1。实测前模拟器通过同一桥接、安全出站、路由资格、预算及记录路径，供应商生成由本地 MockTransport 替代。源记录是本任务新建的 R5 成年测试快照，养息丹为 5 份，不是共享存档。

## 请求与实际结果

1. “我想服用两份养息丹，先拟方案，不要执行。” 已派发。供应商成功结束，回复含 Markdown JSON 代码围栏。内部字段为 use_medicine、medicine、quantity=2，引用对应原文，questions=[]、unsupported=false。E4 的 JSON.parse 对完整原始回复报 E4_REPLY_JSON，整条提案未接受，没有真实规则预检，也没有意图接受。
2. “改成赠送给周禾一份养息丹。他已经答应了。只拟方案，不要送出。” **未派发**。第一条未通过，因此按冻结计划停止；没有去掉围栏回填草稿，没有用模拟草稿替代，没有耗用剩余次数。

源记录前后指纹一致，实际养息丹仍为 5，所有其他字段不变。没有账本写入或人物事实写入。模拟链路已验证服药预检 5→3、改为 gift/数量1 后 preflight=null、旧接受失效、人物条件仍 unknown；这些不能当作 Gemini 连续修订通过。

格式诊断单独保存在 assessment.json：仅离线读取围栏内 JSON 说明原因，不参与草稿、预检或派发。rawAction 等自动检查在解析失败时为 false，不能据此断言模型把服药识别错。

## 用量与时间

| 项目 | 请求 1 |
|---|---:|
| 输入 token | 414 |
| 输出 token | 361 |
| 输出中的推理 token | 258 |
| 总 token | 775 |
| SSE usage.cost 实报 USD | 0.00166425 |
| 归一化微美元（向上取整） | 1665 |
| 首次文本观测 | 6.960 秒 |
| 请求完成 | 7.560 秒 |
| 草稿处理完成（本次失败） | 7.680 秒 |

推理包含在输出中，不重复加总。首次文本来自约 250ms 的宿主进度观测；本次没有剧情正文，不报告“正文首字”。真实派发 1 次、重试 0 次、实际费用低于单次及总额上限。预留 $0.03308925 不是实际费用。

## 保存材料与验收

只读页面 http://127.0.0.1:18889/ 。原离线交互页 http://127.0.0.1:18886/ 保留。

真实目录保存 execution-claim.json、request-1.json、reply-1.txt、proposal-1.json、stage-proposal-1.json、draft-record.json、source-state.json、source-after.json、transport-evidence.json、review.json、assessment.json、dispatch-audit.json、cleanup.json。mock-reference.json 单独标明模拟预检和差异。完整原始 SSE、供应商回执及失败回复都未改写。

E4 草稿存储中的旧请求辅助元数据仍来自离线 build（provider/realCalls 字段不用于计量）；实测冻结 request-1.json 已明确模式和提供者，实际调用数以 transport-evidence、dispatch-audit 和 assessment 为准。未改公共装配核心。

验收页刷新和下载均只读，白名单中不含凭据目录。browser/result.json、desktop.png、mobile.png 保存 9 项页面检查，无脚本错误、外部请求或窄屏横向溢出；刷新前后源状态和证据一致。已查看桌面截图。

## 验证和清理

- Node：E4 原有 12 项 + 实测预算 2 项，共 14 项通过。
- Python：E4 专用文本协议 3 项通过。
- 完整模拟：两个请求均通过同一实际宿主派发链路；不计作真实生成。
- 初次模拟启动前发现新副本误把 sqlite3 替换为 sqlite4，导入阶段即失败；修正后通过导入和完整模拟。该次未捕获凭据或派发供应商。
- 模拟、真实临时 router 目录均删除；主密钥与本地服务令牌只驻留进程内存，没有落盘。
- 真实桥接退出，保留的 18889 仅是无凭据只读页面。
- 未修改 E4 原有解释合同、解析器或 R5 执行规则；未做失败后的补丁或追加模型调用。

复验（不派发模型）：
```text
node --test experiments/ai-rpg-engine/feature-exploration/context-actions-e4/*.test.mjs experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/provider.test.mjs
python -m pytest experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/test_protocol.py -q
node experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/review-check.mjs
```

后续建议：仅针对单一完整 JSON 围栏讨论窄范围格式兼容，或强化纯 JSON 输出合同；不以宽松提取任意正文中的 JSON 代替验证。先离线验证，再确认是否补测；本报告不自动授权修改或新增调用。连续修订、赠送不误转服药及玩家陈述边界的真实效果仍未验证。
