import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type {
  SkillCreatorDraft,
  SkillPackagePayload,
} from "../../utils/skillCreatorApi";
import SkillPackageEditor from "./SkillPackageEditor";

const complexSkillMarkdown = `---
# preserved-comment
name: complex-skill
description: >-
  Handles PDF: evidence # this is YAML text
  while preserving citations.
license: Apache-2.0 # keep-inline-comment
compatibility: |
  Requires an offline sandbox.
  Network access is not needed.
metadata:
  nested:
    labels: [pdf, "evidence:strict"]
    flags:
      preserve-comments: true
allowed-tools:
  - skill_read
  - skill_stage
---

# Complex Skill

Keep every source citation.`;

const draft: SkillCreatorDraft = {
  draft_id: "draft-complex",
  root_name: "complex-skill",
  name: "complex-skill",
  slug: "complex-skill",
  description: "Handles PDF: evidence # this is YAML text while preserving citations.",
  skill_markdown: complexSkillMarkdown,
  files: {},
  status: "draft",
  revision: 3,
  content_revision: 2,
  content_digest: "a".repeat(64),
  quality_required: true,
  quality_status: "not_evaluated",
  frontmatter: {
    name: "complex-skill",
    description: "Handles PDF: evidence # this is YAML text while preserving citations.",
    license: "Apache-2.0",
    compatibility: "Requires an offline sandbox.\nNetwork access is not needed.\n",
    metadata: {
      nested: {
        labels: ["pdf", "evidence:strict"],
        flags: { "preserve-comments": true },
      },
    },
    allowed_tools: ["skill_read", "skill_stage"],
  },
  validation: {
    valid: true,
    validator_version: "skill-package-v2.1",
    issues: [],
    content_digest: "a".repeat(64),
  },
};

function renderEditor(
  value = draft,
  onSave: (payload: SkillPackagePayload) => Promise<void> = vi.fn(async () => undefined),
) {
  render(
    <SkillPackageEditor
      draft={value}
      onCopyAsNew={vi.fn(async () => undefined)}
      onReload={vi.fn(async () => undefined)}
      onSave={onSave}
      saving={false}
    />,
  );
  return { onSave };
}

describe("SkillPackageEditor", () => {
  it("uses the read-only server projection and preserves complex YAML bytes on save", async () => {
    const onSave = vi.fn<(payload: SkillPackagePayload) => Promise<void>>(async () => undefined);
    renderEditor(draft, onSave);

    expect(screen.getByText("服务端已验证投影")).toBeVisible();
    expect(screen.getByText(draft.frontmatter!.description)).toBeVisible();
    expect(screen.getByText(/"preserve-comments": true/)).toBeVisible();
    expect(screen.queryByRole("textbox", { name: /Skill ID 与根目录/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /能力与触发场景/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "源码编辑" }));
    const source = screen.getByLabelText("编辑 SKILL.md");
    expect(source).toHaveValue(complexSkillMarkdown);

    const updatedMarkdown = `${complexSkillMarkdown}\n\nAdd one exact step.`;
    fireEvent.change(source, { target: { value: updatedMarkdown } });
    await userEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const payload = onSave.mock.calls[0]?.[0];
    expect(payload).toBeDefined();
    if (!payload) throw new Error("Expected the editor to save one package payload.");
    expect(payload.skill_markdown).toBe(updatedMarkdown);
    expect(payload.skill_markdown).toContain("# preserved-comment");
    expect(payload.skill_markdown).toContain("description: >-");
    expect(payload.skill_markdown).toContain("license: Apache-2.0 # keep-inline-comment");
    expect(payload.skill_markdown).toContain('labels: [pdf, "evidence:strict"]');
    expect(payload.description).toBe(draft.frontmatter!.description);
    expect(payload.metadata).toEqual(draft.frontmatter!.metadata);
    expect(payload.allowed_tools).toEqual(["skill_read", "skill_stage"]);
  });

  it("does not pseudo-parse YAML when the server projection is unavailable", () => {
    renderEditor({ ...draft, frontmatter: null });

    expect(screen.getByLabelText("编辑 SKILL.md")).toHaveValue(complexSkillMarkdown);
    expect(screen.getByRole("tab", { name: "结构化（只读）" })).toBeDisabled();
    expect(screen.getByText(/服务端未提供有效 frontmatter 投影/)).toBeVisible();
  });

  it("blocks beforeunload only while the package has unsaved changes", async () => {
    renderEditor();

    const cleanEvent = new Event("beforeunload", { cancelable: true });
    expect(window.dispatchEvent(cleanEvent)).toBe(true);
    expect(cleanEvent.defaultPrevented).toBe(false);

    await userEvent.click(screen.getByRole("tab", { name: "源码编辑" }));
    fireEvent.change(screen.getByLabelText("编辑 SKILL.md"), {
      target: { value: `${complexSkillMarkdown}\nchanged` },
    });

    const dirtyEvent = new Event("beforeunload", { cancelable: true });
    expect(window.dispatchEvent(dirtyEvent)).toBe(false);
    expect(dirtyEvent.defaultPrevented).toBe(true);
  });
});
