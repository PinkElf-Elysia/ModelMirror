#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: verify.sh <base> [quick|full] <external-pull|redistributable-bundle>" >&2
}

BASE_INPUT="${1:-}"
MODE="${2:-full}"
DISTRIBUTION_MODE="${3:-}"
if [[ -z "$BASE_INPUT" || "$BASE_INPUT" =~ ^0+$ ]]; then
  echo "comparison base is required and must not be all-zero" >&2
  usage
  exit 2
fi
if [[ "$MODE" != "quick" && "$MODE" != "full" ]]; then
  echo "invalid verification mode: $MODE" >&2
  usage
  exit 2
fi
if [[ "$DISTRIBUTION_MODE" != "external-pull" && "$DISTRIBUTION_MODE" != "redistributable-bundle" ]]; then
  echo "invalid distribution mode: $DISTRIBUTION_MODE" >&2
  usage
  exit 2
fi
MODULE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$MODULE_ROOT/../.." && pwd)"
COMPOSE_PROJECT="${AI_RESEARCH_COMPOSE_PROJECT:-modelmirror-ai-research}"
COMPOSE=(docker compose -p "$COMPOSE_PROJECT" -f compose.yml --profile ai-research)
LITERATURE_COMPOSE=(docker compose -p "$COMPOSE_PROJECT" -f compose.yml --profile literature)
CLIENT_BASELINE_PROOF_DIR=""
CLIENT_CURRENT_PROOF_DIR=""
CLIENT_SOURCE_PROOF_DIR=""
CLIENT_PROOF_CONTAINER=""
CLIENT_BASELINE_CONTEXT=""
CLIENT_CURRENT_CONTEXT=""
CLIENT_SOURCE_CONTEXT=""
PYTEST_BASETEMP=""

BASE=$(git -C "$REPO_ROOT" rev-parse --verify "${BASE_INPUT}^{commit}") || {
  echo "comparison base cannot be resolved: $BASE_INPUT" >&2
  exit 2
}
if ! git -C "$REPO_ROOT" merge-base --is-ancestor "$BASE" HEAD; then
  echo "comparison base is not an ancestor of HEAD: $BASE" >&2
  exit 2
fi
TRUST_FILES=(
  extensions/ai-research/source-lock.json
  extensions/ai-research/module-boundary.json
)
if ! git -C "$REPO_ROOT" diff --quiet --no-ext-diff "$BASE" HEAD -- "${TRUST_FILES[@]}"; then
  echo "AI Research trust configuration changed in the candidate" >&2
  git -C "$REPO_ROOT" diff --name-only --no-ext-diff "$BASE" HEAD -- "${TRUST_FILES[@]}" >&2 || true
  exit 2
fi
if ! git -C "$REPO_ROOT" diff --quiet --no-ext-diff --cached HEAD -- "${TRUST_FILES[@]}"; then
  echo "AI Research trust configuration changed in the workspace index" >&2
  git -C "$REPO_ROOT" diff --name-only --no-ext-diff --cached HEAD -- "${TRUST_FILES[@]}" >&2 || true
  exit 2
fi
if ! git -C "$REPO_ROOT" diff --quiet --no-ext-diff -- "${TRUST_FILES[@]}"; then
  echo "AI Research trust configuration changed in the workspace" >&2
  git -C "$REPO_ROOT" diff --name-only --no-ext-diff -- "${TRUST_FILES[@]}" >&2 || true
  exit 2
fi
cd "$MODULE_ROOT"
mkdir -p runtime/diagnostics runtime/sbom
cleanup() {
  if [[ -n "$CLIENT_PROOF_CONTAINER" ]]; then
    docker rm -f "$CLIENT_PROOF_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [[ "$CLIENT_BASELINE_PROOF_DIR" == "$MODULE_ROOT"/runtime/client-baseline-proof.* ]]; then
    rm -rf -- "$CLIENT_BASELINE_PROOF_DIR"
  fi
  if [[ "$CLIENT_CURRENT_PROOF_DIR" == "$MODULE_ROOT"/runtime/client-current-proof.* ]]; then
    rm -rf -- "$CLIENT_CURRENT_PROOF_DIR"
  fi
  if [[ "$CLIENT_SOURCE_PROOF_DIR" == "$MODULE_ROOT"/runtime/client-source-proof.* ]]; then
    rm -rf -- "$CLIENT_SOURCE_PROOF_DIR"
  fi
  if [[ "$CLIENT_BASELINE_CONTEXT" == "$MODULE_ROOT"/runtime/client-baseline-context.* ]]; then
    rm -rf -- "$CLIENT_BASELINE_CONTEXT"
  fi
  if [[ "$CLIENT_CURRENT_CONTEXT" == "$MODULE_ROOT"/runtime/client-current-context.* ]]; then
    rm -rf -- "$CLIENT_CURRENT_CONTEXT"
  fi
  if [[ "$CLIENT_SOURCE_CONTEXT" == "$MODULE_ROOT"/runtime/client-source-context.* ]]; then
    rm -rf -- "$CLIENT_SOURCE_CONTEXT"
  fi
  if [[ "$PYTEST_BASETEMP" == "$MODULE_ROOT"/runtime/pytest.* ]]; then
    rm -rf -- "$PYTEST_BASETEMP"
  fi
  "${LITERATURE_COMPOSE[@]}" down >/dev/null 2>&1 || true
}
trap cleanup EXIT

