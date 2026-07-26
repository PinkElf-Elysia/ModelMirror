# EvoAgentX Reference and License Notice

This package contains ModelMirror's independently maintained meta-agent
planning schema, prompts, and workflow adapter.

The early implementation referenced EvoAgentX's goal decomposition shape:
`goal -> sub_tasks -> inferred edges`.

Audited upstream source:

- Project: https://github.com/EvoAgentX/EvoAgentX
- Tag: `v0.1.4`
- Commit: `aad19b912f640161ea07e8904d9237cd34fde5f1`
- License: MIT

ModelMirror does not vendor the full EvoAgentX package or use it as a runtime
dependency. No EvoAgentX source file is copied into this package at the
current baseline. Provider, RAG, storage, HITL, memory, and tool runtimes
remain ModelMirror implementations.

Any future selective reuse must retain the upstream copyright and MIT license,
record the exact source path and content digest, audit transitive licensing,
and add a local test mapping before code is accepted.

Canonical audit and roadmap:

- `docs/EVOAGENTX_AUDIT_V014.md`
- `docs/EVOAGENTX_ALIGNMENT.md`
- `docs/XPERT_FREEZE.md`
