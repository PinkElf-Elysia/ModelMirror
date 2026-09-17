# Gemini 单次真实对比执行契约

仅本卡目录。沿用用户提供的角色逐字原文、官方冻结文本、temperature .70/top_p .80/max_tokens 8192、世界书关闭；不设置reasoning，不将其解释为零思考。不修正原素材矛盾，不重写提示词。
现有GPT路由18305与审阅18413保留。新增18307本卡受控入口及18414审阅入口；只复用并冻结ModelMirror ProviderChatTransport和ProviderEgressPolicy源码，以最小包加载，避免导入整套旧服务或改共享存储。上游固定OpenRouter/google-ai-studio/Gemini3.8Flash；非原newAPI资格，不伪造旧控制面认证。
本卡账本迁移至v2，每条保留原模型/路由，既有2次消耗不变。用户已阅slot2且决定换模型同角色对比，记录已阅、不判内容通过。先必要资格认证（合成Reply with OK.，最大输出256），成功后仅一份正式角色输出；合计最多再2次。失败/未知/取消不重试；资格不合格则停止。公开目录及本地检查不派发。
credentials仅从主工作区server/.env内部读取，无新密钥副本。固定请求hash、源码hash、账本预占及独占派发目录先于上游POST；浏览器不接受模型/地址/system配置。保留原始SSE、正文与usage回执。真实之前通过宿主到受控出口mock实测与本地route preview逐字比对。
验证：node --test tests/gemini-provider.test.mjs，Python静态语法及mock传输检查，npm.cmd run verify。回退只撤本轮代码和自有进程；停止前核实归属；保留账本、请求、输出与历史证据，不清理18411/18413或原服务。无提交/发布。

## 实际结果

已激活并执行。25项Node测试、TypeScript及Vite构建通过；另2项Python MockTransport检查通过（逐字节UTF8/CRLF、缺DONE保留原文且不重试）。第一次Node定向测试被只读沙箱EPERM阻止，使用授权执行环境重跑同组3项通过，不计为逻辑缺陷或Provider调用。Windows进程归属读取也先被沙箱拒绝；授权后核对仅本轮18414生成进程再替换为审阅宿主。

资格与生成均通过本卡18307 → 冻结模镜HTTP传输 → OpenRouter → Google AI Studio。不是18305的旧newAPI控制面资格，也未改其配置或复制凭据。17份实际运行源码/资源冻结并保存source-at-dispatch；正式请求与此前prepare的hash完全相同。生成前核对实际宿主至受控入口消息，入口至HTTP序列化JSON也逐字节冻结。

### slot3 必要资格认证

2026-09-17T08:57:52Z开始，08:57:54Z完成。原文“OK.”，HTTP200、finish_reason=stop、DONE；请求和回执模型google/gemini-3.8-flash，provider Google AI Studio。报告5输入、55输出（含53 reasoning）、60总tokens，报告费用0.00021美元。原文/stream/request/wire-request/result均在.local/gemini-real/dispatches/slot-3/。

认证完成并落账后，Node退出发生Windows libuv UV_HANDLE_CLOSING断言（命令exit1）。已重新只读核对账本complete、完整原文、回执与live qualified=true，确认Provider调用已成功；没有重跑认证。原失败保留，不把整个命令写成无异常通过。

### slot4 正式角色输出

2026-09-17T08:58:31Z开始，08:59:14Z完成流接收。
- HTTP200，实际模型google/gemini-3.8-flash，供应商Google AI Studio；无自动重试或回退。
- **finish_reason=length，DONE=true：流正常终止，但输出触及长度上限而截断。** runtime只接受stop为完整回合，因此记unknown，宿主请求failed、完整历史0回合。补充assessment明确结果为已知长度截断；原回执、原状态保留。
- 收到全部原文24672 UTF-8字节、16023个JS字符串码元（包括HTML及注释，并非纯叙事字数）。末尾截于行动推荐“1. 走向东南角”。没有补齐标签、正文或继续生成；安全展示解析器修复DOM结构不改变原文。
- 回执输入19153、输出8188（含598 reasoning）、总27341 tokens；max_tokens=8192。reasoning未设置，不能称为零思考。报告费用0.04506975美元；两次Gemini报告费用合计0.04527975美元，未独立审计账单。
- 延迟约42.97秒，首token约3.70秒。
- 请求/实际wire SHA-256：c7dd6345431f187dbad6133bed5b8366ef3fbe22fbf10c3498400ab65cab8cdd
- 原文 SHA-256：503a5159617df13c3f0f472a6ae1f48292c4e35d135c48589e39153db40d611d
- 冻结 SHA-256：04f4cb50dc72b5690bcc90eaecbc52b9db5a7be4dbbb06472380d12575001263
- 角色原文 SHA-256仍为33766118dc40145e1343b69577067860a6cb2b479f9cac3bdee055f1078681f7。
- .local/gemini-real/dispatches/slot-4/：output.txt、stream.sse、request.json、wire-request.json、result.json、freeze.json、assessment.json。
- 实际wire字节与冻结request相同；独立重新解析SSE得到的content逐字等于output.txt；审阅/raw.txt逐字等于同一原文。官方4文档未改。
- 用户要求表世界；当前情况状态栏实际标“里世界”。角色年代矛盾按用户要求保留。质量/世界类型/格式交给用户评审，不改提示词、不重跑。

## 只读审阅与后续边界

http://127.0.0.1:18414/ 已切换为tools/gemini-review.mjs，明确标注截断；/raw.txt提供完整收到的原文，/receipt.json提供回执。无发送入口。内置浏览器看到正文、状态栏和截断提示；15个含嵌套折叠项，输出区活动脚本/图片/外链0，桌面无横向溢出。未新增独立PNG归档，不宣称全部原版长文版式已通过。

账本累计**4/4已用，剩余0**（旧GPT认证+生成、Gemini认证+生成）。失败、截断不退额。后续没有额度，不续写、不再次认证或生成。用户尚未审阅本份Gemini内容。

恢复审阅：确认18414空闲后，node tools/gemini-review.mjs。不要用--generate重建审阅。18307资格在当前服务进程内有效，重启不代表可重新花额度。旧18411/18413入口及原会话保留。当前review进程PID2864仅为记录，停/重启前必须再次核对归属；route PID6504同理。

回退仅本轮新增代码和自有进程；不得回退CALL_LEDGER恢复额度，所有原文、失败及截断证据必须保留。无Commit/Push/PR/Merge/Deploy/Release/Publish。
