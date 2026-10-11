# B4 首轮正式调用记录
B3.5 UI 已获用户通过；本卡4次及 Gemini 0.70 / 0.80 / 16384 参数获明确授权。未继承旧卡额度。

## 结果
- 装配核验：通过。实际派发仅1条完整作者system与1条用户开场；与派发前冻结逐字一致。无参考回复、空NPC占位、Earth前后置词或插件注入。
- 真实 Provider：失败。上游 HTTP200，控制面HTTP502；终止原因 content_filter / PROHIBITED_CONTENT，含DONE。不是 max_tokens 截断。具体触发内容未确认，不推测。
- 原文保留4256 Unicode字符及完整SSE。原文不可视为完整剧情；会话0个完成回合，宿主按现有回执合同保留unknown，不自动重试或写入历史。
- 用户内容质量验收：未运行。不能据部分输出宣告与原版质量接近/通过。
- 额度：已用1/4，余3；认证0，无重试。不因为回执usage字段为0就归还派发额度。
- 共享栈未修改；未Commit/Push/PR。正式预览18512，旧离线18510保留。

## 验证与原始失败
- node --test god-card/tests/*.test.mjs studio/*.test.mjs plugins/*.test.mjs：296项，295通过，1项旧数据副本检查未运行。
- God + controlled-provider 专项58项通过；状态投影修复后real-host专项2项重跑通过。
- client定向vitest：6文件59项通过。card生产构建通过；client生产构建通过，有既存大包警告。
- npm test:run附加文件路径未能限制vitest范围，误启动全仓库并观察到models.refresh两个失败，随即停止。未重写目录常量，未将全仓库报告成通过。
- 首次client构建误在根目录执行，ENOENT；在client目录重跑通过。
- 正式状态外层曾残留limit0而内层budget4，派发前已修复为一致。
- 自有实例强制停止留下插件锁，导致已创建会话状态REQUEST_FAILED。核实旧PID已停止后归档owner.lock，保留同一会话，明确恢复准备操作；没有重复创建或发送。
- UTF-8校验脚本首次按GBK读取失败，阻止派发；修正后72个运行源文件hash通过。初始78文件清单主要登记构建/工具，first-freeze-source.json补充正确的模块相对路径。
- 自动审批曾拒绝private日志读取，已放弃；另一次因审批使用额度失败，命令未执行。用户要求继续后完成唯一派发。

## 已知展示/恢复边界
共享安全渲染保留文本与details，剥离脚本、远端图片及交互input/class。因此模型正文里的原站宏/微翻转、弹幕和悬浮头像不能声称精细复刻完成；完整输出也尚未取得。本轮不放松安全渲染，不把原站输出模板注入模型。
作者配置页早期“尚未连接模型”的标识与外层正式状态冲突，已改为中性的“天书配置／由外层会话发送”；未修改作者提示词、角色配置或请求。
控制面的过滤结束被聚合为rpg_incomplete_output，宿主UI显示结果待核对。现有unknown恢复不会自动变成完成；本报告用原SSE明确供应商终止原因，不改写历史回执。
回退：优先停本轮自有18511/18512；须核对进程归属，正常close释放插件锁。保留会话、账本、原始输出和失败记录；旧宿主只读副本验证，不迁移。

## Final closeout verification
- Original Earth regression: `node --test experiments/ai-rpg-engine/card-replica/tests/*.test.mjs`: 26 passed. Together with the 296-test host run: 322 tests, 321 passed, 1 skipped.
- Final `RpgGod.test.tsx` rerun: 9 passed; these are a repeat of the six-file frontend scope, not 9 additional unique tests.
- Current snapshot: B4-DELIVERY.json. Previous B3.5 snapshot remains unchanged. Runtime and request evidence were not rewritten during closeout.
