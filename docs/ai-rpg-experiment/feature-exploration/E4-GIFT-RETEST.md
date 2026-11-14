# E4 单次赠送修订真实补测

2026-11-14。用户授权最多1次、费用不超过$0.20、不自动重试，复用已保存回复形成的提案，验证赠送修订及状态不变。新增真实请求已完成1次，无额外调用；原型未修改公共核心、共享配置，没有正式执行或部署。

## 结果

围栏兼容修复通过本次真实返回验证。Gemini原文为完整json代码围栏，内部action=gift、target=zhou、item=medicine、quantity=1，引用分别为周禾、养息丹、一份。模型没有把赠送转成服药。

条件项为 value=“周禾已经答应”、quote=“他已经答应了”。现有E3复用校验要求condition.value逐字出现在当前玩家输入；该值是改写，触发INTENT_CONDITION_EVIDENCE。**整批修订未采用**，不能把原始字段识别正确说成完整赠送链路通过。

宿主保留旧服药草稿及其预检，旧接受记录已失效；页面明确标注这是失败后的旧版保留值，不是将新赠送意图转换为服药。没有把答应写为世界事实，没有赠送执行规则或行动执行。源记录全量指纹不变，实际养息丹5→5，其余状态不变。既有真实回复、失败记录和离线回放材料指纹均不变。

本次没有修改条件校验、自动修正回复或追加调用。后续可以讨论在E4输出合同中明确condition必须摘录玩家原文；不要因为这次失败直接放宽公共核心或人物事实边界。是否继续优化由用户决定。

## 输入和复用依据

本次唯一输入：
> 改成赠送给周禾一份养息丹。他已经答应了。只拟方案，不要送出。

前置草稿来自 .rpg03-work/e4-fence-replay-v1/accepted-draft.json。该草稿由原始Gemini服药回复离线回放得到，预检5→3，只接受意图，不执行。补测先核对原始回复指纹、解析器指纹、原始源记录绑定及冻结请求消息，复制独立草稿记录后才派发；没有重新生成首轮。

## 模型、额度和统计

- 模型 google/gemini-3.8-flash，选定连接，Google AI Studio标准端点，禁止fallback。
- temperature=0.7、top_p=0.8、max_tokens=8192。
- 派发前重新读取公开端点报价：输入$0.75/M、输出/内部推理$3.75/M；保守预留$0.03343125低于$0.20，预留不是实际费用。
- 独立单请求协议只允许gift-1；预算最多1次/$0.20，不沿用原2次额度。未知不重发。

| 统计 | 本次 |
|---|---:|
| 真实派发 | 1 |
| 自动重试 | 0 |
| 输入token | 510 |
| 输出token（含推理） | 590 |
| 其中推理token | 423 |
| 总token | 1100 |
| SSE实报费用USD | 0.002595 |
| 首次文本观测 | 9.802秒 |
| 请求完成 | 10.742秒 |

首次文本为约250ms宿主轮询观测，不是正文首字；本次只有结构化提案。历史首请求$0.00166425单独保留，不计入本次额度；两次真实请求历史累计$0.00425925，但没有完成两条均成功的连续真实链路。

## 验证、材料和清理

先通过单请求预算测试1项、专用Python协议测试3项，再完成完整模拟派发。模拟供应商返回完整json围栏，真实宿主链路验证gift/数量1/对象zhou、旧接受失效、preflight清除、世界条件unknown及源记录不变。模拟的全部通过不替代真实的条件校验失败。

- 模拟目录 .rpg03-work/e4-gift-retest-mock-v1。
- 真实目录 .rpg03-work/e4-gift-retest-real-v1。
- 原始回复 reply-1.txt；指纹5ad21334c9d935c4b2238717d27d1f254b88aac8d657de91a775053431a5a721。
- execution-claim.json、seed.json、request-1.json、proposal-1.json、draft-record.json、transport-evidence.json、stage-gift-1.json、review.json、assessment.json、dispatch-audit.json、cleanup.json及前后源记录均保留。前后实际字段diff为空是整批未采用；rawValue单独保存模型提出的变更。
- 临时router密文凭据目录在模拟和真实两次宿主运行后均已删除；主密钥和服务令牌只驻留进程内存。18901/18902派发服务已退出。
- 独立只读页 http://127.0.0.1:18903/ ，旧18889失败页面保持原样。
- 验收页9项检查通过，无外部请求、脚本异常或窄屏横向溢出；刷新与下载不改变源记录或调用计数。截图browser/desktop.png、mobile.png及result.json已保存，已查看桌面截图。

复验（仅离线）：
```text
node --test experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/retest-provider.test.mjs
python -m pytest experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/test_retest_protocol.py -q
node experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/retest-review-check.mjs
```

本次只新增E4/real下的单次补测编排与只读页面，未改变E4业务解析器或公共核心。真实结果等待用户验收；不把剩余额度视为自动重试授权。
