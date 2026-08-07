import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AuthoringProposalPanel from "./AuthoringProposalPanel";

function response(payload: unknown) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AuthoringProposalPanel", () => {
  it("keeps Skill Creator proposals out of the generic raw JSON review panel", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response({
      items: [
        {
          proposal_id: "creator-proposal",
          kind: "skill_update",
          title: "Creator typed proposal",
          status: "pending",
          revision: 1,
          apply_key: "creator-key",
          source_type: "skill_creator",
          source_id: "creator-session",
          updated_at: 2,
        },
        {
          proposal_id: "runtime-proposal",
          kind: "skill_update",
          title: "Runtime skill proposal",
          status: "pending",
          revision: 1,
          apply_key: "runtime-key",
          source_type: "workflow_agent",
          source_id: "workflow-task",
          updated_at: 1,
        },
      ],
    })));

    render(<AuthoringProposalPanel kindPrefix="skill" />);

    expect(await screen.findByText("Runtime skill proposal")).toBeVisible();
    expect(screen.queryByText("Creator typed proposal")).not.toBeInTheDocument();
  });
});
