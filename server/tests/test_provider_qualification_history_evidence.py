"""Offline evidence integrity is necessary, never sufficient for inheritance."""
import hashlib
import json

import pytest

from server.model_router.qualification_history_evidence import read_reviewed_evidence
from server.model_router.qualification_history_proof import HistoricalProofError


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture(tmp_path):
    snapshot = tmp_path / "synthetic.snapshot"
    snapshot.write_bytes(b"synthetic: actual SQLite verification is the next layer")
    deployment = tmp_path / "deployment.txt"
    deployment.write_bytes(b"SYNTHETIC_TTL=86400\nSYNTHETIC_KEY=never-serialize-this")
    artifact = digest(deployment.read_bytes())
    document = {
        "contract_version": "modelmirror-qualification-history-proof-v1",
        "source_snapshot_sha256": digest(snapshot.read_bytes()),
        "source_epoch_id": "old-epoch", "target_epoch_id": "new-epoch",
        "observed_until": "2026-08-02T00:00:00+00:00",
        "lifetimes": [{"certification_id": "cert-1", "ttl_seconds": 86400,
            "deployment_artifact_sha256": artifact,
            "effective_from": "2026-08-01T00:00:00+00:00",
            "effective_until": "2026-08-02T00:00:00+00:00"}],
    }
    return document, {"source_snapshot": snapshot, "deployment_artifacts": {artifact: deployment},
                      "acknowledge_provenance_review": True}


def read(tmp_path, document, args):
    path = tmp_path / "manifest.json"
    payload = json.dumps(document).encode()
    path.write_bytes(payload)
    return read_reviewed_evidence(path, reviewed_manifest_sha256=digest(payload), **args)


def test_reviewed_metadata_only_and_artifacts_unchanged(tmp_path):
    document, args = fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    proof = read(tmp_path, document, args)
    assert proof.lifetimes[0].ttl_seconds == 86400
    assert "never-serialize-this" not in repr(proof)
    assert all(p.read_bytes() == contents for p, contents in before.items())


def test_v2_requires_explicit_complete_configuration_history_review(tmp_path):
    document, args = fixture(tmp_path)
    document["contract_version"] = "modelmirror-qualification-history-proof-v2"
    document["configuration_continuity"] = {
        "configuration_fingerprint": "a" * 64,
        "artifact_sha256": next(iter(args["deployment_artifacts"])),
        "effective_from": "2026-08-01T00:00:00+00:00",
        "effective_until": document["observed_until"],
        "complete_history_reviewed": True,
    }
    proof = read(tmp_path, document, args)
    assert proof.continuity.configuration_fingerprint == "a" * 64
    document["configuration_continuity"]["complete_history_reviewed"] = False
    with pytest.raises(HistoricalProofError, match="complete_history_review_required"):
        read(tmp_path, document, args)


def test_hash_is_not_a_substitute_for_provenance_review(tmp_path):
    document, args = fixture(tmp_path)
    args["acknowledge_provenance_review"] = False
    with pytest.raises(HistoricalProofError, match="provenance_review_required"):
        read(tmp_path, document, args)


@pytest.mark.parametrize("field,value", [("prompt", "body"), ("tenant_id", "other"),
                                         ("api_key", "secret"), ("snapshot_path", "/etc/passwd")])
def test_extra_fields_and_document_selected_paths_are_rejected(tmp_path, field, value):
    document, args = fixture(tmp_path)
    document[field] = value
    with pytest.raises(HistoricalProofError, match="manifest_schema_invalid"):
        read(tmp_path, document, args)


def test_wrong_manifest_snapshot_and_deployment_digests_rejected(tmp_path):
    document, args = fixture(tmp_path)
    read(tmp_path, document, args)
    with pytest.raises(HistoricalProofError, match="manifest_changed"):
        read_reviewed_evidence(tmp_path / "manifest.json", reviewed_manifest_sha256="0" * 64, **args)
    args["source_snapshot"].write_bytes(b"changed")
    with pytest.raises(HistoricalProofError, match="snapshot_changed"):
        read(tmp_path, document, args)
    document["source_snapshot_sha256"] = digest(b"changed")
    next(iter(args["deployment_artifacts"].values())).write_bytes(b"changed secret")
    with pytest.raises(HistoricalProofError, match="deployment_artifact_changed") as error:
        read(tmp_path, document, args)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("change,reason", [
    (lambda d: d["lifetimes"].append(dict(d["lifetimes"][0])), "duplicate_event"),
    (lambda d: d["lifetimes"][0].update(ttl_seconds=True), "ttl_unproven"),
    (lambda d: d["lifetimes"][0].update(effective_until="2026-08-03T00:00:00+00:00"), "deployment_window_invalid"),
    (lambda d: d.update(target_epoch_id="old-epoch"), "epoch_cycle"),
    (lambda d: d.update(lifetimes=[]), "lifetimes_invalid"),
])
def test_contradictory_or_unbounded_proof_rejected(tmp_path, change, reason):
    document, args = fixture(tmp_path)
    change(document)
    with pytest.raises(HistoricalProofError, match=reason):
        read(tmp_path, document, args)


def test_missing_deployment_source_not_accepted(tmp_path):
    document, args = fixture(tmp_path)
    args["deployment_artifacts"] = {}
    with pytest.raises(HistoricalProofError, match="deployment_artifacts_missing"):
        read(tmp_path, document, args)


def test_duplicate_json_keys_are_not_silently_overwritten(tmp_path):
    _, args = fixture(tmp_path)
    path = tmp_path / "duplicate.json"
    payload = b'{"contract_version":"first","contract_version":"second"}'
    path.write_bytes(payload)
    with pytest.raises(HistoricalProofError, match="duplicate_json_key"):
        read_reviewed_evidence(path, reviewed_manifest_sha256=digest(payload), **args)
