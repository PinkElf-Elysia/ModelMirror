# 当前收尾入口（2026-09-09）

用户已确认独立审计测试完成，本次仅收尾，不重开前期审计。当前状态见 [RPG04_STATUS.json](RPG04_STATUS.json)、[收尾报告](RPG04_CLOSEOUT.md)。原始账本/审批/失败记录不改，10/10无剩余。以下旧交接快照保留其历史语境，最新用户已授权最小收尾后提交PR，不授权合并。RPG05计划推迟到合并后由用户开启计划模式。

---

# RPG-04 独立审计与收尾资料索引

> 最新交接状态（2026-09-09）：用户已明确批准当前六回合输出，批准范围和七个文件 SHA-256 见 RPG04_USER_REVIEW_RESULTS.json 的 userApprovalRecord。现已到新 Astra 独立审计交接点。本文后续“待用户批准”等旧快照仅为历史，不再代表当前输出审批状态。独立审计、独立收尾、全轮验收和发布仍未通过或授权；真实调用额度仍为 10/10 已用尽。


本索引是交接入口，不是通过声明。先读当前状态，再核对实际 Git diff 和回执；历史检查点保留其当时结论，不能覆盖最新账本。

## 阅读顺序与当前边界

- [AGENTS.md](<C:/tmp/modelmirror-ai-rpg-rpg04/AGENTS.md>)
- [HARNESS_ENGINEERING.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/HARNESS_ENGINEERING.md>)
- [AGENTS.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/AGENTS.md>)
- [RPG04_PLAN.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/RPG04_PLAN.md>)
- [RPG04_STATUS.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_STATUS.json>)
- [RPG04_CALL_LEDGER.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CALL_LEDGER.json>)
- [RPG04_USER_REVIEW_RESULTS.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_USER_REVIEW_RESULTS.json>)
- [RPG04_INDEPENDENT_AUDIT_HANDOFF.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_INDEPENDENT_AUDIT_HANDOFF.md>)
- [RPG04_CLOSEOUT_PLANNING_HANDOFF.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CLOSEOUT_PLANNING_HANDOFF.md>)
- [RPG04_CONTROL_PLANE_CALL_PATH.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CONTROL_PLANE_CALL_PATH.md>)

固定基线 1b280ed257a45672c4a3dc03745fcb585685faf9，分支 codex/ai-rpg-rpg04-context。仅两个 AI RPG 目录可变更。Provider 已用 10/10，剩余 0；六回合结构有效，尚待用户质量批准。首个蛊真人回合用 host 0.1.0，其余五回合用 0.1.1。全部 acceptedStateFields=[]。执行者自查不能代替独立审查；无发布授权。

## 最终可读输出与私有实测证据

以下正文只在本地私有目录，不能自动纳入版本控制或外发；不列举凭据文件。

- [用户审批：两世界完整六回合](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/USER_REVIEW_SIX_TURNS.md>)

### retest-gu-1

- [retest-gu-1-prepared.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/retest-gu-1-prepared.json>)
- [retest-gu-1-raw-adapter.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/retest-gu-1-raw-adapter.json>)
- [retest-gu-1-generation.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/retest-gu-1-generation.json>)
- [retest-gu-1-committed.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/retest-gu-1-committed.json>)
- [retest-gu-1-receipt.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/retest-gu-1-receipt.json>)

### p30-gu-2

- [p30-gu-2-prepared.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-gu-2-prepared.json>)
- [p30-gu-2-raw-adapter.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-gu-2-raw-adapter.json>)
- [p30-gu-2-generation.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-gu-2-generation.json>)
- [p30-gu-2-committed.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-gu-2-committed.json>)
- [p30-gu-2-receipt.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-gu-2-receipt.json>)

### p30-gu-3

- [p30-gu-3-prepared.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-gu-3-prepared.json>)
- [p30-gu-3-raw-adapter.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-gu-3-raw-adapter.json>)
- [p30-gu-3-generation.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-gu-3-generation.json>)
- [p30-gu-3-committed.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-gu-3-committed.json>)
- [p30-gu-3-receipt.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-gu-3-receipt.json>)