BOUNDARY_ARGS=(scripts/validate_boundary.py --base "$BASE" --distribution-mode "$DISTRIBUTION_MODE")
python "${BOUNDARY_ARGS[@]}"
PYTEST_BASETEMP=$(mktemp -d "$MODULE_ROOT/runtime/pytest.XXXXXX")
python -m pytest \
  tests/control/test_boundary_base.py \
  tests/control/test_zero_footprint_base.py \
  -q -p no:cacheprovider --basetemp "$PYTEST_BASETEMP"
"${COMPOSE[@]}" config --quiet
"${LITERATURE_COMPOSE[@]}" config --quiet
docker build --target test -f control/Dockerfile -t modelmirror-ai-research-control-test:v0.1 .
docker run --rm modelmirror-ai-research-control-test:v0.1
docker build --target test -f worker/Dockerfile -t modelmirror-ai-research-worker-test:v0.1 .
docker run --rm modelmirror-ai-research-worker-test:v0.1

if [[ "$MODE" == "quick" ]]; then
  exit 0
fi

docker build --sbom=true --provenance=true --target runtime -f control/Dockerfile \
  -t modelmirror-ai-research-control:v0.1 .
docker build --sbom=true --provenance=true --target runtime -f worker/Dockerfile \
  -t modelmirror-ai-research-worker:v0.1 .
docker run --rm modelmirror-ai-research-control:v0.1 \
  cat //usr/share/doc/modelmirror-ai-research/runtime-inventory.json \
  > runtime/sbom/control-runtime-inventory.json
docker run --rm modelmirror-ai-research-control:v0.1 \
  cat //usr/share/doc/modelmirror-ai-research/ui-build-inventory.json \
  > runtime/sbom/ui-build-inventory.json
docker run --rm modelmirror-ai-research-worker:v0.1 \
  cat //usr/share/doc/modelmirror-ai-research/runtime-inventory.json \
  > runtime/sbom/worker-runtime-inventory.json
CONTROL_EXPECTED=$(python -c "import json; print(json.load(open('source-lock.json'))['licenseAudit']['control']['inventorySha256'])")
WORKER_EXPECTED=$(python -c "import json; print(json.load(open('source-lock.json'))['licenseAudit']['worker']['inventorySha256'])")
UI_EXPECTED=$(python -c "import json; print(json.load(open('source-lock.json'))['licenseAudit']['ui']['inventorySha256'])")
[[ "$(sha256sum runtime/sbom/control-runtime-inventory.json | cut -d' ' -f1)" == "$CONTROL_EXPECTED" ]]
[[ "$(sha256sum runtime/sbom/worker-runtime-inventory.json | cut -d' ' -f1)" == "$WORKER_EXPECTED" ]]
[[ "$(sha256sum runtime/sbom/ui-build-inventory.json | cut -d' ' -f1)" == "$UI_EXPECTED" ]]
measure_image() {
  local image="$1" slug="$2" tar_path="runtime/diagnostics/${2}-image.tar"
  docker save -o "$tar_path" "$image"
  gzip -c "$tar_path" > "${tar_path}.gz"
  docker image inspect "$image" --format \
    "{{.Id}}|{{.Size}}|archiveBytes=$(stat -c%s "$tar_path")|gzipBytes=$(stat -c%s "${tar_path}.gz")"
  rm -f "$tar_path" "${tar_path}.gz"
}
{
  measure_image modelmirror-ai-research-control:v0.1 control
  measure_image modelmirror-ai-research-worker:v0.1 worker
} > runtime/diagnostics/image-identities.txt

"${COMPOSE[@]}" up -d
python scripts/acceptance.py initial --state runtime/acceptance-state.json
python scripts/acceptance.py inspect-view-logs --state runtime/acceptance-state.json
"${COMPOSE[@]}" stop ai-research-inspect-view
if ! python scripts/acceptance.py view-degraded --state runtime/view-degraded-state.json; then
  "${COMPOSE[@]}" start ai-research-inspect-view
  exit 1
