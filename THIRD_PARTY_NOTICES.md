# Third-party notices

## Agency Orchestrator core

- Project: `jnMetaCode/agency-orchestrator`
- License: Apache License 2.0
- Fixed source commit: `3b7c43042325a9091393de6ecfa7e9936b0c7932`
- Vendored location: `server/vendor/agency-orchestrator/`
- Provenance manifest: `server/vendor/agency-orchestrator/UPSTREAM_FILES.json`
- Local license copy: `server/vendor/agency-orchestrator/LICENSE`

ModelMirror selectively vendors the Types, DAG, Parser, Template, Condition,
Executor, Verify, Compare, Agent/Skill Loader, Reporter/Resume, Compose and Team
core plus focused upstream tests. The upstream Provider Factory, provider and
CLI connectors, website/Web Studio, Electron code, role libraries, creative
assets and third-party integrations are excluded. A ModelMirror-authored
connector boundary fails closed until the host worker injects a connector;
ModelMirror credentials are never owned by the vendored code. Exact upstream
Blob SHAs and local SHA-256 values are recorded in the provenance manifest,
and all local boundaries or future modifications are documented in
`PATCHES.md`.

## Anthropic skill-creator authoring guidance

- Project: `anthropics/skills`
- Source path: `skills/skill-creator/`
- Copyright: Copyright 2026 Anthropic, PBC
- License: Apache License 2.0
- Upstream: <https://github.com/anthropics/skills/tree/main/skills/skill-creator>
- Local license copy:
  `server/skills/creator_reference/LICENSE-ANTHROPIC-SKILL-CREATOR.txt`
- Modified derivative:
  `server/skills/creator_reference/authoring-playbook-v1.md`

ModelMirror adapts only the upstream creation-stage method: capture concrete
usage intent, plan reusable resources, confirm the plan, build resources in
dependency order, write progressively disclosed Skill instructions, and
validate the resulting package. The local playbook was rewritten for
ModelMirror's typed proposal contract, UTF-8-only package rules, trusted
session requirement coverage, and separate evaluation and installation gates. It omits
the upstream evaluation runner, graders, result viewer, description optimizer,
packaging scripts, and Claude-specific execution paths.

The modified playbook carries a prominent modification notice. Anthropic does
not endorse ModelMirror, and the Apache License does not grant ModelMirror any
right to use Anthropic or Claude trademarks beyond identifying the source.

## Dify Agent Strategy 0.0.42

- Project: `langgenius/dify-official-plugins`
- Package: `langgenius-agent_0.0.42.difypkg`
- Package SHA-256: `7C5FEEE39FC5B534B8822472E3DFD771C9E3C2960A96A164A02B0873D922343B`
- License: Apache License 2.0
- Upstream: <https://github.com/langgenius/dify-official-plugins>
- Integration boundary: behavioral reference for Function Calling and ReAct
  strategy semantics. ModelMirror does not vendor `dify_plugin`, execute the
  package, or copy the upstream SDK implementation line by line.

The ModelMirror implementation under `server/xpert_runtime/agent_strategy/`
is an independent adapter over the existing Runtime Toolset, policy,
middleware, and audit chain. Copyright and license ownership of Dify remains
with LangGenius, Inc. and the upstream contributors under Apache-2.0.

## PenguinHarness built-in Skills

- Project: `Prism-Shadow/penguin-harness`
- Project URL: <https://github.com/Prism-Shadow/penguin-harness>
- License: Apache License 2.0
- Imported scope: the 16 directories under upstream
  `packages/skills/skills/`, each containing `SKILL.md` and `icon.svg`
- Local location: `server/skills/builtin/`
- License copy: `server/skills/builtin/PENGUINHARNESS_LICENSE`
- Import date: 2026-08-05

ModelMirror does not run PenguinHarness, its CLI, SDK, server, web app, logo,
or release binaries. The imported Skill content is packaged as immutable
Agent State snapshots. The local manifest records the upstream path,
Apache-2.0 attribution, capability status, modification flag, and SHA-256
content digest for every Skill.

The following Skill files are modified derivatives and carry an inline
modification notice:

- `agent-creation`: preserves the complete upstream v7 method and adds a
  higher-priority ModelMirror staging, strict quality-review, security, and
  backend-owned atomic-promotion addendum. Upstream-only paths and runtime
  capabilities remain clearly marked as inactive reference material.
- `skill-porting`: adapted to Agent-local snapshots without changing the
  fixed 16-item built-in library.
- `software-engineering`: replaced the upstream product identity with
  ModelMirror General Agent.
- `web-design`: replaced the product identity and default visual framing with
  ModelMirror while preserving detailed implementation guidance.

The remaining 12 Skill instruction files and all 16 icons are copied without
content changes. `agent-evaluation`, `agenthub-models`, `agent-optimization`,
`benchmark-design`, `penguin-cli`, and `penguin-sdk` are reference-only and
are not injected into the Agent runtime in this phase. The Apache-2.0 license
does not grant permission to use PenguinHarness trademarks or visual brand
assets; ModelMirror uses neither.

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

## opencc-js

- Project: `nk2028/opencc-js`
- Package: `opencc-js@1.4.1`
- License: MIT
- Bundled dictionary data: `nk2028/opencc-data@1.4.1`
- Dictionary license: Apache License 2.0
- Integration boundary: the browser loads the `t2cn` converter only after an
  automatic or Chinese speech-to-text result contains Han characters. It is
  used only to normalize Traditional Chinese transcription output to
  Simplified Chinese.

The package's MIT license and the incorporated `opencc-data` Apache 2.0
license are preserved in the upstream package referenced by
`client/package-lock.json`:

- <https://github.com/nk2028/opencc-js/blob/v1.4.1/LICENSE>
- <https://github.com/nk2028/opencc-js/blob/v1.4.1/THIRD_PARTY_LICENSES.md>

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
