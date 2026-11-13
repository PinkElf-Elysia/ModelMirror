# 扩展插图 unknown 修复

工作树 `C:/tmp/mm-rpg-harry-potter`，分支 `codex/rpg-harry-potter`，沿用当前未提交的卡片工作。只改插图完成记录的持久化与相关离线测试、本文记录，不改提示词、模型请求、预算、既有用户存档或共享服务；无提交、部署、迁移和真实模型调用。

已验证：上轮诡秘失败的离线目录 `rpg-eight-RZAxwz` 中图片 asset 已存在，而 job.json 仍为 pending。实现的 finish 在 atomicJson 异常时仍清除 active，外层吞掉异常，随后读取显示 unknown。Windows 文件替换瞬时拒绝是待故障注入确认的原因；仓库已有 publishVersion 对 EPERM/EACCES/EBUSY 做最多六次同一 rename 重试。

计划修改不超过五个文件：studio/illustrations.mjs、插图持久化回归测试及本文；必要时仅补充原修复记录链接。中等风险，涉及本地文件原子替换和任务状态，不新增依赖或公共接口。保持真正未知/失败不重放，持久性错误不无限重试。

验收：新增确定性落盘故障回归先失败后通过，原 illustrations 与 archive-plugins 集成、强杀恢复/卡片/折叠回归；原文冻结哈希与旧四轮存档不变。回退仅撤销插图落盘接线，不修改现有证据或自动重发任务。

## 已实现

`studio/illustrations.mjs` 在初始意图和最终完成记录的 atomicJson 发布阶段复用现有 `publishVersion`。仅对 EPERM、EACCES、EBUSY 重试同一个临时文件的 rename，最多六次，等待 20/40/80/160/320ms；不重复模型请求、图片写入或授权操作。完成记录发布结束前保留 active。永久错误和重试耗尽仍保守显示 unknown；重复请求和重启都不会重新生成。

`studio/illustration-persistence.test.mjs` 使用 Node 24 的 mock.method 和 syncBuiltinESMExports，对本测试目录的指定原子替换注入错误；每例恢复内建绑定，不新增生产测试开关。覆盖瞬时三类锁错误恢复、持续占用的六次上限、ENOSPC 不重试、初始意图先落盘再派发、初始落盘失败零派发，以及恢复后的相同操作 ID 不重放。

证据区分：变更前故障注入得到与旧失败目录一致的“asset 已有、job.json=pending、查询 unknown”，其中瞬时错误恢复与六次上限两项失败；修复后通过。两次带 errno 观测的自然集成运行未碰到原偶发错误，因此不能追溯断言上轮每个失败都是同一个 Windows 错误码；已确认并修复的是可确定复现的原子发布瞬时错误处理缺口。

## 实际验证

以下命令均在 `experiments/ai-rpg-engine` 执行，模型和图像服务全部使用本地桩。

| 检查 | 结果 |
| --- | --- |
| 修复前 `node --test studio/illustration-persistence.test.mjs` | 3 项中 2 项失败，稳定复现 unknown 与无重试；保留失败事实 |
| 修复后 `node --test studio/illustration-persistence.test.mjs studio/illustrations.test.mjs studio/archive-plugins.test.mjs studio/local-owner.test.mjs studio/card-entry.test.mjs studio/session-lifecycle.test.mjs harry-potter-card/harry.test.mjs card-replica/tests/harry-potter-presentation.test.mjs` | 32/32 通过 |
| `node --test studio/archive-plugins.test.mjs`，预先限定连续三次 | 每次 8/8，共 24/24 通过；未放宽断言或延长原测试轮询 |
| 冻结资源与原四轮存档 SHA-256 | 8 个资源匹配原 frozen.json；原始 session 字节匹配上一轮证据 |
| `git diff --check`、暂存列表、修改文件凭据模式扫描 | 通过；暂存列表为空，扫描无命中 |

日志在 `.rpg04-work/hp-repair-20261113/illustration-repeat-1.txt` 至 `illustration-repeat-3.txt`，冻结检查在 `illustration-frozen-check.json`。新增真实调用 0，凭据读取 0。未修改原始图片或故事存档，未部署或提交。此轮仅后端持久化修复，未重跑前端构建或真实图像生成；上轮前端构建结论不作为本次真实服务验证。

相关 Node 测试 API 依据：[mock.method](https://nodejs.org/docs/latest-v24.x/api/test.html)、[内建模块 ESM 绑定同步](https://nodejs.org/docs/latest-v24.x/api/module.html)。


最终主线整合与验收以 [HARRY_POTTER_FINAL_ACCEPTANCE_2026-11-13.md](HARRY_POTTER_FINAL_ACCEPTANCE_2026-11-13.md) 为准；本文中的未提交、旧基线和测试数均为对应历史阶段记录。
