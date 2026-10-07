import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildEvaluationPreflightRequest,
  evaluationOutputForReport,
  normalizeEffectEvidenceStatus,
  normalizeEvaluationCasesForSave,
  normalizePartialCompletion,
  normalizeResourceEvidence,
  normalizeVisionEvidence,
  normalizeWriteEffectEvidence,
  resourceEvidenceSummaryLabel,
  saveEvaluationCasesDraft,
} from "./XpertEvaluationsPage";
import {
  evaluationCaseUploadIdentity,
  parseVisionAnchorsJson,
  resolveEvaluationUploadCaseIndex,
} from "../components/evaluations/EvaluationVisionCases";

afterEach(() => {
  vi.unstubAllGlobals();
});

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

describe("Xpert evaluation write fixtures and report evidence", () => {
  it("存在未应用或无效写入 JSON 时阻断保存且不发请求", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(saveEvaluationCasesDraft({
      datasetId: "dataset_1",
      revision: 3,
      casesText: JSON.stringify([{ case_id: "case_1", message: "更新订单" }]),
      editorState: {
        has_unapplied_changes: true,
        has_invalid_changes: true,
      },
    })).rejects.toThrow("先修正并应用");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("保存时保留合法初始化和效果正文", () => {
    const cases = normalizeEvaluationCasesForSave([{
      case_id: "case_write",
      message: "更新订单状态",
      table_initializations: [{
        table_id: "table-orders",
        schema_version: 4,
        source: "manual",
        records: [{ ref: "order_a", data: { status: "pending" } }],
      }],
      effects: [{
        node_ref: "write_order",
        table_id: "table-orders",
        operation: "update",
        schema_version: 4,
        contract_checksum: "a".repeat(64),
        status: "applied",
        affected_count: 1,
        expected_before: { status: "pending" },
        expected_after: { status: "done" },
      }],
    }]);

    expect(cases[0].table_initializations).toEqual([{
      table_id: "table-orders",
      schema_version: 4,
      source: "manual",
      records: [{ ref: "order_a", data: { status: "pending" } }],
    }]);
    expect(cases[0].effects?.[0]).toMatchObject({
      node_ref: "write_order",
      expected_before: { status: "pending" },
      expected_after: { status: "done" },
    });
  });

  it("报告只投影安全写入回执字段，不显示记录或效果正文", () => {
    const evidence = normalizeWriteEffectEvidence([{
      node_ref: "write_order",
      table_id: "table-orders",
      operation: "update",
      schema_version: 4,
      contract_checksum: "a".repeat(64),
      request_checksum: "b".repeat(64),
      status: "applied",
      affected_count: 1,
      replayed: false,
      error_code: null,
      untouched_before_checksum: "c".repeat(64),
      untouched_after_checksum: "c".repeat(64),
      expected_before: { status: "private-before" },
      expected_after: { status: "private-after" },
      private_effect: { before_records: [{ secret: "must-not-render" }] },
      output: { secret: "must-not-render" },
      records: [{ secret: "must-not-render" }],
    }]);

    expect(evidence).toEqual([{
      node_ref: "write_order",
      table_id: "table-orders",
      operation: "update",
      schema_version: 4,
      contract_checksum: "a".repeat(64),
      request_checksum: "b".repeat(64),
      status: "applied",
      affected_count: 1,
      replayed: false,
    }]);
    expect(JSON.stringify(evidence)).not.toContain("private");
    expect(JSON.stringify(evidence)).not.toContain("records");
    expect(JSON.stringify(evidence)).not.toContain("untouched");
    expect(evaluationOutputForReport(
      '{"records":[{"secret":"must-not-render"}]}',
      "verified",
      evidence,
      null,
    )).toBe("受控写入评测输出已隐藏；报告仅显示安全效果回执。");
  });

  it("畸形非空写标识仍隐藏正文，普通旧文本报告保持不变", () => {
    const privateOutput = '{"records":[{"secret":"must-not-render"}]}';

    expect(evaluationOutputForReport(
      privateOutput,
      null,
      [{ private_effect: { before_records: [{ secret: "must-not-render" }] } }],
      null,
    )).toBe("受控写入评测输出已隐藏；报告仅显示安全效果回执。");
    expect(evaluationOutputForReport(
      privateOutput,
      "recorded",
      [],
      null,
    )).toBe("受控写入评测输出已隐藏；报告仅显示安全效果回执。");
    expect(evaluationOutputForReport(
      "普通文本结果",
      "not_applicable",
      [],
      null,
    )).toBe("普通文本结果");
  });

  it("严格归一化效果状态、checksum 与部分完成摘要", () => {
    expect(normalizeEffectEvidenceStatus("verified")).toBe("verified");
    expect(normalizeEffectEvidenceStatus("recorded")).toBeNull();
    expect(normalizeWriteEffectEvidence([{
      node_ref: "write_order",
      table_id: "table-orders",
      operation: "update",
      schema_version: 4,
      contract_checksum: "forged",
      request_checksum: "b".repeat(64),
      status: "applied",
      affected_count: 1,
      replayed: false,
    }])).toEqual([]);
    expect(normalizePartialCompletion({
      committed_nodes: 2,
      affected_rows: 3,
      rolled_back: false,
      records: [{ secret: "must-not-render" }],
    })).toEqual({
      committed_nodes: 2,
      affected_rows: 3,
      rolled_back: false,
    });
    expect(normalizePartialCompletion({
      committed_nodes: 2,
      affected_rows: 3,
      rolled_back: true,
    })).toBeNull();
  });
});
