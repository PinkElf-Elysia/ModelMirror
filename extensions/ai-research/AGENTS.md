# ModelMirror AI Research V0.2 qualification collaboration rules

These rules apply to every file under `extensions/ai-research/`.

## Normative V0 roadmap

- `AI_RESEARCH_V0_ROADMAP.md` is the normative product, upstream reuse, round sequencing, self-development boundary, and completion baseline for AI Research V0.
- Every V0 task must identify its roadmap round, user-visible research action, reused upstream and source lock, ModelMirror-owned adapter scope, acceptance artifacts, and remaining stages.
- Security, licensing, adaptation, isolation, and evidence are gates inside a product round. They do not replace user-visible capability or justify claiming a round complete.
- A deviation requires a roadmap-defined necessary condition, a written Roadmap Amendment with evidence and the smallest viable change, and explicit user approval before implementation.

## Boundary

- This directory is an optional AI/Agent research extension. AR1 execution remains fixture-only; V0.1 permits the approved live literature workflow. V0.2 product execution remains disabled until the P2R qualification-integrity pre-batch in Amendment V0.2-A2 passes.
- Live literature and V0.2 hypothesis/protocol capabilities must remain `scientificClaim=none`. They are not scientific benchmarks, leaderboards, autonomous research systems, experiment execution services, or production multi-tenant services.
- The Research Console is module-local. Its source, lockfile, build, static output, and runtime must remain independent of the parent client.
- Runtime code must not import, open, or mount parent `client/`, `server/`, databases, storage, credentials, or build outputs. The only parent runtime integration is the approved, bearer-authenticated local model bridge over HTTP.
- Parent changes for V0.2 remain limited to the model bridge under `server/model_router/`, minimal `server/main.py` registration, corresponding server tests, and the existing AI Research CI workflow. Any structured-output bridge change must be bounded to the fixed model and must preserve the V0.1 tool-call contract.
- Do not add this module to the root Compose file, root Python requirements, client dependencies, Plugin manifests, Studio routes, or default images.
- Do not accept caller-supplied commands, module paths, environment variables, model identifiers, system prompts, uploads, provider keys, search-engine settings, or EvalPack installation requests.

## Upstream reuse

- Inspect AI is fixed to 0.3.260 and may only be driven through its public CLI, control/log CLI, and documented agent/tool/sandbox interfaces. Amendment V0.2-A2 permits the qualification-only fixed-phase ResearchStudio host to use an ephemeral Inspect sandbox for the coherence gate; this exception is not a general command API. The coherence Host, trusted receipt validation, and Phase 0–2 handoff contracts are implemented, but the post-coherence Phase 3/4 state machine is not active and P2R remains NO-GO.
- MLflow is fixed to 3.15.1 and may only be used through its documented server and client interfaces.
- Local Deep Research is fixed to v1.10.6 and may only be driven through its documented HTTP/session routes. Do not import its Python package or inspect its encrypted database.
- Microsoft ResearchStudio IdeaSpark is fixed to commit `a785e3aca7a2f0cb9775d45a7f2b5d3bf16f076a`. Only the locked `ResearchStudio-Idea/skills/idea_spark` host loop, prompts, deterministic scripts, schemas, and terminal facts may be adapted; do not run its installer or accept arbitrary upstream commands.
- NoviScl AI-Researcher is fixed to commit `e5dd05a90bcadb436c07283c2f429367c6e525d3`. V0.2 may reuse its locked experiment-plan prompt, examples, and seven-section output contract, but not its provider-key loading, direct provider client, experiment execution, or mutable source checkout.
- OpenAlex and Zotero access must flow through Local Deep Research. Zotero credentials never enter ModelMirror APIs, UI state, project files, or logs.
- Do not patch upstream scientific tasks or scorers. Test fixtures must stay clearly labelled `fixture_only` and `harness_only`.
- V0.2 must generate candidates sequentially, preserve every upstream terminal state, and never add a ModelMirror scientific score, rank, novelty verdict, or hidden fallback to another upstream. A human must select a candidate and complete any missing typed protocol fields before freezing.
- Only a verified V0.1 literature bundle may seed V0.2. The worker may receive a bounded, canonical project bundle and fixed profile identifiers; it must not receive arbitrary model IDs, system prompts, Python modules, commands, paths, environment overrides, or provider credentials.
- A qualification fixture may test schema or degraded-mode behavior, but it cannot qualify the end-to-end V0.2 journey or replace the verified V0.1 bundle gate.
- Keep exact dependency locks, source hashes, image digests, licenses, notices, and an auditable upgrade boundary.
- UI dependencies must use exact versions and registry integrity locks. `workspace:`, `file:`, symlinks, and parent source imports are forbidden.

## Security and evidence

- The worker runs as non-root with no network, read-only root filesystem, no Docker socket, dropped capabilities, bounded resources, and validated paths.
- Process exit code is transport evidence, not evaluation outcome. Preserve the raw EvalLog status and error.
- Persist `cancelRequested`, `cancelApplied`, and raw upstream terminal status separately.
- Do not emit scientific metric names or claims. Allowed metrics are operational only.
- No credentials, full host environment, parent physical paths, runtime databases, logs, or generated artifacts may be committed.
- LDR passwords, session cookies, and CSRF tokens are memory-only. The literature/ordinary-hypothesis bridge token (`AI_RESEARCH_S2S_TOKEN`) and the qualification-only P2R Host token (`AI_RESEARCH_P2R_S2S_TOKEN`) must be independently supplied, non-empty, and different. The generic token must never authorize a request carrying the P2R phase header; the P2R token must never authorize model discovery, literature, or an ordinary hypothesis request. Neither token may be persisted in `research.yaml`, the control ledger, MLflow, artifacts, logs, or browser storage, and the P2R token must not be injected into Control, LDR, or the module Compose services.
- `execution.mode=executed` is admissible only when a trusted runtime receipt proves the tool call, sandbox image, script hash, exit code, complete stdout/stderr hashes, limits, and truncation state. Model-authored script/output text without that receipt is untrusted narrative and must fail qualification.
- Only the coherence phase may execute model-generated Python. A later upstream phase that asks for code execution must stop qualification unless a separately approved amendment preserves this boundary; it must not reuse the coherence tool implicitly.
- Preserve invalidated qualification artifacts, upstream terminal states, hashes, usage, and cost. Correct their authority/status instead of deleting or rewriting the underlying facts.

## Work method

- Keep each implementation batch to at most five files and one verifiable objective.
- Run the narrowest relevant test after each batch.
- Generated runtime data belongs under ignored `runtime/`, `artifacts/`, or named volumes.
- Do not commit, push, open a PR, deploy, publish, or delete persistent volumes without separate authorization.
