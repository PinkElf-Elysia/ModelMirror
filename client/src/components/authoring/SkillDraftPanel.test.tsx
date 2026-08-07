import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SkillDraftPanel from "./SkillDraftPanel";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

const base = {
  name: "PDF Helper",
  slug: "pdf-helper",
  description: "处理 PDF。",
  status: "draft",
  revision: 1,
  content_revision: 1,
  content_digest: "a".repeat(64),
  install_state: "not_installed",
  skill_markdown: "---\nname: pdf-helper\ndescription: 处理 PDF。\n---",
  files: {},
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SkillDraftPanel quality gate", () => {
  it("locks Creator drafts while preserving legacy explicit installation", async () => {
    const creator = {
      ...base,
      draft_id: "creator_draft",
      quality_required: true,
      quality_status: "not_evaluated",
      creator_session_id: "creator_1",
    };
    const legacy = { ...base, draft_id: "legacy_draft", name: "Legacy PDF Helper" };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("creator_draft")) return jsonResponse(creator);
      if (url.includes("legacy_draft")) return jsonResponse(legacy);
      return jsonResponse({ items: [creator, legacy] });
    }));

    render(<SkillDraftPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /PDF Helper.*待评测/ }));
    expect(await screen.findByRole("button", { name: "PR3 后可安装" })).toBeDisabled();
    expect(screen.getByText(/当前内容尚未通过质量门/)).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: /Legacy PDF Helper/ }));
    expect(await screen.findByRole("button", { name: "显式安装" })).toBeEnabled();
  });
});
