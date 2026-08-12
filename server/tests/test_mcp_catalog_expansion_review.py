from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = ROOT / "docs" / "mcp-catalog-expansion" / "review-candidates.json"
ALLOWED_SOURCES = {"awesome-mcp-zh", "awesome-mcp-servers"}


def test_committed_approved_list_is_balanced_and_classified() -> None:
    payload = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    candidates = payload["candidates"]
    assert payload["purpose"] == "adaptation-classification"
    assert payload["runtime_catalog_changed"] is True
    assert payload["runtime_execution_changed"] is True
    assert payload["adaptation"] == {
        "classified_at": "2026-08-09",
        "classified_count": 100,
        "availability": {"ready": 26, "planned": 13, "blocked": 61},
        "ready_boundary": "fixed-reviewed-read-artifact-or-index-sidecar-contract",
        "non_ready_boundary": "no-command-endpoint-credential-or-tool-policy",
    }
    assert len(candidates) == 100
    assert [item["rank"] for item in candidates] == list(range(1, 101))
    assert len({item["canonical_key"] for item in candidates}) == 100
    assert len({item["catalog_id"] for item in candidates}) == 100
    assert all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item["catalog_id"]) for item in candidates)
    assert len({item["category"] for item in candidates}) >= 10
    assert max(Counter(item["category"] for item in candidates).values()) <= 15
    assert max(Counter(item["repo_name"].lower() for item in candidates).values()) <= 2
    for source_id in ALLOWED_SOURCES:
        assert (
            sum(
                any(source["source_id"] == source_id for source in item["sources"])
                for item in candidates
            )
            >= 25
        )
    assert Counter(item["proposed_availability"] for item in candidates) == {
        "ready": 26,
        "planned": 13,
        "blocked": 61,
    }
    ready = [item for item in candidates if item["proposed_availability"] == "ready"]
    assert [item["catalog_id"] for item in ready] == [
        "fatwang2-search1api-mcp",
        "qdrant-mcp-server-qdrant",
        "blazickjp-arxiv-mcp-server",
        "zcaceres-markdownify-mcp",
        "aas-ee-open-websearch",
        "genomoncology-biomcp",
        "nickclyde-duckduckgo-mcp-server",
        "mnemox-ai-idea-reality-mcp",
        "kagisearch-kagimcp",
        "cyberchitta-llm-context-py",
        "haris-musa-excel-mcp-server",
        "idosal-git-mcp",
        "vivekvells-mcp-pandoc",
        "zilliztech-mcp-server-milvus",
        "brave-brave-search-mcp-server",
        "docker-hub-mcp",
        "livetennisapi-livetennisapi-mcp",
        "neo4j-contrib-mcp-neo4j",
        "pab1it0-prometheus-mcp-server",
        "safedep-vet",
        "arcadedata-arcadedb",
        "cr7258-elasticsearch-mcp-server",
        "jpisnice-shadcn-ui-mcp-server",
        "antvis-mcp-server-chart",
        "dataeval-dingo",
        "ozgurcd-gograph",
    ]
    ready_by_id = {item["catalog_id"]: item for item in ready}
    assert ready_by_id["brave-brave-search-mcp-server"]["adapter_version"] == "2.1.0"
    assert ready_by_id["brave-brave-search-mcp-server"]["adaptation_wave"] == 13
    assert ready_by_id["kagisearch-kagimcp"]["adapter_version"] == (
        "1.0.2-compatible-native-v1"
    )
    assert ready_by_id["kagisearch-kagimcp"]["adaptation_wave"] == 14
    assert ready_by_id["blazickjp-arxiv-mcp-server"]["adapter_version"] == (
        "0.6.2-compatible-native-v1"
    )
    assert ready_by_id["blazickjp-arxiv-mcp-server"]["adaptation_wave"] == 14
    assert ready_by_id["fatwang2-search1api-mcp"]["adapter_version"] == (
        "0.5.3-compatible-native-v1"
    )
    assert ready_by_id["fatwang2-search1api-mcp"]["adaptation_wave"] == 15
    assert ready_by_id["livetennisapi-livetennisapi-mcp"]["adapter_version"] == (
        "1.4.0-compatible-native-v1"
    )
    assert ready_by_id["livetennisapi-livetennisapi-mcp"]["adaptation_wave"] == 15
    assert ready_by_id["nickclyde-duckduckgo-mcp-server"]["adapter_version"] == (
        "0.6.1-compatible-native-v1"
    )
    assert ready_by_id["nickclyde-duckduckgo-mcp-server"]["adaptation_wave"] == 16
    assert ready_by_id["docker-hub-mcp"]["adapter_version"] == (
        "0.18.0-compatible-native-v1"
    )
    assert ready_by_id["docker-hub-mcp"]["adaptation_wave"] == 16
    assert ready_by_id["jpisnice-shadcn-ui-mcp-server"]["adapter_version"] == (
        "2.0.0-compatible-native-v1"
    )
    assert ready_by_id["jpisnice-shadcn-ui-mcp-server"]["adaptation_wave"] == 16
    assert ready_by_id["genomoncology-biomcp"]["adapter_version"] == (
        "0.8.25-compatible-native-v1"
    )
    assert ready_by_id["genomoncology-biomcp"]["adaptation_wave"] == 16
    assert ready_by_id["safedep-vet"]["adapter_version"] == (
        "1.18.1-compatible-native-v1"
    )
    assert ready_by_id["safedep-vet"]["adaptation_wave"] == 16
    assert ready_by_id["aas-ee-open-websearch"]["adapter_version"] == (
        "2.1.9-compatible-native-v1"
    )
    assert ready_by_id["aas-ee-open-websearch"]["adaptation_wave"] == 17
    assert ready_by_id["mnemox-ai-idea-reality-mcp"]["adapter_version"] == (
        "0.5.0-compatible-native-v1"
    )
    assert ready_by_id["mnemox-ai-idea-reality-mcp"]["adaptation_wave"] == 17
    assert ready_by_id["idosal-git-mcp"]["adapter_version"] == (
        "c487a298-compatible-native-v1"
    )
    assert ready_by_id["idosal-git-mcp"]["adaptation_wave"] == 17
    assert ready_by_id["zilliztech-mcp-server-milvus"]["adapter_version"] == (
        "0.1.1-compatible-native-read-only-v1"
    )
    assert ready_by_id["zilliztech-mcp-server-milvus"]["adaptation_wave"] == 23
    assert ready_by_id["neo4j-contrib-mcp-neo4j"]["adapter_version"] == (
        "mcp-neo4j-cypher-v0.6.0-compatible-native-read-only-v1"
    )
    assert ready_by_id["neo4j-contrib-mcp-neo4j"]["adaptation_wave"] == 23
    assert ready_by_id["arcadedata-arcadedb"]["adapter_version"] == (
        "26.8.1-compatible-native-read-only-v1"
    )
    assert ready_by_id["arcadedata-arcadedb"]["adaptation_wave"] == 23
    staged = {
        item["catalog_id"]: item
        for item in candidates
        if item["decision_reason_code"]
        == "planned-real-account-readonly-preflight-required"
    }
    assert set(staged) == {
        "cablate-mcp-google-map",
        "comet-ml-opik-mcp",
        "keboola-keboola-mcp-server",
    }
    assert all(item["proposed_availability"] == "planned" for item in staged.values())
    assert all(item["adaptation_wave"] == 17 for item in staged.values())
    assert staged["cablate-mcp-google-map"]["adapter_version"] == (
        "0.0.53-compatible-native-v1"
    )
    assert staged["comet-ml-opik-mcp"]["adapter_version"] == (
        "0.2.15-compatible-native-v1"
    )
    assert staged["keboola-keboola-mcp-server"]["adapter_version"] == (
        "1.75.2-compatible-native-v1"
    )
    vectorize = next(
        item
        for item in candidates
        if item["catalog_id"] == "vectorize-io-vectorize-mcp-server"
    )
    assert vectorize["proposed_availability"] == "blocked"
    assert vectorize["decision_reason_code"] == "blocked-license-metadata-conflict"
    assert vectorize["adapter_version"] == "blocked"
    deferred_by_wave = {
        wave: {
            item["catalog_id"]
            for item in candidates
            if item["adaptation_wave"] == wave
        }
        for wave in (21, 22)
    }
    assert deferred_by_wave[21] == {
        "chopratejas-headroom",
        "samvallad33-vestige",
        "goldentrii-agentrecall",
        "juyterman1000-entroly",
        "patdolitse-piia-engram",
        "beever-ai-beever-atlas",
        "pv-bhat-vibe-check-mcp-server",
    }
    assert deferred_by_wave[22] == {
        "r-huijts-strava-mcp",
        "tiberriver256-mcp-server-azure-devops",
        "tacticlaunch-mcp-linear",
    }
    assert all(
        item["proposed_availability"] == "planned"
        and item["adapter_version"] == ""
        for item in candidates
        if item["adaptation_wave"] in {21, 22}
    )
    accepted_data_services = {
        item["catalog_id"]: item
        for item in candidates
        if item["decision_reason_code"]
        == "ready-isolated-readonly-data-service-facade"
    }
    assert set(accepted_data_services) == {
        "pab1it0-prometheus-mcp-server",
        "qdrant-mcp-server-qdrant",
        "cr7258-elasticsearch-mcp-server",
    }
    assert all(
        item["proposed_availability"] == "ready"
        for item in accepted_data_services.values()
    )
    assert all(
        item["adaptation_wave"] == 19
        for item in accepted_data_services.values()
    )
    assert accepted_data_services["pab1it0-prometheus-mcp-server"]["adapter_version"] == (
        "1.6.2-compatible-native-read-only-v1"
    )
    assert accepted_data_services["qdrant-mcp-server-qdrant"]["adapter_version"] == (
        "0.8.1-compatible-native-read-only-v1"
    )
    assert accepted_data_services["cr7258-elasticsearch-mcp-server"]["adapter_version"] == (
        "2.1.2-compatible-native-read-only-v1"
    )
    accepted_files = {
        item["catalog_id"]: item
        for item in candidates
        if item["decision_reason_code"]
        == "ready-isolated-deterministic-file-artifact-facade"
    }
    assert set(accepted_files) == {
        "zcaceres-markdownify-mcp",
        "vivekvells-mcp-pandoc",
        "antvis-mcp-server-chart",
    }
    assert all(
        item["proposed_availability"] == "ready"
        for item in accepted_files.values()
    )
    assert all(item["adaptation_wave"] == 18 for item in accepted_files.values())
    assert accepted_files["zcaceres-markdownify-mcp"]["adapter_version"] == (
        "1.1.0-compatible-native-v1"
    )
    assert accepted_files["vivekvells-mcp-pandoc"]["adapter_version"] == (
        "0.11.0-compatible-native-v1"
    )
    assert accepted_files["antvis-mcp-server-chart"]["adapter_version"] == (
        "0.9.10-compatible-native-v1"
    )
    accepted_analysis = {
        item["catalog_id"]: item
        for item in candidates
        if item["decision_reason_code"]
        == "ready-isolated-file-analysis-facade"
    }
    assert set(accepted_analysis) == {
        "cyberchitta-llm-context-py",
        "haris-musa-excel-mcp-server",
        "dataeval-dingo",
    }
    assert all(
        item["proposed_availability"] == "ready"
        for item in accepted_analysis.values()
    )
    assert all(item["adaptation_wave"] == 18 for item in accepted_analysis.values())
    assert accepted_analysis["cyberchitta-llm-context-py"]["adapter_version"] == (
        "0.6.4-reviewed-commit-6de16c22-compatible-native-v1"
    )
    assert accepted_analysis["haris-musa-excel-mcp-server"]["adapter_version"] == (
        "0.1.8-compatible-native-v1"
    )
    assert accepted_analysis["dataeval-dingo"]["adapter_version"] == (
        "2.5.0-rule-compatible-native-v1"
    )
    for item in candidates:
        assert item["decision"] in {"adapted-ready", "deferred-planned", "blocked"}
        assert item["decision_reason_code"].startswith(
            ("ready-", "planned-", "blocked-")
        )
        assert item["decision_reason"]
        assert item["adaptation_wave"] in {13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
        assert item["github"]["licenseInfo"]["spdxId"] not in {
            "",
            "NOASSERTION",
            "OTHER",
            "NONE",
        }
        assert {source["source_id"] for source in item["sources"]} <= ALLOWED_SOURCES
        serialized = json.dumps(item, ensure_ascii=False).lower()
        assert "installcommand" not in serialized
        assert "server_command" not in serialized
        assert "credential" not in serialized
        assert "executable" not in item


def test_approved_catalog_generated_outputs_are_current() -> None:
    from scripts.mcp_catalog_integrate_approved import (
        BACKEND_PATH,
        FRONTEND_PATH,
        REPORT_PATH,
        build_approved_payload,
        render_backend,
        render_frontend,
        render_report,
    )

    payload = build_approved_payload(json.loads(REVIEW_PATH.read_text(encoding="utf-8")))
    assert FRONTEND_PATH.read_text(encoding="utf-8") == render_frontend(payload)
    assert BACKEND_PATH.read_text(encoding="utf-8") == render_backend(payload)
    assert REPORT_PATH.read_text(encoding="utf-8") == render_report(payload)
