from __future__ import annotations

from dataclasses import replace

import pytest

from server.coding_runtime.cycles import (
    MAX_HISTORY_PATCH_BYTES,
    MAX_INCREMENTAL_CYCLES,
    CodingCycle,
    CodingCycleHistory,
    CycleState,
)


def _cycle(number: int, *, state: CycleState = CycleState.COMMITTED) -> CodingCycle:
    revision = number * 3
    return CodingCycle(
        number=number,
        revision=revision,
        state=state,
        patch=f"diff --git a/docs/{number}.txt b/docs/{number}.txt\n+round-{number}\n",
        changes={
            "revision": revision,
            "file_count": 1,
            "additions": 1,
            "deletions": 0,
        },
        verification={"result": "not_applicable"},
        apply={"revision": revision, "apply_id": f"apply-{number}"},
        commit={
            "revision": revision,
            "commit_sha": str(number) * 40,
            "message": f"docs: 第 {number} 轮",
        },
        created_at=float(number),
        updated_at=float(number),
    )


def test_history_is_bounded_consecutive_and_exposes_latest_undo_only() -> None:
    history = CodingCycleHistory()
    for number in range(1, MAX_INCREMENTAL_CYCLES + 1):
        history = history.append(_cycle(number))

    assert history.is_full is True
    assert history.next_number == 11
    public = history.to_public()
    assert public[0]["number"] == 10
    assert public[0]["can_undo"] is True
    assert all(item["can_undo"] is False for item in public[1:])
    with pytest.raises(ValueError):
        history.append(_cycle(11))


def test_only_latest_cycle_can_change_state() -> None:
    history = CodingCycleHistory((_cycle(1), _cycle(2)))

    with pytest.raises(ValueError):
        history.require_latest(1)

    latest = history.require_latest(2)
    undone = replace(latest, state=CycleState.UNDONE, updated_at=3.0)
    history = history.replace_latest(undone)
    assert history.latest == undone
    assert history.to_public()[0]["can_undo"] is False


def test_history_rejects_gaps_and_total_patch_overflow() -> None:
    with pytest.raises(ValueError):
        CodingCycleHistory((_cycle(2),))

    with pytest.raises(ValueError):
        replace(_cycle(1), patch="x" * (1024 * 1024 + 1))
    assert MAX_HISTORY_PATCH_BYTES == MAX_INCREMENTAL_CYCLES * 1024 * 1024


@pytest.mark.parametrize("number", [0, 11, True])
def test_cycle_rejects_invalid_number(number: int) -> None:
    with pytest.raises(ValueError):
        replace(_cycle(1), number=number)
