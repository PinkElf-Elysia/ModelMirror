# Shared save compatibility and Harry Potter cover

## Task contract

- Base: initially origin/main c9db3034, rebased onto 0a28b830 (PR #465 plugin upgrade); branch codex/rpg-shared-save-compat-cover; isolated worktree started clean.
- Scope: reviewed shared runtime compatibility mappings, Blue Fish historical prompt binding, library-only Harry Potter cover, related offline tests and help evidence.
- Frozen: current card prompts, Harry Potter assembly/host/transport, shared services/config/data, dependencies and public API. No data migration and no real model calls.
- Risk: saved-session admission must continue to reject changed prompts, setup, transports and unknown code. Old descriptors and receipts must not be rewritten.
- Batches: shared mapping plus frozen digest fixtures and regression; Blue Fish exact compatibility plus history-prompt regression; cover plus help/preview evidence. Each batch has its own check before proceeding.
- Minimum checks: shared-runtime-compatibility.test.mjs; Blue Fish tests; RpgLibrary test. Broader checks: output-edits/failed-turn/HP suites, frontend build, isolated preview and saved-data read checks.
- Rollback: revert this PR's library/compatibility additions; before redeploying older code preserve the existing reviewed compatibility overlay. Never rewrite saved descriptors to hide a mismatch.
- Authorization: user requested commit/push/PR; no merge or new shared deployment in this task.

## Validation

- Passed: 43/43 final offline Node tests across shared compatibility, output edits, failed-turn recovery, plugin upgrades, Blue Fish current/historical bindings and Harry Potter prompt/asset/session recovery.
- Passed: frontend production build and standalone card-replica build. Existing bundle-size/dynamic-import warnings remain.
- Passed: 48/48 frontend behavior tests (RpgLibrary 7, RpgWorkspace 36, RpgPluginUpgrade 5), including the final cover position and unchanged route. Help catalog: 17 passed / 2 existing failures; the new Harry Potter help assertion passes. Total combined run: 65 passed / 2 failed.
- Passed: final standard RPG Docker build after integrating PR #465, tag modelmirror-rpg:compat-cover-0a28b830. Inside that image, 18/18 shared-runtime, plugin-upgrade and Blue Fish legacy regressions pass with --network none, read-only root, isolated /tmp and no shared mounts. Docker was briefly unavailable earlier; the final build succeeded after it returned. No shared-stack restart or deployment attempted.
- Passed: final-code local preview at isolated ports 19888/19889, with empty private test data and real controls disabled. Search Harry Potter, inspect supplied cover, enter unchanged card and observe original letter-opening panel; reload/reset search and replay. No generate action.
- Help screenshot: client/public/help-center/c9db3034/harry-potter-cover.jpg, captured from the visible final preview without editing its pixels. Cover file is byte-identical to the user attachment (SHA256 f6f70b866bd76a5cae607c341b74784423cba83b1f48774353927732de302265).
- Current prompt resources and Harry Potter runtime files are unchanged from the base. The Blue Fish historical prompt is copied byte-for-byte from the previously deployed artifact (SHA256 885def05b8628dce5428b39dcc5387c2548d082f7dc1635f7c765d5fa810f59a).

## Compatibility evidence and limits

The former shared overlay contained 15 exact fingerprint groups. The repository now additionally covers the currently deployed release and the two complete historical descriptors already named by the failed-turn baseline. Fixtures include only runtime code/content digests and routing metadata; user characters, conversations, saved IDs, requests and credentials are excluded. Tests create synthetic setup bindings and invoke the real runtime validators. Unknown artifact/author/setup/model changes remain rejected, and saved runtime hashes are preserved.

The prior failed-turn test rebuilt an old descriptor from current source plus five old file digests. After the shop baseline changed other host artifacts this hybrid no longer matched its original expected hash. It now reads the complete frozen descriptor; the original expected hashes and negative assertions are unchanged.

The Blue Fish adapter carries exact historical adapter/transport tuples and a historical prompt resource. Reopening and mocked continuation retain the original system text; newly created sessions use the untouched current system text. No migration, receipt rewrite, broad version bypass, new dependency, or real provider generation is introduced.

## Existing failures and final integration

The help catalog has two existing failures: manage-rpg-history has 11 numbered steps against the 8-step limit, and play-rpg-cards points at d9dd7094 screenshots while its declared baseline is 45ee30b5. Both were reproduced in an untouched c9db3034 worktree (16 passed / 2 failed); the final integrated branch has the identical failures. No test was disabled or relaxed. The new Harry Potter screenshot has its matching c9db3034 metadata; the prior card screenshot remains linked as historical reference.

The final main integration retains both plugin-upgrade compatibility groups and adds the reviewed shared-release groups. The library/card entry source and cover screenshot remain unchanged by that integration; screenshot evidence identifies its actual c9db3034 capture baseline rather than claiming it was recaptured. The final front-end build and regression cover 0a28b830 plus this patch.

The frozen Blue Fish historical resource retains four original trailing-space lines so its deployed SHA-256 stays exact. These are the only expected diff whitespace findings; source and other documents pass diff --check. Dependencies are unchanged; existing build warnings (bundle size, dynamic/static import overlap, npm dependency audit notices) remain.

## Reproduction

From `experiments/ai-rpg-engine`:

```sh
node --test studio/shared-runtime-compatibility.test.mjs studio/output-edits.test.mjs studio/failed-turn-compatibility.test.mjs studio/turn-failures.test.mjs studio/plugin-upgrade-compatibility.test.mjs plugins/upgrade.test.mjs blue-fish-card/blue-fish.test.mjs blue-fish-card/legacy-runtime.test.mjs harry-potter-card/harry.test.mjs
docker build -f studio/Dockerfile -t modelmirror-rpg:compat-cover-0a28b830 .
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev modelmirror-rpg:compat-cover-0a28b830 node --test studio/shared-runtime-compatibility.test.mjs studio/plugin-upgrade-compatibility.test.mjs blue-fish-card/legacy-runtime.test.mjs
```

From `client`:

```sh
npm run build
node node_modules/vitest/vitest.mjs run src/pages/RpgLibrary.test.tsx src/pages/RpgWorkspace.test.tsx src/pages/RpgPluginUpgrade.test.tsx src/content/help-center/helpContent.test.ts --configLoader runner --maxWorkers=1 --fileParallelism=false
```

The combined frontend command exits 1 only for the two reproduced help baseline failures documented above. No production rollout, merge, new credential read, or real model/image invocation was performed.
