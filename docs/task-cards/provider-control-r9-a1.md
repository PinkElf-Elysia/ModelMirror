# R9A1: Provider source coverage and anti-bypass guard

## Contract and baseline

- Objective: inventory first-party model entry points and sending boundaries, then
  reject unreviewed source changes in CI. This is not runtime routing enforcement.
- Branch: `codex/provider-control-r9-a1`.
- Worktree: `C:\tmp\modelmirror-control-r9-a1`; initially clean.
- Planning base: `8c2a0120226be26f05c875f81c122589b216525d`.
- Fetched implementation base: `4932e3b4ae470ebb30c7f775d53e30961a5a07fd`.
- Cross-audit: PR #399 changes CI Node/dependency setup, baseline tests, help assets,
  Applier cleanup and Agency Worker stdin-disconnect handling. It does not change
  Provider routing contracts. No historical test exemption is inherited.
- Allowed source paths: the five files listed below only. Runtime code, main
  checkout, unrelated worktrees/containers/data, credentials and configuration
  remain untouched. Task-owned baseline and isolated test artifacts are separate.
- API/storage/dependency impact: none. No migration, Provider call, deployment
  or activation is authorized. On 2026-10-03 the user separately authorized
  submission after local validation: commit, push and PR only; no merge or A2.
- Risk: medium (CI coverage assertions), not a data-plane change.

## Deliverables

1. `docs/audits/provider-coverage.json`: canonical, reviewable source manifest.
2. `scripts/check_provider_coverage.py`: stdlib-only, read-only source checker.
3. `server/tests/test_provider_coverage.py`: offline mutation tests.
4. `.github/workflows/quality.yml`: required-by-workflow checks, not a claim about
   repository branch protection settings.
5. This task card: scope, evidence limits and validation log.

The manifest distinguishes `managed_integrated`, `migration_pending`,
`external_domain`, and `excluded`. These are source classifications only:
`managed_integrated` does **not** mean enabled, currently certified, operational,
production-ready or eligible for default traffic.

## Coverage findings

- All 37 Workload registry entries are compared against their exact flags,
  execution shapes and integrated-entry registry without importing server code.
- R5 default/Auto, R3 Canary, RPG scoped bridge and AI Research bridge are separate
  entries. Auto's multi-attempt semantics are not changed.
- File one-shot Vision/OCR, ordinary Workbench and image description, Decisions/
  TEV, Xpert enrichment/memory/evaluation/evolution, Benchmark, Automation, Goal,
  and Skill creation/resource/evaluation paths remain explicitly pending.
- Shared Workflow helpers and configuration lookups are not blanket assertions
  that every caller is managed. Caller context gaps remain R9C/D work.
- Catalog GET, generation/task GET, ordinary workflow HTTP, local IPC, inbound
  body readers, Coding, Dify, Marble and external tool domains are classified
  separately from model POSTs. A method named `send` is not automatically a model.
- RPG scoped integration does not cover historical direct RPG adapters. Those
  remain pending and must be audited in D8, not silently exempted.
- Additional discovered domains: Matrix Oasis and the independent OpenAI Agents
  experiment. Their disposition needs explicit user review; they are registered
  as pending boundary decisions, not migrated or accepted exclusions. A1 does
  not expand R9 into these products.

## Guard semantics and maintenance

Given an unchanged classified source tree, the checker must pass without server
imports, database initialization, network access or file creation.

Given a new or changed sending candidate, the checker must fail until a maintainer
reviews its source and updates its exact record, classification and entry links.
A second identical POST in the same function receives a distinct occurrence ID.
Removed sites fail as stale. Workload flag/shape/integration drift fails separately.

Python identities use path, qualified symbol, AST call/import hash and occurrence,
not line numbers. The containing Python module AST is pinned too: changing a URL
constant must not turn a registered ordinary POST into an unreviewed model POST.
Comments and whitespace do not churn Python identities. Common
network imports, constructors, bound method aliases and registered delegates are
detected. JS/TS candidates pin the entire source module; this is conservative and
can require review for an incidental UI change. It is not a JavaScript parser.

