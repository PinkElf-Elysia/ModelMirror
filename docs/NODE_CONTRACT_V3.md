# NodeContract V3

## Purpose

NodeContract V3 is the single source of truth for the static contract of every
classic workflow node. It removes planner, type, resource, and entrypoint policy
facts from the UI palette and from independent allow/deny lists.

NodeContract V3 does not replace the classic runner or the existing per-node
validator. It does not add a node to Meta Planner. The contract layer describes
the current behavior and lets each entrypoint fail closed when a contract or
compiler adapter is incomplete.

## Contract model

Every `NativeNodeKind` has exactly one `NodeContract` with:

- a bounded configuration JSON Schema;
- typed input and output ports using `WorkflowValueSchema`;
- control, binding, or metadata edge semantics and declared handles;
- side-effect, determinism, idempotency, external-I/O, waiting, and error policy;
- Workflow, Xpert, Goal, Handoff, App, Evaluator, and Evolution availability;
- resource IDs, pinning fields, authorization needs, and dynamic-schema flags;
- Planner support, compilation mode, adapter version, and IR configuration schema.

Edge contracts declare every supported edge mode separately from the modes
that participate in topology. This preserves compatibility nodes such as
`runtime_middleware`, which supports both legacy linear control-flow edges and
Agent binding edges, without adding Validator-specific exceptions.

`contract_status=complete` means these facts are explicit. It does not mean that
Planner, App, Evaluator, or Evolution may use the node. Those decisions remain
explicit fields in the same contract. `contract_status=compatibility` keeps the
existing validator and runner behavior while disabling Planner by default.

Unknown node kinds are rejected. Registry construction also fails when a
`NativeNodeKind` is missing or duplicated.

## Checksums

Each contract has two deterministic SHA-256 values:

- `checksum` covers the full behavior and availability contract.
- `compiler_checksum` covers only compiler-critical configuration, ports,
  edges, resources, and Planner adapter facts.

Display names, descriptions, icons, and palette categories are not compiler
facts. A presentation-only change therefore does not invalidate an adapter.
Capability Snapshot exposes a node only when its complete contract, adapter
version, IR schema checksum, and compiler checksum agree.

## Current migration boundary

The first complete-contract group is:

| Group | Node kinds | Planner state |
| --- | --- | --- |
| Compiler managed | `input`, `output` | enabled in Graph IR V3 |
| Adapter | `workflow_agent` | enabled in Graph IR V3 |
| Resource bindings | `external_xpert`, `knowledge_base`, `toolset_resource`, `plugin_resource` | binding only |
| Pure-data targets | `json_serialize`, `json_deserialize` | unsupported |
| Read targets | `knowledge_retrieval`, `data_table_query`, `vision_understanding` | unsupported |
| Write targets | `data_table_insert`, `data_table_update`, `data_table_delete` | unsupported |
| Metadata and middleware | `annotation`, `runtime_middleware` | metadata or binding contract only |

All other nodes have compatibility contracts and remain executable through the
existing classic validator and runner. Capability Snapshot still exposes only
the existing seven node kinds. Graph IR V3 is the write format; Typed IR V2 is
read-only compatibility input.

JSON nodes retain their current inline-error behavior until the separate pure
node round. Agent Table Query remains disabled in Evaluator. Vision remains
disabled in Evaluator until evaluation datasets can carry explicit file assets.
Condition, loop, and list contracts may describe current handles, but Planner
cannot compile them yet.

## Policy service

`NodePolicyService` is the common static policy query for Xpert publication,
Evaluator, public App deployment, and Structure Evolution. Resource existence,
fixed Toolset safety, middleware configuration, collaboration cycles, and other
domain checks continue to run in their existing services.

The V3 migration preserves the current policy boundary:

- Evaluator rejects Handoff, Human Intervention, every Agent Table node, and
  Vision Understanding.
- public App rejects External Xpert, Plugin, Human Intervention, every Agent
  Table node, and Vision Understanding.
- Structure Evolution can currently add only adapter-backed
  `workflow_agent` control nodes. Resource bindings remain separately scoped.

Conditional entries are not unconditional approval. Their domain preflight
must still pass.

## API and frontend

`GET /api/workflow/node-registry` returns registry version
`xpert-workflow-node-registry-v3`, `contract_version=3`, the registry checksum,
and a safe contract projection for each palette node.

The frontend fallback contains presentation facts only. It has no contract or
Planner claims. If the server response is absent, incomplete, or has a checksum
shape other than V3, contract-dependent operations stay disabled while the
classic palette may continue to render.

Meta Planner Capability Snapshot version is V5 with `ir_version=3` and
`supported_ir_versions=[2,3]`. V5 also projects the typed Headless Authoring
operation schema and per-kind authoring checksum without widening the node set.
Persisted V2 snapshots without contract metadata remain readable. Contract
drift is a warning for an existing proposal; missing or invalid resources still
block approval.

## Change procedure

When adding or changing a node:

1. update `NativeNodeKind` and its unique NodeContract together;
2. keep presentation metadata in the workflow palette only;
3. add or update per-node runtime validation;
4. add a versioned Planner adapter before enabling Planner;
5. verify adapter schema and compiler checksum parity;
6. state every entrypoint policy explicitly;
7. add behavior-equivalence tests for any migrated policy;
8. run Registry, Validator, Planner, Publish, Evaluator, App, and Evolution tests.

Rollback requires restoring the previous Registry and policy call sites. V3
does not migrate Workflow, Proposal, or XpertVersion data.
