# RPG-01 acceptance record

## State

- Round: `RPG-01 — card core semantics and executable contracts`
- Fixed base: `origin/main@06ef51ae8d58c4e33029f02ab7263e24066734b2`
- Isolated worktree: `C:\tmp\modelmirror-ai-rpg-rpg01`
- Branch: `codex/ai-rpg-rpg01-contracts`
- Delivery state: `accepted`
- Claim state: `claimAllowed=true`; the RPG-01 manual gate is closed.

P0 imported the 13-file research snapshot without changing bytes. The imported `MANIFEST.json` initially had SHA-256 `30A35F365D06D512253A549C4E5EB58384DBE2BBD72484DF9CF21EF678160D49`, and all 12 entries managed by that manifest matched. The primary checkout started at `2434257fa9db630ac7b247f73010457f94192f8f` with status SHA-256 `C2978F79CFF526606CF211B0CF88871EFC33F7F335F447A75EA7D79907385D80`.

At final handoff, the primary checkout remained on `codex/workflow-agent-runtime-strategy` at the same HEAD. Its source `MANIFEST.json` still had the initial SHA-256 and all 12 source-file hashes still matched; `experiments/ai-rpg-engine` remained absent there. The current raw `git status --short` SHA-256 was `009B060CF76E94EDE5BF1450988F0C00FD858CB06B1F55D2B0AF05A13DAE80AC`, which does not reproduce the stored P0 status fingerprint. Because P0 retained only the old digest rather than its path list and byte-serialization recipe, full dirty-tree byte identity cannot be proven or its difference attributed. The exact-base parent-scope gate and final path inventory show no RPG-01 change outside the two allowed worktree prefixes; this remaining observation is left for manual acceptance rather than reported as a passing invariant.

## Manual acceptance

The user accepted RPG-01 on 2026-09-04 after the automated results and the unresolved primary dirty-status fingerprint boundary were reported. This closes the RPG-01 manual gate without retroactively describing that fingerprint as a passing invariant. The same instruction authorized the scoped closeout Commit, Push, and pull request. Merge, Deploy, Release, and Publish remain unauthorized. RPG-02 may now be planned separately but has not started and receives no implementation authority from this acceptance.

## Repository receipt

