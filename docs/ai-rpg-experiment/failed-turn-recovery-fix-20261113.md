# 失败回合恢复与删除本轮

## 任务契约

- 基线：origin/main `d9dd7094`，日期 2026-11-13；独立分支 `codex/rpg-failed-turn-fix`。
- 目标：失败后提供原因、已有部分正文及重新发送/修改输入/删除本轮；核对无服务端记录的请求后解除发送锁定；输入和输出均可删除本轮并保留历史版本。
- 修改范围：RPG 共用聊天前端、会话生命周期、失败请求投影、对应离线测试及帮助中心。此单一生命周期修复涉及多个前后端文件，不能拆成各自独立生效的提交。
- 禁止：真实模型调用、共享栈部署或写入、复制用户会话、混入品牌封面和部署兼容覆盖层、依赖升级。
- 接口：已有 lifecycle 接口新增 delete-turn 和 dismiss-request；请求投影增加失败原因与部分正文。没有数据库迁移；关闭记录在原有版本封套中按需保存。
- 验收：最多三次隔离离线场景测试（截断、审查、网关中断）；每个场景包含相应恢复操作。生产构建及本地回归不调用模型。用户已验收共享栈页面。
- 风险：持久化生命周期。关闭请求保留回执、用量和防重放标记；原结果未知不能承诺没有计费。运行中的请求不强制关闭。
- 回退：先停止写入并备份完整会话与 session-versions；旧服务不理解新关闭标记，不得只回退代码处理新记录。共享栈本轮不变更。

## 原因与行为

浏览器保留待核对请求，但服务端核对返回 not-found 时，原实现没有关闭浏览器发送标记。新实现先持久关闭旧请求 ID，再解除本地锁定；核对不自动派发。

失败正文仅作展示，不写入正式剧情或记忆。手动重发使用当前模型及同一会话的新请求 ID。删除已保存回合会将该轮和后续剧情存入历史版本，并回退对应总结与记忆；保留当前模型及插件授权。

## 验证

三次浏览器离线场景通过，详见 [帮助验收记录](../help-center/rpg-failed-turn-d9dd7094.md)。界面验收 5 次内存模拟响应、真实模型调用 0；共享栈没有重新部署或写入。

在 `experiments/ai-rpg-engine` 执行：

```powershell
node --test studio/turn-failures.test.mjs studio/session-lifecycle.test.mjs studio/memory-tasks.test.mjs studio/summary-tasks.test.mjs studio/controlled-provider.test.mjs
node --test studio/failed-turn-compatibility.test.mjs
node --test --test-name-pattern='runtime translation' studio/output-edits.test.mjs
npm.cmd run build --prefix card-replica
```

- 生命周期/失败回归：82 通过，0 失败。
- 主线精确运行指纹兼容：2 通过；原存档不变，内容、设定和 hash 篡改仍拒绝。
- 既有运行指纹拒绝回归：1 通过。
- TypeScript/Vite：通过；仅既有静态/动态混用导入警告。
- `git diff --check`：通过。
- `node client/scripts/verify-help-images.mjs`：失败，12 处均与干净主线副本一致；本 PR 新增问题为 0，新截图为 1000px PNG、各约 30KB，均有文章引用与替代文本。没有修改或弱化检查器。
- 真实模型响应质量、真实扣费、公网故障：未运行；不以合成响应冒充真实供应商验收。

离线界面检查发现并修正网关错误码直出、核对后旧提示及重复错误提示；只重载既有记录复查，没有增加第四次界面场景。主线存档兼容只映射已审阅文件的精确旧指纹，仍调用原完整校验器；没有导入共享栈专用旧版本兼容覆盖或提示词。
