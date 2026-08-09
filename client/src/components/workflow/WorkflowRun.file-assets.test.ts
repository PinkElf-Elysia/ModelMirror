import { describe, expect, it, vi } from "vitest";

import {
  apiErrorMessage,
  confirmWorkflowFileDeletion,
  workflowFileDeleteConfirmation,
  workflowFileScopeId,
} from "./WorkflowRun";

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
});
