# P2-B 创作端预设与作品推荐配置

状态：实现、离线回归与浏览器验收已完成，待用户验收。前批 P2-A 已获用户通过；本批仅完成创作原生参数的预设和推荐配置，不代表整个参数阶段验收。

## 任务卡

- 目标：另存参数预设、搜索与载入预设、显式保存作品推荐；三用途独立。预设载入只改变编辑草稿，应用仍走正式会话配置接口。
- 基线：独立 codex/rpg-generation-comparison，HEAD d9f20fe6。本地缓存 origin/main 为 6977d86c，本批不把它称为已刷新远端，不重排并行 UI 修改。
- 文件范围：authoring/server.mjs、model-ui.mjs、browser.mjs、parameter-ui.test.mjs、本文件，共五个。复用现有样式和 P1 存储，生产依赖不变。
- 接口：现有 /rpg-app/api/authoring/model-settings 下新增 presets 查询/保存和 presets/reconcile；操作 ID、预设库 revision、条目 revision、当前能力预检由既有 ParameterSettingsStore 负责。推荐配置通过原作品草稿 CAS 保存，先明确告知将一起保存当前编辑的作品草稿。
- 持久化：预设独立存放在该创作宿主的模型目录 parameter-presets 子目录；不修改旧记录结构、不迁移会话。浏览器保存写入意图，结果未知只核对原 ID，明确找不到后才允许原 ID 再保存。
- 验收：预设默认/显式/停止词保真、跨模型冲突、能力变化、三用途隔离、重复操作和多标签 CAS、重启恢复、作品导入导出及会话不受推荐影响；浏览器嵌套取消/Esc、搜索、未应用修改、保存刷新、390px 与回桌面。
- 禁止：真实模型调用、认证、额度变更、共享部署、Commit/Push/PR、数据库迁移或新增依赖。保留 18662 及更早入口。
- 回退：撤回本批界面和路由接线；独立预设、作品推荐、会话和账本均保留，不自动清空或迁移。

## 原站观察与本地约定

2026-11-06 实际打开原站测试作品 bdd69f55…，点击“添加模型”，读取参数浮层并关闭。当前浮层包含温度、Top P、Top K、惩罚、回复/上下文长度及思考预算；在该浮层未观察到预设或作品推荐入口。没有保存原站作品或发送生成。

本批预设命名、预设搜索、跨模型载入确认及作品推荐弹窗属于 P0 已批准的本地设计，不宣称复刻原站未观察到的行为。跨模型不替换模型或转换参数，先展示差异；参数不兼容时阻止应用。

## 使用方式与生效边界

1. 在“模型与参数”选择剧情、摘要或记忆宫殿用途，再选模型和参数。点击“另存为预设”填写名称，保存到本宿主的预设库；这一步不应用到会话。名称为 1–80 个 Unicode 字符，同名预设分别保留。
2. “载入预设”仅显示当前用途，可按名称或模型搜索。选中后先核对来源模型、当前模型和参数差异，再载入编辑草稿；只有“应用到会话”成功后才改变后续请求。
3. 跨模型载入保留当前模型和原参数；不支持的参数仍可见，阻止应用，须明确移除或改选兼容模型。默认值仍为默认标记，显式值和停止词空格不改写；重复默认项折叠展示。
4. “作品推荐”可核对并载入现有推荐，或明确“保存草稿与推荐”。此操作一起保存当前作品草稿，仅更新当前用途；不发布作品版本，不修改既有会话、不复制授权或额度。
5. 预设保存结果未知时，保留原操作，刷新后仍可“核对原操作”。只有服务明确未找到回执后才提供“原编号再保存”，不自动提交。多标签修改冲突会保留名称和参数，读取最新状态后再由用户明确保存。
6. 取消和 Esc 关闭当前子面板；父层未应用参数保留。关闭主面板时，已有未应用修改仍需明确放弃。

预设采用 P1 的本地库容量和记录上限，本批提供新建、搜索和载入；不增加未实现的编辑、删除或云同步按钮。推荐配置通过作品导入导出往返，保存阶段不自动创建新会话。

## 验证结果

