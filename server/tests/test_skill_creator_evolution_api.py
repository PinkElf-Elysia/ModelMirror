from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from server.skills import creator_api


class _Store:
    @staticmethod
    def serialize(item):
        return dict(item)


class _EvolutionService:
    VERSION = "skill-creator-resource-evolution-v1"
    enabled = True

    def __init__(self) -> None:
        self.evolution_store = _Store()
        self.resource_plan_store = _Store()
        self.calls: list[str] = []

    def require_enabled(self) -> None:
        return None

    def status(self):
        return {
            "evolution_enabled": self.enabled,
            "evolution_version": self.VERSION,
            "evolution_planner_available": True,
            "evolution_store_available": True,
        }

    async def generate(self, *_args, **_kwargs):
        self.calls.append("generate")
        return {"plan_id": "skillevo_api", "revision": 1, "digest": "a" * 64}

    def save_answers(self, *_args, **_kwargs):
        self.calls.append("answers")
        return {"plan_id": "skillevo_api", "revision": 2, "digest": "b" * 64}

    def patch(self, *_args, **_kwargs):
        self.calls.append("patch")
        return {"plan_id": "skillevo_api", "revision": 3, "digest": "c" * 64}

    def confirm(self, *_args, **_kwargs):
        self.calls.append("confirm")
        return (
            {"plan_id": "skillevo_api", "revision": 4, "digest": "d" * 64},
            {"plan_id": "skillplan_api", "revision": 6, "digest": "e" * 64},
        )


@pytest.mark.asyncio
async def test_evolution_routes_use_frozen_revision_and_digest_contracts() -> None:
    previous_creator = creator_api._service
    previous_evolution = creator_api._evolution_service
    creator = SimpleNamespace(require_enabled=lambda: None)
    evolution = _EvolutionService()
    app = FastAPI()
    app.include_router(creator_api.router)
    try:
        creator_api.configure_skill_creator(creator)
        creator_api.configure_skill_creator_evolution(evolution)  # type: ignore[arg-type]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            generated = await client.post(
                "/api/skills/creator/sessions/session-api/evolution-plan/generate",
                json={
                    "evaluation_run_id": "skill_eval_run_api",
                    "expected_session_revision": 7,
                    "expected_draft_state_revision": 5,
                    "expected_draft_revision": 3,
                    "expected_draft_digest": "f" * 64,
                    "expected_review_revision": 1,
                    "expected_run_revision": 9,
                    "expected_resource_plan_revision": 4,
                    "expected_resource_plan_digest": "9" * 64,
                    "expected_evolution_revision": None,
                    "expected_evolution_digest": None,
                },
            )
            assert generated.status_code == 200, generated.text
            base = {
                "plan_id": "skillevo_api",
                "expected_session_revision": 7,
                "expected_draft_state_revision": 5,
                "expected_draft_revision": 3,
                "expected_draft_digest": "f" * 64,
                "expected_plan_revision": 1,
                "expected_plan_digest": "a" * 64,
            }
            answered = await client.put(
                "/api/skills/creator/sessions/session-api/evolution-plan/answers",
                json={**base, "answers": {"question-one": "Use the confirmed policy."}},
            )
            assert answered.status_code == 200, answered.text
            patched = await client.patch(
                "/api/skills/creator/sessions/session-api/evolution-plan",
                json={**base, "changes": {"non_goals": ["Do not add network access."]}},
            )
            assert patched.status_code == 200, patched.text
            confirmed = await client.post(
                "/api/skills/creator/sessions/session-api/evolution-plan/confirm",
                json=base,
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["resource_plan"]["plan_id"] == "skillplan_api"
        assert evolution.calls == ["generate", "answers", "patch", "confirm"]
    finally:
        creator_api.configure_skill_creator(previous_creator)
        creator_api.configure_skill_creator_evolution(previous_evolution)


@pytest.mark.asyncio
async def test_iterate_is_only_blocked_when_evolution_v2_is_enabled() -> None:
    previous_creator = creator_api._service
    previous_evolution = creator_api._evolution_service
    creator = SimpleNamespace(require_enabled=lambda: None)
    evolution = _EvolutionService()
    app = FastAPI()
    app.include_router(creator_api.router)
    try:
        creator_api.configure_skill_creator(creator)
        creator_api.configure_skill_creator_evolution(evolution)  # type: ignore[arg-type]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/skills/creator/sessions/session-api/iterate",
                json={
                    "evaluation_run_id": "skill_eval_run_api",
                    "expected_session_revision": 7,
                    "expected_revision": 5,
                    "expected_digest": "f" * 64,
                    "expected_review_revision": 1,
                },
            )
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == "skill_creator_evolution_plan_required"
            evolution.enabled = False
            fallback = await client.post(
                "/api/skills/creator/sessions/session-api/iterate",
                json={
                    "evaluation_run_id": "skill_eval_run_api",
                    "expected_session_revision": 7,
                    "expected_revision": 5,
                    "expected_digest": "f" * 64,
                    "expected_review_revision": 1,
                },
            )
            assert fallback.status_code == 400, fallback.text
            assert fallback.json()["detail"]["code"] != "skill_creator_evolution_plan_required"
    finally:
        creator_api.configure_skill_creator(previous_creator)
        creator_api.configure_skill_creator_evolution(previous_evolution)
