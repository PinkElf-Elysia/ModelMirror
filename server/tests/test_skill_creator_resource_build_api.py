from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from server.skills import creator_api


class _BuildStore:
    def __init__(self) -> None:
        self.item = SimpleNamespace(
            build_id="skillbuild_api",
            session_id="skillcreator_api",
            revision=1,
            digest="a" * 64,
        )

    def require(self, build_id: str):
        assert build_id == self.item.build_id
        return self.item

    @staticmethod
    def serialize(item):
        return {
            "build_id": item.build_id,
            "session_id": item.session_id,
            "revision": item.revision,
            "digest": item.digest,
        }


class _BuildService:
    VERSION = "resource-authoring-build-v1"
    enabled = True

    def __init__(self) -> None:
        self.build_store = _BuildStore()
        self.calls: list[str] = []

    def require_enabled(self) -> None:
        return None

    def status(self):
        return {
            "resource_build_enabled": True,
            "resource_build_version": self.VERSION,
            "resource_builder_available": True,
            "script_sandbox_configured": True,
        }

    def current_projection(self, _session_id: str):
        return self.build_store.serialize(self.build_store.item)

    async def start(self, *_args, **_kwargs):
        self.calls.append("start")
        return self.build_store.item

    async def next(self, *_args, **_kwargs):
        self.calls.append("next")
        return self.build_store.item

    def review_resource(self, *_args, **_kwargs):
        self.calls.append("review")
        return self.build_store.item

    def finalize(self, *_args, **_kwargs):
        self.calls.append("finalize")
        return self.build_store.item, None


@pytest.mark.asyncio
async def test_resource_build_routes_use_versioned_optimistic_requests() -> None:
    previous_creator = creator_api._service
    previous_planning = creator_api._resource_planning_service
    previous_build = creator_api._resource_build_service
    creator = SimpleNamespace(require_enabled=lambda: None)
    planning = SimpleNamespace(require_enabled=lambda: None)
    build = _BuildService()
    app = FastAPI()
    app.include_router(creator_api.router)
    try:
        creator_api.configure_skill_creator(creator)
        creator_api.configure_skill_creator_resource_planning(planning)
        creator_api.configure_skill_creator_resource_build(build)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post(
                "/api/skills/creator/sessions/skillcreator_api/resource-build",
                json={
                    "plan_id": "skillplan_api",
                    "expected_session_revision": 3,
                    "expected_plan_revision": 2,
                    "expected_plan_digest": "b" * 64,
                },
            )
            assert started.status_code == 201, started.text
            fetched = await client.get("/api/skills/creator/resource-builds/skillbuild_api")
            assert fetched.status_code == 200, fetched.text
            mutation = {
                "expected_session_revision": 3,
                "expected_revision": 1,
                "expected_digest": "a" * 64,
            }
            advanced = await client.post(
                "/api/skills/creator/resource-builds/skillbuild_api/next",
                json=mutation,
            )
            assert advanced.status_code == 200, advanced.text
            reviewed = await client.post(
                "/api/skills/creator/resource-builds/skillbuild_api/resources/resource_one/review",
                json={**mutation, "decision": "accept", "feedback": ""},
            )
            assert reviewed.status_code == 200, reviewed.text
            finalized = await client.post(
                "/api/skills/creator/resource-builds/skillbuild_api/finalize",
                json={**mutation, "decision": "revise", "feedback": "Clarify failure behavior."},
            )
            assert finalized.status_code == 200, finalized.text
            assert finalized.json()["proposal"] is None
        assert build.calls == ["start", "next", "review", "finalize"]
    finally:
        creator_api.configure_skill_creator(previous_creator)
        creator_api.configure_skill_creator_resource_planning(previous_planning)
        creator_api.configure_skill_creator_resource_build(previous_build)
