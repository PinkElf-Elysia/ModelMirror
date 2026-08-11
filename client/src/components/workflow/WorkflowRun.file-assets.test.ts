import { describe, expect, it, vi } from "vitest";
import type { FileOutput } from "../../data/fileOutputs";

import {
  apiErrorMessage,
  confirmWorkflowFileDeletion,
  recoveredWorkflowOutputs,
  workflowOutputsForRun,
  workflowFileDeleteConfirmation,
  workflowFileScopeId,
} from "./WorkflowRun";

function output(outputId: string, sourceRunId: string | null): FileOutput {
  return {
    output_id: outputId,
    asset_id: `file-${outputId}`,
    purpose: "workflow",
    scope_id: "workflow:draft-123",
    producer_kind: "mcp_artifact",
    display_name: `${outputId}.json`,
    format: "json",
    media_type: "application/json",
    byte_size: 32,
    preview_kind: "text",
    status: "completed",
    expires_at: "2026-08-17T00:00:00Z",
    warnings: [],
    error_code: null,
    source_run_id: sourceRunId,
    source_message_id: null,
    source_node_id: "node-1",
    created_at: "2026-08-10T00:00:00Z",
    updated_at: "2026-08-10T00:00:00Z",
  };
}

describe("WorkflowRun file assets", () => {
  it("derives a fixed workflow scope instead of accepting a user path", () => {
    expect(workflowFileScopeId("draft-123")).toBe("workflow:draft-123");
  });

  it("reads tenant-safe API detail messages", () => {
    expect(
      apiErrorMessage(
        { detail: { code: "file_asset_not_found", message: "文件不存在或无权访问。" } },
        "fallback",
      ),
    ).toBe("文件不存在或无权访问。");
    expect(apiErrorMessage(null, "fallback")).toBe("fallback");
  });

  it("cancels destructive deletion when the user does not confirm", () => {
    const confirmAction = vi.fn(() => false);

    expect(confirmWorkflowFileDeletion(confirmAction)).toBe(false);
    expect(confirmAction).toHaveBeenCalledOnce();
    expect(confirmAction).toHaveBeenCalledWith(workflowFileDeleteConfirmation);
    expect(workflowFileDeleteConfirmation).toContain("不可撤销");
    expect(workflowFileDeleteConfirmation).toContain("最后一个引用");
  });

  it("groups current-run outputs without hiding recovered historical outputs", () => {
    const outputs = [
      output("output-current", "run-current"),
      output("output-old", "run-old"),
      output("output-unbound", null),
    ];

    expect(workflowOutputsForRun(outputs, "run-current").map((item) => item.output_id)).toEqual([
      "output-current",
    ]);
    expect(recoveredWorkflowOutputs(outputs, "run-current").map((item) => item.output_id)).toEqual([
      "output-old",
      "output-unbound",
    ]);
  });
});
