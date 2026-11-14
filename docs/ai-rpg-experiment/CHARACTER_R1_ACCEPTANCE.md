# R1 验收结果与 R2 入口条件

日期：2026-11-13（America/Phoenix）。基线 `0cb019df01642d71a5eef55eeee31d1086ab037d`，分支 `codex/rpg-character-r1`，工作树 `C:/tmp/mm-cr1`。结束时远端主线仍为该提交。

## 交付结论

R1 已完成内部会话、成员、消息和私有资料基础，旧单角色只读适配，以及旧存档兼容/隔离回归。现有 UI、HTTP、网关、单角色宿主、世界合同及精确兼容哈希文件均没有修改。没有新生产依赖、迁移、真实调用、部署、提交或推送。

新增 14 个文件：`character/conversation/` 下 12 个实现、类型和测试文件，以及 [接口与 R2 交接](CHARACTER_R1.md) 和本报告。未修改基线中的已有文件。素材、会话、原始回执和本地验收数据不纳入版本控制。

## 验收矩阵

| 项目 | 结果 | 证据与解释 |
| --- | --- | --- |
| 新合同、持久化、隔离、适配与旧存档 | 通过 | final-member-view.log：22 个顶层测试全部通过，无跳过；旧世界探针内部另执行 3 个测试 |
| 新公开 TypeScript 合同 | 通过 | final-member-types.log，严格类型检查 |
| R0 角色、作品、运行绑定、目录与组合模块 | 通过 | r0-engine.log：96/96 |
| R0 共享宿主、摘要、历史及记忆存储 | 通过 | r0-hosts.log：144/144 |
| Windows 短路径 mock 网关 | 通过 | gateway-windows.log：158/158，独立 basetemp |
| Linux 禁网网关/路由 | 通过 | gateway-linux.log：240/240，4 条既有弃用警告 |
| 故事馆及创作入口组件 | 通过 | frontend.log：对应两文件 29 项通过 |
| 帮助内容 | 失败 | 同一 frontend.log：17/19；整体 46 通过、2 失败，与 R0 相同 |
| 帮助截图资产 | 失败 | help-images.log：与 R0 完全相同的 10 条诊断 |
| 父前端和聊天前端构建 | 通过 | client-build.log、chat-build.log；保留既有构建警告 |
| 原证据及先前文档 | 通过 | protected-before/after.json：7432 个文件哈希未变 |
| 旧源码、网关、兼容清单字节 | 通过 | git diff HEAD 无已有文件修改；仅新增限定路径文件 |
| 群聊页面、真实生成、实际共享栈、全仓测试/CI | 未运行 | 不属于本轮验收，不能由以上结果推断通过 |

测试数据均为原创中性夹具。Windows mock 路径只用合成令牌和 MockTransport；Linux 临时容器固定本地镜像 `sha256:4a4c4e53c5ab65049fad665c564f667274372b84dc1bbc1c6251abe68225baf1`，`--network none`，源码只读挂载，主应用初始化数据写私有 tmpfs。没有读取共享令牌、路由或额度。

## 场景覆盖

- 同名角色、同一模板的两个实例使用不同 memberId；跨会话引用拒绝。
- 玩家快照与角色封存版本固定；修改草稿并封存新版本不改旧成员、开场或历史。
- 加入前和离场期间消息不可读；重新加入保留此前参与期间历史；限定接收消息与私有资料不泄漏到兄弟成员。
- 点名和可见性独立；成员视图隐藏其他绑定、操作账本、内部参与区间、全局修订和隐藏消息信息，同时保留可见旧消息涉及的发言者身份。
- 开场在角色第一条发言位置只追加一次，重入不重复；对话示例不成为实际历史。
- 相同请求返回原 receipt，参数变化/修订过期/竞争提交拒绝；ID 在重启后稳定。
- 提交前故障与提交后结果未知分别恢复；必须显式核对原写入。错误恢复目标拒绝，不能借另一个会话绕开未决状态。
- 非法来源、伪造模型结果、工具权限字段、未知格式、超限文本、损坏指纹和源码漂移均拒绝。
- 人工私有资料不进入标准卡导出；旧适配保留记忆启用设置，不自动启用或召回条目。

## 旧存档证据边界

世界矩阵为 `a6563a9b`、`3fffd5b4` → 当前实现的 Offline/Summary/Joint。测试从对应 Git 提交实际运行旧代码生成中性存档，再恢复续聊并确认原 binding/executionHash 保留、篡改 runtimeHash 拒绝。

