# Agency Orchestrator vendored core

This directory contains the selectively vendored Apache-2.0 core from
`jnMetaCode/agency-orchestrator` at the immutable revision recorded in
`UPSTREAM_REVISION`.

`UPSTREAM_FILES.json` is the machine-verifiable provenance map. The copied
files remain byte-identical to upstream. `PATCHES.md` records ModelMirror-only
boundaries and any future inline modifications.

The vendored core is not a second service and must not access ModelMirror
credentials. Provider selection is deliberately absent; the host-owned worker
bridge injects a connector at runtime.
