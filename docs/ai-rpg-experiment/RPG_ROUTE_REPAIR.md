# RPG route binding repair and Gemini 4 Argon

## Contract

- Authorized 2026-11-05: minimally repair two failed Lord of Mysteries starts, add `google/gemini-4-argon` to RPG text choices, share the existing per-card 100-call pool, then resume combined PR preparation.
- Branch `codex/rpg-platform-redesign`, baseline `d9f20fe691e8ddef8623d779a25a9359717f2e8d`. Previously accepted changes are unstaged and preserved.
- Allowed: exact RPG bridge/stable-route methods, their tests, card model allowlists and exact runtime compatibility, affected help and evidence. Batches are limited to five files.
- Protected: keys, gateway ordering/configuration, other modules, original author HTML/prompts, saved sessions and unknown operations. No schema migration, quota reset, dependencies or public endpoint additions. Optional internal route-pinning parameter preserves old callers.
- Runtime risk: provider dispatch and persistence. Test with local synthetic transport first. Real acceptance is bounded to one Argon text certification plus one new synthetic RPG turn per Gemini 3.8 Flash and Gemini 4 Argon, no automatic retry. No old request is replayed.

## Acceptance and rollback

- Reproduce with two qualified routes: preferred route lacks model metadata, fallback has complete metadata. Assert selection and dispatch use the same connection for synthetic and both Gemini IDs.
- Test exact failure receipts for undispatched route/metadata drift and retain unknown results when evidence/persistence is incomplete. Run `python -m pytest server/tests/test_rpg_bridge.py server/tests/test_provider_chat_stable_service.py -q`.
- Run affected model policy, compatibility, archive host and controlled-provider Node tests; assert all model choices consume one unchanged card budget.
- Before shared cutover capture exact source/data hashes and runtime topology; overlay only reviewed changed files on existing images, preserve all configuration and volumes. Health, model catalogs, grants, old saves and bounded synthetic real turns must be checked.
- Rollback only this patch's images/files while retaining current data and free-opening-compatible readers. Never restore stale session data or replay uncertain operations.

## Results

### Cause and behavior

The catalog selected the qualified fallback route because the preferred gateway lacked complete model metadata. Preflight selected again by model ID, which could choose the other route. The exact-binding check then rejected before dispatch with `rpg_selection_changed`, but its response lacked the bound receipt needed by the RPG host. The two old starts therefore remained unknown.

Preflight now pins the catalog-selected connection. Confirmed pre-dispatch failures return a bound failure receipt; failure to persist that evidence still remains unknown. Duplicate claimed requests remain non-replayable. No old claim, operation or conversation was rewritten.

All six card hosts accept `google/gemini-4-argon`; the God long profile keeps its existing 32768 output limit. Model selection and dispatch retain exact model, parameters and route bindings. Reviewed prior runtime descriptors tolerate catalog revision refreshes without rewriting saved bindings or bypassing the fresh dispatch check. Models share each card's existing 100-call pool; there is no quota reset.

### Executed acceptance

| Check | Result | Scope |
| --- | --- | --- |
| Regression before repair | Failed as expected | Five new route/receipt checks reproduced the defect, including synthetic, Flash and Argon model IDs. |
| Backend targeted checks | Passed in isolated Linux | `python -m pytest server/tests/test_rpg_bridge.py server/tests/test_provider_chat_stable_service.py -q`: 103 checks passed in the first isolated run; the application-import check initially failed because source storage was read-only, then passed from a disposable writable source copy. Four existing FastAPI lifecycle warnings remain. The Windows attempt had 102 passed, one skipped and an import failure due to missing duckdb; it is not a green full run. |
| God long profiles | Passed | `python -m pytest server/tests/test_rpg_bridge.py -q -k god_long`: both Gemini profile cases passed. |
| Model/host regression | Passed | 49 checks across `studio/argon-models.test.mjs`, `studio/controlled-provider.test.mjs`, `studio/free-opening.test.mjs`, `studio/archive-plugins.test.mjs`, `god-card/tests/real-host.test.mjs`, `god-card/tests/model-policy.test.mjs`, and `ancient-card/real-host.test.mjs`. Six real-shaped hosts select Flash then Argon and consume one unchanged 100-call pool; compatibility tampering is rejected. |
| Argon certification | Passed, real provider | One bounded text certification through the existing OpenRouter route; requested and actual model both Argon. |
| Lord of Mysteries starts | Passed, real provider | Two new synthetic browser sessions, one Flash and one Argon. Each saved exactly one complete turn; control receipts show the requested actual model and one dispatch. No old operation was replayed. |
| Shared pool | Passed | The card's existing two attempts plus these two story tests leave used 4, reserved 0, remaining 96 of 100. The separate certification did not debit the RPG pool. |
| Configuration/data preservation | Passed | Route patch cutover retained all seven pre-existing sessions and 43 data files byte-for-byte. Subsequent real tests added two sessions and one normal model-selector grant; all pre-existing data remained unchanged. |
| Final layout/help checks | Passed | The final 390px Summary 2.0 replay exposed a grid minimum-width overflow. Two CSS constraints fixed it; a new offline session, small-summary edit/save and screenshot verified the result. Card TypeScript/Vite build passed; 33 focused client/help/model/summary checks passed; all registered help-image assets passed verification. |
| Static-resource cutover | Passed | The final static-only overlay preserved all nine sessions and 63 data files, exact configuration, plugin grants and all other container IDs; deployed files match the build. |

