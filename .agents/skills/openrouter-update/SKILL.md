---
name: openrouter-update
description: Audit and refresh ModelMirror's OpenRouter model snapshots, serving variants, lifecycle states, market sidebar metadata, and specialized image, video, audio, transcription, embedding, or rerank contracts. Use for scheduled OpenRouter drift checks, adding newly listed models, Batch reconciliation, metadata drift repair, pre-PR full-catalog verification, or explaining snapshot and callable-count differences.
---

# OpenRouter update

Keep the catalog reproducible, reviewable, and honest about what has actually
been verified. Use the repository scripts as the only implementation of catalog
parsing; this skill coordinates them and adds safety boundaries.

## Choose the mode

State the selected mode before acting.

- `audit` is the default. Fetch public catalog data, freeze it, run comparisons,
  and report drift. Do not edit the repository.
- `update` requires an explicit request to change the catalog. Work from the
  latest `origin/main` in an isolated worktree when the primary checkout is
  dirty or another task is active.
- `preview-publish` is not implied by `update`. Start a preview, commit, push, or
  create a PR only when the user explicitly authorizes that exact action.

Scheduled tasks use `audit` unless their saved prompt explicitly requests
updates. They must not read API keys, make billed model calls, change the shared
stack, commit, push, or create a PR.

## Freeze one evidence window

Run `scripts/fetch-and-manifest.ps1` from this skill. It fetches and hashes four
public sources in one bounded window:

1. the complete model directory;
2. the dedicated image directory;
3. the dedicated video directory;
4. the model-market sidebar data.

If any source fails or changes shape, stop and report an incomplete evidence
window. The fetcher rejects paginated or truncated model results and requires
the market's unique base-model coverage to equal the non-Batch directory; the
audit wrapper repeats the cross-source and local-comparison coverage checks.
Never interpret a partial fetch as “no drift.” Keep the frozen files outside
the repository unless the user explicitly asks to retain an audit artifact.

The fetch script rejects repository-internal output by default. Pass
`-AllowRepositoryOutput` only when the user explicitly requests a durable
in-repository evidence artifact. Each audit run also needs a new or empty output
directory; never overwrite a prior result.

If the user supplies a complete frozen manifest and forbids network access,
reuse that evidence window directly and do not run the fetch script again.

For a specialized target, also inspect its current model page, filtered catalog
entry, endpoint data, and official contract documentation. Do not send user
content or call the model merely to discover its contract.

## Audit before editing

Run `scripts/audit-current.ps1` from this skill against the complete manifest.
It delegates to:

- `scripts/audit-model-modalities.mjs` for presence, lifecycle, Batch, and raw
  metadata;
- `scripts/audit-openrouter-classifications.mjs` for operations, jobs, pricing
  basis, filter options, and market-sidebar metadata;
- `scripts/check-multimodal-readiness.mjs [report-path]` for the versioned local
  readiness receipt.

The readiness script accepts only an optional report path. It does not accept
catalog, image, or video arguments, and it does not prove real-provider
callability.

The audit wrapper exits `0` when no actionable drift remains and exits `2` for
catalog, contract, lifecycle, Batch, pricing, or structural-market drift.
Volatile benchmark and tool-call observations stay in `summary.json` but do not
alert by default; pass `-IncludeVolatileAsDrift` only when those observations are
intentionally gating. Scheduled monitors must use the default behavior and
persist `summary.monitor.actionable_signature` in automation-owned state outside
the repository. Notify on the first exit `2` or when that signature changes;
stay quiet when the same actionable backlog repeats. Fetch/audit execution
failures always notify. Any other non-zero exit, a missing complete
`summary.json`, or a retained `INCOMPLETE` marker is an execution failure; do
not replace the last successful monitor state. After every successful audit,
including clean exit `0`, persist the new status and signature outside the
repository so a drift that disappears and later returns can notify again.
Scheduled audits must not pass `-AllowDrift` or `-IncludeVolatileAsDrift`.
Use `-AllowDrift` only for an explicitly interactive inspection where the
caller consumes `summary.json` regardless of drift.

A scheduled runner can invoke the complete read-only path without inspecting
the implementation:

