from datetime import UTC, datetime, timedelta
import sqlite3

import pytest

from server.model_router.repository import RouterRepositoryError, SQLiteRouterRepository
from server.model_router.qualification_events import (
    begin_qualification_event, complete_qualification_event, invalidate_connection_intervals,
    invalidate_certification_intervals,
    read_qualification_state,
    admission_certification,
)
from server.model_router.qualifications import qualification_time_status, qualification_refresh_time_status
from server.model_router.schemas import RouterConnectionCreate


START = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"t" * 32):
        pass
    connection = sqlite3.connect(tmp_path / "router.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    yield connection
    connection.close()


def certify(db, identifier, *, at=START, status="passed", ttl=None, tenant="local", blocked=False, connection_id="synthetic"):
    db.execute("""INSERT INTO provider_chat_certifications
        (id, tenant_id, connection_id, connection_fingerprint, contract_version,
         requested_model, idempotency_key_hash, status, created_at, updated_at)
        VALUES (?, ?, ?, 'fp', 'v1', 'model', ?, 'running', ?, ?)""",
        (identifier, tenant, connection_id, identifier, at.isoformat(), at.isoformat()),
    )
    begin_qualification_event(db, tenant_id=tenant, source="provider_chat", certification_id=identifier, configured_ttl=ttl)
    db.execute("UPDATE provider_chat_certifications SET status = ?, completed_at = ? WHERE tenant_id = ? AND id = ?",
               (status, at.isoformat(), tenant, identifier))
    return complete_qualification_event(db, tenant_id=tenant, source="provider_chat", certification_id=identifier, continuity_blocked=blocked)


def test_successful_renewal_preserves_interval_but_not_single_event(db):
    first = certify(db, "one")
    second = certify(db, "two", at=START + timedelta(days=29))
    assert second["interval_id"] == first["interval_id"]
    assert second["expires_at"] == (START + timedelta(days=59)).isoformat()
    stored = db.execute("SELECT expires_at FROM provider_qualification_events WHERE certification_id='one'").fetchone()[0]
    assert stored == first["expires_at"]


@pytest.mark.parametrize("status", ["failed", "uncertain"])
def test_failed_attempt_cannot_be_filtered_out_of_renewal_history(db, status):
    first = certify(db, "one")
    failed = certify(db, "two", at=START + timedelta(hours=1), status=status)
    third = certify(db, "three", at=START + timedelta(hours=2))
    assert failed["interval_id"] is None
    assert third["interval_id"] != first["interval_id"]
    assert third["renewal_reason"] == "provider_qualification_continuity_blocked"


def test_credential_or_config_restore_does_not_resurrect_old_interval(db):
    first = certify(db, "one")
    invalidate_connection_intervals(db, tenant_id="local", connection_id="synthetic",
        observed_at=(START + timedelta(hours=1)).isoformat(), reason_code="provider_qualification_connection_changed")
    second = certify(db, "two", at=START + timedelta(hours=2))
    assert second["series_id"] == first["series_id"]
    assert second["interval_id"] != first["interval_id"]


def test_tenant_isolation_including_identical_certification_id(db):
    first = certify(db, "one")
    other = certify(db, "one", tenant="other")
    assert other["series_id"] != first["series_id"]
    invalidate_connection_intervals(db, tenant_id="local", connection_id="synthetic",
        observed_at=START.isoformat(), reason_code="provider_qualification_hard_failure")
    assert db.execute("SELECT closed_at FROM provider_qualification_intervals WHERE tenant_id='other'").fetchone()[0] is None


def test_event_and_certification_rollback_together(db):
    certify(db, "one")
    db.rollback()
    for table in ("provider_chat_certifications", "provider_qualification_events", "provider_qualification_intervals", "provider_qualification_series"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_missing_historical_ttl_is_not_invented(db):
    db.execute("BEGIN")
    assert complete_qualification_event(db, tenant_id="local", source="provider_chat", certification_id="old", continuity_blocked=False) is None


def test_idempotent_event_read_keeps_original_ttl_and_expiry(db):
    first = certify(db, "one", ttl="300")
    reread = begin_qualification_event(db, tenant_id="local", source="provider_chat", certification_id="one", configured_ttl="2592000")
    assert reread == first
    assert complete_qualification_event(db, tenant_id="local", source="provider_chat", certification_id="one", continuity_blocked=False) == first


def test_transaction_is_mandatory(db):
    with pytest.raises(ValueError, match="provider_qualification_transaction_required"):
        begin_qualification_event(db, tenant_id="local", source="provider_chat", certification_id="one", configured_ttl=None)


def test_late_readonly_resolution_preserves_uncertain_fact_and_does_not_backdate(db):
    uncertain = certify(db, "one", status="uncertain")
    verified = START + timedelta(days=3)
    db.execute("UPDATE provider_chat_certifications SET status='passed', updated_at=? WHERE id='one'", (verified.isoformat(),))
    resolved = complete_qualification_event(db, tenant_id="local", source="provider_chat", certification_id="one", continuity_blocked=False)
    facts = db.execute("SELECT status, observed_at FROM provider_qualification_observations ORDER BY sequence").fetchall()
    assert [tuple(row) for row in facts] == [("uncertain", START.isoformat()), ("passed", verified.isoformat())]
    assert uncertain["interval_id"] is None
    assert resolved["completed_at"] == verified.isoformat()
    assert resolved["expires_at"] == (verified + timedelta(days=30)).isoformat()
    interval = db.execute("SELECT valid_from FROM provider_qualification_intervals").fetchone()
    assert interval[0] == verified.isoformat()
    assert db.execute("SELECT completed_at FROM provider_chat_certifications").fetchone()[0] == START.isoformat()


def test_old_inflight_call_hard_failure_breaks_renewed_interval(db):
    first = certify(db, "one")
    renewed = certify(db, "two", at=START + timedelta(days=1))
    assert renewed["interval_id"] == first["interval_id"]
    invalidate_certification_intervals(db, tenant_id="local", source="provider_chat",
        certification_id="one", observed_at=(START + timedelta(days=2)).isoformat())
    recertified = certify(db, "three", at=START + timedelta(days=3))
    assert recertified["interval_id"] != first["interval_id"]
    assert db.execute("SELECT closure_reason FROM provider_qualification_intervals WHERE id=?",
        (first["interval_id"],)).fetchone()[0] == "provider_qualification_hard_failure"


def test_validity_read_uses_frozen_expiry_and_hard_failure(db, monkeypatch):
    certify(db, "one", ttl="300")
    state = read_qualification_state(db, "local", "provider_chat", "one")
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS", "2592000")
    assert qualification_time_status(state, now=START + timedelta(seconds=299))[0] is None
    assert qualification_time_status(state, now=START + timedelta(seconds=300))[0] == "provider_chat_certification_expired"
    invalidate_certification_intervals(db, tenant_id="local", source="provider_chat", certification_id="one", observed_at=START.isoformat())
    state = read_qualification_state(db, "local", "provider_chat", "one")
    assert qualification_time_status(state, now=START)[0] == "provider_chat_certification_invalidated"
    assert qualification_time_status(None, now=START)[0] == "provider_chat_certification_expiry_unknown"


def test_uncertain_refresh_eligibility_is_not_a_valid_qualification(db):
    certify(db, "one", status="uncertain", ttl="300")
    state = read_qualification_state(db, "local", "provider_chat", "one")
    assert qualification_refresh_time_status(state, now=START)[0] is None
    assert qualification_time_status(state, now=START)[0] == "provider_chat_certification_not_passed"
    assert qualification_refresh_time_status(state, now=START + timedelta(seconds=300))[0] == "provider_chat_certification_expired"


def test_repeated_uncertain_get_does_not_extend_resolution_deadline(db):
    certify(db, "one", status="uncertain", ttl="300")
    observed = START + timedelta(seconds=290)
    db.execute("UPDATE provider_chat_certifications SET updated_at=? WHERE id='one'", (observed.isoformat(),))
    complete_qualification_event(db, tenant_id="local", source="provider_chat", certification_id="one", continuity_blocked=False)
    state = read_qualification_state(db, "local", "provider_chat", "one")
    assert state["completed_at"] == observed.isoformat()
    assert state["first_uncertain_at"] == START.isoformat()
    assert qualification_refresh_time_status(state, now=START + timedelta(seconds=299))[0] is None
    assert qualification_refresh_time_status(state, now=START + timedelta(seconds=300))[0] == "provider_chat_certification_expired"
    assert db.execute("SELECT COUNT(*) FROM provider_qualification_observations").fetchone()[0] == 2


def test_uncertain_without_immutable_start_cannot_gain_fresh_window(db):
    assert qualification_refresh_time_status({"status": "uncertain", "completed_at": START.isoformat(), "ttl_seconds": 300}, now=START)[0] == "provider_chat_certification_time_invalid"


def test_admission_rejects_cross_tenant_even_for_already_passed_row(db):
    certify(db, "one", tenant="other")
    row = db.execute("SELECT * FROM provider_chat_certifications WHERE tenant_id='other'").fetchone()
    with pytest.raises(ValueError, match="provider_qualification_tenant_mismatch"):
        admission_certification(db, "local", "provider_chat", row)
    assert admission_certification(db, "other", "provider_chat", row)["id"] == "one"


@pytest.mark.parametrize("result_class,dispatched,closed", [
    ("hard_failure", True, True), ("transient_failure", True, False),
    ("hard_failure", False, False), ("success", True, False),
])
def test_canary_terminal_write_closes_only_dispatched_hard_failure(tmp_path, result_class, dispatched, closed):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"t" * 32) as repository:
        repository.claim_chat_certification("local", certification_id="cert",
            connection_id="synthetic", connection_fingerprint="fp", contract_version="v1",
            requested_model="model", idempotency_key_hash="key")
        repository.complete_chat_certification("local", "cert", status="passed", checks={}, warning_codes=[])
        repository.claim_chat_canary_run("local", run_id="run", connection_id="synthetic",
            connection_fingerprint="fp", certification_id="cert", contract_version="v1",
            requested_model="model", session_id_hash="hash", baseline_overlap=False)
        if dispatched:
            repository.mark_chat_canary_dispatched("local", "run")
        repository.complete_chat_canary_run("local", "run", status="failed" if result_class != "success" else "succeeded",
            result_class=result_class, checks={}, warning_codes=[])
        with sqlite3.connect(repository.database_path) as db:
            interval = db.execute("SELECT closed_at FROM provider_qualification_intervals").fetchone()
            assert (interval[0] is not None) is closed
            assert db.execute("SELECT COUNT(*) FROM provider_chat_canary_runs WHERE dispatched=1").fetchone()[0] == int(dispatched)


@pytest.mark.parametrize("shape", ["chat_text", "chat_json_object", "embedding_vectors", "rerank_documents", "chat_audio_input"])
@pytest.mark.parametrize("result_class,error_code,dispatched,closed", [
    ("hard_failure", None, True, True),
    ("provider_error", "provider_workload_http_401", True, True),
    ("provider_error", "provider_workload_actual_model_mismatch", True, True),
    ("provider_error", "provider_workload_empty_stream", True, True),
    ("provider_error", "provider_embedding_http_401", True, True),
    ("provider_error", "provider_rerank_http_403", True, True),
    ("provider_error", "provider_embedding_model_mismatch", True, True),
    ("provider_error", "provider_rerank_model_mismatch", True, True),
    ("provider_error", "provider_multimodal_invalid_sse", True, True),
    ("provider_error", "provider_embedding_http_429", True, False),
    ("provider_error", "provider_rerank_http_422", True, False),
    ("provider_error", "provider_workload_http_429", True, False),
    ("provider_error", "provider_workload_http_5xx", True, False),
    ("transport_error", "provider_workload_timeout", True, False),
    ("hard_failure", None, False, False),
])
def test_workload_terminal_closes_exact_qualification_in_same_transaction(
    tmp_path, shape, result_class, error_code, dispatched, closed,
):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"t" * 32) as repository:
        conn = repository.create_connection("local", RouterConnectionCreate(
            name="synthetic", kind="openrouter", base_url="https://example.com/v1",
            api_key="synthetic-test-key",
        ))
        for tenant in ("local", "other"):
            # Independent synthetic source rows allow identical IDs across tenants.
            with sqlite3.connect(repository.database_path) as db:
                db.row_factory = sqlite3.Row
                if shape == "chat_text":
                    certify(db, "cert", tenant=tenant, connection_id=conn.id)
                    source = "provider_chat"
                else:
                    db.execute("""INSERT INTO provider_workload_certifications
                        (tenant_id,id,connection_id,connection_fingerprint,contract_version,
                         execution_shape,requested_model,profile_json,profile_fingerprint,
                         idempotency_key_hash,status,created_at,updated_at)
                        VALUES (?, 'cert', ?, 'fp','v1', ?, 'model', '{}', 'profile',
                                'key', 'running', ?, ?)""",
                        (tenant, conn.id, shape, START.isoformat(), START.isoformat()))
                    source = "provider_workload"
                    begin_qualification_event(db, tenant_id=tenant, source=source, certification_id="cert", configured_ttl=None)
                    db.execute("UPDATE provider_workload_certifications SET status='passed' WHERE tenant_id=?", (tenant,))
                    complete_qualification_event(db, tenant_id=tenant, source=source, certification_id="cert", continuity_blocked=False)
        repository.claim_workload_run("local", run_id="run", entry_id="meta_agent", policy_fingerprint="policy")
        repository.claim_workload_call("local", call_id="call", run_id="run", entry_id="meta_agent",
            execution_shape=shape, requested_model="model", connection_id=conn.id,
            certification_id="cert", connection_fingerprint="fp", logical_call_key_hash="hash", call_sequence=1)
        # This repository-terminal unit test seeds a dispatch fact; no transport runs.
        with sqlite3.connect(repository.database_path) as db:
            db.execute("UPDATE provider_workload_calls SET dispatched=? WHERE tenant_id='local' AND id='call'", (int(dispatched),))
        # A subsequent finalizer error rolls back both terminal and qualification
        # writes, so no partial failed/qualified state can escape the transaction.
        with pytest.raises(RouterRepositoryError, match="provider_workload_call_run_mismatch"):
            repository.complete_workload_call("local", "call", status="failed", result_class=result_class,
                error_code=error_code, complete_run_id="wrong-run")
        with sqlite3.connect(repository.database_path) as db:
            assert db.execute("SELECT status FROM provider_workload_calls WHERE tenant_id='local'").fetchone()[0] == "running"
            assert db.execute("SELECT closed_at FROM provider_qualification_intervals WHERE tenant_id='local'").fetchone()[0] is None
        repository.complete_workload_call("local", "call", status="failed", result_class=result_class, error_code=error_code)
        with sqlite3.connect(repository.database_path) as db:
            states = dict(db.execute("SELECT tenant_id, closed_at FROM provider_qualification_intervals"))
            assert (states["local"] is not None) is closed
            assert states["other"] is None