- 最终受影响测试 **84/84 通过，0 跳过**，包含参数 UI 的 24 项、参数存储、创作 HTTP 与旧模型配置界面回归。全部为离线假控制面，不代表真实模型验收。
- 请求捕获验证三个用途独立、默认/显式值保真、能力变化拒绝、同源与字段边界、重复操作及多标签 revision 冲突。预设保存、预检和推荐保存不产生生成任务。
- 推荐配置经过保存、发布版本、导出和新目录导入核对；system 空行和缩进保留，原会话配置、版本绑定和额度保持不变。
- 浏览器实际核对了命名取消/Esc、搜索空结果、跨模型冲突阻断、正式应用、推荐取消/保存、作品刷新恢复、多标签冲突和三个用途列表隔离。
- 浏览器截断回执测试：预设保存 POST **1 次**，刷新后按同一操作 ID 核对 POST **1 次**，预设 **1 份**，未再次提交保存；页面提供可执行的恢复入口。
- 桌面 1280×720、手机 390×844、模拟短视口 390×520 和切回桌面均已观察；无页面横向溢出，底部操作可见、按钮无重叠。短视口检查不等同于实体手机键盘验收。
- 主前端及 RPG 构建成功。主前端保留原有“大于 4500 kB”分包警告，没有为通过检查调整阈值。
- 35 项前批源码或证据 hash 保持不变；18662 原会话仍为 revision 6 / model revision 6 / 0 回合，配置一致。18662 及旧入口未重启、未迁移。

首次旧接口形状回归失败的日志保留在 legacy-response-failure.log；原因是为未启用预设的旧宿主增加了 false 字段，现已改为仅在功能启用时增加字段，旧断言未修改，重跑通过。

浏览器最初使用连接直接中断的故障注入时，代理观察到相同写请求两次、宿主幂等仅保存一份；应用代码没有重试逻辑，不能把该尝试报告成“只提交一次”。该记录保留在 connection-loss-observation.json。随后改用完整 HTTP 响应中的截断 JSON，验证了应用的未知结果与原编号恢复，详见 browser-recovery-proof.json。临时故障代理 18666 已关闭，不影响验收入口。

## 文件与证据

本批仅改五个版本控制文件：

| 文件 | 内容 |
| --- | --- |
| experiments/ai-rpg-engine/authoring/server.mjs | 预设存储生命周期、受限 HTTP 接线及旧响应兼容 |
| experiments/ai-rpg-engine/authoring/model-ui.mjs | 预设命名、搜索、核对、恢复及作品推荐界面 |
| experiments/ai-rpg-engine/authoring/browser.mjs | 作品推荐与现有草稿保存、操作恢复链路接线 |
| experiments/ai-rpg-engine/authoring/parameter-ui.test.mjs | 七项新增预设/推荐测试与已有参数回归 |
| 本文 | 操作说明、结果、限制与回退 |

验收入口：[18664 预设与作品推荐](http://127.0.0.1:18664/?work=work-ff9fd0893c5f1937857dd78a52b861998b42a086b11869fa47fba66b55c4ac88)。该入口使用本地假控制面，生成执行关闭；接口中的 realProvider 标记仅表明走受控宿主路径，不构成真实供应商调用证据。

外置证据目录：C:/tmp/modelmirror-rpg-p2b-evidence。

- final-tests.log、client-build.log、rpg-build.log、verification-summary.json：结果及复现范围。
- p2b-preset-http.json、p2b-recommendation-http.json、request-evidence.json：HTTP、导入导出和离线受控请求。
- browser-work.json、browser-presets.json、browser-control-capture.json：界面保存结果及零生成捕获。
- response-loss-proxy.json、browser-recovery-proof.json：原 ID 恢复，不自动重新保存。
- p2b-desktop.jpg、p2b-mobile-review.jpg、p2b-mobile-short.jpg、p2b-return-desktop.jpg、p2b-conflict.jpg、p2b-unknown.jpg、p2b-recovered.jpg：浏览器证据；p2b-mobile.jpg 保留推荐弹窗。
- protected-prior.json、old-entry-protection.json、P2-B.diff、P2-B-HASHES.json：旧批保护、本批差异和源码/产物/证据 hash。

复现命令（仓库根目录）：

```powershell
$env:RPG_TEST_PYTHON='C:/tmp/modelmirror-gate-q-venv-4764406c/Scripts/python.exe'
node --test --test-concurrency=1 experiments/ai-rpg-engine/authoring/parameter-ui.test.mjs experiments/ai-rpg-engine/authoring/generation-settings.test.mjs experiments/ai-rpg-engine/authoring/server.test.mjs experiments/ai-rpg-engine/authoring/model-ui.test.mjs
npm.cmd --prefix client run build
npm.cmd --prefix experiments/ai-rpg-engine run build
git diff --check
```

## 未覆盖与回退

聊天插件、六卡聊天接线、多模型比较与真实供应商参数效果不属于本批，不能由上述结果推定通过；按计划继续分批验收。没有新增真实调用、认证、额度、生产依赖、数据库迁移、共享部署或 Commit/Push/PR。

回退只撤回上述界面与接口接线，关闭新增入口，保留独立预设库、作品推荐、操作记录、原会话和账本。before/ 快照用于核对本批差异，不能覆盖后来新增的修改；有未知保存结果时先核对原操作。本批无现存数据转换或删除。
