import { describe, expect, it } from "vitest";
import { draftExecutionDisposition } from "./ragPipelineActivation";

describe("4C parser admission", () => {
  it.each(["vector", "fulltext", "hybrid"])("blocks a legacy parser draft in %s instead of offering a build", (mode) => {
    const disposition = draftExecutionDisposition({
      status: "legacy_read_only",
      components: { chunker: "current", lexical: "current", parser: "legacy_read_only" },
    }, mode, 3);
    expect(disposition.canExecute).toBe(false);
    expect(disposition.status).toBe("blocked");
    expect(disposition.message).toContain("解析");
  });
  it("keeps complete new parser drafts buildable without claiming promotion", () => {
    const disposition = draftExecutionDisposition({
      status: "current", components: { chunker: "current", lexical: "current", parser: "current" },
    }, "fulltext", 3);
    expect(disposition.canExecute).toBe(true);
    expect(disposition.status).toBe("normal");
  });
});