- Implementation commit: `6ebcf1b0ee9e4ad4e911aaba36b8dc2fc6b6c4ec`
- Remote branch: `codex/ai-rpg-rpg01-contracts`
- Pull request: [#359 — feature: 添加 AI RPG 核心合同](https://github.com/PinkElf-Elysia/ModelMirror/pull/359)
- Pull request target and recorded state: `main`, `OPEN`, non-draft
- Locally observed `origin/main` at pull request creation: `34f804fa94d24b6ef1837248c25f5e15028aa01c`
- External effects at recording: Commit, Push, and pull request creation completed; Merge, Deploy, Release, and Publish were not performed.

## Implemented contract evidence

| Gate | Evidence |
|---|---|
| Isolation | Module policy, executable boundary scan, exact-base parent-scope scan, and temporary adversarial fixtures |
| Card package | Strict Draft 2020-12 schema plus global ID, provenance, typed-reference, state, plugin-requirement, and extension policy checks |
| Player setup | Full fictional sample with five talents; background/identity, rank/power, ownership/activation, and text/permission separation |
| Turn exchange | Four input kinds and the five-field proposal; query, suggestion, information-module, and typed state-proposal authority checks |
| Plugin boundary | Closed manifest vocabulary, exact versions, static dependencies, required blocking, recommended fallback, and no installation action |
| Plugin closure | Card readiness traverses only required/recommended dependency closures; unrelated manifests are ignored and every recommended transitive failure keeps its declared fallback |
| Extension preflight | Iterative JSON/depth/node checks reject cycles and executable-shaped compound keys before structural validation |
| Determinism | Stable sorted diagnostics, opaque content handling, frozen-input and repeated-call tests, no contract I/O or model/network call |

The focused suite contains 5 boundary tests and 28 contract tests. On 2026-09-04, the isolated worktree produced the following automated evidence:

| Command or gate | Result |
|---|---|
| `npm.cmd ci` | Exit 0; 5 packages installed, 6 packages audited, 0 vulnerabilities reported |
| `npm.cmd run test:boundary` | Exit 0; 5 passed, 0 failed, including a descendant-HEAD publication regression |
| `npm.cmd run test:contracts` | Exit 0; 28 passed, 0 failed |
| Aggregate dependency, fixture, document, boundary, and parent-scope gate | Exit 0; `RPG01_AUTOMATED_GATES_OK` |
| Fixed parent scope | Passed at the required base; 32 changed paths, all under the two allowed prefixes |

These results established automated readiness for manual review. Manual acceptance is now recorded, so the delivery state is `accepted` and `claimAllowed=true`.

## Required final commands

```powershell
cd C:\tmp\modelmirror-ai-rpg-rpg01\experiments\ai-rpg-engine
npm.cmd ci
npm.cmd run test:boundary
npm.cmd run test:contracts
npm.cmd run verify:rpg01 -- --base 06ef51ae8d58c4e33029f02ab7263e24066734b2
git -C C:\tmp\modelmirror-ai-rpg-rpg01 diff --check
git -C C:\tmp\modelmirror-ai-rpg-rpg01 status --short
```

The parent frontend/backend suite is intentionally not an RPG-01 gate because the parent runtime is unchanged. Module contracts, adversarial boundaries, parent scope, dependency registry, research-document hashes, and the probe ledger are authoritative for this round.

## Exclusions and downstream handoff

RPG-01 does not prove a running RPG, model/provider behavior, prompt quality, worldbook retrieval, resource conversion, plugin execution, rendering safety, UI usability, market behavior, or long-session continuity. HTML-like content is proven only to remain opaque data. ModelMirror still owns models, credentials, routing, budgets, sessions, cancellation, receipts, and memory.

The manual gate for RPG-02 consumption is satisfied, but RPG-02 still requires its own plan and explicit implementation instruction. RPG-03 consumes turn/plugin boundaries for an isolated runtime and ModelMirror adapter. RPG-04 defines the original prompt and context algorithm. RPG-05 renders information modules safely and keeps player choice separate from model suggestions. None may reinterpret private extension data as a core task, economy, save, death, rebirth, settlement, or inheritance engine.

## Next-round model handoff

The current Sol-led task owns the complete RPG-01 closeout because only deterministic repository receipt, validation, and pull-request checks remain. Starting with RPG-02 planning, the recommended operating model is Astra as the primary agent and Sol agents for bounded, independently verifiable subtasks. Astra retains architecture decisions, exception handling, integration, final acceptance, and any stateful computer interaction. No two agents may operate the same browser page or other stateful UI concurrently.

Copyable transition prompt for the next Astra-led task:

```text
你是 AI RPG 实验线后续轮次的 Astra 主智能体。请采用“Astra 主导，Sol 执行明确子任务”的方式继续。

交接事实：
- RPG-01 已由用户于 2026-09-04 人工验收，状态为 accepted、claimAllowed=true。
- 固定开发基线为 06ef51ae8d58c4e33029f02ab7263e24066734b2；隔离分支为 codex/ai-rpg-rpg01-contracts。
- 实现提交为 6ebcf1b0ee9e4ad4e911aaba36b8dc2fc6b6c4ec；PR 为 https://github.com/PinkElf-Elysia/ModelMirror/pull/359。
- RPG-01 的权威材料位于 docs/ai-rpg-experiment/** 与 experiments/ai-rpg-engine/**。合同测试 28/28、边界测试 5/5、聚合门禁 RPG01_AUTOMATED_GATES_OK。
- RPG-01 没有运行时、模型接入、提示词编排、资源转换、UI、市场或动态插件加载；没有使用网站探针或模型调用，首轮 20 次探针额度仍为 20。
- 用户只授权了 RPG-01 的 Commit、Push 和 PR；未授权 Merge、Deploy、Release、Publish。RPG-02 尚无实施授权，必须先按既有路线单独制定计划。

你的交接事项：
1. 先读取 RPG01_STATUS.json、RPG01_ACCEPTANCE.md、研究 README、路线图、审计与 MANIFEST，并实时核验 PR #359 和工作树状态；不要把可能变化的 CI 状态当作既成事实。
2. 以既有合同为消费边界制定 RPG-02 计划，保持“一切皆插件”、卡片市场与最小核心框架的架构方向，不把任务经济、存档、死亡复活、跨世界继承等卡片特化机制硬编码进核心。
3. 由你负责范围判断、架构取舍、异常处理、跨子任务整合、最终门禁和向用户报告。涉及浏览器或其他有状态电脑界面时由你单独串行操作。
4. 未获得用户明确实施授权前，只完成计划和可审计材料；不要开始 RPG-02 代码、探针、模型调用、合并或部署。

Sol 子智能体执行方式：
- 只下发边界清楚、互不重叠、可独立校验的任务，例如单一 Schema/fixture、单组测试、许可证核对、文档一致性检查或只读资料归纳。
- 每项任务写明允许路径、输入事实、禁止事项、预期产物和门禁；默认不得 Commit、Push、建 PR、调用模型、使用探针或操作共享浏览器状态。
- 要求 Sol 只返回必要结论、证据、文件与命令结果；你必须复核其实际改动和测试，不直接把子智能体的完成声明当作验收。
- 遇到跨模块设计、门禁漂移、异常 UI 状态、证据冲突或范围不清时，由 Sol 停止并上报，你继续判断；不要让多个智能体并发修改同一文件或操作同一页面。
```

## External effects and rollback

RPG-01 used no target-site probe and no model call. It made no parent client/server/RAG/memory/plugin/Matrix/Docker/CI change. Before acceptance it performed no Commit, Push, PR, Merge, Deploy, or Publish; the user then authorized only the closeout Commit, Push, and pull request. The repository receipt above records those completed effects. Ajv packages were installed only in the isolated module for tests. Rollback is to close the pull request and delete its isolated branch/worktree after verifying their exact identities; the primary checkout requires no code rollback.
