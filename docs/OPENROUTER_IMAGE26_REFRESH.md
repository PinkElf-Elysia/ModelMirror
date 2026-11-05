# Ripple and GPT Image 2.6 update

Branch: codex/openrouter-nov04-image26, based on current origin/main after merged #427. Scoped to three requested models; no commit, push, PR or deployment authorized this round. No default text/agent models changed.

Frozen public four-source manifest: C:/tmp/openrouter-image26-window/manifest.json. Initial readiness failure is preserved under C:/tmp/openrouter-image26-before: prior receipt aged past five further explicit expiration dates. Receipt now reflects current lifecycle, while refresh regression uses its explicit fixed November 4 clock.

- ricursiveai/ripple-65b-preview: 1,048,576 context, text chat, native tools, $0.20/$0.60 per million input/output tokens; ordinary placement after six rows.
- openai/gpt-image-2.6-flare and openai/gpt-image-2.6-sunburst: replace the two original 2.5 image slots within six rows. Old 2.5 models remain counted and reachable in ordinary positions after six rows.
- Dedicated image contracts support nine aspect-ratio choices, six quality levels, PNG/JPEG/WebP, background, 1–10 outputs, 0–16 references and compression 0–100. Reuse the existing bounded synchronous image workspace; advertised streaming is not enabled, and moderation/complexity passthrough is not exposed.
- Dedicated endpoint pricing retrieved publicly for both models: text input $5/M, image input $8/M, output image $30/M tokens. General catalog prompt price $8/M differs from dedicated text-input rate; keep those sources distinct. UI uses the dedicated fee schedule and does not fabricate a fixed preflight total.

Validation: 89 frontend refresh/placement/pricing tests, backend image suite 15 tests and 19 pricing/updater guard tests passed. Production build and diff checks passed (existing large-chunk warning). No real provider calls, gateway qualification, UI replay, screenshots or human acceptance claimed. Help paragraph updated, not visually validated. Final comparison C:/tmp/openrouter-image26-final/summary.json confirms no missing target models; remaining broader drift is not waived or claimed clean.

Snapshot 790; current receipt live 615, uncertain 132, expired 43, onsite/adaptation denominator 747; 77 uncounted Batch. Upstream 620 non-Batch records. Rollback only this round's local changes; no persisted data/configuration migration.

## Follow-up drift reconciliation

User explicitly requested remaining drift repair. New four-source manifest C:/tmp/openrouter-image26-drift-window/manifest.json, window 2026-11-05 02:03:09–02:03:27 UTC, exact market coverage 620. Before audit C:/tmp/openrouter-image26-drift-before/summary.json retained 16 metadata and 51 structural-market mismatches, no missing models or Batch changes. Reconciled metadata, lifecycle and market snapshot while retaining GPT Image 2.6 specialized parameter guards and front-six-row placement. Strict final audit C:/tmp/openrouter-image26-drift-after/summary.json clean. Counts above unchanged. Volatile observations are separate from structural drift, not inferred capabilities.

Follow-up validation: 92 frontend refresh/image pricing/Batch tests, 24 backend image/Batch tests and 25 script guard tests passed. An initial audit command used the client directory and could not locate the wrapper; rerun from repository root succeeded without bypassing any check. No real provider calls, preview/human acceptance or publication performed. A future PR must re-freeze sources and rerun the complete audit.

## Publication approval

User explicitly waived this round's help, preview replay and screenshot gates and requested PR submission. Pre-PR manifest C:/tmp/openrouter-image26-pr-window/manifest.json captured one additional Gemma 4 26B structural market change, reconciled before strict final audit C:/tmp/openrouter-image26-pr-final/summary.json (clean, exact coverage 620). Current GitHub main and local origin/main both 6aa5924dd64b706a684ab2fd0dcc6da1d9feeb08. No paid calls, merge or deployment authorized or performed. Automatic test and safety gates are not waived.
