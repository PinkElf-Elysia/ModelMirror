# RPG platform redesign: PR preflight, 2026-11-05

## Source and deployment boundaries

- Accepted scope: the staged RPG platform redesign, Summary 2.0 in ordinary chats, unified six-card chat/plugins, creator scrolling and compact identity form, editable free opening, and centered bordered chat entry. Six author-owned HTML configuration pages and author prompts remain unchanged apart from the explicitly requested platform entry/archive handoff.
- Independent candidate: `codex/rpg-platform-redesign`, based on `d9f20fe691e8ddef8623d779a25a9359717f2e8d` (PR #442). Original accepted source at #441 remains in its own worktree.
- #442 arrived during preflight. Five conflicts were resolved by retaining its CG gallery, variable editor and quota explanation together with the accepted layout and user-facing copy. No upstream dependency or server feature was removed; this task adds no dependency relative to #442.
- UI-only synchronization used the accepted #441-based `rpg-pr-final` images, without a global #442 upgrade. A later authorized route repair overlays two server files, three RPG runtime files and final static chat assets. It preserves gateway configuration, keys, feature switches and all other services; see [the route-repair record](../../ai-rpg-experiment/RPG_ROUTE_REPAIR.md).
- Local cutover receipts outside Git record 21 preserved RPG files, five preserved sessions, unchanged creator business data, registrations/grants and gateway readiness. The 148-file runtime/source audit had zero mismatches. Private operational receipts are intentionally not attached.

## Independent preview and visible replay

The first authoring and help-writing pass used the #441 candidate on independent ports 15454/15455/15456. After #442 merged, a fresh latest-main worktree, rebuilt client/card bundles, new preview containers and new synthetic data were created on 15464/15465/15466. No shared mounts or provider credentials were used. The frontend API fallback points to an unavailable loopback address; RPG generation uses fixed offline samples. The creator reports `runtimeReady: false` and `providerCalls: 0`.

The already-written seven guides were replayed on this fresh #442 integration. Browser storage and searches started empty on the new origin; screenshots below were captured from that candidate. The initial shared-stack browser checks were read-only. Later, separately authorized real acceptance used one Argon certification and two new synthetic Lord of Mysteries turns, with no replay of old requests.

| Guide / task | Visible replay and result |
| --- | --- |
| play-rpg-cards | Story library shows six worlds and creator links. Create `雾港来信` through Works management, choose audience, enter opening HTML and system prompt, save and refresh: saved state and rendered opening appear. More contains Export full work package; no real export/import was performed in this final pass. |
| Free opening | Earth setup → centered Enter chat → empty composer with zero turns. Explicitly send synthetic harbour text: one saved fixed-sample turn; sidebar also becomes one. No bound-character disclosure or implicit send. |
| play-god-card | God setup → Enter chat → same empty chat layout and eight-plugin panel. Author configuration HTML remains nested and isolated. |
| use-rpg-rolling-summary | Install then enable Summary 2.0 in the ordinary Earth session. The 2.0 dialog shows default story model, large summary and small summaries. One offline turn creates a small summary; edit, save, close and reopen preserves the edit. No paid regeneration was used. |
| set-rpg-history-window | Enable history, set four complete turns, save and reopen: control value is 4. Full transcript remains visible. |
| use-rpg-memory-palace | Enable memory, add a synthetic harbour entry, save: manual protection and enabled state appear. Choose the offline memory model: selection persists. No automatic memory-provider call was made. |
| choose-rpg-model | Open model chooser, search a nonexistent name: empty-result state. Clear search and select the single offline sample. Offline profile labels remain fixed by the test host; this offline replay did not validate real routing. The later repair acceptance selected Argon in the shared UI and verified successful Flash and Argon receipts and saved turns. |
| branch-rpg-story | Create `雾港 · 另一条路` from turn one. Both branch and original show one turn in the sidebar; original transcript and settings remain readable. No model is called by branch creation. |
| #442 overlap check | Creator Basic settings retains Use variable panel, data/template fields and initial-state preview. Other settings → CG → Add retains Set as special CG and the 0/20 counter. These smoke checks supplement upstream feature tests; real model-driven variable updates/unlocks were not exercised. |

The creator's existing Return to RPG link points to the shared default port, so isolated replay returned through the independently opened preview library instead of following that link. This pre-existing cross-port behavior is not presented as a successful isolated round trip.

## Screenshots and documentation

Updated seven RPG articles plus metadata and cross-links. Eleven fresh PNG screenshots are in `client/public/help-center/d9f20fe6/`, with synthetic content only. Four #442 CG/variable screenshots and its two new guides remain intact. Thirteen superseded RPG screenshots moved out of public assets into the explicitly labeled pre-redesign evidence archive.

Screenshot checks: actual PNG, width 750–1000px, each below 250KB, valid article references. Dialog screenshots retain nearby platform context. The creator screenshot was replaced with the compact identity form. The mobile-summary screenshot was recaptured at a 390px viewport after fixing its grid overflow, then scaled to 780px for the asset-width rule. The temporary preview restart did not retain its tmpfs session files; a new offline session was created through the UI, Summary 2.0 installed/enabled, one fixed-sample turn sent, and its small summary edited/saved before capture. Shared data was unaffected. Both final screenshots were visually inspected.

## Executed checks

| Check | Result | Evidence / scope |
| --- | --- | --- |
| Client production build | Passed | `VITE_RPG_AUTHORING_PORT=15466 npm.cmd run build`; TypeScript + Vite; existing bundle-size warnings remain. |
| Card production build | Passed | `cd experiments/ai-rpg-engine/card-replica; npm.cmd run build`. |
| Client and help tests | Passed | Vitest: 13 selected files, 208 checks. Help rerun after baseline/path updates: 73 checks passed. Final model/summary/help rerun: 33 passed in three files. |
| Authoring | Passed | `npm.cmd run test:authoring`: 890/890, including #442 CG/variable cases. |
| Host, God and Xingjian | Passed | `node --test --test-concurrency=4 studio/*.test.mjs god-card/tests/*.test.mjs xingjian-card/*.test.mjs`: 300 passed, 2 skipped. |
| Linux container host | Passed | `docker exec modelmirror-rpg-pr442-host node --test studio/container-host.test.mjs`: 2 passed; covers the Windows-skipped kernel lease test. |
| Legacy v1 | Passed | Existing and new exact-binding/tamper checks included in host suite. Real copied fixture check skipped because `RPG_B3_LEGACY_COPY` is absent. |
| Compose build arg | Passed | Sanitized configuration resolves creator port 19040 into client build args; runtime-only env is not claimed to alter static assets. |
| Shared synchronization | Passed | Byte/config comparisons and 148-file audit; existing sessions and grants preserved. |
| Scope / secrets / whitespace | Passed | Named task paths only; no conflict markers or common live-key/private-key/token patterns; `git diff --check`; no staged changes. Pattern scan is not a proof that arbitrary secrets cannot exist. |
| Route repair / real providers | Passed | Scoped backend and 49 host/model checks, one real Argon text certification, two real Lord of Mysteries starts; see the linked repair record for initial environment failures and scoped reruns. |
| Full Python suite / remote CI | Not run in local preflight | Do not infer full integration coverage from targeted regression; remote status belongs to the eventual PR. |

## Remaining boundaries and rollback

- User acceptance covers the staged UI. The later real checks prove only the two tested Gemini starts on the selected existing route. Generated-story quality, real summary/memory generation, illustrations, variable updates and external billing are not covered.
- One host fixture test still needs a separately supplied real legacy-copy fixture; no fixture was invented to turn that skip green.
- Keep a Summary 2.0/free-opening-compatible reader if reverting UI. Swap only the previous frontend-capable images with the same mounts/configuration; never restore older save files or replay uncertain operations.
- Shared #441-based synchronization and #442-based PR integration are deliberately separate. Deploying #442's new creator backend is a subsequent scoped deployment, not part of this front-end synchronization.
- The user authorized a combined fix PR after preflight. Its description is prepared locally with these boundaries. Merge and a global #442 backend deployment are outside this authorization.