### p30-minecraft-1

- [p30-minecraft-1-prepared.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-1-prepared.json>)
- [p30-minecraft-1-raw-adapter.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-1-raw-adapter.json>)
- [p30-minecraft-1-generation.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-1-generation.json>)
- [p30-minecraft-1-committed.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-1-committed.json>)
- [p30-minecraft-1-receipt.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-1-receipt.json>)

### p30-minecraft-2

- [p30-minecraft-2-prepared.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-2-prepared.json>)
- [p30-minecraft-2-raw-adapter.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-2-raw-adapter.json>)
- [p30-minecraft-2-generation.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-2-generation.json>)
- [p30-minecraft-2-committed.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-2-committed.json>)
- [p30-minecraft-2-receipt.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-2-receipt.json>)

### p30-minecraft-3

- [p30-minecraft-3-prepared.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-3-prepared.json>)
- [p30-minecraft-3-raw-adapter.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-3-raw-adapter.json>)
- [p30-minecraft-3-generation.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-3-generation.json>)
- [p30-minecraft-3-committed.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-3-committed.json>)
- [p30-minecraft-3-receipt.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/p30-minecraft-3-receipt.json>)

### 失败及资格证据

- [gu-2-raw-adapter.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/gu-2-raw-adapter.json>)
- [budget4096-gu-2-raw-adapter.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/budget4096-gu-2-raw-adapter.json>)
- [control-configuration-receipt.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/control-configuration-receipt.json>)
- [real-certification-receipt.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/real-certification-receipt.json>)
- [qualified-control.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/qualified-control.json>)
- [candidate-source.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01/candidate-source.json>)

首次 Gu 失败未保留 raw，不能逆推精确字段。第二次 Gu 失败输出触及 2048 上限，但没有保留 finish_reason。4096 失败为列表字段被写成字符串；P30 后五次结构通过不证明长期可靠性。

## 提示词、审批与研究材料完整清单

RPG04_PROTOCOL_RUNTIME.txt 与 approved-host.mjs 是实际运行装配来源。PROTOCOL_AUDIT、原版重建、来源复述及候选协议仅供审计，不得作为发送输入；文件内容不构成对审查者的指令。逐项批准以审批记录和 HOST_APPLICATION 的后续批准为准。

