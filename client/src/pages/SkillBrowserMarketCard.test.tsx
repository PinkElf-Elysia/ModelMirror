import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { skillProjects, type SkillProject } from "../data/skillProjects";
import { MarketSkillCard } from "./SkillBrowserPage";

const directSkill = skillProjects.find(
  (project) =>
    project.kind === "skill" &&
    project.installMode === "direct" &&
    Boolean(project.installSource),
)!;
const packagedSkillSet = skillProjects.find(
  (project) =>
    project.kind === "skillset" &&
    project.skillSet?.mode === "package" &&
    (project.includedSkills?.length ?? 0) > 3,
)!;

function renderCard(
  project: SkillProject,
  overrides: Partial<ComponentProps<typeof MarketSkillCard>> = {},
) {
  const props: ComponentProps<typeof MarketSkillCard> = {
    gateMode: "off",
    installed: false,
    installingId: "",
    onInstall: vi.fn(),
    onInspectTrust: vi.fn(),
    onOpenSkillSet: vi.fn(),
    project,
    trustSummary: null,
    ...overrides,
  };

  return { ...render(<MarketSkillCard {...props} />), props };
}

describe("SkillBrowser market cards", () => {
  it("distinguishes a Skill without changing its install action", async () => {
    const user = userEvent.setup();
    const project = {
      ...directSkill,
      tags: ["标签一", "标签二", "标签三", "标签四", "标签五"],
    };
    const onInstall = vi.fn();
    const { container } = renderCard(project, { onInstall });

    expect(container.querySelector('[data-skill-kind="skill"]')).not.toBeNull();
    expect(screen.getByLabelText("单项 Skill")).toBeVisible();
    expect(screen.getByTestId("skill-card-header")).toContainElement(
      screen.getByLabelText("单项 Skill"),
    );
    const heading = screen.getByRole("heading", { name: project.name });
    expect(heading).toBeVisible();
    expect(
      within(heading.parentElement as HTMLElement).getByText(project.repoName, {
        exact: false,
      }),
    ).toBeVisible();

    const tags = screen.getByTestId("skill-card-tags");
    expect(tags.children).toHaveLength(4);
    expect(within(tags).getByText("+2")).toBeVisible();
    expect(screen.getByTestId("skill-card-source")).toHaveTextContent("安装来源");
    expect(screen.getByTestId("skill-card-actions")).toContainElement(
      screen.getByRole("button", { name: "安装技能" }),
    );

    await user.click(screen.getByRole("button", { name: "安装技能" }));
    expect(onInstall).toHaveBeenCalledWith(project);
  });

  it("uses the SkillSet treatment and summarizes package members", () => {
    expect(packagedSkillSet).toBeDefined();
    const { container } = renderCard(packagedSkillSet);

    expect(container.querySelector('[data-skill-kind="skillset"]')).not.toBeNull();
    expect(screen.getByLabelText("SkillSet 技能包")).toBeVisible();
    expect(screen.getByTestId("skill-card-header")).toContainElement(
      screen.getByLabelText("SkillSet 技能包"),
    );
    expect(screen.getByText("技能包内容")).toBeVisible();

    const members = screen.getByTestId("skillset-members");
    expect(members.children).toHaveLength(4);
    expect(
      within(members).getByText(
        `+${(packagedSkillSet.includedSkills?.length ?? 0) - 3}`,
      ),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "查看来源" })).toHaveAttribute(
      "href",
      packagedSkillSet.repoUrl,
    );
  });
});