Real acceptance used exactly one certification and two new story calls. Full Python regression, real summary/memory generation, image generation, every model on every card, and remote CI were not executed as part of this repair. No claim about those paths is inferred from the scoped checks.

### Deployment and rollback

Only the server bridge/stable route files and the RPG model-policy/compatibility files were overlaid on the existing images. The later RPG overlay contains rebuilt static chat assets only. Final images are `modelmirror-server:rpg-route-repair-20261105` and `modelmirror-rpg:rpg-route-repair-narrow-20261105`; client, authoring, newAPI, OmniRoute and the older RPG service retain their prior images and configuration.

Private operational receipts, original source backups and the real-call screenshot remain outside Git. A rollback swaps only the affected image with identical mounts/environment. Keep the reviewed free-opening/Summary 2.0 reader and Argon compatibility for newly created sessions; never restore stale save files or replay unknown requests. Combined PR publication is authorized after this preflight; remote checks remain a separate evidence boundary.


## Mixed HTML / Markdown display repair

The four archive hosts (Xingjian, Ancient, Cultivation and Lord of Mysteries) used an HTML-only display sanitizer. A reply containing native disclosures plus fenced status blocks, Markdown prose and pipe tables therefore lost paragraph and line-break semantics. Earth and God already protected fences but their Markdown parser did not support GFM tables. The browser also bypassed host-rendered HTML for replies containing no HTML tags.

All six paths now share the existing safe presentation pipeline and the RPG engine's already pinned Marked 18.1.1 parser. Fences stay literal, prose is parsed within disclosures, safe font colors become spans, and GFM tables are rendered. Original card-specific classes, reviewed local God images, author line spacing and active-content filtering remain intact. The browser uses the host's display projection for plain Markdown too; an intentionally empty sanitized projection stays empty. Excessive nesting produces a raw-view hint instead of failing the session read.

This change is display-only. No saved raw reply, prompt, history, model parameters, runtime descriptor or plugin grant is rewritten. No new production dependency, model call or migration was added. User-provided prose was replayed only on localhost and is not checked in; the regression fixture uses neutral synthetic content with the same structure.

- Before the fix, all 12 new format checks failed across the six mapped card paths. After the fix, the card package's 58 checks passed; the final raw-view fallback change passed all 13 focused mixed-output checks.
- Client chat/free-opening/summary regression: four files, 55 checks passed; after adding the empty-projection case, the affected workspace file passed all 21 checks. Full client TypeScript and card production build passed. Native disclosure state is preserved across unrelated React renders.
- Actual supplied reply: 9 disclosures, 8 code blocks, 3 tables and 28 paragraphs. The shared session's saved raw text matches the attachment after newline normalization. The normal shared chat page was checked visually, including 390px layout; no new provider dispatch was made.
- The scoped RPG image overlay preserved all 10 sessions and 89 data files byte-for-byte, author-card source files, plugin grants, environment, mounts, gateway readiness and every unrelated container ID. Image: `modelmirror-rpg:mixed-output-20261105-v3`. Rollback image: `modelmirror-rpg:rpg-route-repair-narrow-20261105`, retaining current data.
- PR preflight follow-up: corrected eight unsupported Testing Library query options without changing assertions. Reviewed 16 changed coverage modules and retained all classifications; Python 3.12 coverage tests passed 26/26 and the final inventory validated 976 candidates with zero errors. The initial read-only test container lacked a writable temporary directory; rerunning with isolated `/tmp` passed. These local checks do not claim a full remote CI result.
