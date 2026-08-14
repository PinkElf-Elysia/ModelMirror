import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { skillProjects } from "../data/skillProjects";
import { SkillSetMemberPanel } from "./SkillBrowserPage";

const memberSkillSet = skillProjects.find(
  (project) => project.skillSet?.mode === "members",
)!;

function MemberPanelHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} type="button">
        查看成员
      </button>
      {open ? (
        <SkillSetMemberPanel
          batchProgress={null}
          installedSkills={[]}
          installingId=""
          onClose={() => setOpen(false)}
          onInstallAll={vi.fn()}
          onInstallMember={vi.fn()}
          onInspectTrust={vi.fn()}
          project={memberSkillSet}
          trustIndex={null}
        />
      ) : null}
    </>
  );
}

describe("SkillSet member overlay", () => {
  it("opens as a modal and restores focus after Escape", async () => {
    const user = userEvent.setup();
    render(<MemberPanelHarness />);

    const trigger = screen.getByRole("button", { name: "查看成员" });
    await user.click(trigger);

    expect(
      screen.getByRole("dialog", { name: memberSkillSet.name }),
    ).toBeVisible();
    expect(document.body.style.overflow).toBe("hidden");

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
  });

  it("leaves the member modal open while the trust layer is present", async () => {
    const user = userEvent.setup();
    render(<MemberPanelHarness />);

    await user.click(screen.getByRole("button", { name: "查看成员" }));
    const trustLayer = document.createElement("div");
    trustLayer.id = "skill-trust-panel";
    document.body.append(trustLayer);

    await user.keyboard("{Escape}");

    expect(screen.getByRole("dialog", { name: memberSkillSet.name })).toBeVisible();
    trustLayer.remove();
  });
});
