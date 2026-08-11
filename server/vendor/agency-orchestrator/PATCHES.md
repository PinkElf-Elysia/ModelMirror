# ModelMirror patches and boundaries

Pinned upstream revision: `3b7c43042325a9091393de6ecfa7e9936b0c7932`.

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

## Deliberately excluded

The upstream Provider Factory and all provider implementations, website/Web
Studio, Electron code, role libraries, creative assets, MCP integration and
third-party CLI adapters are not vendored. Any later upstream upgrade or
inline edit requires a separate Diff audit, a prominent modification notice in
the edited source file, and an entry here with the original upstream Blob SHA.
