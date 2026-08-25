from __future__ import annotations

import asyncio

from ai_research_control.service import ResearchService


class TerminalRaceStore:
    def get(self, run_id: str) -> dict[str, object]:
        return {"run_id": run_id, "phase": "running", "case_id": "success"}

    def request_cancel(self, run_id: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "phase": "terminal",
            "outcome": "success",
            "case_id": "success",
            "cancel_requested": False,
        }


class UnexpectedWorker:
    async def cancel(self, run_id: str) -> dict[str, object]:
        raise AssertionError(f"terminal run was sent to Worker: {run_id}")


def test_cancel_race_does_not_send_terminal_run_to_worker() -> None:
    service = object.__new__(ResearchService)
    service.store = TerminalRaceStore()
    service.worker = UnexpectedWorker()

    result = asyncio.run(service.cancel("ar0_terminal_race"))

    assert result["phase"] == "terminal"
    assert result["outcome"] == "success"
    assert result["cancel_requested"] is False
