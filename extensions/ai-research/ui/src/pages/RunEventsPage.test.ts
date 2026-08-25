import { expect, test } from "vitest";

import { groupTimelineEvents } from "./RunEventsPage";
import type { RunEvent } from "../types";

function event(sequence: number, eventType: string): RunEvent {
  return { sequence, eventType, payload: { sequence }, createdAt: `2026-08-24T00:00:0${sequence}Z` };
}

test("only consecutive worker updates are grouped and remain individually available", () => {
  const grouped = groupTimelineEvents([
    event(1, "run.queued"),
    event(2, "run.worker_update"),
    event(3, "run.worker_update"),
    event(4, "run.cancel_requested"),
    event(5, "run.worker_update"),
  ]);

  expect(grouped.map((items) => items.map((item) => item.sequence))).toEqual([
    [1],
    [2, 3],
    [4],
    [5],
  ]);
});
