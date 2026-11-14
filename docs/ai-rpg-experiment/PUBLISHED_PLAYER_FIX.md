# Published player and large forms

Baseline: 0cb019df. Branch: codex/rpg-published-player. Initial tree clean.
Scope: authoring display bridge, publish entry, independent published player and story-library links, focused offline tests and help. No provider requests, data migration, shared-stack deployment, commit or publication.
Acceptance: compiled browser bridge sends bounded control deltas with many unchanged options; pending input survives patches. Published entries open immutable-version player routes without editing/preview controls; editing stays in workbench. Offline HTTP publish/load and existing-session contracts pass.
Preservation: no writes to shared data; preview uses isolated store. Revert code and stop only this task's preview to roll back. No production dependencies.
Batches: display bridge + tests; player routing/chrome + tests; library links/help. More than five files overall is necessary to keep producer and consumer routes and documentation consistent, each batch remains focused.

## Verification (2026-11-13)

- `node --test experiments/ai-rpg-engine/authoring/{published-player,large-form,presentation,library,creator-experience}.test.mjs` (expanded explicit paths in PowerShell): 31 passed. After the final help/chrome edits, the 12 directly affected tests passed again.
- `npm.cmd exec -- vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false src/pages/RpgLibrary.test.tsx src/pages/RpgAuthoringEntry.test.tsx` from client: 29 passed.
- `npm.cmd run build` from client: passed. Initial missing React resolution in the isolated card-replica checkout was resolved by reusing its existing node_modules junction; no dependency manifests changed. Existing bundle-size warning remains.
- `node --check experiments/ai-rpg-engine/authoring/browser.mjs` and `git diff --check`: passed.
- Isolated Python Playwright/Chromium: imported and published a copy of the Harry Potter package; clicked the publish dialog; entered the dedicated player; filled a character name; switched all ten ability categories; checked generated profile; switched to chat and back without losing the profile; checked desktop 1440px and mobile 390px layouts and visible edit link. No browser script errors or HTTP errors in the final interaction run. No provider request was made.
- Help screenshot: new PNG at 960px wide, 147,897 bytes. `npm.cmd run verify:help-images` still reports nine pre-existing issues in unchanged JPG references and unrelated orphan screenshots. This change does not fix that global check.

## Changed files

- authoring/presentation.mjs and large-form.test.mjs: delta control synchronization; retain 128-change bound, preserve pending edits and radio/option state.
- authoring/server.mjs, browser.mjs, published-player.mjs, published-player.css and published-player.test.mjs: immutable `/play` entry, old-link redirect, independent player chrome and retained opening state.
- client/src/pages/rpgLibrary.ts, RpgPage.tsx and RpgLibrary.test.tsx: published story-card links target the player.
- creator-guide.html, publish-rpg-work.md and replacement help screenshot: current publishing flow and player.

## Preview and boundaries

Isolated preview is on port 19740. Its work store is under `.rpg04-work/published-player-preview/final-data`; it only has an offline fixture provider. Preview URL and browser evidence are in the adjacent `preview.json` / `acceptance.json`. Author prompts and shared files were not modified. The original shared draft was not published. No commit, push, PR or shared deployment was performed.

The existing sandbox lifetime and instance caps remain enforced. Abruptly terminating a browser test without pagehide can leave a short-lived preview until expiry; test contexts now navigate away and wait for release before closing. No capacity limit was relaxed. Shared authoring was previously running without its conversation runtime; deploying the UI alone does not enable real generation. That runtime configuration is outside this offline change.

Rollback: restore the changed source files and stop only this task's isolated preview. Published versions and session schemas are unchanged; no data migration or shared-data restore is required.
## Follow-up: native conversation (2026-11-13)
User rejected the workbench playback remaining in the published conversation. Keep the in-progress work; replace that UI, not session protocols. Scope: dedicated published conversation controller/view, card workspace stylesheet reuse, thin subscription/command interface to the existing joint session controller, offline browser tests. No shared state changes, runtime fingerprint changes, schema migration or provider calls. Batches: UI bridge + player + view (five files), then static routes/styles/tests/help. The dedicated timeline, sidebar and composer must be exercised with saved turns, not only an empty page. Roll back these frontend changes without rewriting any sessions.


### Follow-up result