Run `python -B scripts/check_provider_coverage.py --discover` for diagnostics.
This prints candidate metadata only; it neither modifies nor approves the manifest.
Never auto-approve all discoveries or use a file/directory wildcard to authorize
future sends. Review ordinary HTTP exceptions as carefully as model senders.

Static checks cannot prove runtime control flow, reflective/dynamic invocation,
native binaries, generated code, or a new language/tool outside the enumerated
source roots. Those need code review and later runtime evidence. This check must
not be presented as a security sandbox or full-platform zero-bypass proof.
Test/fixture/vendor/build directories and root maintenance scripts are outside
this product-source scan; they must not become a route for production imports.

## Validation

| Check | Command/evidence | Status |
| --- | --- | --- |
| Offline mutation tests | `python -B -m unittest discover -s server/tests -p test_provider_coverage.py -v`: 20 passed | 通过 |
| Inventory consistency | `python -B scripts/check_provider_coverage.py`: 80 entries, 930 candidates, 357 modules | 通过 |
| Frontend full tests | `npm run test:run`: 149 files / 1109 tests + 1 response-header test | 通过 |
| Frontend typecheck/build/help assets | `npm run typecheck`; `npm run build`; `npm run verify:help-images` | 通过 |
| Provider affected regression | coverage + Chat contract + Workload + Multimodal control tests; final rerun 81 passed | 通过 |
| Full backend tests (Linux, CI split) | remaining suite: 6969 passed, 30 skipped, 0 failed; workflow contract separately: 7 passed | 通过，跳过边界见下文 |
| Linux companion CI checks | OpenRouter guards: 21 passed; Agency worker core: 75 passed; upstream scripts exit 0 | 通过 |
| Windows CI-specific checks | Project Host native/cleanup and Applier temporary-file checks: 92 passed, 1 skipped (directory symlinks unavailable) | 通过，保留环境缺口 |
| Compose configuration | Core, independent newAPI and checked-in newAPI overlay `config --quiet` | 通过 |
| Effective deployed overlays | no deployment inspected or changed; A1 creates no preview | 未运行 |
| Diff and secret review | `git diff --check`, new-file whitespace checks, five-file credential-pattern scan (zero matches) | 通过 |
| Preview/runtime/paid smoke | no data-plane or UI change; no deployment authorized | 未运行 |

Initial sandboxed unit run: 18 passed, one temporary-directory permission error.
The same command under approved test-fixture permissions passed all 19 tests;
no test was skipped or relaxed. No application data was read or modified.

The Provider regression initially reported 44 passed / 36 setup errors because
the existing system `pytest-of-21547` directory denied access. The unchanged test
selection with a new task-owned `--basetemp` passed all 80. Test environments and
temporary artifacts are task-local and not deliverables.

### Historical Windows full-suite blocker (not waived or patched)

- Command: `python -B -m pytest server/tests/ -q -x -p no:cacheprovider
  --basetemp=<new task-owned directory> --tb=short`.
- First failure: `test_coding_applier_engine.py::test_apply_is_atomic_and_idempotent`.
- Cause: `server/coding_applier/engine.py:943` uses `os.fchmod`, absent on Windows.
- Reproduced with the identical interpreter/dependencies in a clean detached
  `4932e3b4` worktree at `C:\tmp\modelmirror-control-r9-a1-baseline`:
  one selected test failed with the same stack; Git status remained clean.
- The baseline command's pytest result is **failed** even though a subsequent
  `git status` in the shell returned exit 0. Do not use shell exit 0 as test proof.
- The first Windows full run was stopped near 6% after multiple failures to
  investigate; those other Windows failures have NOT all been attributed.
- No Coding fix, skip-list, pytest monkeypatch or historical exemption was applied.
  The subsequent compatible-platform full run below passes. This resolves the
  missing Linux gate, not a claim that the entire backend suite supports Windows.
  A2 has not begun.

### Linux full-suite completion (2026-10-03)

- Source: tracked archive of `4932e3b4ae470ebb30c7f775d53e30961a5a07fd`
  plus exactly the five R9A1 deliverables. No host environment, `.env`, Windows
  virtualenv or Windows `node_modules` was copied into the source snapshot.
