# EvoAgentX Reference and License Notice

This package contains ModelMirror's independently maintained meta-agent
planning schemas, prompts, deterministic compiler, and proposal adapter.

Audited upstream source:

- Project: https://github.com/EvoAgentX/EvoAgentX
- Tag: `v0.1.4`
- Commit: `aad19b912f640161ea07e8904d9237cd34fde5f1`
- License: MIT, Copyright (c) 2025 EvoAgentX

Meta Planner Graph IR V3 continues to adapt the layered concepts found in these audited upstream
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

Prompt Evolution independently adapts the bounded mutation, selection, and
early-stop concepts audited in:

- `evoagentx/optimizers/evoprompt_optimizer.py`

It does not copy EvoPrompt's runtime, logging, benchmark integration, or model
provider implementation. ModelMirror candidates execute through the existing
read-only Xpert Evaluator and can only create revision-bound authoring
proposals after an isolated validation gate.

Structure Evolution independently adapts the bounded candidate search,
evaluation feedback, selection, and early-stop concepts audited in:

- `evoagentx/optimizers/sew_optimizer.py`
- `evoagentx/optimizers/aflow_optimizer.py`

It does not copy dynamic graph execution, Python code generation, file
replacement, benchmark downloads, or EvoAgentX runtime objects. ModelMirror
uses a fixed Pydantic mutation language, deterministic Workflow compiler,
Capability Snapshot, read-only Evaluator, cost gate, and revision-bound
Authoring Proposal.

The adapted concepts are task decomposition, capability-aware workflow
generation, and separate agent configuration generation. ModelMirror's
implementation uses its own Pydantic contracts, Workflow Node Registry,
Runtime Middleware Registry, AuthoringProposalStore, XpertStore, workflow
validator, and publish preflight.

NodeContract V3, Graph IR V3, Headless Authoring, the pure-node Adapter pack,
and the bounded control-flow Adapter/analyzer pack
independently apply the audited parameter-schema and layered validation concepts
to ModelMirror's own node, entrypoint-policy, compiler adapter, intent,
resolved-graph, and typed Patch contracts. No EvoAgentX schema or runtime object
is copied. Graph IR remains a ModelMirror contract and EvoAgentX is not used to
execute workflows.

No EvoAgentX source file is copied into this package and EvoAgentX is not a
runtime dependency. Provider, RAG, storage, HITL, memory, tool, workflow, and
publication runtimes remain ModelMirror implementations.

Any future selective source reuse must retain the upstream copyright and MIT
license, record the exact source path and content digest, audit transitive
licensing, and add a local test mapping before code is accepted.

Canonical audit and roadmap:

- `docs/EVOAGENTX_AUDIT_V014.md`
- `docs/EVOAGENTX_ALIGNMENT.md`
- `docs/EVOAGENTX_EVOLUTION.md`
- `docs/META_AGENT.md`
- `docs/XPERT_FREEZE.md`
