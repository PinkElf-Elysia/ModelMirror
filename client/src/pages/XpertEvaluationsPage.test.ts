import { describe, expect, it } from "vitest";

import {
  buildEvaluationPreflightRequest,
  normalizeEvaluationCasesForSave,
  normalizeResourceEvidence,
  normalizeVisionEvidence,
  resourceEvidenceSummaryLabel,
} from "./XpertEvaluationsPage";
import {
  evaluationCaseUploadIdentity,
  parseVisionAnchorsJson,
  resolveEvaluationUploadCaseIndex,
} from "../components/evaluations/EvaluationVisionCases";

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

describe("Xpert evaluation visual fixtures", () => {
  it("reduces a safe manifest to asset_id before saving cases", () => {
    const cases = normalizeEvaluationCasesForSave([
      {
        case_id: "case_1",
        message: "读取图表",
        attachment: {
          asset_id: "file_fixture_1",
          sha256: "a".repeat(64),
          format_id: "png",
          media_type: "image/png",
          byte_size: 42,
          page_count: 1,
          display_name: "chart.png",
          dataset_id: "dataset_1",
          dataset_scope_id: "evaluation:dataset_1",
          scope_id: "evaluation:dataset_1",
        },
        vision: [{
          node_ref: "vision_reader",
          asset_sha256: null,
          model_id: null,
          page_count: null,
          status: "success",
          required_blocks: [],
          content_anchors: [{ kind: "ocr", text: "收入", page_number: null }],
        }],
      },
    ]);

    expect(cases[0].attachment).toEqual({ asset_id: "file_fixture_1" });
    expect(cases[0].vision).toEqual([{
      node_ref: "vision_reader",
      asset_sha256: null,
      model_id: null,
      page_count: null,
      status: "success",
      required_blocks: [],
      content_anchors: [{ kind: "ocr", text: "收入", page_number: null }],
    }]);
  });

  it("binds dataset identity and selected cases into preflight", () => {
    expect(buildEvaluationPreflightRequest({
      dataset_id: "dataset_1",
      dataset_version: 3,
      case_ids: ["case_1"],
      baseline: null,
      candidates: [{
        kind: "xpert_version",
        xpert_id: "xpert_1",
        version: 2,
        label: "候选",
      }],
      model_policy: "snapshot",
      override_model_id: null,
    })).toMatchObject({
      dataset_id: "dataset_1",
      dataset_version: 3,
      case_ids: ["case_1"],
    });
  });

  it("projects only safe vision evidence and keeps verified usage explicit", () => {
    const evidence = normalizeVisionEvidence(
      "failed",
      [{
        node_ref: "vision_reader",
        asset_id: "file_fixture_1",
        asset_sha256: "b".repeat(64),
        model_id: "openai/vision-model",
        page_count: 3,
        selected_page_count: 2,
        processed_page_count: 1,
        failed_page_count: 1,
        status: "partial",
        block_counts: { ocr: 1, description: 0, table: 0, chart: 1 },
        anchor_checks: [{ index: 0, matched: true }, { index: 1, matched: false }],
        execution_summary: {
          model_calls: 2,
          known_total_tokens: 321,
          unverified_token_calls: 0,
          token_usage_verified: true,
          token_source: "managed_receipt",
          uncertain_calls: 0,
        },
        raw_payload: "must-not-render",
        physical_path: "C:/private/source.pdf",
        original: "must-not-render",
      }],
      {
        vision_actual_tokens: 321,
        vision_token_usage_verified: true,
        vision_model_calls: 2,
        vision_unverified_usage_calls: 0,
        vision_uncertain_dispatches: 1,
        model_calls: 99,
      },
    );

    expect(evidence).toEqual({
      status: "failed",
      reads: [{
        node_ref: "vision_reader",
        asset_sha256: "b".repeat(64),
        model_id: "openai/vision-model",
        page_count: 3,
        selected_page_count: 2,
        processed_page_count: 1,
        failed_page_count: 1,
        status: "partial",
        anchors_matched: 1,
        anchors_total: 2,
        receipt_present: true,
        usage: {
          model_calls: 2,
          actual_tokens: 321,
          unverified_token_calls: 0,
          token_usage_verified: true,
          uncertain_dispatches: 0,
        },
      }],
      usage: {
        model_calls: 2,
        actual_tokens: 321,
        unverified_token_calls: 0,
        token_usage_verified: true,
        uncertain_dispatches: 1,
      },
    });
    expect(JSON.stringify(evidence)).not.toContain("raw_payload");
    expect(JSON.stringify(evidence)).not.toContain("physical_path");
    expect(JSON.stringify(evidence)).not.toContain("must-not-render");
  });

  it("does not infer vision activity from generic model usage", () => {
    expect(normalizeVisionEvidence(
      "not_applicable",
      [],
      { model_calls: 7, total_tokens: 900 },
    )).toBeNull();
  });

  it("preserves nullable anchor pages and rejects unknown or out-of-range fields", () => {
    expect(parseVisionAnchorsJson('[{"kind":"chart","text":"收入","page_number":2}]'))
      .toEqual([{ kind: "chart", text: "收入", page_number: 2 }]);
    expect(parseVisionAnchorsJson('[{"kind":"ocr","text":"收入","page_number":null}]'))
      .toEqual([{ kind: "ocr", text: "收入", page_number: null }]);
    expect(() => parseVisionAnchorsJson('[{"kind":"chart","text":"收入","path":"x"}]'))
      .toThrow("不允许字段");
    expect(() => parseVisionAnchorsJson('[{"kind":"ocr","text":"收入","page_number":21}]'))
      .toThrow("1 至 20");
  });

  it("resolves an upload back to the same case after reordering", () => {
    const original = [
      { case_id: "case_a", message: "A" },
      { case_id: "case_b", message: "B" },
    ];
    const identity = evaluationCaseUploadIdentity(original[1]);

    expect(resolveEvaluationUploadCaseIndex([original[1], original[0]], identity)).toBe(0);
    expect(resolveEvaluationUploadCaseIndex([
      { message: "重复" },
      { message: "重复" },
    ], evaluationCaseUploadIdentity({ message: "重复" }))).toBeNull();
  });
});
