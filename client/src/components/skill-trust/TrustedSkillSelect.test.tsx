import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TrustedSkillSelect, { type TrustSelectableSkill } from "./TrustedSkillSelect";

function skill(
  skillId: string,
  allowed: boolean,
  status: TrustSelectableSkill["trust_activation_status"],
): TrustSelectableSkill {
  return {
    skill_id: skillId,
    name: skillId,
    trust_state: allowed ? "receipt_matched" : "unverified_legacy",
    trust_router_eligible: allowed,
    trust_activation_status: status,
    trust_activation_allowed: allowed,
    trust_acknowledgement_required: !allowed,
    trust_acknowledgement_satisfied: allowed,
    trust_reason_codes: allowed ? [] : ["skill_trust_receipt_missing"],
  };
}

describe("TrustedSkillSelect", () => {
  it("keeps blocked Skills visible but prevents selection", () => {
    const onChange = vi.fn();
    render(
      <TrustedSkillSelect
        ariaLabel="选择 Skill"
        onChange={onChange}
        skills={[
          skill("safe-skill", true, "ready"),
          skill("legacy-skill", false, "blocked"),
        ]}
        value=""
      />,
    );

    expect(screen.getByRole("option", { name: "legacy-skill（旧来源未核验）" })).toBeDisabled();
    expect(screen.getByText(/1 个 Skill 因未确认/)).toBeVisible();
    fireEvent.change(screen.getByLabelText("选择 Skill"), {
      target: { value: "safe-skill" },
    });
    expect(onChange).toHaveBeenCalledWith("safe-skill");
  });
});
