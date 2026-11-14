# E4 离线验收
2026-11-14。离线实现已获用户验收。以下保留离线证据；后续三次分别授权的真实请求及最终人工验收见 [E4-CLOSEOUT.md](E4-CLOSEOUT.md)。当前已授权收尾提交 PR，仍不接正式执行或部署。

## 入口与使用
独立页面 http://127.0.0.1:18886/ ，首次为干净草稿。入口只在本任务服务，不在正式故事馆。
1. 点击“查看全部登记物资”：看见养息丹5份及其他登记物资；查询不派发模拟解释。
2. 点击“拟服用方案 · 养息丹”：物资是明确点击来源，数量仍待补充。
3. 输入“两份”，点击“发送模拟输入”：预检5→3；左侧实际仍5。
4. 点击“接受意图，暂不执行”，再“导出交接预览”：得到e4-handoff/1，不执行。
5. 输入“改成一份”并发送：预计5→4，旧接受失效。
6. 输入“改为赠送”并发送：旧参数清空，无规则预检；展开“直接修改明确值”，选养息丹、数量1、周禾，玩家条件填“他已经答应了”。保存后显示“尚无执行规则”，在场/同意仍未知。
7. 输入“不是周禾”：只撤回对象；其他明确值保留，不能导出未接受草稿。
8. 输入“改为服用”：清除赠送对象和条件；用完整样例“我想服用两份养息丹”继续。
9. 右上切换“灯塔 · 夜间值守”，输入“我想添两份灯油”：同一界面预检5→3；查看全部物资可见绷带数量未设定。切回修仙，其草稿仍独立保留。
10. 刷新页面不重复模拟生成；展开修订记录可看前后差异，证据区区分请求和固定模拟回复。

本页只解释列出的固定样例，不是任意自然语言模型。样例按钮只填入文本，发送才模拟解释。其他文字保留并提示未覆盖；可选择行动重新开始或直接编辑明确值。接受意图不等于世界授权。

## 证据与结果
- E4全套12项通过：两个来源、同规则预检、点击/文本一致、未知/不足/非法数量、赠送未知条件、修订/撤回/切换、持久化、重放、未知请求、源版本变化、HTTP无执行入口。
- E3、R5纯规则/登记和上下文装配26项回归通过。
- 最后只调整历史差异记录与界面说明后，E4存储5项及浏览器10条流程重新通过；不把重复运行相加。
- 桌面1440×1050、窄屏390×844，无横向溢出、脚本错误或外部请求。已查看桌面/窄屏截图。
- 浏览器在全新测试目录操作；下载实际交接JSON，确认源记录指纹不变，实际库存仍5。
- 父项目前端构建不适用：没有改client、依赖或正式路由。未跑全项目整治。

复验（仓库根）：
```text
node --test experiments/ai-rpg-engine/feature-exploration/context-actions-e4/*.test.mjs
node --test experiments/ai-rpg-engine/feature-exploration/free-action-e3/*.test.mjs experiments/ai-rpg-engine/agent-core/actions/rules.test.mjs experiments/ai-rpg-engine/agent-core/context/assemble.test.mjs
node experiments/ai-rpg-engine/feature-exploration/context-actions-e4/browser-check.mjs <已有Playwright包目录>
node --check experiments/ai-rpg-engine/feature-exploration/context-actions-e4/public/app.mjs
```

最终浏览器证据（不跟踪）：
experiments/ai-rpg-engine/.rpg03-work/e4-validation/browser-nsklfC/
含result.json、desktop.png、mobile.png、handoff.json及独立测试记录。早先browser-G8aLtk、browser-sV72Ye保留，未覆写。
用户验收数据：.rpg03-work/e4-preview/；启动日志/进程号：.rpg03-work/e4-service/。

## 来源与未验证边界
R5 ActionStore在本任务目录创建原创成年测试会话后封存为只读快照；并非复用E3真实回复中的3份库存，也非主线正在运行的玩家会话。数量5来自既有R5测试初始化。
E4新读取器只接受其可信启动配置的精确适配绑定和无Mod/自定义配置的记录；不会把R4记录放宽成R5，也不是通用存档导入器。生产运行manifest或不同绑定的适配另行核对。
没有真实模型理解、人物行为效果、E3/R5正式执行联调、角色私有认知或任意插件组合的验证。无需由离线结果推断token/费用下降。
