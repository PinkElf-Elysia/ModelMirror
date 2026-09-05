import { describe, expect, it } from "vitest";

import {
  normalizeResourceEvidence,
  resourceEvidenceSummaryLabel,
} from "./XpertEvaluationsPage";

describe("Xpert evaluation resource evidence", () => {
  it("uses the item-level resource_reads emitted by the backend", () => {
    const evidence = normalizeResourceEvidence("verified", [
      {
        node_ref: "table_lookup",
        kind: "data_table_query",
        resource_id: "table_1",
        schema_version: 3,
        query_checksum: "a".repeat(64),
        result_count: 1,
        record_ids: ["record_1"],
      },
    ]);

    expect(evidence).toMatchObject({
      status: "verified",
      reads: [
        {
          node_ref: "table_lookup",
          schema_version: 3,
          query_checksum: "aaaaaaaaaaaa",
          result_count: 1,
          record_ids: ["record_1"],
        },
      ],
    });
  });

  it("renders aggregate counters without inventing an available status", () => {
    expect(
      resourceEvidenceSummaryLabel({
        verified: 2,
        failed: 1,
        missing: 3,
        not_applicable: 0,
      }),
    ).toBe("verified 2 · failed 1 · missing 3 · not_applicable 0");
    expect(resourceEvidenceSummaryLabel({ supported: true })).toBeNull();
  });
});