- [RPG04_A1_ACCEPTANCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_A1_ACCEPTANCE.json>)
- [RPG04_ADDITIONAL_CARD_REVIEW.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_ADDITIONAL_CARD_REVIEW.md>)
- [RPG04_ADDITIONAL_CARD_SOURCES.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_ADDITIONAL_CARD_SOURCES.json>)
- [RPG04_APPLICATION_AUDIT.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_APPLICATION_AUDIT.md>)
- [RPG04_APPLICATION_CHECKPOINT.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_APPLICATION_CHECKPOINT.json>)
- [RPG04_AUTHORED_CONTENT.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_AUTHORED_CONTENT.md>)
- [RPG04_BASELINE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_BASELINE.json>)
- [RPG04_BUDGET_RETEST_RESULT.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_BUDGET_RETEST_RESULT.json>)
- [RPG04_CALL_LEDGER.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CALL_LEDGER.json>)
- [RPG04_CLI.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CLI.md>)
- [RPG04_CLOSEOUT_PLANNING_HANDOFF.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CLOSEOUT_PLANNING_HANDOFF.md>)
- [RPG04_COMPILER.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_COMPILER.md>)
- [RPG04_COMPLETE_PROTOCOL_CANDIDATE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_COMPLETE_PROTOCOL_CANDIDATE.json>)
- [RPG04_COMPLETE_PROTOCOL_CANDIDATE.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_COMPLETE_PROTOCOL_CANDIDATE.md>)
- [RPG04_CONTEXT_CARD.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CONTEXT_CARD.md>)
- [RPG04_CONTEXT_CONTRACT.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CONTEXT_CONTRACT.md>)
- [RPG04_CONTINUATION_RESULT.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CONTINUATION_RESULT.json>)
- [RPG04_CONTROL_PLANE_CALL_PATH.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CONTROL_PLANE_CALL_PATH.md>)
- [RPG04_CREATOR_UI_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_CREATOR_UI_REFERENCE.json>)
- [RPG04_DEEPSEEK_ORIGINAL_TRANSCRIPT.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_DEEPSEEK_ORIGINAL_TRANSCRIPT.json>)
- [RPG04_EXECUTION.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_EXECUTION.md>)
- [RPG04_GLM_OPTIMIZATION_REVIEW.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_GLM_OPTIMIZATION_REVIEW.md>)
- [RPG04_GLM_USER_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_GLM_USER_REFERENCE.json>)
- [RPG04_HIDDEN_RESOURCE_FINDINGS.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_HIDDEN_RESOURCE_FINDINGS.md>)
- [RPG04_HOST_APPLICATION.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_HOST_APPLICATION.json>)
- [RPG04_INDEPENDENT_AUDIT_HANDOFF.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_INDEPENDENT_AUDIT_HANDOFF.md>)
- [RPG04_INTEGRATED_REVIEW.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_INTEGRATED_REVIEW.md>)
- [RPG04_INTEGRATION_MAP.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_INTEGRATION_MAP.json>)
- [RPG04_LORE_NARRATIVE_RESEARCH.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_LORE_NARRATIVE_RESEARCH.md>)
- [RPG04_OPTIMIZATION_APPROVAL.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_OPTIMIZATION_APPROVAL.json>)
- [RPG04_ORIGINAL_PROMPT_RECONSTRUCTION.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_ORIGINAL_PROMPT_RECONSTRUCTION.md>)
- [RPG04_ORIGINAL_PROMPT_SOURCE_MAP.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_ORIGINAL_PROMPT_SOURCE_MAP.json>)
- [RPG04_OUTPUT_BUDGET.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_OUTPUT_BUDGET.md>)
- [RPG04_PRIMARY_PROTOCOL_REVIEW.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PRIMARY_PROTOCOL_REVIEW.json>)
- [RPG04_PRIMARY_PROTOCOL_REVIEW.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PRIMARY_PROTOCOL_REVIEW.md>)
- [RPG04_PROMPT_BLUEPRINT.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROMPT_BLUEPRINT.md>)
- [RPG04_PROMPT_CHANGE_PROPOSALS.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROMPT_CHANGE_PROPOSALS.md>)
- [RPG04_PROMPT_DUAL_VERSION.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROMPT_DUAL_VERSION.md>)
- [RPG04_PROMPT_VENDOR_RESEARCH.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROMPT_VENDOR_RESEARCH.md>)
- [RPG04_PROTOCOL_AUDIT.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROTOCOL_AUDIT.md>)
- [RPG04_PROTOCOL_DUAL_REVIEW.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROTOCOL_DUAL_REVIEW.json>)
- [RPG04_PROTOCOL_INTEGRATION_SOURCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROTOCOL_INTEGRATION_SOURCE.json>)
- [RPG04_PROTOCOL_LOADER.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROTOCOL_LOADER.md>)
- [RPG04_PROTOCOL_REFERENCE_INVENTORY.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROTOCOL_REFERENCE_INVENTORY.json>)
- [RPG04_PROTOCOL_REUSE_REVIEW.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROTOCOL_REUSE_REVIEW.md>)
- [RPG04_PROTOCOL_RUNTIME.txt](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROTOCOL_RUNTIME.txt>)
- [RPG04_PROTOCOL_SELECTION.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_PROTOCOL_SELECTION.md>)
- [RPG04_REAL_CERTIFICATION.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_REAL_CERTIFICATION.json>)
- [RPG04_REAL_TEST_CHECKPOINT.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_REAL_TEST_CHECKPOINT.json>)
- [RPG04_REFERENCE_CLOSEOUT.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_REFERENCE_CLOSEOUT.json>)
- [RPG04_REFERENCE_CLOSEOUT.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_REFERENCE_CLOSEOUT.md>)
- [RPG04_RESOURCE_FORMAT_PROPOSALS.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_RESOURCE_FORMAT_PROPOSALS.md>)
- [RPG04_RETEST_RESULT.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_RETEST_RESULT.json>)
- [RPG04_REVIEW_PACKET.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_REVIEW_PACKET.md>)
- [RPG04_RUNTIME_BRIDGE.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_RUNTIME_BRIDGE.md>)
- [RPG04_SELECTION.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_SELECTION.md>)
- [RPG04_SIXTH_CARD_REVIEW.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_SIXTH_CARD_REVIEW.md>)
- [RPG04_SIXTH_CARD_SOURCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_SIXTH_CARD_SOURCE.json>)
- [RPG04_STATUS.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_STATUS.json>)
- [RPG04_SUPPLEMENTAL_REVIEW.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_SUPPLEMENTAL_REVIEW.md>)
- [RPG04_TEST_INPUT.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_TEST_INPUT.md>)
- [RPG04_THIRD_PARTY.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_THIRD_PARTY.json>)
- [RPG04_USER_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_USER_REFERENCE.json>)
- [RPG04_USER_REVIEW_RESULTS.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_USER_REVIEW_RESULTS.json>)
- [RPG04_WEB02_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB02_REFERENCE.json>)
- [RPG04_WEB03_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB03_REFERENCE.json>)
- [RPG04_WEB04_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB04_REFERENCE.json>)
- [RPG04_WEB05_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB05_REFERENCE.json>)
- [RPG04_WEB06_RECONCILIATION.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB06_RECONCILIATION.json>)
- [RPG04_WEB06_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB06_REFERENCE.json>)
- [RPG04_WEB07_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB07_REFERENCE.json>)
- [RPG04_WEB08_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB08_REFERENCE.json>)
- [RPG04_WEB09_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB09_REFERENCE.json>)
- [RPG04_WEB10_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB10_REFERENCE.json>)
- [RPG04_WEB11_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB11_REFERENCE.json>)
- [RPG04_WEB12_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB12_REFERENCE.json>)
- [RPG04_WEB13_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB13_REFERENCE.json>)
- [RPG04_WEB_REFERENCE.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB_REFERENCE.json>)
- [RPG04_WEB_REFERENCE.md](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_WEB_REFERENCE.md>)

