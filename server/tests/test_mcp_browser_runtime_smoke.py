from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path

import pytest

from server.sandbox_sidecar import smoke_browser_adapters as adapter_smoke
from server.sandbox_sidecar import smoke_browser_runtime as runtime_smoke


def _secure_inspect(*, user: str = "65532:65532", network: str = "none") -> dict:
    return {
        "Config": {"User": user},
        "HostConfig": {
            "NetworkMode": network,
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges", "seccomp=test.json"],
            "ReadonlyPaths": [
                "/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys",
                "/proc/sysrq-trigger",
            ],
            "MaskedPaths": ["/proc/kcore", "/proc/keys", "/proc/timer_list"],
            "PidsLimit": 16,
            "Memory": 64 * 1024 * 1024,
            "NanoCpus": 250_000_000,
            "Devices": [],
        },
        "Mounts": [],
    }


def test_runtime_helper_snippets_compile() -> None:
    snippets = (
        runtime_smoke.FIXTURE_CODE,
        runtime_smoke.CAPABILITY_RESTART_PROBE_CODE,
        runtime_smoke.UNTRUSTED_PEER_CODE,
        runtime_smoke.SECCOMP_PROBE_CODE,
        runtime_smoke.CHROMIUM_PROBE_CODE,
        runtime_smoke.PROCESS_SNAPSHOT_CODE,
        runtime_smoke.CGROUP_DIAGNOSTIC_CODE,
        runtime_smoke.PID1_PROBE_CODE,
        runtime_smoke.PROCFS_LANDLOCK_PROBE_CODE,
        runtime_smoke.POST_RESTART_PROBE_CODE,
    )
    for index, source in enumerate(snippets):
        compile(source, f"<wave7-runtime-smoke-{index}>", "exec")


def test_post_restart_probe_does_not_match_its_own_python_command() -> None:
    source = runtime_smoke.POST_RESTART_PROBE_CODE
    assert "import os" in source
    assert "int(entry.name) == os.getpid()" in source


def test_chromium_probe_uses_its_own_container_userns_baseline() -> None:
    source = runtime_smoke.CHROMIUM_PROBE_CODE
    assert 'os.readlink("/proc/self/ns/user")' in source
    assert 'os.readlink("/proc/1/ns/user")' not in source
    assert 'argv0.endswith("modelmirror-chromium")' in source
    assert "AutofillServerCommunication" in source
    assert "NetworkTimeServiceQuerying" in source
    assert "PreconnectToSearch" in source
    assert "NoSearchDomainCheck" in source


def test_runtime_resources_are_isolated_and_bounded() -> None:
    base = f"{runtime_smoke.PREFIX}-1234abcd"
    assert runtime_smoke._resource(base, "cdp-browser") == (
        "mm-wave7-runtime-smoke-1234abcd-cdp-browser"
    )
    with pytest.raises(runtime_smoke.SmokeFailure, match="unsafe smoke resource"):
        runtime_smoke._resource("modelmirror", "server")
    with pytest.raises(runtime_smoke.SmokeFailure, match="unsafe smoke resource"):
        runtime_smoke._resource(base, "../shared")


