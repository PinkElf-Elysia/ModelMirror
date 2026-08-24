# ModelMirror AI Research AR0 collaboration rules

These rules apply to every file under `extensions/ai-research/`.

## Boundary

- This directory is an optional, fixture-only engineering module. It is not a scientific benchmark, model integration, leaderboard, or production multi-tenant service.
- Runtime code must not import, open, mount, or depend on parent `client/`, `server/`, configuration, credentials, databases, storage, or build outputs.
- Parent changes are limited to `.dockerignore` and `.github/workflows/ai-research.yml`. Any other parent change requires a separate approved task.
- Do not add this module to the root Compose file, root Python requirements, client dependencies, Plugin manifests, Studio routes, or default images.
- Do not accept arbitrary commands, module paths, environment variables, model identifiers, prompts, uploads, provider keys, or EvalPack installation requests.

## Upstream reuse

- Inspect AI is fixed to 0.3.260 and may only be driven through its public CLI, control CLI, and log CLI.
- MLflow is fixed to 3.15.1 and may only be used through its documented server and client interfaces.
- Do not patch upstream scientific tasks or scorers. Test fixtures must stay clearly labelled `fixture_only` and `harness_only`.
- Keep exact dependency locks, source hashes, image digests, licenses, notices, and an auditable upgrade boundary.

## Security and evidence

- The worker runs as non-root with no network, read-only root filesystem, no Docker socket, dropped capabilities, bounded resources, and validated paths.
- Process exit code is transport evidence, not evaluation outcome. Preserve the raw EvalLog status and error.
- Persist `cancelRequested`, `cancelApplied`, and raw upstream terminal status separately.
- Do not emit scientific metric names or claims. Allowed metrics are operational only.
- No credentials, full host environment, parent physical paths, runtime databases, logs, or generated artifacts may be committed.

## Work method

- Keep each implementation batch to at most five files and one verifiable objective.
- Run the narrowest relevant test after each batch.
- Generated runtime data belongs under ignored `runtime/`, `artifacts/`, or named volumes.
- Do not commit, push, open a PR, deploy, publish, or delete persistent volumes without separate authorization.
