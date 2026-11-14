# R2 组合后可提交候选白名单

2026-11-14：**73 文件**，其中 9 个已跟踪修改、64 个新增。工作树 `C:/tmp/mm-r2-int`，分支 `codex/rpg-character-r2-integration`，基线 `92849f3142ae67805e9cb627495ac8fab7d25df2`。未暂存、提交、推送或更新远端 PR。

## 与原范围的关系

原 62 文件按清单带入，原工作树逐文件哈希不变。新增 11 项：帮助目录 index.ts、帮助回归 helpContent.test.ts、既有单角色指南，6 张原创中性 PNG，组合验收与帮助证据说明各 1 份。原 R1 的 14 文件仍是显式前置依赖，不假定已入主线。

- 原 62 文件：R1 基础及文档 14；群聊模块及测试 28；角色编辑器及测试 4；共享作品库/导航接线 2；群网关合同及测试 2；其余文档/帮助 12。
- 新 11 文件：帮助登记/更新/回归 3；公开中性 PNG 6；验收文档 2。
- 生产源代码相比原候选没有新增修复；authoring/server.mjs 组合了最新主线原有内容，R2 差异仍仅 +2 行。

## 排除与门禁

正式角色素材、头像、作品/会话目录、host.json、原请求/回复、密钥、日志、测试导出、临时目录、依赖与构建产物不在白名单。PNG 例外仅为下列六张中性帮助图，不能混入正式材料截图。源码中的原创中性测试夹具不等于用户正式卡片。

相关回归与两构建已运行，但有明确披露的主线既有失败和整批时序敏感失败；详见 [组合验收](CHARACTER_R2_INTEGRATION_ACCEPTANCE.md)。此清单表示可审阅的提交边界，不表示全部 CI 绿灯或授权已发布。

后续仅按确认白名单暂存，提交前再比对 Git。不要整目录复制原数据，不要无范围 git add。当前暂存为空。

## 完整路径

```text
client/public/help-center/92849f31/character-editor.png
client/public/help-center/92849f31/character-group-chat.png
client/public/help-center/92849f31/character-group-create.png
client/public/help-center/92849f31/character-group-mobile.png
client/public/help-center/92849f31/character-library.png
client/public/help-center/92849f31/character-preview.png
client/src/content/help-center/articles/create-single-character-card.md
client/src/content/help-center/articles/use-character-group-chat.md
client/src/content/help-center/helpContent.test.ts
client/src/content/help-center/index.ts
docs/ai-rpg-experiment/CHARACTER_R1.md
docs/ai-rpg-experiment/CHARACTER_R1_ACCEPTANCE.md
docs/ai-rpg-experiment/CHARACTER_R2.md
docs/ai-rpg-experiment/CHARACTER_R2_ACCEPTANCE.md
docs/ai-rpg-experiment/CHARACTER_R2_EVIDENCE_AUDIT.md
docs/ai-rpg-experiment/CHARACTER_R2_HANDOFF.md
docs/ai-rpg-experiment/CHARACTER_R2_INTEGRATION_ACCEPTANCE.md
docs/ai-rpg-experiment/CHARACTER_R2_NARRATIVE_FIX.md
docs/ai-rpg-experiment/CHARACTER_R2_PR_DRAFT.md
docs/ai-rpg-experiment/CHARACTER_R2_PR_SCOPE.md
docs/ai-rpg-experiment/CHARACTER_R2_UI_ACCEPTANCE.md
docs/ai-rpg-experiment/CHARACTER_STUDIO_UI.md
docs/ai-rpg-experiment/CHARACTER_WORKSPACE_FLOW.md
docs/ai-rpg-experiment/CHARACTER_WORKSPACE_UI_ACCEPTANCE.md
docs/help-center/CHARACTER_R2_92849F31.md
experiments/ai-rpg-engine/authoring/server.mjs
experiments/ai-rpg-engine/character/conversation/archive-support.mjs
experiments/ai-rpg-engine/character/conversation/archives.test.mjs
experiments/ai-rpg-engine/character/conversation/contracts.mjs
experiments/ai-rpg-engine/character/conversation/contracts.test.mjs
experiments/ai-rpg-engine/character/conversation/legacy.mjs
experiments/ai-rpg-engine/character/conversation/legacy.test.mjs
experiments/ai-rpg-engine/character/conversation/runtime.mjs
experiments/ai-rpg-engine/character/conversation/runtime.test.mjs
experiments/ai-rpg-engine/character/conversation/store.mjs
experiments/ai-rpg-engine/character/conversation/store.test.mjs
experiments/ai-rpg-engine/character/conversation/test-support.mjs
experiments/ai-rpg-engine/character/conversation/types.ts
experiments/ai-rpg-engine/character/editor.css
experiments/ai-rpg-engine/character/editor.html
experiments/ai-rpg-engine/character/editor.mjs
experiments/ai-rpg-engine/character/editor.test.mjs
experiments/ai-rpg-engine/character/group/browser.mjs
experiments/ai-rpg-engine/character/group/browser.test.mjs
experiments/ai-rpg-engine/character/group/context.mjs
experiments/ai-rpg-engine/character/group/context.test.mjs
experiments/ai-rpg-engine/character/group/contracts.mjs
experiments/ai-rpg-engine/character/group/evidence-fixtures.mjs
experiments/ai-rpg-engine/character/group/evidence.test.mjs
experiments/ai-rpg-engine/character/group/fixture-provider.mjs
experiments/ai-rpg-engine/character/group/foundation.test.mjs
experiments/ai-rpg-engine/character/group/gateway-acceptance.mjs
experiments/ai-rpg-engine/character/group/gateway-receipt.mjs
experiments/ai-rpg-engine/character/group/gateway-transport.mjs
experiments/ai-rpg-engine/character/group/host.mjs
experiments/ai-rpg-engine/character/group/host.test.mjs
experiments/ai-rpg-engine/character/group/launch.mjs
experiments/ai-rpg-engine/character/group/message-display.mjs
experiments/ai-rpg-engine/character/group/message-display.test.mjs
experiments/ai-rpg-engine/character/group/page.css
experiments/ai-rpg-engine/character/group/page.html
experiments/ai-rpg-engine/character/group/recovery.test.mjs
experiments/ai-rpg-engine/character/group/runtime.mjs
experiments/ai-rpg-engine/character/group/runtime.test.mjs
experiments/ai-rpg-engine/character/group/server.mjs
experiments/ai-rpg-engine/character/group/server.test.mjs
experiments/ai-rpg-engine/character/group/store.mjs
experiments/ai-rpg-engine/character/group/test-support.mjs
experiments/ai-rpg-engine/character/group/types.ts
experiments/ai-rpg-engine/character/group/workspace.test.mjs
experiments/ai-rpg-engine/character/http.mjs
server/model_router/rpg_authoring_execution.py
server/tests/test_rpg_group_contract.py
```
