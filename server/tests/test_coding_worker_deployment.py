from pathlib import Path


def test_v14_sidecar_is_non_root_and_has_no_host_control_mounts() -> None:
    root = Path(__file__).parents[2]
    dockerfile = (root / "server/coding_worker/Dockerfile.v14").read_text()
    shell_sandbox = (root / "server/coding_worker/shell_sandbox.py").read_text()
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
    assert "pids_limit: 192" in executor
    assert "(resource.RLIMIT_NPROC, (256, 256))" in shell_sandbox
    assert "coding_worker_tools:" in compose and "internal: true" in compose
    assert "coding-worker-network" in compose
    assert 'command: ["python", "-m", "coding_worker.egress_proxy"]' in compose
    assert "CODING_WORKER_EGRESS_GRANT_KEY: ${CODING_WORKER_EGRESS_GRANT_KEY:-}" in compose
    assert "'action':'health','payload':{}" in compose
    assert "'task_id':'healthcheck'" not in compose


def test_v15_claude_provider_has_a_pinned_private_image_and_secret_only_mount() -> None:
    root = Path(__file__).parents[2]
    dockerfile = (root / "server/coding_worker/Dockerfile.claude").read_text()
    compose = (root / "docker-compose.coding-worker-v15-claude.yml").read_text()

    assert "CLAUDE_CODE_VERSION=2.1.89" in dockerfile
    assert "CLAUDE_CODE_INTEGRITY=sha512-" in dockerfile
    assert "Claude Code package integrity mismatch" in dockerfile
    assert 'test "$(claude --version | awk' in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert 'CMD ["python", "-m", "coding_worker.sidecar"]' in dockerfile
    assert "git " not in dockerfile
    assert "ripgrep" not in dockerfile

    provider = compose.split("coding-worker-provider-b:", 1)[1].split(
        "secrets:", 1
    )[0]
    assert "CODING_WORKER_PROVIDER_KIND: claude-code" in provider
    assert "CODING_WORKER_ROUTE_ID: coding/quality" in provider
    assert "CODING_WORKER_CLAUDE_SECRET_PATH: /run/secrets/" in provider
    assert "coding_worker_provider_b:/run/modelmirror-coding" in provider
    assert "coding_worker_broker:/run/modelmirror-coding-broker:ro" in provider
    assert "coding_worker_slot_b:/worker-data" not in provider
    assert "CODING_WORKER_ROUTE_KEY" not in provider
    assert "CODING_WORKER_MODEL_BASE_URL" not in provider
    assert "docker.sock" not in provider
    assert ".ssh" not in provider
    assert "internal: true" in compose
    assert "CODING_WORKER_ROUTE_SLOTS_JSON" in compose
    assert "coding-worker-claude-egress:" in compose
    assert "CODING_WORKER_PROVIDER_NETWORK_DOMAINS: api.anthropic.com" in compose
    assert "CODING_WORKER_PROVIDER_EGRESS_TOKEN" in compose
    assert (
        "CODING_WORKER_PROVIDER_ALLOW_DOCKER_DESKTOP_DNS_PROXY: "
        "${CODING_WORKER_PROVIDER_ALLOW_DOCKER_DESKTOP_DNS_PROXY:-false}"
    ) in compose
    assert "CODING_WORKER_PROVIDER_PROXY_URL: http://provider:" in provider
    proxy = compose.split("\n  coding-worker-claude-egress:", 1)[1].split(
        "secrets:", 1
    )[0]
    assert "modelmirror_claude_api_key" not in proxy
    assert "coding_worker_provider_b" not in proxy
