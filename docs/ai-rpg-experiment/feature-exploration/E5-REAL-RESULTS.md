# E5 Gemini 实测结果：一次完成，语义验收未通过

2026-11-14。独立工作树 `C:/tmp/modelmirror-rpg-context-plans-e5`，分支 `codex/rpg-context-plans-e5`，基线 `bfc3d748438637215530f66a50cdd4e4d915f852`。用户已通过离线原型；本次真实结果待用户查看，不标记真实验收通过。

## 结果与原因

完整模拟派发2次通过后，真实调用第1次完成。Gemini返回合法JSON，但没有生成预期的两步计划：

```json
{"ops":[],"question":null,"limit":"输入包含分支/后验条件逻辑（“如果他不收，就不送”），当前仅支持最多三个线性有序步骤，不支持分支流程。"}
```

这是模型范围判定与预期的偏差，不是DNS、资格、派发或JSON解析失败。当前E5合同允许每步记录一个未核实条件，并不执行分支；本次模型将“如果他不收，就不送”当成了不支持的控制流。来源和对象数据正常进入实际请求，严格校验没有放宽，也没有修改原回复。

首条关键检查失败后立即停止。第2条撤回服药/改赠送两份/玩家陈述请求没有派发，未重试、未补充提示词再调用，也没有用模拟计划代替真实第一版继续。原始记录operationStatus=completed表示响应与结构处理完成；passed=false与E5_SEMANTIC_CHECK_FAILED表示业务验收未通过，不能混为成功。

## 模型、预算与统计

模型 `google/gemini-3.8-flash`，既有OpenRouter选定连接，限定 `google-ai-studio` 标准端点，无路由fallback。temperature=0.7、top_p=0.8、max_tokens=8192、tools=[]、tool_choice=none。

用户授权最多2次、每次$0.20、合计$0.40；实际1次、重试0次。派发前重新冻结资格和标准端点报价：输入$0.75/M，输出及其中推理$3.75/M。第1条按输入字节上界与最大输出预留$0.03327；预留不是实际收费。

| 请求 | 输入token | 输出token | 其中推理 | 供应商实报USD | 首次文本 | 请求完成 | 本地解析/校验结果可见 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 两步与条件 | 472 | 598 | 556 | 0.00259650 | 12.932秒 | 12.993秒 | 13.100秒 |
| 2 连续修订 | 未派发 | — | — | 未产生该次调用 | — | — | — |

输出598包含推理556，不能再相加计费。原SSE实报cost=0.0025965；宿主微美元字段四舍五入为2597，非额外收费。首次文本来自宿主250ms进度观测；JSON提案没有叙事正文首字。原字段planAvailableMs在本次仅代表合法空计划/范围提示完成解析，不表示目标计划可用。

## 状态、证据与清理

- 实际养息丹始终5，完整源记录前后SHA256均为 `4c31e7c9807042cbf257f968ccc800ba463dcda1f7862b8ce162dd5ee6b94f36`。
- 真实回复SHA256：`94aff4009d88d04b18b5a9f05c65400a7ce87af59e949d4e84d2ae8655540f34`。
- 只有1条供应商派发记录，状态succeeded；业务验收失败单独保留。
- 模拟和真实各自临时router目录均已删除；凭据仅密文保存于该目录，主密钥和本地服务token没有写入磁盘。共享配置未修改。
- 真实记录不含任何正式执行，未修改公共核心或E4文件，未部署、迁移或Commit/Push/PR。
- 普通18916离线页保持原样；实际source为本任务新建的成年样例，不是共享玩家存档。

非跟踪运行目录：`experiments/ai-rpg-engine/.rpg03-work/e5-plans-real-v1/`。包含execution-claim（冻结清单、合同和代码指纹）、request-1、reply-1、proposal-1（解析与计划差异）、stage-plan-1、transport-evidence（原SSE/回执）、source-state/source-after、plan-final、review、assessment、dispatch-audit、cleanup、mock-reference，以及page-validation桌面/窄屏截图。没有request-2或reply-2。第一版空计划没有保存为可导出的已确认计划。

## 验证命令与证据范围

```powershell
$e5Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-plans-e5 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
$e4Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-actions-e4 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
node --test @e5Tests @e4Tests
C:\anaconda\python.exe -m pytest experiments/ai-rpg-engine/feature-exploration/context-plans-e5/real/test_protocol.py -q
node experiments/ai-rpg-engine/feature-exploration/context-plans-e5/real/review-check.mjs C:/tmp/mm-r3a-pw/node_modules/playwright
```

已实际执行：E5 Node15项、E4原有22项、Python3项全部通过。完整模拟2次经过同一资格、预算、私有协议、上游请求和回执链；模拟响应由MockTransport替代，没有真实生成。模拟预检5→3、两步稳定ID及撤回修订通过，不代表真实模型通过。

只读页面7项检查通过：桌面1440×1080、窄屏390×844、原回复下载内容一致、展开与刷新、失败和未调用说明、GET/来源限制、没有外部请求或新增模型调用。已查看两张截图。没有为了实测修改公共后端，因此未追加父项目全量测试。

## 验收入口与后续

真实只读证据：http://127.0.0.1:18919/ 。离线交互原型：http://127.0.0.1:18916/ 。页面刷新、展开和下载不会调用模型，真实证据页没有执行或生成按钮。

独立页面仅本机预览；停止时核对PID及E5 review.mjs进程，不停止其他服务。原始记录保留，删除凭据不会删除证据。不应重跑launch命令“刷新”页面，启动器也拒绝覆盖既有实测目录。

下一步建议仅澄清E5提案合同中的边界：允许保留单步“接受才赠送”类原文条件，不执行条件、不生成备选分支；真正嵌套/循环/并行/多分支仍按范围限制。先用已保存回复与离线样例验证，再重新申请最小实测。此建议本次未实现，未增加补丁或真实调用；第二条真实连续修订与玩家陈述验证仍缺证据。
