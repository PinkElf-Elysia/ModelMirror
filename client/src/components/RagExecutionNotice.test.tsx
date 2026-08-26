import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RagExecutionNotice } from "./RagExecutionNotice";

describe("RagExecutionNotice", () => {
  it("labels explicit local fallback without presenting it as model success", () => {
    render(<RagExecutionNotice executionMode="local_non_model" />);

    expect(screen.getByRole("status")).toHaveTextContent("本地非模型降级");
    expect(screen.getByRole("status")).toHaveTextContent("不代表模型调用成功");
  });

  it("does not add a warning to managed or legacy answers", () => {
    const { rerender } = render(<RagExecutionNotice executionMode="managed" />);
    expect(screen.queryByRole("status")).toBeNull();

    rerender(<RagExecutionNotice executionMode="legacy" />);
    expect(screen.queryByRole("status")).toBeNull();
  });
});
