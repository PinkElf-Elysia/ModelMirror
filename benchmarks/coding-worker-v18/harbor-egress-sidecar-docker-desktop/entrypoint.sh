#!/bin/sh
set -eu

GOST_CONFIG=/opt/egress-sidecar/gost.yaml
ALLOWLIST=/opt/egress-sidecar/allowlist.txt
NFTABLES_RULESET_NAME=gost_egress
READY_FILE=/tmp/harbor-docker-egress-control-sidecar.ready

INITIAL_NETWORK_MODE="${EGRESS_CONTROL_INITIAL_NETWORK_MODE:-public}"
INITIAL_ALLOWED_HOSTS="${EGRESS_CONTROL_INITIAL_ALLOWED_HOSTS:-}"

cleanup() {
  rm -f "$READY_FILE"
  nft delete table inet "$NFTABLES_RULESET_NAME" 2>/dev/null || true
}

mkdir -p "$(dirname "$ALLOWLIST")"
: > "$ALLOWLIST"
cleanup
trap cleanup INT TERM EXIT

/bin/gost -C "$GOST_CONFIG" &
gost_pid="$!"

case "$INITIAL_NETWORK_MODE" in
  public)
    network-policy allow-all
    ;;
  no-network)
    network-policy deny-all
    ;;
  allowlist)
    set -f
    # shellcheck disable=SC2086
    network-policy allow $INITIAL_ALLOWED_HOSTS
    set +f
    ;;
  *)
    echo "invalid EGRESS_CONTROL_INITIAL_NETWORK_MODE: $INITIAL_NETWORK_MODE" >&2
    exit 2
    ;;
esac

touch "$READY_FILE"
echo "modelmirror Harbor probe sidecar ready: $INITIAL_NETWORK_MODE"
wait "$gost_pid"
