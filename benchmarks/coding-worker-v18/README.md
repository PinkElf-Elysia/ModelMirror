# Coding Worker V18 calibration tasks

This directory is an evaluation-only Harbor 0.21.0 task set. It is not a
production Server dependency and it is not an equivalence certification.

The public `environment/project` directory is the only source tree exposed to
an agent. Oracle solutions live under `solution`; `tests` contains only the
public verifier wrapper and workspace-policy checks. Hidden checker bodies live
in a separately provisioned read-only directory outside this repository and
are copied into an ephemeral Harbor task package only after their per-task
hashes and the aggregate sealed-bundle hash have been verified. Interactive
timing is frozen in `scenario.json`.
The ModelMirror Worker Agent keeps `MODELMIRROR_HARBOR_BENCHMARK_ROOT` pointed
at this public directory; only Harbor's verifier build receives the ephemeral
task path containing the checker body.
`fixture-bundle.json` and each `environment/source.json` are generated from the
tracked task inputs by `scripts/coding_worker_harness.py compile --write`.

The initial calibration matrix contains twelve tasks, three each for Python,
TypeScript/React, repository development, and persistent-session behavior.
Every task has an unrelated regression assertion and an asset canary. The
registry-navigation task is the long-context fixture and contains at least 30
source/configuration files.

No real-model run is implied by checking in these tasks. The 48-run calibration
requires a separate authorized evaluation window.
Both agents receive the task's 900-second timeout. The Worker adapter uses the
same 900 seconds as its API task budget and derives token usage from durable,
provider-neutral usage events; a checker-passing trial without provable
nonzero usage is rejected instead of becoming a successful calibration run.
Native OpenCode receives an explicit deny-by-default permission map. Only
workspace file/Todo tools and the exact commands frozen by the task are
enabled. Native LSP is fail-closed because OpenCode 1.18.9 language-server
processes inherit the Server environment and may load repository plugins; the
Worker's isolated LSP remains available, and this asymmetric calibration limit
is bound into the route digest. Visible checks are classified as inspect
operations; only scenario shell commands explicitly marked `mutate` count as
side effects. Turn, tool, public-output and normalized token limits are bound
into the route digest and are checked against the ATIF record.

The native side uses the checked-in `NativeOpenCodeHarnessAgent`, which reuses
Harbor's pinned OpenCode installation but drives OpenCode 1.18.9 through its
authenticated loopback Session API. It records a reduced ledger for question,
steering, explicit compaction, component-fault and reconcile actions. A run
cannot be accepted without that ledger, the matching public messages, actual
question/compaction events, and a one-to-one unknown/reconcile operation chain.
All frozen shell commands pass through an agent-private platform wrapper that
uses an empty allowlisted environment and uid/gid 65534, while the authenticated
server and model route secrets remain in a root-only directory. For the frozen
restart case, that supervisor records the exact command's successful exit and
blocks its tool receipt; the controller then terminates the complete OpenCode
process group and binds the original call ID, intent hash, and result hash
before it can settle the operation.
Vendor reasoning frames and credentials are discarded before persistence.

Harbor's Docker environment cannot enforce `no-network` from a native Windows
controller. Build `harbor-controller.Dockerfile` and run the fixed Linux
controller with only the Docker API socket, this repository read-only, and a
dedicated writable jobs directory. The task Agent and verifier containers do
not receive the Docker socket. The controller image is evaluation-only and is
never a Server dependency.

`harbor-network-overlay.yml` reserves a routable, explicit subnet because this
development host's default Docker address pools are exhausted. It is not marked
`internal`: Docker Desktop otherwise prevents the task container from resolving
or reaching the exact egress sidecar. The evaluation-only sidecar remains the
enforcement boundary and accepts only the frozen hostname allowlist; direct
task-container egress is rejected. Harness commands must use `--n-concurrent 1`
with this overlay. Worker concurrency is exercised inside the product scheduler;
separate Harbor trials must not share this subnet concurrently.

The deterministic Oracle/Nop/near-miss gate uses the repository's
`StaticNoNetworkDockerEnvironment`, which applies Harbor's own
`network_mode: none` overlay and rejects every networked phase. It exists only
for task validity checks on Docker Desktop. A separate
`DockerDesktopAllowlistProbeEnvironment` can exercise a few explicitly selected
real-model scenarios through the exact-host sidecar, but it is hard-rejected by
`run-round` and is not calibration evidence. Only the three Session fixtures
currently use the daemon-attested offline OpenCode runtime; `run-round` also
fails closed until all twelve task images have equivalent runtime coverage.
Real-model calibration must use the standard Harbor Docker environment on a
Linux host that passes Harbor's dynamic egress-control preflight. `run-round`
also requires one or more exact
`--allow-agent-host` values: Harbor converts the otherwise network-free agent
phase into an allowlist containing only the frozen model gateway hostname(s).
IP literals, wildcards and localhost are rejected. The native model name,
Worker route and canonical host allowlist are hashed into every run record;
the clean candidate commit and runtime-attested controller image ID are also
bound to every run. `run-round` must execute inside the frozen Linux controller;
it reconciles its own container ID and immutable image ID through the Docker
daemon and does not accept a caller-supplied digest. `report` derives all three
identities from the complete 48-run matrix.
`validate`, `task-gate`, and `run-round` require
`--sealed-checker-root <absolute-directory>`; the public task tree must not
contain a hidden checker body. Every run record stores the observed sealed
bundle digest (including the complete fixture-bundle digest), and `report`
derives that binding from the complete matrix
instead of accepting a caller-supplied hash.

The authorized targeted probe on 2026-08-21 was deliberately not a calibration:
the clarification and steering/compaction Session tasks passed their sealed
checkers, while the restart/reconcile task was rejected after the model attempted
non-allowlisted shell commands and produced the wrong stable index order. The
reconcile ledger itself closed with zero duplicate side effects, unsettled
operations, orphaned interactions, or platform-coordination failures. This 2/3
result keeps the Harness Experimental and does not support an equivalence claim.