- Published conversation now mounts `published-chat.mjs` / `.css`, reusing the builtin `card-replica/src/chat-workspace.css` layout. It has a session sidebar, continuous saved-turn list, bottom composer, model/plugin actions and reading preferences. The old playback panel is not mounted in player mode; workbench editing keeps it.
- `joint-ui.mjs` exposes subscription and existing commands; changing session invalidates late estimate results. Per-session browser drafts survive switching and reload. A late opening initialization cannot overwrite an already visible chat.
- No runtime fingerprint inputs, session schemas, shared work/session/config data or permissions were changed. Session discovery is the version-scoped browser-known list plus branch references, not a server-wide index. This UI change does not claim builtin-card feature parity for regeneration/recycle-bin or enable a real-provider runtime.
- Final Node checks: 70 passed across published-chat, published-player, joint-client, creator-experience, large-form, presentation, library and playback-ui; syntax checks and diff whitespace check passed. Earlier client 29 tests and build remain the evidence for the unchanged React-side edits.
- Isolated Playwright final run passed: saved fixture reply, two-session draft roundtrip, plugin dialog, mobile sidebar, reading mode and return to original opening; no JavaScript errors. An intentionally different first-turn draft produced the expected opening-mismatch validation, cleared on session switch. One successful fixed fixture reply, zero real model calls. Subsequent browser runs reused that saved turn without sending again.
- Early setup used a simulated 128K context smaller than the long Harry Potter prompt under the existing byte-based estimate; this fixture-only attempt stopped before provider dispatch. Preserved separately on 19742. Final 19743 uses a 1M simulated catalog capacity and unchanged author prompt. Failed browser contexts exhausted temporary opening sandboxes; backed up only this task's final fixture store and restarted its verified process to release them, without raising caps.
- Current acceptance preview: port 19743, immutable version and URL in `.rpg04-work/published-player-preview/native-final-preview.json`; final browser result in `native-acceptance.json`, desktop/mobile screenshots beside it. Old 19740 is superseded for this acceptance. Shared stack remains untouched. No commit, PR or deployment performed.

Follow-up files: authoring/published-chat.mjs, published-chat.css, published-chat.test.mjs, published-player.mjs, published-player.css, published-player.test.mjs, browser.mjs, joint-ui.mjs, server.mjs; publish-rpg-work.md and this record. Restore these frontend changes to roll back; no session rewrite required.

Final visual pass: plain-text replies now use native prose rather than a fixed-height frame; HTML/Markdown remains in the sandbox. The six directly affected tests passed after this change, and the complete desktop/mobile interaction check passed again without another send. The direct `#chat` URL was separately checked with no selected session and a late opening load; composer stayed locked until explicit session setup.


## Authorized shared deployment (2026-11-14 UTC)

User accepted the isolated UI, then explicitly authorized deployment, preservation of all works/sessions/plugin settings, zero real-model calls, verification and a separate PR. This section supersedes the earlier no-deployment status.

- Baseline remained `origin/main` at `0cb019df`. Client built with the existing authoring port `19040`; existing client proxy environment and network were retained. The authoring image includes current authoring/character dependencies and the shared card stylesheet, using the unchanged installed dependency lock. No production dependency or schema change.
- Updated only `modelmirror-client` and `modelmirror-rpg-authoring`. The builtin-card RPG host `modelmirror-rpg-next` retained its original container ID, mounts, image and configuration. Authoring stays in its pre-existing creator-only mode; this does not enable real story generation for locally published works.
- Before cutover: private tar backups and file-hash inventories; a second work-library backup after its writer stopped. Three works and one published entry were checked against an isolated copy before cutover. Exact source/host/port checks remain enabled.
- After cutover and browser checks: all 10 work-library files preserved except the expected `works/owner.json` process-lock replacement. All 421 RPG data files are byte-identical, including session data, plugin registries/grants and call ledgers. No existing work was published, rewritten or migrated.
- Shared browser acceptance: `/rpg` refreshed its local library and linked published cards to `/play`; full opening/chat entry, direct `#chat`, loaded work title, absent old playback panel and mobile session drawer passed. All persistent-write/model endpoints were blocked in the browser test; only read/display preparation and release were permitted. Zero browser script errors and zero real model calls. The no-runtime asynchronous title refresh was corrected and rechecked in both the copied store and shared stack.
- Final checks: 71 relevant Node tests and 29 frontend tests passed; client typecheck/build passed with its existing bundle-size warning. After the final two-file initialization fix, the six directly affected tests and deployed browser checks passed again. The previously documented nine unrelated help-image issues remain outside this change.

Deployment artifacts, backups, configuration snapshots, hash inventories, screenshots and receipts remain private at `C:/tmp/modelmirror-published-player-deploy-20261114/`; none are committed. Deployed images: `modelmirror-client:published-player-20261114` and `modelmirror-rpg-authoring:published-player-20261114-r2`.

Rollback: stop only the two new frontend/authoring containers, restore the retained stopped `*-before-published-player` containers to their original names and start them with the same persistent volume. Keep later works and sessions; do not overwrite their data with the backup. An emergency data restore would require separate reconciliation of writes after the backup. The RPG conversation host needs no rollback. No merge authorized or performed.

Before PR submission, `origin/main` advanced to `74917aee` (voice-library PR #470). Its changed paths do not overlap this patch. Deployment remains the tested `0cb019df` baseline plus this fix; the PR targets current main without reverting the independently merged voice changes.
