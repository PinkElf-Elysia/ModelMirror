# ModelMirror AI Research V0.1 collaboration rules

These rules apply to every file under `extensions/ai-research/`.

## Normative V0 roadmap

- `AI_RESEARCH_V0_ROADMAP.md` is the normative product, upstream reuse, round sequencing, self-development boundary, and completion baseline for AI Research V0.
- Every V0 task must identify its roadmap round, user-visible research action, reused upstream and source lock, ModelMirror-owned adapter scope, acceptance artifacts, and remaining stages.
- Security, licensing, adaptation, isolation, and evidence are gates inside a product round. They do not replace user-visible capability or justify claiming a round complete.
- A deviation requires a roadmap-defined necessary condition, a written Roadmap Amendment with evidence and the smallest viable change, and explicit user approval before implementation.

## Boundary

- This directory is an optional AI/Agent research extension. AR1 execution remains fixture-only; V0.1 additionally permits the approved live literature workflow through fixed Local Deep Research adapters.
- Live literature capability must remain `scientificClaim=none`. It is not a scientific benchmark, leaderboard, autonomous research system, or production multi-tenant service.
- The Research Console is module-local. Its source, lockfile, build, static output, and runtime must remain independent of the parent client.
- Runtime code must not import, open, or mount parent `client/`, `server/`, databases, storage, credentials, or build outputs. The only parent runtime integration is the approved, bearer-authenticated local model bridge over HTTP.
- Parent changes for V0.1 are limited to the model bridge under `server/model_router/`, minimal `server/main.py` registration, corresponding server tests, and the existing AI Research CI workflow.
- Do not add this module to the root Compose file, root Python requirements, client dependencies, Plugin manifests, Studio routes, or default images.
- Do not accept arbitrary commands, module paths, environment variables, model identifiers, prompts, uploads, provider keys, search-engine settings, or EvalPack installation requests.

## Upstream reuse

- Inspect AI is fixed to 0.3.260 and may only be driven through its public CLI, control CLI, and log CLI.
- MLflow is fixed to 3.15.1 and may only be used through its documented server and client interfaces.
- Local Deep Research is fixed to v1.10.6 and may only be driven through its documented HTTP/session routes. Do not import its Python package or inspect its encrypted database.
- OpenAlex and Zotero access must flow through Local Deep Research. Zotero credentials never enter ModelMirror APIs, UI state, project files, or logs.
- Do not patch upstream scientific tasks or scorers. Test fixtures must stay clearly labelled `fixture_only` and `harness_only`.
- Keep exact dependency locks, source hashes, image digests, licenses, notices, and an auditable upgrade boundary.
- UI dependencies must use exact versions and registry integrity locks. `workspace:`, `file:`, symlinks, and parent source imports are forbidden.

## Security and evidence

- The worker runs as non-root with no network, read-only root filesystem, no Docker socket, dropped capabilities, bounded resources, and validated paths.
- Process exit code is transport evidence, not evaluation outcome. Preserve the raw EvalLog status and error.
- Persist `cancelRequested`, `cancelApplied`, and raw upstream terminal status separately.
- Do not emit scientific metric names or claims. Allowed metrics are operational only.
- No credentials, full host environment, parent physical paths, runtime databases, logs, or generated artifacts may be committed.
- LDR passwords, session cookies, and CSRF tokens are memory-only. The scoped model-bridge token may be supplied independently to the core server and extension, but must never be persisted in `research.yaml`, the control ledger, MLflow, artifacts, or browser storage.

## Work method

- Keep each implementation batch to at most five files and one verifiable objective.
- Run the narrowest relevant test after each batch.
- Generated runtime data belongs under ignored `runtime/`, `artifacts/`, or named volumes.
- Do not commit, push, open a PR, deploy, publish, or delete persistent volumes without separate authorization.
