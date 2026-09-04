# Verification and publication boundaries

## Evidence levels

Use these labels consistently:

1. `catalog_present`: the model appears in the frozen public directory.
2. `contract_adapted`: code and UI represent the documented endpoint and price.
3. `configured_available`: an authorized gateway advertises a usable route.
4. `manually_verified`: a bounded real request passed and its sanitized receipt
   was reviewed.

Do not skip levels in prose. A static test or HTTP 200 from the page is not a
provider call, and a provider call is not proof of every format or parameter.

## Test routing

- Always run `client/src/data/models.refresh.test.ts` after snapshot/count/order
  changes.
- Run Batch frontend and backend tests when serving variants change.
- Run the dedicated image, video, STT, TTS, embedding, or rerank tests for a
  specialized operation.
- Run `node scripts/check-multimodal-readiness.mjs` after changing catalog or
  readiness counts.
- Run the frontend build and affected backend tests before handoff.
- Preserve expected drift exit codes: the comparison scripts exit nonzero when
  drift remains. Inspect their JSON; do not relabel that exit as an execution
  failure.
- Freeze HTTP response bytes directly. Do not cast response content to a
  PowerShell string and re-encode it: Windows PowerShell and PowerShell 7 can
  otherwise produce different hashes and false description drift for non-ASCII
  punctuation. Keep a non-ASCII smoke in cross-runtime validation.

## Provider validation

Never read secrets or make a billed request unless the task explicitly
authorizes it. Use a short, non-sensitive fixture and the smallest supported
request when authorization exists. Sanitize IDs, provider errors, content, and
credentials from receipts. A manual result promotes only the tested contract.

## Worktree and preview

Inspect status and worktrees first. Use an isolated worktree from freshly
fetched `origin/main` when the primary checkout is dirty. Do not reuse or stop
another task's preview or container. A preview request authorizes only the
scoped preview; preserve shared-stack configuration and data.

## Publication

Implementation does not imply commit, push, PR, merge, or deploy authority.
Before an authorized PR:

1. fetch the base and inspect topology;
2. freeze a second full evidence window;
3. rerun both catalog audits and readiness validation;
4. run focused tests and build;
5. inspect final and staged diffs;
6. publish only intended files and report any remaining drift.

Rollback is normally the single catalog/adaptation commit. Never delete the
historical readiness receipt or uncertain records to make counts pass.