```powershell
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path .).Path
$skill = Join-Path $repo ".agents\skills\openrouter-update\scripts"
$snapshot = Join-Path ([IO.Path]::GetTempPath()) (
  "openrouter-update-" + [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
)
$manifest = & (Join-Path $skill "fetch-and-manifest.ps1") `
  -OutputDirectory $snapshot
if (-not (Test-Path -LiteralPath $manifest)) {
  throw "OpenRouter evidence manifest was not created"
}
$auditOutput = Join-Path (Split-Path -Parent $manifest) "audit"
$powerShell = (Get-Process -Id $PID).Path
& $powerShell -NoProfile -ExecutionPolicy Bypass -File `
  (Join-Path $skill "audit-current.ps1") `
  -RepositoryRoot $repo `
  -SnapshotDirectory (Split-Path -Parent $manifest) `
  -OutputDirectory $auditOutput
$auditExit = $LASTEXITCODE
if ($auditExit -notin 0, 2) {
  throw "OpenRouter audit execution failed with exit $auditExit"
}
$summary = Get-Content (Join-Path $auditOutput "summary.json") -Raw |
  ConvertFrom-Json
if (-not $summary.status -or (Test-Path (Join-Path $auditOutput "INCOMPLETE"))) {
  throw "OpenRouter audit did not produce a complete summary"
}
# Persist summary.status and summary.monitor.actionable_signature externally.
# Notify for a new/changed actionable signature; otherwise remain quiet.
```

The cross-source gate compares the exact, case-sensitive non-Batch model ID set,
not only its size. It strips only a terminal `:batch`; free variants, aliases,
and other IDs remain distinct. Equal-sized substitutions, missing identities,
and duplicate general-model identities leave the audit incomplete.

Read [contracts-and-counting.md](references/contracts-and-counting.md) before
classifying a new model, Batch entry, alias, free variant, or media price. Read
[verification-and-publication.md](references/verification-and-publication.md)
before changing code, operating a preview, or publishing work.

## Update in bounded phases

In `update` mode, preserve a before-audit and apply one phase at a time:

1. New non-Batch models:
   `node scripts/update-openrouter-models.mjs --input <models.json> --missing-only`.
2. Batch serving variants:
   `node scripts/update-openrouter-models.mjs --input <models.json> --batch-only`.
3. Structured model metadata:
   `node scripts/update-openrouter-models.mjs --input <models.json> --metadata-only`.
4. Lifecycle states:
   `node scripts/update-openrouter-models.mjs --input <models.json> --lifecycle-only`.
5. Market sidebar snapshot:
   `node scripts/update-openrouter-market-filters.mjs --input <market.json>`.

The flags above are mutually exclusive. Review each diff before continuing and
rerun both audits after every phase. Never run a mechanical phase over a target
whose specialized contract or product placement needs manual adaptation.

Add specialized models to the protection sets in both the updater and auditor
when generic metadata cannot express their contract. Update the matching
registry, UI, cost estimator, readiness receipt, and focused tests. Place normal
new cards through the stable post-sixth-row refresh mechanism unless the user
explicitly requests a flagship or default-model change.

## Verify and report

Run the focused tests for every touched surface, then the frontend build and the
affected backend suite. A directory entry proves catalog presence only; report
contract adaptation, configured gateway availability, and paid/manual validation
as separate states.

After changing this skill's guards or a duration-price overlay, run:

`node --test scripts/update-openrouter-models.test.mjs scripts/openrouter-pricing-contracts.test.mjs .agents/skills/openrouter-update/scripts/inspect-json.test.mjs .agents/skills/openrouter-update/scripts/stable-signature.test.mjs`

Immediately before a requested PR, freeze all four public sources again and run
the full audit. Report drift that appeared during implementation rather than
silently folding it into the requested change.

Every handoff must include:

- source window timestamps and hashes or the manifest path;
- local snapshot, non-Batch upstream, Batch, uncertain, and expired counts;
- exact missing, stale, and metadata-drift groups;
- structural versus volatile market drift;
- changed files and test results;
- unperformed provider validation and publication actions.