- Test-only image: `modelmirror-r9a1-linux-validation:4932e3b4`, image ID
  `sha256:8cd2dcf996cfd7c0d2bdf261d5d9358df528343ed9d625f0d101e768e202e8a7`.
  Debian Linux, Python 3.12.14 and Node 24.18.0; repository requirements installed
  unchanged and RPG/Agency dependencies installed from their existing lockfiles.
  This is a local Linux CI-equivalent test, not an actual GitHub Actions result.
- Test container has network `none`, zero mounts and no published ports. Tests
  receive a clean `env -i` environment, task-local HOME/upload root and no Provider
  credentials. Existing application containers and persistent data are untouched.
- Commands, matching the existing CI process split:
  `python -B -m pytest server/tests/test_workflow_run_contract.py -q -p no:cacheprovider`
  (7 passed), then `python -B -m pytest server/tests/ -q
  --ignore=server/tests/test_workflow_run_contract.py -p no:cacheprovider`
  (6969 passed, 30 skipped, 7 warnings, exit 0, 1229.20 seconds).
  The workflow file is separately tested, not waived.
- The original Windows `os.fchmod` failing test also passed individually on Linux,
  alongside all 20 new coverage tests. Inventory check: 80 entries / 930 candidates.
- Local evidence directory: `C:\tmp\modelmirror-r9a1-linux-validation`.
  `backend-full.xml` SHA256:
  `066f954f9e83c63b74c605bcd01485cc021dcd85de0728d25a6f613f03d40765`.
  `workflow-contract.xml` SHA256:
  `4f4c11fe49030639c5773612e255f6b0ecb1039c89c6770153c81f99018b0827`.
  Full log and Worker output are retained locally, not committed.
- All 30 skips are existing conditional tests: Windows native contracts (9),
  dedicated mcp-files/Office sidecar dependencies (12), fixed language servers
  in the executor image (5), optional docx/matplotlib (2), isolated renderer (1),
  deployed upstream worker (1). No skip rule or test selection was changed beyond
  the existing CI workflow split. Those external/optional integrations are not
  claimed tested by this run; A1 changes neither their source nor their deployment.
- Supplemental Windows CI selection used the task-owned Python environment:
  `pytest server/tests/test_coding_project_host_windows.py
  server/tests/test_coding_project_host.py::test_host_commit_windows_native_apply_commit_and_undo
  server/tests/test_coding_project_host.py::test_host_commit_windows_private_object_cleanup_rejects_replacement
  server/tests/test_coding_applier_tempfile.py -q -ra -p no:cacheprovider`
  with fresh `--basetemp=C:\tmp\modelmirror-r9a1-windows-ci-01`:
  92 passed, one existing directory-symlink availability skip, exit 0.
  This does not claim every Windows-conditional test in the Linux report ran.
- Warnings remain visible: existing lifecycle/PyPDF2 deprecations, Pydantic forward
  reference and duplicate Dify operation ID. Dependency installation also reported
  one high-severity npm advisory; no audit fix, dependency upgrade or security
  clearance is claimed. Initial test-image setup mixed old/new npm files and failed;
  isolating the Node distribution under `/opt/r9-node` fixed that harness issue.

Environment: Python 3.12.14, Windows; Node 24.19.0 bundled runtime. CI remains
Node 24.18.0 / Python 3.12 on Ubuntu. Runtime package manifests and lockfiles are
unchanged. Existing frontend large-chunk warning remains; no build limit changed.

Help Center Impact: None. This is an offline source/CI guard; no user-facing
feature, UI, settings workflow, readiness value or help screenshot changes.

## Stop and rollback

- Stop on source/coverage inconsistency, unreadable source, unexplained failures,
  secret/content exposure, or a request to expand an independent execution domain.
- False positives need exact classification, not disabled checks or wildcard
  exceptions. New coverage never authorizes a paid test or production migration.
- Rollback only these five files through a reviewed revert. No database snapshot,
  policy rollback or service restart is needed. Existing transport safety remains.
- A1 completion does not authorize A2 before merge. Submission is separately
  authorized above; stop after PR creation and report actual remote CI status.
- Current acceptance boundary: A1 source-guard implementation and local automatic
  gates, including Linux full backend, have passed with the explicit conditional
  integration gaps above. Submission authorized; GitHub CI is a separate result.
  No R9 completion, production readiness or automatic baseline-failure waiver.
