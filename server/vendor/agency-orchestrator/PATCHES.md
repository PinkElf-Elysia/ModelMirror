# ModelMirror patches and boundaries

Pinned upstream revision: `e3f69fdf9da8a4630edbb8abeb116893b983b57d`.

## Imported files

Every file listed in `UPSTREAM_FILES.json` is copied byte-for-byte from the
recorded upstream Blob SHA. There are no inline modifications in round 0.

## ModelMirror-only boundary file

- `src/connectors/factory.ts` is a ModelMirror-authored replacement boundary;
  it is not derived from upstream `src/connectors/factory.ts`.
- Purpose: keep all upstream Provider Connectors, CLI integrations and API-key
  lookup code outside the vendored closure while preserving the core modules'
  stable import path.
- The boundary fails closed until a caller explicitly injects a connector.

## Round 1 inline adaptation

- `src/cli/compose.ts`
  - Original upstream Blob SHA:
    `8b946bfa49f92f95a795d3bbd0e2c4dd5d10bcf8`
  - Adds a host-provided `RoleSummary[]` injection point so ModelMirror can use
    its existing expert catalog without creating a second role directory.
  - Makes pinned line-ups fail closed when any requested role path is absent.
  - Reports whether the upstream repair chain changed the initial YAML.
  - The generation, deterministic role correction, dependency/variable repair,
    DAG validation and at-most-one LLM repair behavior remain upstream logic.

## Round 2.5 selective upstream sync

- Audited upstream range:
  `3b7c43042325a9091393de6ecfa7e9936b0c7932..e3f69fdf9da8a4630edbb8abeb116893b983b57d`.
- Imported only the core dependency-ID reliability change:
  - `src/cli/compose.ts` now deterministically rewrites an unambiguous output
    variable mistakenly used in `depends_on` to its producer step ID, while
    rejecting ambiguous, self-referential or cyclic rewrites and removing
    duplicate dependencies.
  - `src/core/parser.ts` reports that `depends_on` requires step IDs rather
    than output variable names.
  - `test/depends-on-ids.ts` pins the new repair behavior.
- `src/cli/compose.ts` was rebased onto upstream Blob SHA
  `95f173675f0b17d2576bdefebf228d5d19740ad2`; the Round 1 ModelMirror host
  injection, pinned-lineup validation and `repairUsed` reporting remain the
  only inline ModelMirror adaptations.

## Round 3 execution adaptation

- `src/core/executor.ts`
  - Original upstream Blob SHA:
    `7ed21655e822950882b7274747ed489901a6f37b`.
  - Adds an optional host-provided in-memory `AgentDefinition` resolver.
  - ModelMirror uses this seam to execute against its current expert catalog
    without writing a second role directory. When omitted, the upstream
    `loadAgent(agentsDir, rolePath)` behavior is unchanged.
  - DAG scheduling, template rendering, batching, acceptance verification and
    one-round rework remain the upstream implementation.
- Provider, Studio, website, doctor, sponsor and unrelated CLI changes in the
  audited range remain excluded.

## Round 5 reusable expert-team assets

- `src/cli/prompt.ts`
  - Imported byte-for-byte from upstream Blob SHA
    `e788c12d0bce68ad2bc33e6be098408ca187084e`.
  - ModelMirror reuses Prompt Record version history, persistence helpers and
    Prompt Garden through the host-owned Worker bridge.
  - Prompt optimization, testing and scoring are not exposed by the Expert
    Team product API in this round. If used later, the existing connector
    factory boundary still requires explicit host injection.
- `src/cli/team.ts`
  - The already-vendored upstream Team/Loadout storage is exposed through a
    bounded Worker asset method. The storage root is injected only by the
    Python host, and ModelMirror validates current expert IDs before saving.
- `src/skills/loader.ts`
  - Original upstream Blob SHA:
    `25aaca6547ce041adcf61b1369725cffae4c55a9`.
  - Adds an optional host-owned in-memory Skill resolver while keeping the
    upstream directory loader as the default.
  - The Skill injector is used only for prompt-method text in constrained LLM
    steps. Tools, scripts, network access and side effects stay disabled.
- `src/core/executor.ts`
  - Reuses the same original upstream Blob SHA recorded for Round 3 and adds
    the optional in-memory Skill resolver beside the existing Agent resolver.
  - Adds an optional host-owned template-context view. ModelMirror uses it only
    to bound the dependency excerpts rendered into a final synthesis step;
    complete upstream results remain stored and reusable. Omitting the hook
    preserves upstream template rendering byte-for-byte.
  - Adds an optional host-owned deterministic acceptance hook. ModelMirror uses
    it for explicit final character-count limits before delegating all semantic
    checks to the upstream verifier and its existing one-rework path.
  - Scheduling, template rendering, verification and rework logic remain the
    upstream implementation; omitting the resolver preserves file loading.

## Round 7 durable HITL adaptation

- `src/cli/compose.ts`
  - Reuses upstream Blob SHA
    `95f173675f0b17d2576bdefebf228d5d19740ad2` and adds an optional
    host-owned system-prompt appendix after the upstream Compose prompt.
  - ModelMirror uses the seam only to state execution invariants that differ
    from the upstream CLI runtime, especially durable HITL full-DAG barriers.
    Omitting the appendix preserves the upstream prompt byte-for-byte.
- `src/core/executor.ts`
  - Reuses the original upstream Blob SHA
    `7ed21655e822950882b7274747ed489901a6f37b`.
  - Adds an optional host-owned interaction resolver for `human_input` and
    `approval` steps. ModelMirror uses it to return a durable checkpoint,
    persist the wait in Python, exit the Node Worker, and resume later.
  - Adds an optional host-owned deterministic output normalizer that runs
    before acceptance verification and before rework verification. ModelMirror
    uses it only to canonicalize entries from an explicitly closed user list;
    omitting the hook preserves the generated output byte-for-byte.
  - Adds an optional host-owned strict verification policy hook. ModelMirror
    uses it only for an expert draft immediately before an approval checkpoint:
    if verification is unavailable or still fails after the upstream one-round
    rework, that draft step fails before any approval is requested while its
    last bounded output remains available for diagnosis and targeted retry.
    Omitting the hook preserves upstream's non-blocking quality-signal behavior.
  - When the resolver is omitted, the existing upstream CLI/readline behavior
    remains the default.
  - Scheduling, template rendering, result restoration, verification and the
    one-rework path remain the upstream implementation.

## Deliberately excluded

The upstream Provider Factory and all provider implementations, website/Web
Studio, Electron code, role libraries, creative assets, MCP integration and
third-party CLI adapters are not vendored. Any later upstream upgrade or
inline edit requires a separate Diff audit, a prominent modification notice in
the edited source file, and an entry here with the original upstream Blob SHA.
