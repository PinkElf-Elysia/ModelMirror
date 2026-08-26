import { describe, expect, it } from "vitest";
import {
  localizeWorkflowStepOutput,
  parseWorkflowBatchReceipts,
  workflowRunCompletedSummary,
  workflowStepOutputPreview,
} from "./WorkflowRun";

const validReceipt = {
  index: 0,
  status: "completed",
  projectId: "wf_0123456789abcdef0123456789abcdef",
  version: 3,
  executionId: "wfx_0123456789abcdef0123456789abcdef",
  taskId: "wft_fedcba9876543210fedcba9876543210",
  runId: "12345678-1234-4123-8123-123456789abc",
  result: "订单 A 已处理",
};

describe("workflow batch receipt presentation", () => {
  it("recognizes the strict typed receipt contract", () => {
    expect(parseWorkflowBatchReceipts(JSON.stringify([validReceipt]))).toEqual([
      validReceipt,
    ]);
    expect(workflowRunCompletedSummary(JSON.stringify([validReceipt]))).toBe(
      "批次完成：1 项",
    );
  });

  it("does not misclassify arbitrary arrays or malformed identifiers", () => {
    expect(parseWorkflowBatchReceipts('[{"status":"completed"}]')).toBeNull();
    expect(parseWorkflowBatchReceipts(JSON.stringify([
      { ...validReceipt, executionId: "attacker-controlled" },
    ]))).toBeNull();
    expect(parseWorkflowBatchReceipts('["普通结果"]')).toBeNull();
  });

  it("preserves ordinary completion text and the empty fallback", () => {
    expect(workflowRunCompletedSummary("普通最终结果")).toBe("普通最终结果");
    expect(workflowRunCompletedSummary(undefined)).toBe("运行完成。");
  });

  it("localizes only batch progress lines without changing arbitrary output", () => {
    expect(localizeWorkflowStepOutput("completed 1/2\ncompleted 2/2", "iteration")).toBe(
      "已完成 1/2\n已完成 2/2",
    );
    expect(localizeWorkflowStepOutput("completed 1/2", "output")).toBe("completed 1/2");
    expect(localizeWorkflowStepOutput("order completed 1/2", "iteration")).toBe(
      "order completed 1/2",
    );
  });

  it("folds only large HTTP response bodies while keeping the real output intact", () => {
    const sentinel = "R24_LARGE_BODY_SENTINEL";
    const output = JSON.stringify({
      statusCode: 200,
      contentType: "text/html",
      receivedBytes: 140004,
      body: sentinel.repeat(100),
    });

    const preview = workflowStepOutputPreview(output, "http_request", "http_response");
    expect(preview).toContain("HTTP 200 · text/html · 140004 字节");
    expect(preview).toContain("完整内容仍保存在变量 http_response 中");
    expect(preview).not.toContain(sentinel);
    expect(output).toContain(sentinel);
    expect(workflowStepOutputPreview(output, "output", "http_response")).toBe(output);
  });

  it("keeps small or malformed HTTP output unchanged", () => {
    const small = JSON.stringify({ statusCode: 200, body: "简短响应" });
    expect(workflowStepOutputPreview(small, "http_request")).toBe(small);
    expect(workflowStepOutputPreview("not-json", "http_request")).toBe("not-json");
  });
});
