# EvoAgentX Reference and License Notice

This package contains ModelMirror's independently maintained meta-agent
planning schemas, prompts, deterministic compiler, and proposal adapter.

Audited upstream source:

- Project: https://github.com/EvoAgentX/EvoAgentX
- Tag: `v0.1.4`
- Commit: `aad19b912f640161ea07e8904d9237cd34fde5f1`
- License: MIT, Copyright (c) 2025 EvoAgentX

Meta Planner V2 adapts the layered concepts found in these audited upstream
files:

- `evoagentx/workflow/task_planning.py`
- `evoagentx/workflow/workflow_generator.py`
- `evoagentx/workflow/agent_generator.py`

Xpert Evaluator independently adapts the separation and aggregation concepts
audited in:

- `evoagentx/evaluators/evaluator.py`
- `evoagentx/evaluators/aflow_evaluator.py`
- `evoagentx/benchmark/benchmark.py`
- `evoagentx/benchmark/metrics.py`

The adapted concepts are task decomposition, capability-aware workflow
generation, and separate agent configuration generation. ModelMirror's
implementation uses its own Pydantic contracts, Workflow Node Registry,
Runtime Middleware Registry, AuthoringProposalStore, XpertStore, workflow
validator, and publish preflight.

No EvoAgentX source file is copied into this package and EvoAgentX is not a
runtime dependency. Provider, RAG, storage, HITL, memory, tool, workflow, and
publication runtimes remain ModelMirror implementations.

Any future selective source reuse must retain the upstream copyright and MIT
license, record the exact source path and content digest, audit transitive
licensing, and add a local test mapping before code is accepted.

Canonical audit and roadmap:

- `docs/EVOAGENTX_AUDIT_V014.md`
- `docs/EVOAGENTX_ALIGNMENT.md`
- `docs/META_AGENT.md`
- `docs/XPERT_FREEZE.md`
