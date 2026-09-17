# 删除冲突句后单次真实复测

用户授权：同意结论，删除这句授权复测。只执行一次，不自动重试。前一份Gemini用户意见为“目前有明显改善，大部分质量接近原版”，不外推为本份验收。

## 实际结果

- HTTP200；SSE DONE=true；finish_reason=stop；真实模型google/gemini-3.8-flash，实际Provider为Google AI Studio。
- 原文第22行当前情况标记为表世界；原文无“里世界”字面。末尾行动推荐完整闭合。本次技术返回完整，不代表全部剧情/格式要求合格。
- completion_tokens=6928（含reasoning334），prompt_tokens=19140，总26068。上限16384。
- 路由报告cost=$0.040335；这是Provider usage报告，未作账单审计。latency约36.28秒，TTFT约3.59秒。
- 原批账本仍4/4；新增RETEST_LEDGER单次已用1/1；累计5次，剩余0。认证复用，没有额外认证/探针/重试。
- 已通过实际宿主保存完整回合，会话fc93443b-7bdd-4222-b3ac-b7cde8543895。内容质量等待用户人工审阅。

## 审阅和原文

[本地只读审阅](http://127.0.0.1:18415/)；[完整模型原文](.local/gemini-retest/dispatches/slot-1/output.txt)；[技术回执](.local/gemini-retest/dispatches/slot-1/result.json)。

浏览器已展开当前情况，实见表世界；正文与折叠栏可见。只做本份必要展示检查，不声称全长面板和所有操作验收完成。截图仅工具内联查看，没有独立PNG归档。

原始输出21768 UTF-8 bytes，14461 Unicode characters（含HTML等），未补写、删改或修复。安全展示与原文分离。旧18414截断结果仍保留。

## 冻结与差异

- 角色全文和玩家输入“开始这一世。”与前次完全一致；角色年代矛盾保留。
- system仅删除“【开启深层世界】当前世界为深层世界”这句文字，空段保留；原DOCX和冻结prompts不改。前后置词未改，首轮不注入；世界书全关。
- 另一变化为max_tokens8192→16384；temperature=.70、top_p=.80及所有其余请求字段不变。没有发送reasoning、top_k、两项惩罚，也没有context截断。
- 原system hash：13041342ea3f2cafc8dfff9140e65868dc8261fbf69b0ffd3d833f2f4b72d5b7
- 运行system hash：4f632fff9b567183bc5fae281131320a4cf27035a0f39956b1f53ea5a09ef9ee
- 请求/实际wire hash：841ceb410377921fa4f03b6a0c6eadc09c3d9a46bb1716eea05f0350058d6727
- 原文hash：ffba3e78a924ec5347a46f06a59a9ca83e018bf096ad2a703a87ad2b580650d7
- 冻结hash：3da7db0a9ad60f36c7babc1993d90640367c723c589c4325694a54060f642d16
- 派发后19个冻结文件复核通过；原prompts、MANIFEST、四次账本hash不变。见[复核证据](.local/gemini-retest/postflight.json)。
- 首轮真实宿主检查见[实际宿主请求](.local/gemini-retest/actual-host-request.json)；上游wire和raw都保留于dispatches/slot-1。

本次结果支持定位的输入冲突是关键诱因；由于随机生成且两项变量同时变化，不声称严格单变量归因或后续轮稳定性通过。首轮/续轮装配与所有相关条目见WORLD_MODE_AUDIT.md。

## 操作与回退

当前18308仅允许该独立账本，单次已消耗；18415仅GET审阅，无生成入口。不要重跑prepare/generate。重启只读入口使用node tools/gemini-review.mjs --retest，先验证端口归属。回退只恢复本轮源码差异；保留原件、原文、会话、账本与冻结证据，不通过回退恢复额度。不停止旧入口。无Commit/Push/PR/Merge/Deploy/Release/Publish。
