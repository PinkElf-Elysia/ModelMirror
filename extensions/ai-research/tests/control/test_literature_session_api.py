from __future__ import annotations

from fastapi.testclient import TestClient

import ai_research_control.app as app_module
from ai_research_control.ldr_client import LdrAuthenticationError
from ai_research_control.service import NotReady


class SessionService:
    configured = True

    def __init__(self, settings: object) -> None:
        self.state = {"status": "locked", "username": None}

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def literature_session(self) -> dict[str, str | None]:
        return self.state

    async def unlock_literature(
        self, *, username: str, password: str
    ) -> dict[str, str | None]:
        if not self.configured:
            raise NotReady("fixed literature model bridge is not configured")
        if password != "correct-password":
            raise LdrAuthenticationError("LDR credentials were rejected")
        self.state = {"status": "ready", "username": username}
        return self.state

    async def clear_literature_session(self) -> dict[str, str | None]:
        self.state = {"status": "locked", "username": None}
        return self.state


def test_unlock_status_and_clear_are_no_store(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "ResearchService", SessionService)
    with TestClient(app_module.app) as api:
        locked = api.get("/api/v1/literature/session")
        assert locked.json() == {"status": "locked", "username": None}
        assert locked.headers["cache-control"] == "no-store"

        unlocked = api.post(
            "/api/v1/literature/session/unlock",
            json={"username": "researcher", "password": "correct-password"},
        )
        assert unlocked.status_code == 200
        assert unlocked.json() == {"status": "ready", "username": "researcher"}
        assert unlocked.headers["cache-control"] == "no-store"

        cleared = api.delete("/api/v1/literature/session")
        assert cleared.json() == {"status": "locked", "username": None}
        assert cleared.headers["cache-control"] == "no-store"


def test_unlock_rejects_bad_input_credentials_and_missing_bridge(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "ResearchService", SessionService)
    with TestClient(app_module.app) as api:
        assert api.post(
            "/api/v1/literature/session/unlock",
            json={"username": "../bad", "password": "correct-password"},
        ).status_code == 422
        assert api.post(
            "/api/v1/literature/session/unlock",
            json={"username": "researcher", "password": "wrong"},
        ).status_code == 423
        api.app.state.research.configured = False
        assert api.post(
            "/api/v1/literature/session/unlock",
            json={"username": "researcher", "password": "correct-password"},
        ).status_code == 503
