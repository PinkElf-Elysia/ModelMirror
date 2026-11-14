> 当前状态：用户已通过 E2 原型验收并授权提交 PR。本文保留阶段记录，最新结果与对接边界见 [E2-CLOSEOUT.md](E2-CLOSEOUT.md) 和 [E2-HANDOFF.md](E2-HANDOFF.md)。不再增加补丁或真实调用，不部署。
# E2 歧义修复：单次 Gemini 真实补测

状态：已完成真实补测，等待用户验收；结构化目标通过，文字残余保留，不追加调用或补丁。

## 授权与派发

最多 1 次、单次/合计上限 $0.20、不自动重试。实际经既有认证路由调用 google/gemini-3.8-flash，Google AI Studio 标准端点，供应商回退关闭。
参数 temperature=0.7、top_p=0.8、max_tokens=8192、tools=[]、tool_choice=none。
输入：“帮我给那人一些丹药，先想想怎么安排，暂时不要动。”
同一 R4 历史状态副本，没有预选对象、没有旧模型回复。

先完整模拟派发通过，再真实调用 1 次，provider_chat_run succeeded。
本次预留 $0.119346；原始材料独立存放，不重用前次两次授权或运行目录。

## 原始回复结果

- refs、requirements、steps 都为空，主动询问对象、丹药种类、数量。
- 没有默认绑定周禾、数量1或容器关系，没有声称已执行。
- 本地 status=clarification、expected=[]、intentDiagnostics=[]、hostIntervened=false：未靠本地清空才通过。
- 实际丹药 3→3，全部源状态未变，stateRevision=1。
- questions 中的周禾、养息丹是举例，没有进入绑定。

残余：goal 将对象收窄为“同门”，limitations 写“对方与你初识，关系与信任尚浅”。对象未确认，不能证明这适用于最终对象；这是缺少依据的文字假设。记录后续优化，不将本次结构检查通过等同于所有语义推断已解决。

## 统计

| 项目 | 结果 |
|---|---:|
| 输入 token | 33,877 |
| 输出 token（包含推理） | 732 |
| 其中推理 | 585 |
| 总 token | 34,609 |
| 缓存 token | 0 |
| 供应商实报 USD | 0.02815275 |
| 冻结报价估算 USD（微美元向上取整） | 0.028153 |
| 首次输出 | 11.66 秒 |
| 回复完成 | 12.95 秒 |
| 提案可读 | 12.96 秒 |
| 真实请求 / 重试 | 1 / 0 |

首次输出是宿主约 250ms 轮询观测，JSON 提案不报告叙事正文首字。单次样例不证明长期稳定性。

## 证据与验收

非跟踪目录：
- experiments/ai-rpg-engine/.rpg03-work/e2-proposal-retest-mock-v1/
- experiments/ai-rpg-engine/.rpg03-work/e2-proposal-retest-real-v1/

真实目录包含 execution-claim、request-1、reply-1.txt、proposal-1（原始 JSON 与宿主结果分开）、stage-proposal-1、transport-evidence、review、assessment、cleanup、dispatch-audit、browser-review，以及桌面/手机截图。报价和源代码指纹已冻结。

源 SHA256 仍为 2f7104d5f41f21f8591b02a59434e8f147693f7bf560708c90648e245c939e77。
两个临时 router/ 凭据目录均已删除；主密钥和本地服务令牌未写盘。模拟和真实派发服务均退出，仅只读页面运行。公共核心、共享配置未改，不接正式执行、不部署、不提交。

最新只读页面：http://127.0.0.1:18869/
前次真实页面：http://127.0.0.1:18865/（保留原貌，最新页含回看链接）。

页面验证通过：1440px/390px 无溢出或页面错误，原文可展开下载；刷新不产生调用；POST=403、非白名单凭据路径=404。已查看桌面截图。
