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
- Provider, Studio, website, doctor, sponsor and unrelated CLI changes in the
  audited range remain excluded.

## Deliberately excluded

The upstream Provider Factory and all provider implementations, website/Web
Studio, Electron code, role libraries, creative assets, MCP integration and
third-party CLI adapters are not vendored. Any later upstream upgrade or
inline edit requires a separate Diff audit, a prominent modification notice in
the edited source file, and an entry here with the original upstream Blob SHA.
