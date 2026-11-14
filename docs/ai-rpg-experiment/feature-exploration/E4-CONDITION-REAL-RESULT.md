# E4 条件逐字保留：单次真实补测结果

2026-11-14。用户授权新合同补测最多1次/$0.20、先模拟、不自动重试。实际新增真实调用1次，未调用其他模型；业务校验与本次目标通过，用户已确认本轮原型验收通过。原验收脚本的两个硬编码期望值遗漏句号，误报失败；原始判定和回复均保留，另附独立合同核对，不将其写成“所有原脚本检查通过”。

## 原始回复与业务行为

输入仍为：“改成赠送给周禾一份养息丹。他已经答应了。只拟方案，不要送出。”

Gemini返回单一完整json围栏，解析通过：
- action=gift，target=zhou，item=medicine，quantity=1。
- condition.value和quote均为“他已经答应了。”，含玩家原文句号，没有替换指代或概括。
- questions=[]，unsupported=false。

现有业务校验接受整批修订，当前草稿status=intent-only；旧确认记录为null，旧服药preflight为null。人物在场和同意仍unknown。giftEstimate仅条件估算，不是执行规则。实际养息丹仍5，完整源记录指纹前后一致，没有赠送或服药执行，没有把玩家陈述写为世界事实。

本次只复用此前保存并离线接受的服药提案，不重新调用首轮；当前原始回复没有被改写或自动修补。

## 验收脚本误报及独立核对

冻结脚本对rawVerbatim与claimOnly使用“他已经答应了”（无句号）做严格相等比较，因此输出两项false，runner报告E4_RETEST_SEMANTIC。业务操作自身operationStatus=completed、diagnostic=null，其余检查均true。

新合同要求摘录连续原文并保留标点，模型本次满足该要求。原始输入[13,20)正是“他已经答应了。”；condition.value等于quote且与该原文片段完全相同。独立condition-audit.mjs只读原证据并另存contract-audit.json，核对业务接受、正确赠送、旧预检/确认清除、状态不变及既有材料不变等13项。原始reply/proposal/review/assessment/draft/source记录均逐一核对未被改写。

未修改业务解析器或校验，也未重新调用。此处属于实测脚本的期望值错误，不是放宽规则来接受模型改写。以后新脚本应按合同允许的连续原文片段验证，不能硬编码省略原句标点的唯一字符串；本次冻结脚本保留作为证据。

## 模型与统计

模型google/gemini-3.8-flash，既有选定连接，Google AI Studio标准端点，fallback禁用。temperature=0.7、top_p=0.8、max_tokens=8192。

派发前重新核对端点报价（输入$0.75/M、输出/内部推理$3.75/M）和路由资格；独立预算上限1次/$0.20，不继承历史额度。

| 指标 | 本次 |
|---|---:|
| 真实调用 | 1 |
| 重试 | 0 |
| 输入token | 598 |
| 输出token（含推理） | 868 |
| 其中推理 | 701 |
| 总token | 1466 |
| SSE实报USD | 0.0037035 |
| 归一化微美元（向上取整） | 3704 |
| 首次文本观测 | 12.818秒 |
| 请求完成 | 13.695秒 |
| 草稿可用 | 13.829秒 |

首次文本为宿主约250ms轮询观测；本次没有剧情正文，不报告正文首字。推理已计入输出，不重复累加。历史费用与本次授权分开。

## 证据、验证与清理

- 完整模拟先通过，使用同一新合同、复用提案、预算、路由资格和桥接，生成由MockTransport拦截。模拟临时凭据清理后才真实派发。
- 单请求预算测试1项、专用Python协议3项通过。
- 页面9项检查通过，桌面和390px窄屏无脚本错误、外部请求或横向溢出；原判定和独立核对均可下载，刷新不改数据或新增调用。已查看桌面截图。
- 独立只读验收页 http://127.0.0.1:18913/ 。此前18889、18903页面及其材料不变。
- 模拟目录 .rpg03-work/e4-condition-check-mock-v1；真实目录 .rpg03-work/e4-condition-check-real-v1。
- 原始回复reply-1.txt指纹6e0eb2f6c2ba5d9ff3c682f46ee956e8315b21716186f65d3dd879f35c4a497d。
- 保存execution-claim、seed、request、reply、proposal、draft-record、stage-gift-1、transport-evidence、review、assessment、contract-audit、dispatch-audit、cleanup及前后source记录。browser/包含桌面、窄屏截图和验证结果。
- 新合同指纹748fd35e32393bef8afe1068450cd4dbb3ed21cca1c12034611b18c243c6003c；启动核对新冻结请求，同时重建旧合同文件指纹证明未改解析器/计算。没有放开旧实测指纹检查。
- 模拟和真实router密文凭据目录均删除，主密钥和服务令牌只驻留内存。18911/18912派发服务退出；18913仅无凭据只读页面。
- 仅新增E4实测编排/页面及文档，没有修改公共核心、共享配置或接正式执行、部署、Commit/Push/PR。

复验仅离线：
```text
node --test experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/condition-provider.test.mjs
python -m pytest experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/test_condition_protocol.py -q
node experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/condition-review-check.mjs
```

本次是固定样例的真实验证，不能推广为任意条件提取均稳定，也不代表赠送执行能力已实现。用户验收通过后仅整理文档和提交 PR，不追加补丁或调用；最终交接见 E4-CLOSEOUT.md。