def test_runtime_cleanup_only_addresses_recorded_exact_resources() -> None:
    class Runner:
        calls: list[tuple[str, ...]] = []

        def run(self, *arguments: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            self.calls.append(arguments)
            is_inspect = len(arguments) > 1 and arguments[1] == "inspect"
            return subprocess.CompletedProcess(
                arguments,
                1 if is_inspect else 0,
                "",
                "No such object" if is_inspect else "",
            )

    runner = Runner()
    ledger = runtime_smoke.ResourceLedger(
        runner,  # type: ignore[arg-type]
        containers=["mm-wave7-runtime-smoke-1234abcd-c"],
        volumes=["mm-wave7-runtime-smoke-1234abcd-v"],
        networks=["mm-wave7-runtime-smoke-1234abcd-n"],
    )
    ledger.cleanup()
    assert runner.calls == [
        ("rm", "-f", "mm-wave7-runtime-smoke-1234abcd-c"),
        ("container", "inspect", "mm-wave7-runtime-smoke-1234abcd-c"),
        ("volume", "rm", "-f", "mm-wave7-runtime-smoke-1234abcd-v"),
        ("volume", "inspect", "mm-wave7-runtime-smoke-1234abcd-v"),
        ("network", "rm", "mm-wave7-runtime-smoke-1234abcd-n"),
        ("network", "inspect", "mm-wave7-runtime-smoke-1234abcd-n"),
    ]
    assert not any(
        token in {"compose", "down", "prune", "system"}
        for call in runner.calls
        for token in call
    )


def test_timeout_after_daemon_create_is_still_exactly_cleaned() -> None:
    class Runner:
        exists = False
        calls: list[tuple[str, ...]] = []

        def run(self, *arguments: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            self.calls.append(arguments)
            if arguments[:2] == ("container", "inspect"):
                if self.exists:
                    return subprocess.CompletedProcess(arguments, 0, "[]", "")
                return subprocess.CompletedProcess(arguments, 1, "", "No such object")
            if arguments[0] == "run":
                self.exists = True
                raise subprocess.TimeoutExpired(arguments, 1)
            if arguments[:2] == ("rm", "-f"):
                self.exists = False
            return subprocess.CompletedProcess(arguments, 0, "", "")

    runner = Runner()
    ledger = runtime_smoke.ResourceLedger(runner)  # type: ignore[arg-type]
    base = f"{runtime_smoke.PREFIX}-1234abcd"
    with pytest.raises(subprocess.TimeoutExpired):
        runtime_smoke._start_fixture(
            runner,  # type: ignore[arg-type]
            ledger,
            base=base,
            image="audit-image",
            network="audit-network",
            address="198.18.1.10",
        )
    assert runner.exists is True
    assert ledger.containers == [f"{base}-fixture"]
    ledger.cleanup()
    assert runner.exists is False
    assert ("rm", "-f", f"{base}-fixture") in runner.calls


def test_runtime_security_inspection_is_fail_closed() -> None:
    payload = _secure_inspect()
    runtime_smoke._assert_common_security(
        payload, user="65532:65532", network_mode="none"
    )
    payload["HostConfig"]["CapDrop"] = []
    with pytest.raises(runtime_smoke.SmokeFailure, match="capability"):
        runtime_smoke._assert_common_security(
            payload, user="65532:65532", network_mode="none"
        )


def test_browser_procfs_inspection_requires_docker_system_path_guards() -> None:
    payload = _secure_inspect()
    runtime_smoke._assert_browser_procfs_boundary(payload)

    payload["HostConfig"]["ReadonlyPaths"].remove("/proc/sys")
    with pytest.raises(runtime_smoke.SmokeFailure, match="read-only"):
        runtime_smoke._assert_browser_procfs_boundary(payload)

    payload = _secure_inspect()
    payload["HostConfig"]["MaskedPaths"].remove("/proc/kcore")
    with pytest.raises(runtime_smoke.SmokeFailure, match="masked"):
        runtime_smoke._assert_browser_procfs_boundary(payload)

    payload = _secure_inspect()
    payload["HostConfig"]["SecurityOpt"].append("systempaths=unconfined")
    with pytest.raises(runtime_smoke.SmokeFailure, match="unconfined"):
        runtime_smoke._assert_browser_procfs_boundary(payload)


def test_landlock_procfs_probe_covers_sensitive_and_category_boundaries() -> None:
    source = runtime_smoke.PROCFS_LANDLOCK_PROBE_CODE
    for path in (
        "/proc/1/mem", "/proc/1/environ", "/proc/1/fd/0",
        "/proc/sys/kernel/core_pattern", "/proc/sysrq-trigger",
    ):
        assert path in source
    assert "os.O_WRONLY | os.O_CREAT" in source
    assert "os.O_WRONLY | os.O_TRUNC" in source
    assert 'os.open("/proc/self/comm", os.O_WRONLY)' in source
    assert "_run_landlock_procfs_probe(runner, name)" in inspect.getsource(
        runtime_smoke._start_browser
    )


def test_runtime_seccomp_contract_requires_only_reviewed_namespace_exceptions() -> None:
    profile = Path(runtime_smoke.__file__).with_name("seccomp_profile.browser.json")
    runtime_smoke._validate_seccomp_file(profile)
    probe = runtime_smoke.SECCOMP_PROBE_CODE
    for syscall in (
        "mount", "pivot_root", "open_by_handle_at", "pidfd_getfd",
        "bpf", "keyctl", "userfaultfd", "io_uring_setup",
    ):
        assert f'"{syscall}"' in probe


def test_runtime_adapter_pairs_are_fixed() -> None:
    assert runtime_smoke.ADAPTERS == (
        "chrome-devtools-mcp",
        "playwright-mcp",
    )
    assert set(runtime_smoke.ADAPTER_TOKENS) == set(runtime_smoke.ADAPTERS)
    assert runtime_smoke._selected_adapters(None) == runtime_smoke.ADAPTERS
    assert runtime_smoke._selected_adapters("chrome-devtools-mcp") == (
        "chrome-devtools-mcp",
    )
    with pytest.raises(runtime_smoke.SmokeFailure, match="unsupported fixed"):
        runtime_smoke._selected_adapters("user-controlled-adapter")
    assert "choices=ADAPTERS" in inspect.getsource(runtime_smoke.main)


def test_runtime_timeout_session_is_fixed_and_cleans_after_one_restart() -> None:
    assert runtime_smoke.TIMEOUT_FIXTURE_PATH == "/__modelmirror_timeout"
    assert runtime_smoke.TIMEOUT_FIXTURE_DELAY_SECONDS == 25
    assert adapter_smoke.BROWSER_LIMITS["navigation_timeout_seconds"] == 20
    assert (
        adapter_smoke.BROWSER_LIMITS["navigation_timeout_seconds"]
        < runtime_smoke.TIMEOUT_FIXTURE_DELAY_SECONDS
        < adapter_smoke.BROWSER_LIMITS["egress_tunnel_idle_seconds"]
    )
    assert 'os.environ["MM_WAVE7_TIMEOUT_PATH"]' in runtime_smoke.FIXTURE_CODE
    assert (
        'os.environ["MM_WAVE7_TIMEOUT_DELAY_SECONDS"]'
        in runtime_smoke.FIXTURE_CODE
    )

    helper_source = inspect.getsource(runtime_smoke._start_gateway_helper)
    assert 'helper_command.append("--expect-timeout")' in helper_source
    pair_source = inspect.getsource(runtime_smoke._run_adapter_pair)
    assert "attempt=3" in pair_source
    assert "TIMEOUT_FIXTURE_PATH" in pair_source
    assert pair_source.count("_post_restart_cleanup(runner, browser)") == 3

    timeout_source = inspect.getsource(runtime_smoke._run_gateway_timeout_attempt)
    assert 'expect_timeout=True' in timeout_source
    assert timeout_source.count("[0] !=") == 2
    assert timeout_source.count("[0] + 1") == 2
    assert "TIMEOUT_RUNTIME_EVENTS" in timeout_source


def test_gateway_timeout_outcome_requires_non_retryable_20_second_contract() -> None:
    response = {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {
            "code": -32008,
            "message": "redacted",
            "data": {"reason": "unknown_outcome", "retryable": False},
        },
    }
    adapter_smoke._assert_timeout_outcome(response, 20.0)

    for invalid in (
        {"error": {"code": -32011, "data": {"reason": "unknown_outcome", "retryable": False}}},
        {"error": {"code": -32008, "data": {"reason": "browser_upstream_unavailable", "retryable": False}}},
        {"error": {"code": -32008, "data": {"reason": "unknown_outcome", "retryable": True}}},
    ):
        with pytest.raises(RuntimeError):
            adapter_smoke._assert_timeout_outcome(invalid, 20.0)
    with pytest.raises(RuntimeError, match="elapsed window"):
        adapter_smoke._assert_timeout_outcome(response, 17.9)
    with pytest.raises(RuntimeError, match="elapsed window"):
        adapter_smoke._assert_timeout_outcome(response, 28.1)


def test_gateway_timeout_cli_has_no_user_selected_duration() -> None:
    main_source = inspect.getsource(adapter_smoke.main)
    gateway_source = inspect.getsource(adapter_smoke.gateway_smoke)
    assert 'sys.argv[5] == "--expect-timeout"' in main_source
    assert "expect_timeout=len(sys.argv) == 6" in main_source
    assert "--timeout-seconds" not in main_source
    assert "allow_error=True" in gateway_source
    assert "_assert_timeout_outcome" in gateway_source


def test_capability_rotation_probe_uses_the_control_key_owner_without_caps() -> None:
    source = inspect.getsource(runtime_smoke._probe_egress_rotation)
    assert source.count('user="65532:65532"') == 2
    assert 'user="0:0"' not in source
    assert "cap_add=" not in source


def test_runtime_failure_logs_are_bounded_and_redacted() -> None:
    secret = "a" * 64
    page_text = "private page body"
    diagnostic = runtime_smoke._safe_log_tail(
        f"control_key={secret}\nurl=https://secret.example/path\n"
        f'content: {page_text}\nblob={'b' * 256}'
    )
    assert secret not in diagnostic
    assert "secret.example" not in diagnostic
    assert page_text not in diagnostic
    assert "b" * 128 not in diagnostic
    assert len(diagnostic) <= 2048


def test_cgroup_diagnostic_accepts_only_fixed_numeric_fields() -> None:
    expected = {
        field: 1 if field == "probe_ok" else index
        for index, field in enumerate(runtime_smoke.CGROUP_DIAGNOSTIC_FIELDS)
    }

    class Runner:
        def run(self, *arguments: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            assert arguments[-1] == runtime_smoke.CGROUP_DIAGNOSTIC_CODE
            return subprocess.CompletedProcess(arguments, 0, json.dumps(expected), "")

    assert runtime_smoke._safe_cgroup_snapshot(  # type: ignore[arg-type]
        Runner(), "browser"
    ) == expected
    assert all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in expected.values()
    )


def test_cgroup_diagnostic_discards_invalid_or_sensitive_probe_output() -> None:
    secret = "d" * 64

    class Runner:
        def run(self, *arguments: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                arguments, 0, json.dumps({"probe_ok": 1, "secret": secret}), ""
            )

    snapshot = runtime_smoke._safe_cgroup_snapshot(  # type: ignore[arg-type]
        Runner(), "browser"
    )
    assert tuple(snapshot) == runtime_smoke.CGROUP_DIAGNOSTIC_FIELDS
    assert snapshot["probe_ok"] == 0
    assert all(isinstance(value, int) for value in snapshot.values())
    assert secret not in json.dumps(snapshot)


def test_cgroup_merge_preserves_events_and_peak_but_uses_final_currents() -> None:
    observed = {
        field: 1 if field == "probe_ok" else 0
        for field in runtime_smoke.CGROUP_DIAGNOSTIC_FIELDS
    }
    observed.update({
        "pids_events_max": 3,
        "pids_current": 17,
        "memory_events_oom": 2,
        "memory_current": 40_000_000,
        "memory_peak": 50_000_000,
    })
    final = dict(observed)
    final.update({
        "pids_events_max": 0,
        "pids_current": 2,
        "memory_events_oom": 0,
        "memory_current": 20_000_000,
        "memory_peak": 22_000_000,
    })

    merged = runtime_smoke._merge_cgroup_diagnostics(observed, final)
    assert tuple(merged) == runtime_smoke.CGROUP_DIAGNOSTIC_FIELDS
    assert merged["pids_events_max"] == 3
    assert merged["memory_events_oom"] == 2
    assert merged["memory_peak"] == 50_000_000
    assert merged["pids_current"] == 2
    assert merged["memory_current"] == 20_000_000


def test_chromium_probe_failure_reports_safe_state_and_process_snapshot() -> None:
    secret = "c" * 64

    class Runner:
        cgroup_calls = 0

        def run(self, *arguments: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if arguments[0] == "exec" and arguments[-1] == runtime_smoke.CHROMIUM_PROBE_CODE:
                return subprocess.CompletedProcess(arguments, 1, "", "no Chromium")
            if arguments[0] == "exec" and arguments[-1] == runtime_smoke.PROCESS_SNAPSHOT_CODE:
                return subprocess.CompletedProcess(
                    arguments, 0,
                    '[{"pid":7,"comm":"node","exe":"node","argv0":"node","flags":{}}]',
                    "",
                )
            if arguments[0] == "exec" and arguments[-1] == runtime_smoke.CGROUP_DIAGNOSTIC_CODE:
                self.cgroup_calls += 1
                cgroup = {
                    field: 1 if field == "probe_ok" else 0
                    for field in runtime_smoke.CGROUP_DIAGNOSTIC_FIELDS
                }
                if self.cgroup_calls == 1:
                    cgroup.update({
                        "pids_events_max": 3,
                        "pids_current": 11,
                        "memory_current": 33_000_000,
                        "memory_peak": 44_000_000,
                    })
                else:
                    cgroup.update({
                        "pids_events_max": 0,
                        "pids_current": 2,
                        "memory_current": 22_000_000,
                        "memory_peak": 23_000_000,
                    })
                return subprocess.CompletedProcess(arguments, 0, json.dumps(cgroup), "")
            if arguments[0] == "inspect":
                return subprocess.CompletedProcess(
                    arguments, 0,
                    '[{"State":{"Status":"exited","ExitCode":7,"OOMKilled":false,'
                    '"Restarting":false},"RestartCount":0}]',
                    "",
                )
            if arguments[0] == "logs":
                return subprocess.CompletedProcess(
                    arguments, 0,
                    f"RuntimeError: failed control_key={secret} content: private body",
                    "",
                )
            raise AssertionError(arguments)

    with pytest.raises(runtime_smoke.SmokeFailure) as captured:
        runtime_smoke._wait_chromium_probe(
            Runner(),  # type: ignore[arg-type]
            "browser", "helper",
            cgroup_before=runtime_smoke._empty_cgroup_snapshot(),
            timeout=1,
        )
    message = str(captured.value)
    assert '"ExitCode":7' in message
    assert '"comm":"node"' in message
    assert 'cgroup_observed={"probe_ok":1,"pids_events_max":3,"pids_current":2' in message
    assert '"memory_current":22000000,"memory_peak":44000000}' in message
    assert 'cgroup_final={"probe_ok":1,"pids_events_max":0,"pids_current":2' in message
    assert '"memory_current":22000000,"memory_peak":23000000}' in message
    assert '"OOMKilled":false' in message
    assert secret not in message
    assert "private body" not in message


def test_every_runtime_creator_reserves_before_cli_and_calibration_is_not_auto_removed() -> None:
    creators = (
        runtime_smoke._create_network,
        runtime_smoke._create_volume,
        runtime_smoke._start_fixture,
        runtime_smoke._run_seccomp_calibration,
        runtime_smoke._start_egress,
        runtime_smoke._probe_egress_rotation,
        runtime_smoke._start_browser,
        runtime_smoke._run_untrusted_peer_probe,
        runtime_smoke._start_gateway_helper,
    )
    for creator in creators:
        source = inspect.getsource(creator)
        assert "_reserve_resource(" in source, creator.__name__
        assert source.index("_reserve_resource(") < source.index("runner.run("), creator.__name__
    assert '"--rm"' not in inspect.getsource(runtime_smoke._run_seccomp_calibration)


def test_browser_runtime_uses_the_reviewed_process_budget() -> None:
    source = inspect.getsource(runtime_smoke._start_browser)

    assert 'pids=256, memory="1g", cpus="1.5"' in source
    assert "pids=128" not in source
