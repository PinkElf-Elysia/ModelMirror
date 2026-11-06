# November 6 OpenRouter refresh

Base: `45ee30b5`, branch `codex/openrouter-nov06-refresh`. Initial source window: `C:/tmp/openrouter-nov06-window/manifest.json`; before/after audits: `C:/tmp/openrouter-nov06-before/summary.json` and `C:/tmp/openrouter-nov06-final/summary.json`.

- Added Gemini Omni 1.1/1.2 Flash, OpenRouter Auto Code and paid Ling 3.0 Tiny after six rows. Old free or uncertain IDs are not replaced by paid IDs.
- Omni uses existing dedicated asynchronous video generation with first/last frames and catalog duration/resolution limits; 1.1 offers 3–10 s, 1.2 3–15 s. The bounded path requires native audio and a cost estimate for manual verification; no silent/no-audio egress. Source-video editing and `task` passthrough remain unadapted. Existing manual provider gate is retained.
- Auto Code is a dynamic-price chat router, not a dedicated image generator. Upstream modality declaration is not proof that every selected provider route accepts it.
- Reconciled 27 model metadata and 48 market structural mismatches, plus DeepSeek V4.1 Flash Batch pricing/parameters. Batch count remains 77, excluded from snapshot count. Nemotron transcription uses $0.06000012/audio hour based on the current audio-second SKU, not token pricing. Old readiness receipts remain historical evidence.
- Snapshot: 794 = 619 live + 133 uncertain + 42 expired. Upstream non-Batch exact coverage 619. 752 onsite/adaptation entries is not a real-callable count.

Scope is catalog, video request/fee guard and tests only. No default model, credentials, dependency declarations, lockfile, persistence, or shared runtime changes. For rollback, revert this scoped local increment. No manual UI or provider acceptance is claimed.

Validation: 85 frontend catalog/placement/Batch tests, 62 backend video/STT/Batch tests and 25 script guard tests passed. Final strict audit `C:/tmp/openrouter-nov06-handoff/summary.json` clean. Initial build reported a duplicate disabled attribute (fixed) and missing React in the newly referenced RPG package; installed its existing locked dependencies with `npm ci --ignore-scripts` in the isolated worktree, without changing manifests or locks. npm reported two high-severity advisories for that existing dependency set; no unscoped upgrade attempted.

## Publication refresh

- Added `parallel/decisions-fast` to the existing text Decisions workspace, not Chat. The site retains a 50-question cap. The adapter forwards text state and typed questions to `/api/alpha/decisions`, preserving structured answers and citations. The UI warns that Parallel may use submitted content for live web research.
- The [public endpoint](https://openrouter.ai/api/v1/models/parallel/decisions-fast/endpoints) and market SKU `decisions:requests` both showed $0 per request on November 6. This is a request rate, not a Token estimate, permanent free status or a verified billing receipt.
- Reconciled 7 remaining catalog metadata and 4 structural market records in the retry window. The final publication freeze added structural market drift for `~deepseek/deepseek-v4-flash-latest`, `deepseek/deepseek-v4-flash-0731`, `deepseek/deepseek-v4-pro`, and `google/gemma-4-26b-a4b-it`; synchronized those changes. Strict audit (`C:/tmp/openrouter-nov06-release-161422/manifest.json`, `audit-final/summary.json`) is clean: 620 exact upstream non-Batch IDs, 795 local snapshots (620 live + 133 uncertain + 42 expired), 753 onsite entries and 77 Batch variants outside the snapshot count.
- Backend regression: 97 passed, including mocked Parallel request and citation preservation. Help, preview replay and screenshots were waived for this publication. No real provider call, manual acceptance, merge or deployment was performed.