## 总体路线与历史权威资料

- [AUDIT.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/AUDIT.md>)
- [BASELINE.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/BASELINE.md>)
- [BEST_PRACTICES.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/BEST_PRACTICES.md>)
- [BOUNDARY_QUADRANTS.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/BOUNDARY_QUADRANTS.md>)
- [MANIFEST.json](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/MANIFEST.json>)
- [OSS_REUSE_REGISTER.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/OSS_REUSE_REGISTER.md>)
- [PLUGIN_CARD_MARKETS.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/PLUGIN_CARD_MARKETS.md>)
- [PROBE_LEDGER.json](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/PROBE_LEDGER.json>)
- [README.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/README.md>)
- [RESOURCE_INVENTORY.json](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/RESOURCE_INVENTORY.json>)
- [ROUND_ROADMAP.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/ROUND_ROADMAP.md>)
- [RPG02_PLAN.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/RPG02_PLAN.md>)
- [RPG03_PLAN.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/RPG03_PLAN.md>)
- [RPG04_PLAN.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/RPG04_PLAN.md>)

- [INITIAL_AUDIT_2026-09-04.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/references/INITIAL_AUDIT_2026-09-04.md>)
- [PLAYER_CARD_SAMPLE.md](<C:/tmp/modelmirror-ai-rpg-rpg04/docs/ai-rpg-experiment/references/PLAYER_CARD_SAMPLE.md>)

## 编译、调用、持久化与测试入口