fi
"${COMPOSE[@]}" start ai-research-inspect-view
python scripts/acceptance.py outbox-create --state runtime/outbox-state.json
"${COMPOSE[@]}" stop ai-research-tracking
python scripts/acceptance.py required-not-ready --state runtime/outbox-state.json
if ! python scripts/acceptance.py outbox-terminal --state runtime/outbox-state.json; then
  "${COMPOSE[@]}" start ai-research-tracking
  exit 1
fi
"${COMPOSE[@]}" start ai-research-tracking
python scripts/acceptance.py outbox-recovery --state runtime/outbox-state.json

python scripts/acceptance.py worker-restart-create --state runtime/worker-restart-state.json
"${COMPOSE[@]}" restart ai-research-worker
python scripts/acceptance.py worker-restart-recovery --state runtime/worker-restart-state.json
for _ in 1 2; do
  "${COMPOSE[@]}" restart ai-research-control ai-research-tracking
  python scripts/acceptance.py recovery --state runtime/acceptance-state.json
done
if [[ "${AI_RESEARCH_LIVE_ACCEPTANCE:-}" == "1" ]]; then
  "${LITERATURE_COMPOSE[@]}" up -d ai-research-model-relay ai-research-ldr
  python scripts/literature_acceptance.py initial --state runtime/literature-acceptance-state.json
  for _ in 1 2; do
    "${LITERATURE_COMPOSE[@]}" restart ai-research-control ai-research-ldr
    python scripts/literature_acceptance.py recovery --state runtime/literature-acceptance-state.json
  done
else
  echo "warning: live model/OpenAlex/Zotero journey was not run; V0.1 real acceptance remains open" >&2
fi
mapfile -t RUN_IDS < <(python -c \
  "import json; a=json.load(open('runtime/acceptance-state.json')); o=json.load(open('runtime/outbox-state.json')); w=json.load(open('runtime/worker-restart-state.json')); print(*a['runs'],o['runId'],w['runId'],sep='\\n')" \
  | tr -d '\r')
AUDIT_ARGS=()
for run_id in "${RUN_IDS[@]}"; do AUDIT_ARGS+=(--run-id "$run_id"); done
"${COMPOSE[@]}" exec -T ai-research-control python -m ai_research_control.audit_runtime "${AUDIT_ARGS[@]}"
"${COMPOSE[@]}" exec -T ai-research-control python -c \
  "import json,os,socket; s=socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect(os.environ['AI_RESEARCH_WORKER_SOCKET']); s.sendall(b'x'*70000+b'\\n'); value=json.loads(s.makefile('rb').readline()); s.close(); assert value['ok'] is False"

"${COMPOSE[@]}" exec -T ai-research-worker python -c \
  '
import socket
import urllib.request

unexpected = []
checks = [
    ("dns", lambda: socket.getaddrinfo("example.com", 443)),
    ("tcp", lambda: socket.create_connection(("1.1.1.1", 443), timeout=1)),
    ("http", lambda: urllib.request.urlopen("http://example.com", timeout=1)),
    ("host", lambda: socket.getaddrinfo("host.docker.internal", 8000)),
]
for name, check in checks:
    try:
        check()
    except OSError:
        continue
    unexpected.append(name)
if unexpected:
    raise SystemExit("network unexpectedly available: " + ",".join(unexpected))
'
"${COMPOSE[@]}" exec -T ai-research-control python -c \
  '
import socket
import urllib.request

unexpected = []
checks = [
    ("dns", lambda: socket.getaddrinfo("example.com", 443)),
    ("tcp", lambda: socket.create_connection(("1.1.1.1", 443), timeout=1)),
    ("http", lambda: urllib.request.urlopen("http://example.com", timeout=1)),
    ("host", lambda: socket.getaddrinfo("host.docker.internal", 8000)),
]
for name, check in checks:
    try:
        check()
    except OSError:
        continue
    unexpected.append(name)
if unexpected:
    raise SystemExit("network unexpectedly available: " + ",".join(unexpected))
'

ENVIRONMENT_SERVICES=(
  ai-research-control
  ai-research-console-gateway
  ai-research-tracking
  ai-research-worker
  ai-research-inspect-view
)
if [[ "${AI_RESEARCH_LIVE_ACCEPTANCE:-}" == "1" ]]; then
  ENVIRONMENT_SERVICES+=(ai-research-model-relay ai-research-ldr)
fi
CONTAINER_ENV=""
for service in "${ENVIRONMENT_SERVICES[@]}"; do
  container_id=$("${LITERATURE_COMPOSE[@]}" ps -q "$service")
  if [[ -z "$container_id" ]]; then
    echo "running Compose service is missing: $service" >&2
    exit 1
  fi
  CONTAINER_ENV+=$(docker inspect "$container_id" --format '{{json .Config.Env}}')$'\n'