@pytest.mark.parametrize("renewal,allowed", [
    ("passed", True), ("running", True), ("failed", False),
    ("changed_profile", False), ("expired", False),
])
def test_workload_dispatch_rechecks_latest_qualification_after_plan(tmp_path, renewal, allowed):
    """Renewal can finish between planning and the atomic dispatch marker."""
    with SQLiteRouterRepository.open(tmp_path, master_key=b"t" * 32) as repository:
        conn = repository.create_connection("local", RouterConnectionCreate(
            name="synthetic", kind="openrouter", base_url="https://example.com/v1",
            api_key="synthetic-test-key",
        ))
        def claim(identifier, profile="profile"):
            repository.claim_workload_certification(
                "local", certification_id=identifier, connection_id=conn.id,
                connection_fingerprint="fp", contract_version="v1",
                execution_shape="chat_json_object", requested_model="model",
                profile={}, profile_fingerprint=profile, idempotency_key_hash=identifier,
            )
        claim("cert-a")
        repository.complete_workload_certification("local", "cert-a", status="passed", checks={}, warning_codes=[])
        repository.replace_workload_policy("local", entry_id="meta_agent", expected_revision=0,
            policy_fingerprint="policy", local_fallback_mode="none", bindings=[{
                "execution_shape": "chat_json_object", "model_id": "model",
                "connection_id": conn.id, "certification_id": "cert-a",
                "certification_source": "provider_workload", "connection_fingerprint": "fp",
                "qualification_fingerprint": "qualification",
            }])
        repository.activate_workload_policy("local", entry_id="meta_agent", expected_revision=1,
            policy_fingerprint="policy", no_open_p0_p1=True, acknowledge_fail_closed=True)
        repository.claim_workload_run("local", run_id="run", entry_id="meta_agent", policy_fingerprint="policy")
        repository.claim_workload_call("local", call_id="call", run_id="run", entry_id="meta_agent",
            execution_shape="chat_json_object", requested_model="model", connection_id=conn.id,
            certification_id="cert-a", connection_fingerprint="fp", logical_call_key_hash="hash", call_sequence=1)
        claim("cert-z", "changed" if renewal == "changed_profile" else "profile")
        if renewal != "running":
            repository.complete_workload_certification("local", "cert-z",
                status="failed" if renewal == "failed" else "passed", checks={}, warning_codes=[])
        if renewal == "expired":
            # An older event remains unexpired. The new current event does not.
            now = datetime.now(UTC)
            with sqlite3.connect(repository.database_path) as db:
                db.execute("UPDATE provider_qualification_events SET completed_at=?,expires_at=? WHERE certification_id='cert-z'",
                    ((now - timedelta(seconds=301)).isoformat(), (now - timedelta(seconds=1)).isoformat()))
                db.execute("UPDATE provider_qualification_intervals SET valid_from=?",
                    ((now - timedelta(days=1)).isoformat(),))
        def dispatch():
            repository.mark_workload_call_dispatched("local", "call", run_id="run", entry_id="meta_agent",
                execution_shape="chat_json_object", requested_model="model", connection_id=conn.id,
                certification_id="cert-a", connection_fingerprint="fp", policy_fingerprint="policy")
        if allowed:
            dispatch()
        else:
            with pytest.raises(RouterRepositoryError, match="provider_workload_dispatch_preconditions_changed"):
                dispatch()
        with sqlite3.connect(repository.database_path) as db:
            assert db.execute("SELECT dispatched FROM provider_workload_calls WHERE id='call'").fetchone()[0] == int(allowed)