角色矩阵为 `af3beb63`、`0cb019df` → 当前实现。生成原存档后复制，在副本上恢复、继续聊天和生成只读投影；原副本哈希不变，同级会话不变，旧消息 ID 不因续聊改变。源码漂移使用另一份新建隔离副本，拒绝旧角色读取，不修改原存档哈希。

旧会话存在 unknown 请求时，适配器只读取已经提交的开场和完整回合，返回请求状态，不发布部分结果、不调用 recover/send。读取前后数据哈希及夹具派发次数一致。

没有打开正式爱莉希雅会话进行迁移或真实续聊。d9dd7094 的历史证据保留，未扩大为本轮任意版本兼容承诺。

## 保留的失败与限制

首次第二批测试有 1 项新实现失败：调用既有 greeting 函数时参数顺序错误；修正新 store 后本批及最终测试通过。batch2.log 原失败及 batch2-corrected.log 均保留，旧函数和测试断言未改。

两项既有帮助失败仍为：branch-rpg-story/manage-rpg-history 各 11 步，超过 5–8 步约束；play-rpg-cards 截图路径为 d9dd7094，但声明基线为 45ee30b5。截图检查的 10 条诊断与 R0 逐条一致，包括 JPG 被 PNG 检查拒绝及衍生错误尺寸解释、尺寸超限和未引用资源。未删截图、未放宽门禁，不宣称所有仓库检查全绿。

Windows 继续只承诺本轮短数据目录验收范围，没有修改系统长路径策略。存储复用现有 Windows 目录 fsync 限制，不宣称断电持久性已验证。

R1 是可信宿主内部服务，不是登录鉴权系统。所有权管理视图包含私有数据，不能直接暴露给浏览器或送模型；网络接入必须在 R2 加入受信主体绑定。

## 进入 R2 的条件

R1 的存储、标识、可见性、旧适配和兼容条件已通过。可在以下边界内另行启动 R2：

1. 保留本轮版本与读取路径；新成员/消息运行合同变化必须重新核对指纹和旧版本矩阵。禁止更新存档哈希强行兼容；需要迁移时另获授权。
2. 由可信宿主把用户操作绑定到会话和在场成员；只用成员视图及其固定模板装配上下文。人工私有资料与旧 memoryEnabled/条目启用状态必须明确处理。
3. R2 补齐 2–3 角色手动点名、群场景/开场、群级预算、派发幂等、未知请求恢复、停止及页面。当前不存在模型结果写入入口，不能用 fixture 来源伪造真实验收。
4. 群聊消息网关合同及可信回执采纳需要单独实现，并核对迟到结果对应的成员、版本、修订和权限。不能放宽旧单聊或世界合同来绕过。
5. 继续保留帮助门禁既有失败及 Windows 短路径条件。Agent 化工作树、多 Agent、世界状态接入及通用 Harness 不纳入 R2 前置依赖。

本轮停在 R1，不启动 R2，也不沿用历史真实调用额度。

## 复验命令与本地证据

工作树根目录下：

```powershell
$env:CHARACTER_R1_TEST_ROOT='C:/tmp/mm-cr1-data/new-tests'
node --test --test-concurrency=2 experiments/ai-rpg-engine/character/conversation/*.test.mjs
node client/node_modules/typescript/bin/tsc --noEmit --strict --target es2022 --module esnext --moduleResolution bundler --skipLibCheck experiments/ai-rpg-engine/character/conversation/types.ts
node --test --test-concurrency=4 experiments/ai-rpg-engine/character/*.test.mjs experiments/ai-rpg-engine/authoring/runtime-binding.test.mjs experiments/ai-rpg-engine/authoring/library.test.mjs experiments/ai-rpg-engine/authoring/store.test.mjs experiments/ai-rpg-engine/local-state/*.test.mjs
npm.cmd --prefix client run test -- --run --maxWorkers=1 --fileParallelism=false src/pages/RpgLibrary.test.tsx src/pages/RpgAuthoringEntry.test.tsx src/content/help-center/helpContent.test.ts
node client/scripts/verify-help-images.mjs
npm.cmd --prefix client run build
npm.cmd --prefix experiments/ai-rpg-engine/card-replica run build
```

完整命令、退出码和耗时在 `C:/tmp/mm-cr1-data/*.json`；同名 `.log` 是原始输出。新测试数据位于该短目录；旧 Git 源码副本在新工作树忽略目录 `.rpg04-work/character-r1-archives/`。其他既有测试继续使用其本工作树忽略目录；未借用共享栈存档。不要复用已有 pytest basetemp 重跑，以免测试工具清理原证据。

回退为停止本轮自有验证进程，撤销新增模块与文档；保留所有数据和证据。没有旧路径切换、共享服务部署或数据迁移，因此无需生产回滚。
