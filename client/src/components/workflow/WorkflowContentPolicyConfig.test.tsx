import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import WorkflowContentPolicyConfig from "./WorkflowContentPolicyConfig";

describe("WorkflowContentPolicyConfig", () => {
  it("edits stable structured rules without exposing a custom replacement template", () => {
    const onChange = vi.fn();
    render(
      <WorkflowContentPolicyConfig
        config={{
          phase: "both",
          rules: [{
            id: "rule_1",
            label: "Credentials",
            detector: "secret_pattern",
            action: "block",
            terms: [],
            caseSensitive: false,
          }],
        }}
        onChange={onChange}
      />,
    );

    expect(screen.getByText(/替换为 \[已脱敏\]/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/替换模板/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "rule_1 检测器" }), {
      target: { value: "literal_terms" },
    });
    expect(onChange).toHaveBeenLastCalledWith(
      "rules",
      [expect.objectContaining({ id: "rule_1", detector: "literal_terms" })],
    );
  });
});