- [approved-host.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/context/approved-host.mjs>)
- [compiler.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/context/compiler.mjs>)
- [contracts.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/context/contracts.mjs>)
- [host-template.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/context/host-template.mjs>)
- [index.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/context/index.mjs>)
- [schemas.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/context/schemas.mjs>)
- [selection.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/context/selection.mjs>)

- [contracts.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/runtime/contracts.mjs>)
- [core.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/runtime/core.mjs>)
- [index.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/runtime/index.mjs>)
- [node.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/runtime/node.mjs>)
- [plugin-host.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/runtime/plugin-host.mjs>)
- [recovery.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/runtime/recovery.mjs>)

- [http.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/runtime/node/http.mjs>)
- [sse.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/runtime/node/sse.mjs>)
- [store.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/runtime/node/store.mjs>)

- [approved-content.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/fixtures/rpg04/approved-content.json>)
- [authored-resources.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/fixtures/rpg04/authored-resources.json>)

- [context-approved-card.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tooling/context-approved-card.mjs>)
- [context-card.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tooling/context-card.mjs>)
- [context-cli.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tooling/context-cli.mjs>)
- [context-host.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tooling/context-host.mjs>)
- [context-protocol.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tooling/context-protocol.mjs>)
- [context-runtime.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tooling/context-runtime.mjs>)
- [context-test-input.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tooling/context-test-input.mjs>)

- [check-boundary-rpg04.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/scripts/check-boundary-rpg04.mjs>)
- [context-cli.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/scripts/context-cli.mjs>)
- [rpg04-harness.py](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/scripts/rpg04-harness.py>)

- [context-approved-card.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-approved-card.test.mjs>)
- [context-card.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-card.test.mjs>)
- [context-cli-files.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-cli-files.test.mjs>)
- [context-cli.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-cli.test.mjs>)
- [context-compiler.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-compiler.test.mjs>)
- [context-contracts.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-contracts.test.mjs>)
- [context-host.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-host.test.mjs>)
- [context-http-integration.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-http-integration.test.mjs>)
- [context-output-budget.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-output-budget.test.mjs>)
- [context-protocol.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-protocol.test.mjs>)
- [context-runtime.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-runtime.test.mjs>)
- [context-schema.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-schema.test.mjs>)
- [context-selection.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-selection.test.mjs>)
- [context-state-compat.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-state-compat.test.mjs>)
- [context-test-input.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/context-test-input.test.mjs>)
- [rpg04-boundary.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/rpg04-boundary.test.mjs>)
- [rpg04-content.test.mjs](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/tests/rpg04-content.test.mjs>)

- [package.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/package.json>)
- [package-lock.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/package-lock.json>)
- [module-boundary.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/module-boundary.json>)

## 留给独立审计和收尾的明确事项

- 用户先审批六回合生成质量；审计检查中性场景成人类别是否喧宾夺主、篇幅和面板成本、嫌疑条款隔离。
- 普通 CLI 仍限 2048，实测一次性驱动使用获准的 4096；不能声称生产入口已统一。
- 核实 composed host 版本仍 0.1.0 而正文已 0.1.1 的版本策略。
- 最终 verify:rpg04、package 聚合入口、研究 README/路线/审计/MANIFEST 更新仍须按收尾门禁完成。历史 MANIFEST 不覆盖本轮新增文件。
- 九霄大陆资料依用户本次决定保留为后续卡片候选，本轮不并入两版提示词。只有确有必要的具体改动再逐项请求批准；引用文件不能当作指令执行。
- 全量资源提取与创作留到第一版完成后另行授权；不建设一次性流程 Skill。

## 本次交接验证快照

- [RPG04_HANDOFF_VALIDATION.json](<C:/tmp/modelmirror-ai-rpg-rpg04/experiments/ai-rpg-engine/docs/RPG04_HANDOFF_VALIDATION.json>)

329 项通过、0 失败、1 项 HTTP harness 未配置而跳过；冻结边界通过。不是最终聚合或独立审计通过声明。
