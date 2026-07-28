# Third-party notices

## agency-agents-zh

- Project: `jnMetaCode/agency-agents-zh`
- License: MIT
- Vendored content: 268 Chinese agent role definitions and prompts
- Fixed source commit: `2ecfabf8e944ccdfed63ad8c44d5241290af6977`
- Generated snapshots: `client/src/data/agents.ts` and
  `server/data/agents.json`
- Reproducible importer: `scripts/update-agency-agents.mjs`

ModelMirror preserves each role's upstream path and commit-pinned source URL.
The local importer adds ModelMirror-specific department metadata, user-facing
task scenarios and capability tags; it does not change the upstream prompt
body.

### MIT License

Copyright (c) 2025 Michael Sitarzewski (original English version)
Copyright (c) 2026 jnMetaCode (Chinese translation and localization)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## OmniRoute

- Project: `diegosouzapw/OmniRoute`
- License: MIT
- Audited API contract: `release/v3.8.49`
- Audited source commit: `36f8fd10052f`
- Runtime image: `diegosouzapw/omniroute:3.8.48`
- OCI index digest: `sha256:badb560971fdc23c2fb84b3e8695116239ff215b4cca4b07076201a8efae7f0d`
- Integration boundary: Docker sidecar using `/v1/models`,
  `/v1/chat/completions`, auto-combo candidate reads, routing headers and
  allowlisted response telemetry. The adapter maps mode headers to matching
  `auto/*` aliases for runtime `3.8.48` compatibility and rejects per-request
  budgets by default because that image does not enforce them.

The official `3.8.49` Docker tag was not published when this integration was
implemented. The runtime therefore remains pinned to the latest available
immutable `3.8.48` image while the adapter is audited against the
`release/v3.8.49` API contract. Replace the image only after repeating catalog,
streaming, budget, telemetry and rollback checks.

No OmniRoute source code is vendored into ModelMirror. The native migration
keeps a behavior-only fixture at
`server/model_router/fixtures/omniroute-v3.8.49-routing.json`; it records the
audited release, commit, documented default score factors and safety
invariants. It does not contain executable OmniRoute source. Any future direct
port must be listed here with its upstream path, commit and preserved license
header before it can enter a production path.

The following ModelMirror modules are independent Python behavioral
reimplementations, not direct source copies:

- `server/model_router/routing.py`: references the behavior, score-factor
  defaults and mode packs documented/implemented by upstream
  `open-sse/services/autoCombo/scoring.ts` at commit `36f8fd10052f`.
- `server/model_router/repository.py` and `engine.py`: ModelMirror-native
  SQLite, tenant, circuit-breaker, LKGP and dispatch implementation; only the
  externally observable Auto-Combo behavior is aligned.
- `server/context_engine/core.py`: ModelMirror-native deterministic context
  optimizer inspired by the RTK/Caveman feature description. No OmniRoute
  compression source was copied.

If any of these modules is later replaced by a direct port, the individual
file must preserve the upstream copyright/license header and this notice must
be changed from “behavioral reimplementation” to “direct port”.

### MIT License

Copyright (c) 2026 diegosouzapw

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
