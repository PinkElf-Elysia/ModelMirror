# R3a 实现与验收报告

日期：2026-11-14。基线 2a7dfea1；工作树 C:/tmp/mm-cr3a；分支 codex/rpg-character-r3a。R3a 已完成离线实现，待用户页面验收；无提交、推送、共享部署或真实模型调用。

## 交付

单角色与群聊共用玩家档案和创建时身份快照；会话列表、命名、正文/成员搜索、最近活动与可恢复归档；安全 Markdown、原文查看/复制、TXT/MD 独立导出；按标签页和群 ID 恢复未发送草稿。新增帮助登记及两张原创中性 PNG 截图。源码与接口见 [交接说明](CHARACTER_R3A_HANDOFF.md)。

## 验证结果

所有日志保存在 C:/tmp/mm-cr3a-data，不入库。fixture/mock 与真实产品验收明确分开。

| 检查 | 结果 | 证据文件 |
| --- | --- | --- |
| 完整 character/conversation/group/daily | 119 项，116 通过、3 既有世界失败 | character-delivery.log |
| 最后 UI 焦点修订专项 | 28/28，通过搜索定位、初始快照焦点、旧宿主及群草稿 | navigation-snapshot-final.log |
| 合并基线 2a 独立源码重现 | 相同 3 项旧世界失败 | baseline-2a-legacy.log |
| 共享 authoring/studio 宿主回归 | 232/232 | shared-hosts.log |
| Windows 短路径受控网关 mock | 196/196 | gateway-windows.log |
| Linux 禁网、只读容器合同 | 196/196 | gateway-linux-with-engine.log |
| 相关前端四文件 | 页面三文件 99/99；帮助最终 19/22，仅 3 既有失败，总计 118/121 | frontend-catalog-fixed.log + help-registration-fixed.log |
| 父前端最终构建 | 通过；保留大 bundle 提示 | parent-build-delivery.log |
| 聊天前端构建 | 通过 | chat-build.log |
| 帮助截图检查 | 原有 24 项诊断，新两张 PNG 零新增 | help-images-png.log |

完整角色命令：`node --test --test-concurrency=3 --test-timeout=120000 character/*.test.mjs character/conversation/*.test.mjs character/group/*.test.mjs character/daily/*.test.mjs`（engine 目录）。设置 CHARACTER_R1_TEST_ROOT、CHARACTER_R2_TEST_ROOT、CHARACTER_R3A_TEST_ROOT 为本轮独立目录。旧 baseline-fixture 自建数据在本工作树被忽略的 .rpg04-work 下；不读取正式素材或原会话。

已证明：档案多修订及旧会话快照不变；CAS/操作碰撞与重启稳定；提交前后故障恢复；待核对请求阻止归档；不可见内容与私有记忆不进入搜索/导出；HTML/链接/资源显示限制；同源/伪造身份拒绝；新旧合同隔离；精确旧描述符恢复、续聊和漂移拒绝。2a 基线生成的单角色、群聊只读恢复前后存档字节相同，续聊保留原绑定与 runtimeHash。其他历史基线的状态见完整日志，不覆盖原证据。

## 既有失败与首次失败

- a6563a9b 世界三项：offline 的 AUTHORING_RUNTIME_BINDING_MISMATCH、summary 的 OFFLINE_SESSION_CORRUPT、joint 的 JOINT_SESSION_CORRUPT。在未带 R3a 的 2a 源码副本同样失败。3fffd5b4 世界、af3beb63/0cb019df 角色，以及当前合并基线兼容用例通过。未修改世界/R1 合同或放宽旧清单。
- 帮助三项：旧文章 11 步超过 8 步限制；listen-rpg-story 的声明基线 ae67eb25 与测试 fefc14b0 不同；voice-library 截图 c187d01e 与声明 ae67eb25 不同。旧截图格式/宽度与残留资产合计 24 项仍保留。
- R2 历史并发测试首次失败仍是历史事实，本轮共享宿主 aggregate 通过，不覆盖该记录。
- 本轮父构建首次早于聊天依赖安装完成，因缺少 React 失败；依赖完成后通过。Linux 首轮遗漏只读 engine 挂载导致 3 项失败，补齐挂载后通过。首轮帮助登记/新 JPEG 格式与搜索焦点测试问题已修复，原日志仍在独立目录。
- 单角色原有只读装配预览使用 POST；新增定位测试最初误将所有 POST 判为写入，改为明确禁止预览以外的 POST，并继续验证无发送/写入。未删除旧断言。

## 页面与未验证边界

桌面及 390×844：档案保存、单角色/群聊身份选择、命名、正文搜索、归档/恢复、群草稿刷新；下载 TXT/MD 并核对原始 Markdown 字符；搜索定位到消息、初始快照重绘后仍保留焦点。移动目录无横向溢出。最终预览使用 preview-final 中的新中性示例；旧 preview 及中间证据完整保留。

