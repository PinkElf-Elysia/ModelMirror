from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.mcp.sandbox_proxy import ALLOWED_ADAPTERS
from server.sandbox_sidecar.compute_mcp import (
    ADAPTER_TOOL_NAMES,
    BUILDERS,
    MAX_DATA_ROWS,
    VegaLiteSession,
    calculate,
    convert_time_payload,
    current_time_payload,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_calculator_contract_and_guardrails() -> None:
    assert set(ADAPTER_TOOL_NAMES["calculator-mcp"]) == {
        "add",
        "sub",
        "mul",
        "div",
        "mod",
        "sqrt",
    }
    assert calculate("add", 2, 3)["result"] == 5
    assert calculate("sqrt", 81)["result"] == 9

    with pytest.raises(ValueError, match="除数不能为零"):
        calculate("div", 1, 0)
    with pytest.raises(ValueError, match="数值必须有限"):
        calculate("mul", 1e100, 10)


def test_time_contract_uses_iana_zones_and_validates_clock_input() -> None:
    assert set(ADAPTER_TOOL_NAMES["time-mcp"]) == {
        "get_current_time",
        "convert_time",
    }
    current = current_time_payload("Asia/Shanghai")
    assert current["timezone"] == "Asia/Shanghai"
    converted = convert_time_payload("Asia/Shanghai", "09:30", "UTC")
    assert converted["source"]["timezone"] == "Asia/Shanghai"
    assert converted["target"]["timezone"] == "UTC"

    with pytest.raises(ValueError, match="HH:MM"):
        convert_time_payload("UTC", "25:00", "Asia/Shanghai")


def test_vegalite_contract_is_ephemeral_bounded_and_rejects_urls() -> None:
    assert set(ADAPTER_TOOL_NAMES["vegalite-mcp"]) == {
        "save_data",
        "visualize_data",
    }
    session = VegaLiteSession()

    saved = session.save_data(
        "sales",
        [{"month": "一月", "amount": 12.5}],
    )
    assert saved == {
        "name": "sales",
        "rows": 1,
        "bytes": saved["bytes"],
        "storage": "ephemeral-memory",
    }
    rendered = session.visualize_data(
        "sales",
        json.dumps(
            {
                "mark": "bar",
                "encoding": {
                    "x": {"field": "month", "type": "nominal"},
                    "y": {"field": "amount", "type": "quantitative"},
                },
            }
        ),
    )
    assert rendered["artifact"]["data"]["values"] == [
        {"month": "一月", "amount": 12.5}
    ]

    with pytest.raises(ValueError, match="远程 URL"):
        session.visualize_data(
            "sales",
            json.dumps({"data": {"url": "https://example.invalid/data.json"}}),
        )
    with pytest.raises(ValueError, match="最多包含"):
        session.save_data(
            "too_many_rows",
            [{"value": index} for index in range(MAX_DATA_ROWS + 1)],
        )


def test_wave_one_proxy_and_runtime_are_fixed_and_isolated() -> None:
    assert set(BUILDERS) == ALLOWED_ADAPTERS == {
        "calculator-mcp",
        "time-mcp",
        "vegalite-mcp",
    }

    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (
        PROJECT_ROOT / "server" / "sandbox_sidecar" / "Dockerfile"
    ).read_text(encoding="utf-8")
    sidecar_server = (
        PROJECT_ROOT / "server" / "sandbox_sidecar" / "server.py"
    ).read_text(encoding="utf-8")
    landlock = (
        PROJECT_ROOT / "server" / "sandbox_sidecar" / "landlock_exec.py"
    ).read_text(encoding="utf-8")

    assert "network_mode: none" in compose
    assert "read_only: true" in compose
    assert "pids_limit: 128" in compose
    assert "mem_limit: 512m" in compose
    assert "cpus: 1.0" in compose
    assert "USER 65532:65532" in dockerfile
    assert "preexec_fn" not in sidecar_server
    assert '"--read-only"' in sidecar_server
    assert '"--compute-limits"' in sidecar_server
    assert "resource.RLIMIT_CPU" in landlock
    assert "resource.RLIMIT_AS" in landlock
    assert "resource.RLIMIT_FSIZE" in landlock
    assert "resource.RLIMIT_NPROC" not in landlock
    assert 'workspace_writable="--read-only" not in options' in landlock
