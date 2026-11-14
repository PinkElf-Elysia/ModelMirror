# E3 离线验收

当前更新：用户已通过离线验收；授权的2次Gemini补测已完成，实报$0.05499225，临时凭据已清理，用户已通过真实页面验收；模型未主动追问仅记录为后续优化项，不再增加补丁或真实调用。见 [E3-REAL-RESULT.md](E3-REAL-RESULT.md)。下文保留离线阶段记录，其中未实测/无额度描述仅指当时状态。
日期：2026-11-14。实施完成，等待用户页面验收。真实调用 0 次，无部署、Commit、Push 或 PR。

## 页面与体验
独立预览：http://127.0.0.1:18881/
启动时草稿为空。依次点击样例填入，再点“发送模拟回复”：
1. 给那人一些丹药
2. 周禾
3. 养息丹，两份；如果他不收就算了
4. 接受当前方案，暂不执行；可导出独立 E3 文件
5. 改成一份，先问他需不需要
也可展开“直接修改明确值”，选拟赠数量，填写1并保存；此操作不生成模型请求。
“不是周禾”只清除对象，物品和数量保留。“算了”需要明确取消范围；“取消计划”按钮才取消整项计划。
模型解释标记为待核对候选，接受记录绑定整体方案版本；用户界面输入标记明确修改。任何有效修订使接受记录失效。
固定模拟器只响应页面列出的样例；其他文字保存并提示未覆盖，不假装真实语言理解。

## 验证结果
- E3 19 项 Node 测试通过：纯状态转换、连续请求、引用与数量依据、受限 HTTP、持久化、重复请求、截断、未知结果、来源过期、取消和新计划。
- E2 与查询/装配回归 28 项通过。合计47项不同测试，不把重复运行累计成更多测试。
- 独立 Edge：7组流程通过；1440桌面、390窄屏无横向溢出及页面脚本错误。已人工查看截图。
- 旧 E2 真实回复离线重放及 E3 四轮模拟请求测量通过；原始证据未修改。
- 最后验证来源仍为历史记录版本1，养息丹3份；来源文件 SHA256 不变。
- Node 语法检查通过。页面为原生 HTML/CSS/JS，不涉及父项目前端构建或公共桥接变更。
- 首次纯逻辑测试因新工作树缺少 handlebars 失败；按现有锁文件从本地缓存 npm ci 后重跑通过。没有新增或升级依赖。

命令（仓库根）：
```text
node --test experiments/ai-rpg-engine/feature-exploration/free-action-e3/*.test.mjs
node --test experiments/ai-rpg-engine/feature-exploration/free-action-e2/*.test.mjs experiments/ai-rpg-engine/local-state/tools/query.test.mjs experiments/ai-rpg-engine/agent-core/context/assemble.test.mjs
node experiments/ai-rpg-engine/feature-exploration/free-action-e3/browser-check.mjs <已有Playwright包目录>
node experiments/ai-rpg-engine/feature-exploration/free-action-e3/replay-check.mjs <旧E2歧义回复.txt> <旧E2补测回复.txt>
```
测试用原创夹具。浏览器和重放需已授权历史来源；缺失时失败，不自动下载、伪造或派发。

## 非跟踪证据
目录相对 experiments/ai-rpg-engine：
- .rpg03-work/e3-validation/browser-KbFdjh/：最终浏览器 result.json、桌面/手机截图及交接导出。
- .rpg03-work/e3-validation/browser-LgALte/：先前页面验证，保留。
- .rpg03-work/e3-validation/replay.json：旧回复指纹、离线重放、四轮请求字符/字节。
- .rpg03-work/e3-source/session-1.json：从 E2 授权副本复制的历史来源，未跟踪。
- .rpg03-work/e3-preview/：独立用户验收草稿。
- .rpg03-work/e3-preview-service/：进程编号、日志；启动时PID48408，关闭前重新核对身份。

固定来源 SHA256：2f7104d5f41f21f8591b02a59434e8f147693f7bf560708c90648e245c939e77。
不是主线当前R5实例，不宣称实时R5联调；E2的严格R4绑定保持。

## 测量及限制
四轮模拟每次系统消息42439字符/113787 UTF-8字节；用户消息42/453/513/704字符。
只有相关有效意图、当前待答问题和本次回复进入连续上下文，未把全部修订记录回传。
原卡资料仍大，未进行压缩；没有匹配分词器，不报告token估算、费用下降或真实首字时间。
模拟调用不证明 Gemini 会稳定理解省略、否定或修订；真实测试需后续授权。
旧回复格式被拒绝，仅证明格式隔离，不是自然语言错误已根除。
