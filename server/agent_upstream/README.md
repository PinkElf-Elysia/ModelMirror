# Upstream Agent Workbench

This package hosts ModelMirror's control-plane adapter for the byte-identical,
pinned PenguinHarness execution core at revision
`047505dccc0cc16ad92be11011347d635f33ceb0`.

## R3R-1 scope

- The Node 24 worker runs one isolated process per Shadow Run and speaks strict
  UTF-8 NDJSON protocol `modelmirror.upstream-workbench/1`.
- FastAPI owns the API, SQLite records, model lease, SSE, lifecycle, and all
  filesystem mutations.
- The worker can request only `read_file`, `write_file`, and `edit_file`.
  Python resolves every path inside the run's Shadow Workspace and keeps
  `.modelmirror/**` read-only except for the constrained Goal status update.
- A completed upstream Goal becomes `candidate_ready`. It does not invoke the
  Browser Sidecar and does not create an App, AppVersion, Artifact, Evidence,
  preview token, or publication record.
- `AGENT_APP_ENGINE_SHADOW_ENABLED=0` is the default. Disabled routes return
  404 and the workbench panel is not mounted.

## Source and build boundary

- `vendor/penguin_harness/` is immutable upstream source. Do not patch files in
  that directory.
- `provenance/vendor-manifest.json` binds every vendored path to its upstream
  Git blob SHA-1. `provenance/verify_vendor.py` fails on drift.
- ModelMirror-owned worker and adapter code lives outside `vendor/`.
- The server image builds the upstream packages with a separate pinned Node
  24.19 runtime and pnpm 11.18.0. The existing global Node/npm/npx used by MCP
  is not replaced.
- The supply-chain artifacts in `provenance/` describe the minimal production
  closure, not the entire PenguinHarness monorepo lockfile.

## Verification

```powershell
python -m pytest server/tests/test_agent_upstream_*.py -q
python server/agent_upstream/provenance/verify_vendor.py
python server/agent_upstream/provenance/verify_supply_chain.py
```

The upstream Core and Skills tests must run in the pinned Linux Node 24 build
environment. Windows-host POSIX shell and symlink failures are not accepted as
equivalent evidence.

## Rollback

Set `AGENT_APP_ENGINE_SHADOW_ENABLED=0` and rebuild only `server` and `client`.
Existing Shadow Run metadata and workspaces remain read-only; ordinary chat,
Agent State, workflows, MCP, and prior App data are unaffected.