done
if grep -E 'OPENROUTER_API_KEY|LLM_GATEWAY_KEY|DIFY_API_KEY|PROVIDER.*(KEY|TOKEN|SECRET)|sk-(or-v1-)?[A-Za-z0-9_-]{32,}|gh[pousr]_[A-Za-z0-9]{30,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY' <<<"$CONTAINER_ENV"; then
  echo "provider credential names were exposed to module containers" >&2
  exit 1
fi

build_client_proof() {
  local git_ref="$1"
  local context="$2"
  local image="$3"
  local archive="$context/client.tar"
  git -C "$REPO_ROOT" archive --format=tar --output="$archive" "$git_ref" client
  tar -xf "$archive" -C "$context"
  rm -f -- "$archive"
  docker build \
    -f "$MODULE_ROOT/scripts/client-proof.Dockerfile" \
    -t "$image" \
    "$context"
}

extract_client_proof() {
  local image="$1"
  local destination="$2"
  CLIENT_PROOF_CONTAINER=$(docker create "$image")
  docker cp "$CLIENT_PROOF_CONTAINER:$CLIENT_PROOF_SOURCE" "$destination"
  docker rm "$CLIENT_PROOF_CONTAINER" >/dev/null
  CLIENT_PROOF_CONTAINER=""
}

LOCKED_BASE=$(python -c "import json; print(json.load(open('source-lock.json'))['modelMirrorBaseCommit'])")
CLIENT_SOURCE_PROOF_DIR=$(mktemp -d "$MODULE_ROOT/runtime/client-source-proof.XXXXXX")
CLIENT_BASELINE_PROOF_DIR=$(mktemp -d "$MODULE_ROOT/runtime/client-baseline-proof.XXXXXX")
CLIENT_CURRENT_PROOF_DIR=$(mktemp -d "$MODULE_ROOT/runtime/client-current-proof.XXXXXX")
CLIENT_SOURCE_CONTEXT=$(mktemp -d "$MODULE_ROOT/runtime/client-source-context.XXXXXX")
CLIENT_BASELINE_CONTEXT=$(mktemp -d "$MODULE_ROOT/runtime/client-baseline-context.XXXXXX")
CLIENT_CURRENT_CONTEXT=$(mktemp -d "$MODULE_ROOT/runtime/client-current-context.XXXXXX")
CLIENT_PROOF_SOURCE="/proof/dist/."
case "$(uname -s)" in
  MINGW*|MSYS*) CLIENT_PROOF_SOURCE="//proof/dist/." ;;
esac
build_client_proof \
  "$LOCKED_BASE" \
  "$CLIENT_SOURCE_CONTEXT" \
  modelmirror-ai-research-client-proof:v0.1-source
build_client_proof \
  "$BASE" \
  "$CLIENT_BASELINE_CONTEXT" \
  modelmirror-ai-research-client-proof:v0.1-baseline
build_client_proof \
  HEAD \
  "$CLIENT_CURRENT_CONTEXT" \
  modelmirror-ai-research-client-proof:v0.1
extract_client_proof \
  modelmirror-ai-research-client-proof:v0.1-source \
  "$CLIENT_SOURCE_PROOF_DIR"
extract_client_proof \
  modelmirror-ai-research-client-proof:v0.1-baseline \
  "$CLIENT_BASELINE_PROOF_DIR"
extract_client_proof \
  modelmirror-ai-research-client-proof:v0.1 \
  "$CLIENT_CURRENT_PROOF_DIR"
python scripts/zero_footprint.py \
  --base "$BASE" \
  --source-client-dist "$CLIENT_SOURCE_PROOF_DIR" \
  --baseline-client-dist "$CLIENT_BASELINE_PROOF_DIR" \
  --client-dist "$CLIENT_CURRENT_PROOF_DIR"
rm -rf -- \
  "$CLIENT_SOURCE_PROOF_DIR" \
  "$CLIENT_BASELINE_PROOF_DIR" \
  "$CLIENT_CURRENT_PROOF_DIR" \
  "$CLIENT_SOURCE_CONTEXT" \
  "$CLIENT_BASELINE_CONTEXT" \
  "$CLIENT_CURRENT_CONTEXT"
CLIENT_SOURCE_PROOF_DIR=""
CLIENT_BASELINE_PROOF_DIR=""
CLIENT_CURRENT_PROOF_DIR=""
CLIENT_SOURCE_CONTEXT=""
CLIENT_BASELINE_CONTEXT=""
CLIENT_CURRENT_CONTEXT=""
