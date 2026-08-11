from pathlib import Path


def test_v14_sidecar_is_non_root_and_has_no_host_control_mounts() -> None:
    root = Path(__file__).parents[2]
    dockerfile = (root / "server/coding_worker/Dockerfile.v14").read_text()
    server_dockerfile = (root / "server/Dockerfile").read_text()
    compose = (root / "docker-compose.coding-worker-v14.yml").read_text()

    assert "COPY coding_worker ./coding_worker" in server_dockerfile
    assert "USER 65532:65532" in dockerfile
    assert 'CMD ["python", "-m", "coding_worker.sidecar"]' in dockerfile
    assert "OPENCODE_VERSION=1.18.9" in dockerfile
    assert "PYRIGHT_VERSION=1.1.411" in dockerfile
    assert "TYPESCRIPT_LANGUAGE_SERVER_VERSION=5.3.0" in dockerfile
    assert "TYPESCRIPT_VERSION=5.9.3" in dockerfile
    assert "coding-worker-provider-a:" in compose
    assert "coding-worker-provider-b:" in compose
    assert "coding-worker-slot-a:" in compose
    assert "coding-worker-slot-b:" in compose
    assert "CODING_WORKER_V14_ENABLED: ${CODING_WORKER_V14_ENABLED:-false}" in compose
    assert "CODING_WORKER_V15_ENABLED: ${CODING_WORKER_V15_ENABLED:-false}" in compose
    assert "CODING_WORKER_SHELL_ENABLED: ${CODING_WORKER_SHELL_ENABLED:-false}" in compose
    assert (
        "CODING_WORKER_CODE_INTELLIGENCE_ENABLED: "
        "${CODING_WORKER_CODE_INTELLIGENCE_ENABLED:-false}"
    ) in compose
    assert "CODING_WORKER_CLAUDE_ENABLED: ${CODING_WORKER_CLAUDE_ENABLED:-false}" in compose
    assert "/var/run/docker.sock" not in compose
    assert ".ssh" not in compose
    assert "network_mode: host" not in compose
    assert compose.count('user: "65532:65532"') == 3  # provider/executor B inherit A
    assert ":/worker-data" in compose
    assert ":/run/modelmirror-coding-broker:ro" in compose
    assert "CODING_WORKER_MODE: executor" in compose
    assert "CODING_WORKER_ROUTE_KEY" not in compose.split("coding-worker-slot-a:", 1)[1].split("coding-worker-egress:", 1)[0]
    assert "coding_worker_slot_a:/worker-data:ro" in compose
    executor = compose.split("coding-worker-slot-a:", 1)[1].split(
        "coding-worker-slot-b:", 1
    )[0]
    assert "coding_worker_tools" in executor
    assert "coding_internal" not in executor
    assert "coding_worker_tools:" in compose and "internal: true" in compose
    assert "coding-worker-network" in compose
    assert 'command: ["python", "-m", "coding_worker.egress_proxy"]' in compose
    assert "CODING_WORKER_EGRESS_GRANT_KEY: ${CODING_WORKER_EGRESS_GRANT_KEY:-}" in compose
