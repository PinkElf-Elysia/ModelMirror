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

## External effects and rollback

RPG-01 used no target-site probe and no model call. It made no parent client/server/RAG/memory/plugin/Matrix/Docker/CI change. Before acceptance it performed no Commit, Push, PR, Merge, Deploy, or Publish; the user then authorized only the closeout Commit, Push, and pull request. The resulting repository receipt is appended after creation. Ajv packages were installed only in the isolated module for tests. Rollback is to close the pull request and delete its isolated branch/worktree after verifying their exact identities; the primary checkout requires no code rollback.