第一条浏览器通道在原生“放弃修改”确认框处超时，此项原生浏览器操作不记通过；取消切换、beforeunload 和重复保存有离线交互回归。头像解析/拒绝/保存由离线回归验证，浏览器文件选择器上传尚未完成单独人工验收。真实手机软键盘、Safari、跨浏览器下载体验、大规模档案/历史性能和新身份的真实模型表现未验证。

新版是本地独立预览，不是共享产品已部署。目录为本地所有者视图，不是角色运行权限接口。没有消息编辑、重生成、分支、自动总结、多 Agent、世界连接或 Harness 化；不因 mock 通过宣称这些能力成立。

## 预览与进入下一轮条件

- 会话：http://127.0.0.1:43150/characters?desk=sessions
- 玩家档案：http://127.0.0.1:43150/characters?desk=profiles
- 角色库：http://127.0.0.1:43150/characters
- 群聊：http://127.0.0.1:43151/groups

进入 R3b 前：用户完成上述页面人工验收；单独确认编辑/重生成/候选/分支行为和失效规则；对既有失败明确隔离或修复责任。真实模型验收另定模型、次数、预算、可发送材料和停止条件。此次停在 R3a，不自动启动下一轮。

## 范围与证据保护

修改限于 character 下 daily 新模块和必要宿主/UI、帮助文章/登记/断言、两张中性帮助截图、本轮文档。无 package/lock、server、世界 authoring、R1 conversation 修改。全部非 character 及 R1 已冻结 mjs 与开工摘要一致；见 source-drift-final.json。原素材、正式版本、共享会话及历史验收证据未作为写入目标；没有素材或会话加入 Git。

收尾去掉 session.mjs 的一处空行空格时，仅重新记录这一精确源码摘要；基线清单不变，并重跑 119 项合同回归。开发中间态 preview 不冒充最终可恢复存档，最终示例使用新目录建立，旧目录不删除。回退见接口交接；不自动迁移或修改旧数据。

## 文件清单

以下 44 个文件为本轮未提交差异；包含两张中性帮助截图，不包含本地数据或验收日志。

- `client/public/help-center/2a7dfea1/character-daily-sessions.png`
- `client/public/help-center/2a7dfea1/character-player-profiles.png`
- `client/src/content/help-center/articles/manage-character-conversations.md`
- `client/src/content/help-center/helpContent.test.ts`
- `client/src/content/help-center/index.ts`
- `docs/ai-rpg-experiment/CHARACTER_R3A_ACCEPTANCE.md`
- `docs/ai-rpg-experiment/CHARACTER_R3A_HANDOFF.md`
- `docs/ai-rpg-experiment/CHARACTER_R3A_TASK.md`
- `docs/help-center/CHARACTER_R3A_2A7DFEA1.md`
- `experiments/ai-rpg-engine/character/daily/client.mjs`
- `experiments/ai-rpg-engine/character/daily/client.test.mjs`
- `experiments/ai-rpg-engine/character/daily/compatibility.json`
- `experiments/ai-rpg-engine/character/daily/compatibility.mjs`
- `experiments/ai-rpg-engine/character/daily/compatibility.test.mjs`
- `experiments/ai-rpg-engine/character/daily/http.test.mjs`
- `experiments/ai-rpg-engine/character/daily/identity.mjs`
- `experiments/ai-rpg-engine/character/daily/launch.mjs`
- `experiments/ai-rpg-engine/character/daily/markdown.mjs`
- `experiments/ai-rpg-engine/character/daily/markdown.test.mjs`
- `experiments/ai-rpg-engine/character/daily/page.css`
- `experiments/ai-rpg-engine/character/daily/page.html`
- `experiments/ai-rpg-engine/character/daily/page.mjs`
- `experiments/ai-rpg-engine/character/daily/page.test.mjs`
- `experiments/ai-rpg-engine/character/daily/store.mjs`
- `experiments/ai-rpg-engine/character/daily/store.test.mjs`
- `experiments/ai-rpg-engine/character/daily/types.ts`
- `experiments/ai-rpg-engine/character/daily/workspace.mjs`
- `experiments/ai-rpg-engine/character/daily/workspace.test.mjs`
- `experiments/ai-rpg-engine/character/editor.html`
- `experiments/ai-rpg-engine/character/editor.mjs`
- `experiments/ai-rpg-engine/character/editor.test.mjs`
- `experiments/ai-rpg-engine/character/group/browser.mjs`
- `experiments/ai-rpg-engine/character/group/browser.test.mjs`
- `experiments/ai-rpg-engine/character/group/context.mjs`
- `experiments/ai-rpg-engine/character/group/contracts.mjs`
- `experiments/ai-rpg-engine/character/group/message-display.mjs`
- `experiments/ai-rpg-engine/character/group/page.html`
- `experiments/ai-rpg-engine/character/group/runtime.mjs`
- `experiments/ai-rpg-engine/character/group/runtime.test.mjs`
- `experiments/ai-rpg-engine/character/group/server.mjs`
- `experiments/ai-rpg-engine/character/group/store.mjs`
- `experiments/ai-rpg-engine/character/group/types.ts`
- `experiments/ai-rpg-engine/character/http.mjs`
- `experiments/ai-rpg-engine/character/session.mjs`
