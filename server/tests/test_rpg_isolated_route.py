"""Offline regression for the isolated RPG route dependency slice."""
import json
import pytest
from server.model_router import rpg_bridge as bridge
from server.model_router import rpg_local_state_tools as native
from server.tests.test_rpg_bridge import setup, HEADERS, stream
from server.tests.test_provider_chat_stable_service import _qualify_scoped_model, SCOPED_MODEL_ID

def test_isolated_backup_keeps_route_positions_and_no_unselected_credentials(setup, monkeypatch):
    client, stable, repo, meta, captured, response = setup
    monkeypatch.setenv("RPG_LOCAL_STATE_TOOLS_ENABLED", "true")
    meta["supported_parameters"] += ["tools", "tool_choice"]
    connections = repo.list_connections("local")
    backup = next(c.id for c in connections if c.kind == "openrouter")
    primary = next(c.id for c in connections if c.kind == "newapi")
    _qualify_scoped_model(repo, backup)
    with repo._connect() as db:
        public_primary = dict(db.execute("SELECT * FROM router_connections WHERE id=?", (primary,)).fetchone())
        public_primary.update(enabled=0, api_key_ciphertext="", masked_key="")
        route = dict(db.execute("SELECT * FROM provider_chat_capability_routes WHERE capability='chat_text' AND connection_id=?", (primary,)).fetchone())
        db.execute("DELETE FROM provider_catalog_models WHERE connection_id<>?", (backup,))
        db.execute("DELETE FROM provider_chat_model_qualifications WHERE connection_id<>? OR model_id<>?", (backup, SCOPED_MODEL_ID))
        db.execute("DELETE FROM router_connections WHERE id<>?", (backup,))
        db.execute("DELETE FROM provider_chat_capability_routes WHERE connection_id<>?", (backup,))
    selected = next(m for m in bridge.catalog(stable) if m["model"] == SCOPED_MODEL_ID)
    assert selected["available"]
    body = {"sessionId":"isolated", "requestId":"broken", "selectionId":selected["selectionId"], "selectionRevision":selected["selectionRevision"],
            "parameters":selected["parameters"], "messages":[{"role":"system","content":"fixture"},{"role":"user","content":"query"}],
            "tools":[native.TOOL], "tool_choice":"auto"}
    broken = client.post("/api/rpg/v1/local-state/chat/completions", headers=HEADERS, json=body)
    assert broken.status_code == 502
    assert broken.json()["receipt"]["dispatched"] is False
    assert captured == []
    with repo._connect() as db:
        attempt = dict(db.execute("SELECT * FROM provider_chat_attempts ORDER BY rowid DESC LIMIT 1").fetchone())
        assert attempt["position"] == 0
        stored_position = db.execute("SELECT position FROM provider_chat_capability_routes WHERE capability='chat_text' AND connection_id=?", (backup,)).fetchone()[0]
        assert stored_position == 1
        db.execute("INSERT INTO provider_chat_capability_routes ("+",".join(route)+") VALUES ("+",".join("?" for _ in route)+")", tuple(route.values()))
        db.execute("INSERT INTO router_connections ("+",".join(public_primary)+") VALUES ("+",".join("?" for _ in public_primary)+")", tuple(public_primary.values()))
    body["requestId"] = "fixed"
    response["content"] = stream(model=SCOPED_MODEL_ID, raw="Complete mock final.")
    fixed = client.post("/api/rpg/v1/local-state/chat/completions", headers=HEADERS, json=body)
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["receipt"]["dispatched"] is True
    assert len(captured) == 1
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM router_connections WHERE api_key_ciphertext<>''").fetchone()[0] == 1
        assert db.execute("SELECT enabled FROM router_connections WHERE id=?", (primary,)).fetchone()[0] == 0
        attempt = dict(db.execute("SELECT * FROM provider_chat_attempts ORDER BY rowid DESC LIMIT 1").fetchone())
        assert attempt["position"] == 1 and attempt["status"] == "succeeded"