def test_restoring_old_profile_cannot_bridge_intervening_profile(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"t" * 32) as repository:
        conn = repository.create_connection("local", RouterConnectionCreate(
            name="synthetic", kind="openrouter", base_url="https://example.com/v1", api_key="synthetic"))
        states = []
        for identifier, profile in (("one", "a"), ("two", "b"), ("three", "a")):
            repository.claim_workload_certification("local", certification_id=identifier,
                connection_id=conn.id, connection_fingerprint="fp", contract_version="v1",
                execution_shape="chat_json_object", requested_model="model", profile={"variant": profile},
                profile_fingerprint=profile, idempotency_key_hash=identifier)
            repository.complete_workload_certification("local", identifier, status="passed", checks={}, warning_codes=[])
            states.append(repository.get_certification_qualification("local", "provider_workload", identifier))
        assert states[0]["series_id"] == states[2]["series_id"]
        assert states[0]["interval_id"] != states[2]["interval_id"]
        assert repository.certification_time_status("local", "provider_workload", "one")[0] == "provider_chat_certification_invalidated"


def test_rerank_access_modes_keep_independent_qualification_continuity(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"t" * 32) as repository:
        conn = repository.create_connection("local", RouterConnectionCreate(
            name="synthetic", kind="openrouter", base_url="https://example.com/v1", api_key="synthetic"))
        states = []
        for identifier, mode in (("one", "dedicated"), ("two", "llm_json"), ("three", "dedicated")):
            repository.claim_workload_certification("local", certification_id=identifier,
                connection_id=conn.id, connection_fingerprint="fp", contract_version="v1",
                execution_shape="rerank_documents", requested_model="model",
                profile={"rerank_access_mode": mode}, profile_fingerprint=mode, idempotency_key_hash=identifier)
            repository.complete_workload_certification("local", identifier, status="passed", checks={}, warning_codes=[])
            states.append(repository.get_certification_qualification("local", "provider_workload", identifier))
        assert states[0]["series_id"] != states[1]["series_id"]
        assert states[0]["interval_id"] == states[2]["interval_id"]
        assert repository.certification_time_status("local", "provider_workload", "two")[0] is None
