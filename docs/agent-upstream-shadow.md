# Upstream Agent Workbench Shadow Engine (R3R-1)

R3R-1 replaces the failed custom AppBuild execution loop with a feature-flagged
Shadow Engine based on a pinned, byte-identical PenguinHarness Core. It is an
execution-kernel compatibility milestone, not an application delivery feature.

## Fixed inputs

- ModelMirror base: `4a43f23ff8abb09a2857b5cc2cf24e0510e05a06`
- Upstream: `Prism-Shadow/penguin-harness`
- Upstream revision: `047505dccc0cc16ad92be11011347d635f33ceb0`
- Protocol: `modelmirror.upstream-workbench/1`
- Node runtime: 24.19.0, pinned by image digest
- Package manager: pnpm 11.18.0 with frozen lockfile

## Architecture and trust boundary

FastAPI and SQLite remain the only control plane. Each Shadow Run starts a new
Node worker process in the server container. The worker runs the upstream
Session, Goal, Context, and Trace semantics, while Python owns:

- model resolution and temporary gateway lease;
- task/run state, SSE, cancellation, retries, and restart interruption;
- the three file tools and all realpath/symlink/output limits;
- final candidate validation and hash calculation.

R3R-1 deliberately has a narrow candidate contract: exactly one self-contained
`index.html` is the handoff artifact. Its SHA-256 is computed only from that
entrypoint; `PLAN.md`, scratch notes, and other inspectable workspace files are
excluded. A later multi-file delivery round must introduce a versioned manifest
before widening this contract.

The worker receives a minimal environment and a sensitive model-lease IPC
frame. Keys are not written to SQLite, events, error messages, or reports. Its
stdout is protocol-only; malformed frames, sequence gaps, duplicates, unknown
fields, and stdout pollution fail closed.

The Node Permission Model is defense in depth. Network is not treated as
isolated by that mechanism, so the worker gets no unrelated service secrets and
cannot invoke Docker, Browser, App promotion, or publication paths.

## API and states

The feature flag `AGENT_APP_ENGINE_SHADOW_ENABLED` defaults to `0`. When enabled:

- `POST /api/agent-workspace/apps/engine-shadow-runs`
- `GET /api/agent-workspace/apps/engine-shadow-runs`
- `GET /api/agent-workspace/apps/engine-shadow-runs/{run_id}`
- `POST /api/agent-workspace/apps/engine-shadow-runs/{run_id}/stop`
- read-only event, workspace-listing, and workspace-file endpoints

Terminal states are `candidate_ready`, `blocked`, `budget_limited`, `stopped`,
`interrupted`, and `failed`. Active runs become `interrupted` after server
restart. An upstream Goal marked complete maps only to `candidate_ready`.

## Deliberate R3R-1 exclusions

R3R-1 does not call the Browser Sidecar and cannot create App, AppVersion,
Artifact, Evidence, preview, download, or publication records. The workbench
labels every result as an unverified Shadow candidate. Only the three safe file
tools are available; commands, images, subagents, MCP, and external Skills are
deferred.

## Supply-chain evidence

`server/agent_upstream/provenance/` contains:

- an immutable 164-file upstream blob manifest;
- a CycloneDX 1.6 SBOM for the 188-component production closure;
- normalized license inventory and pnpm audit report;
- pinned OSV-Scanner 2.4.0 raw output and policy result.

The license gate rejects unknown/unlicensed/NOASSERTION/GPL/AGPL/SSPL entries.
The vulnerability gate rejects HIGH or CRITICAL findings. At implementation
time the closure had zero HIGH/CRITICAL findings, while two non-blocking
findings remain explicit: one LOW advisory for esbuild 0.27.7 and one MODERATE
advisory for `@anthropic-ai/sdk` 0.81.0. They must be reassessed when the pinned
upstream revision changes.

## Acceptance and rollback

R3R-1 human acceptance uses one penguin request and one non-penguin held-out
request. Inspect Goal rounds, token usage, tool activity, real candidate files,
sanitized tool-failure counts, stop behavior, authentication failure, worker
crash, and restart interruption.
Verify the database contains no Browser Run, App, Version, Artifact, or
Evidence created by Shadow execution.

Rollback by setting `AGENT_APP_ENGINE_SHADOW_ENABLED=0` and rebuilding only
`server` and `client`. Data is retained. Do not silently fall back to the
abandoned Python AppBuild engine.
