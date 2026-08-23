import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { plannerModelOptions } from "../../pages/MetaAgentPage";
import MetaPlannerV2, { candidateGenerationOutcome } from "./MetaPlannerV2";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Meta Planner managed compatibility", () => {
  it("does not hide eligible text models behind an arbitrary catalog cap", () => {
    expect(plannerModelOptions.length).toBeGreaterThan(120);
    expect(plannerModelOptions.some((model) => model.id === "openai/gpt-4o-mini")).toBe(
      true,
    );
  });

  it("does not present an unresolved repaired candidate as successful", () => {
    expect(candidateGenerationOutcome({ valid: false }, true)).toEqual({
      error: "候选已保留，但一次定向修复后仍未通过验证，需要人工修复。",
      notice: "",
    });
  });

  it("preserves the paid-call receipt when the follow-up proposal load fails", async () => {
    const receipt = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "meta_agent",
      routing_mode: "managed_required",
      run_reference: "workrun_receipt_preserved",
      status: "passed",
      call_count: 1,
      reason_codes: [],
      calls: [
        {
          call_sequence: 1,
          model_id: "openai/gpt-4o-mini",
          dispatched: true,
          status: "passed",
          total_tokens: 17,
        },
      ],
    };
    const jsonResponse = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/meta-agent/capabilities") {
          return jsonResponse({
            version: "test-snapshot-v1",
            snapshot_hash: "snapshot-hash",
            generated_at: 1,
            nodes: [],
            middleware: [],
            external_xperts: [],
            knowledge_bases: [],
            toolsets: [],
            plugins: [],
            prompt_profiles: [],
            default_scope: {
              allowed_node_kinds: [],
              external_xpert_ids: [],
              knowledge_base_ids: [],
              toolset_ids: [],
              plugin_ids: [],
              prompt_profile_ids: [],
              middleware_ids: [],
            },
          });
        }
        if (url.startsWith("/api/xperts?")) {
          return jsonResponse({ items: [], total: 0 });
        }
        if (url.startsWith("/api/runtime/authoring-proposals?")) {
          return jsonResponse({ items: [] });
        }
        if (
          url === "/api/meta-agent/generate-xpert-candidate" &&
          init?.method === "POST"
        ) {
          return jsonResponse({
            proposal_id: "proposal_receipt_test",
            proposal_revision: 1,
            mode: "create",
            target_xpert_id: null,
            base_revision: null,
            plan: { summary: "Test plan", assumptions: [], tasks: [] },
            candidate: {
              name: "Receipt candidate",
              description: "Test candidate",
              tags: [],
              starters: [],
              draft: {
                workflow: {
                  id: "workflow_receipt_test",
                  title: "Receipt test",
                  nodes: [],
                  edges: [],
                },
              },
            },
            validation: { valid: true, stages: [] },
            warnings: [],
            repair_used: false,
            capability_snapshot_version: "test-snapshot-v1",
            capability_snapshot_hash: "snapshot-hash",
            provider_route_receipts: receipt,
          });
        }
        if (url === "/api/runtime/authoring-proposals/proposal_receipt_test") {
          return jsonResponse({ detail: "proposal reload failed" }, 500);
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <MetaPlannerV2 />
      </MemoryRouter>,
    );

    await screen.findByText("test-snapshot-v1");
    fireEvent.change(
      screen.getByPlaceholderText(
        "例如：构建一个负责研究、事实核查与审稿协作的智能体",
      ),
      { target: { value: "Generate a bounded candidate for receipt testing." } },
    );
    fireEvent.click(screen.getByRole("button", { name: "生成候选智能体" }));

    await screen.findByText("proposal reload failed");
    await waitFor(() => {
      expect(screen.getByText("已纳管")).toBeInTheDocument();
      expect(screen.getByText("1 次模型调用")).toBeInTheDocument();
    });
  });
});
