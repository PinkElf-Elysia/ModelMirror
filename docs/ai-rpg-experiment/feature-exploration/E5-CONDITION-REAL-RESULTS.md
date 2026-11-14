# E5 单步条件补测结果

2026-11-14；独立工作树 C:/tmp/modelmirror-rpg-context-plans-e5，分支 codex/rpg-context-plans-e5，基线 bfc3d748438637215530f66a50cdd4e4d915f852。用户本轮明确授权先完整模拟、再最多1次Gemini，合计≤$0.20、无自动重试、临时凭据加密落盘并清理。实际模拟1次、真实1次，无重试。

## 结论

本次原始回复包含两个动作use_medicine与gift，物品medicine、对象zhou、数量2/1；赠送条件逐字为“如果他不收，就不送。”，kind=prerequisite，question/limit均null。模型不再将此单步条件拒为控制流超限。但第一步changes错误地包含actionId、itemId、quantity三个字段，触发原E4_CHANGES校验，整批未采用。

现有服药草稿槽位只接受item、quantity，action在动作层；实际规则执行参数actionId、itemId不等同于草稿字段。第一步已因changes数量>2被拒；两步中的itemId也不是合法槽位。本次没有删除/重命名字段、没有改写原始回复、没有放宽校验或追加调用。

合理推断：实际上下文中的capabilities.parameters展示规则参数，而E5提示没有明确列出changes.field的行动级白名单，使模型混用了两种结构。此为基于请求与回复的解释，不是已证明的模型内部原因。后续建议仅在E5模型合同明确区分草稿槽位与规则参数，并用本次原回复做严格拒绝回归；本轮未实施该修复。

只有原始文本层面的两步/条件观察成立；有效计划、真实5→3预检和整链成功均未成立。源记录完全未变，实际丹药仍5。上次未发送的连续撤回/数量修订/玩家陈述仍未真实验证。

## 用量与时间

模型google/gemini-3.8-flash，经既有OpenRouter选定连接，限定google-ai-studio标准端点，禁用fallback。temperature=0.7、top_p=0.8、max_tokens=8192、工具关闭。派发前重新核对标准端点与报价（输入$0.75/M，输出及其中推理$3.75/M）。

| 项目 | 实际值 |
|---|---:|
| 真实请求 / 重试 | 1 / 0 |
| 输入token | 723 |
| 输出token | 1518 |
| 其中推理token | 1148 |
| 供应商实报费用 | $0.00623475 |
| 最大预算预留（非实收费） | $0.03408375 |
| 授权总上限 | $0.20 |
| 首次文本（宿主观测） | 12.634秒 |
| 请求完成 | 14.566秒 |
| 有效计划可用时间 | 未产生有效计划 |

推理已包含在输出内，不重复累计。本任务是JSON提案，没有叙事正文首字。微美元字段6235是实报费用的四舍五入，非额外计费。

## 验证和证据

- 完整模拟1次经过真实装配、资格、预算、临时桥接和回执处理；上游由MockTransport替代。两步/原文条件/仅首步预检5→3通过，实际库存5。不能代替模型效果证据。
- Node离线回归44项通过：E5 22、E4原有22。Python旧及新单次协议共6项通过。测试包含超额编号拒绝、未知结果不重发、严格字段及条件校验。
- 只读页面7项通过：桌面1440×1080、窄屏390×844、原回复下载一致、展开/刷新、失败提示、路由和来源限制、无外部请求或新增调用。已查看截图。
- 源记录前后hash：98b10a86e7f84957eba73cb07759edb8ee3885b62623291d2bbfac434ecdf6e3。
- 原始回复hash：9f2c9aa49ad0db7e39138ccdf159b2a1ead4c43fab6a7a606a39c3b1cfc6e9fb。
- 模拟和真实的临时router目录已删除；密钥只保存在进程内存，磁盘凭据为原密文。本地服务token未落盘，共享配置未改。

命令（仓库根）：
```powershell
$e5Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-plans-e5 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
$e4Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-actions-e4 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
node --test @e5Tests @e4Tests
C:\anaconda\python.exe -m pytest experiments/ai-rpg-engine/feature-exploration/context-plans-e5/real/test_protocol.py experiments/ai-rpg-engine/feature-exploration/context-plans-e5/real/test_condition_protocol.py -q
node experiments/ai-rpg-engine/feature-exploration/context-plans-e5/real/condition-review-check.mjs C:/tmp/mm-r3a-pw/node_modules/playwright
```

非跟踪记录分别在experiments/ai-rpg-engine/.rpg03-work/e5-condition-mock-v1和e5-condition-real-v1。保存request-1、reply-1、proposal-1（未采用前后null）、原SSE/回执、execution-claim参数及代码指纹、source-state/source-after、dispatch-audit、cleanup、assessment、raw-observation和页面截图。raw-observation只解释原回复，不生成修复计划或预检。旧e5-plans-real-v1和18919页面保持原样。

## 交付边界

新增文件均在E5 real目录：condition-* / condition_*单请求私有入口、测试、统计和只读页面。没有修改公共核心、E4、共享配置或模型提案合同；没有接正式执行、部署、迁移、Commit/Push/PR。

本次验收：http://127.0.0.1:18922/ 。上次失败：http://127.0.0.1:18919/ 。离线原型：http://127.0.0.1:18916/ 。新页面是只读证据，刷新/下载不调用模型，不覆盖用户离线草稿。查看结果不需要重新运行launch脚本，脚本拒绝覆盖既有目录。停止预览仅核对并停止本任务condition-review.mjs进程，证据保留。

当前调用额度已用完。整体E5真实验收未通过；后续修复与补测另行确定，不能把剩余美元视为额外请求授权。
