> 当前状态：用户已通过 E2 原型验收并授权提交 PR。本文保留阶段记录，最新结果与对接边界见 [E2-CLOSEOUT.md](E2-CLOSEOUT.md) 和 [E2-HANDOFF.md](E2-HANDOFF.md)。不再增加补丁或真实调用，不部署。
# E2 离线验收

2026-11-14。页面：http://127.0.0.1:18862/ 。用户验收待完成。
真实调用 0 次，未部署、未发布 PR。

## 结果与证据

- E2 Node 14/14：只读查询、条件计算、未知值、歧义、非法/重复 ID、截断、结构外字段、过期来源、并发修订、源待处理回合、确认撤销导出、数量修订、HTTP 仅 GET、跨站拒绝及配置限制。
- 公共回归 6/6：local-state/tools/query.test.mjs 与 agent-core/context/assemble.test.mjs。
- 浏览器 6 组：实际保存状态、预估、确认导出刷新、数量修改、歧义和组合行动、输入修改后锁定、移动端交互。
- 主线原 session-1.json 与 E2 副本前后 SHA256 一致。
- 公共跟踪文件相对冻结提交无改动；新增文件只在探索模块和对应文档。

初次核心测试 5 项失败：有限措辞检查误判“未取得对方同意”。修正为明确完成措辞后，同批 10 项通过，断言未削弱。之后发现数量修订标题仍含原用量，改为明确显示本地修订用量并补断言。

真实记录当前丹药为 3。拟赠 2 时显示若执行后 1；确认、导出、刷新后实际仍是 3。拟用改为 1 时预估 2，旧确认清除，不新增模拟调用。
HTTP 对照服务先读到 5 再读到 3，验证 GET 读取及过期拒绝；它是离线受控服务，不冒充已接主线实时服务。

## 命令

工作树根目录：

```powershell
node --test experiments/ai-rpg-engine/feature-exploration/free-action-e2/*.test.mjs
node --test experiments/ai-rpg-engine/local-state/tools/query.test.mjs experiments/ai-rpg-engine/agent-core/context/assemble.test.mjs
node --check experiments/ai-rpg-engine/feature-exploration/free-action-e2/server.mjs
node --check experiments/ai-rpg-engine/feature-exploration/free-action-e2/public/app.mjs
node experiments/ai-rpg-engine/feature-exploration/free-action-e2/browser-check.mjs C:\Users\21547\AppData\Local\npm-cache\_npx\51691537fc71f2b0\node_modules\playwright
```

使用既有锁文件离线 npm ci --offline --ignore-scripts --no-audit --no-fund；没有新增依赖。
静态页面无 TS 或构建链变更，没有运行父项目全量整治。

已有 Playwright 1.63.0-alpha-2026-08-05，独立 headless Edge，不读取用户浏览器配置，网络仅允许本次临时服务。测试草稿与用户验收数据分离。
最终证据：experiments/ai-rpg-engine/.rpg03-work/e2-validation/browser-ktctWq/
- result.json：6 组通过，pageerror 为空，模拟生成 5，真实调用 0。
- desktop-review.png、desktop-confirmed.png、mobile.png：1440×1000、390×844 无横向溢出。
- handoff.json：executionStatus=not_executed。
- drafts/：冻结请求与原始模拟回复。
较早截图位于 browser-3rMYaO，已经人工视觉检查；数量标题修订后又运行最终检查，早期结果保留。

尝试通过 Codex 自动打开页面，工具返回 primary app window is not ready；独立 HTTP 已确认可用，提供直接链接验收。

## 验收方式

1. “生成模拟提案”：核对丹药实际 3、拟用 2、条件结果 1。
2. 确认、导出、刷新：保持待执行，实际状态不变。
3. 改数量：旧确认取消；超量阻止确认。
4. 试澄清对象、帮忙与交谈、未知能力。
5. 展开请求和依据，核对状态版本、真实 ID 与模拟标记。

## 未完成与限制

真实模型效果、任意自由输入未测试；不能用模拟证明理解能力。
当前页面使用历史记录副本，主线实时 GET 联调待服务恢复。
非空 Mod / profiles 未适配；自然语言语义未经过额外模型审查。
E1 公共 offline-session 夹具漏拷 voice-library 的旧失败没有在此轮改动或宣称修复。
记录持久性沿用 SessionRecords，不承诺任意断电保证。

## 授权真实补测追加

离线原型用户验收通过后，已先跑完整模拟派发，再完成授权的两次 Gemini 提案补测。实报总费用为 0.05746875 美元，无自动重试，源状态未变，凭据已清理。歧义场景部分未达标，不能将调用成功写成业务全部通过。详见 [真实补测结果](E2-REAL-RESULT.md)。独立页面 http://127.0.0.1:18865/ 等待人工验收。
