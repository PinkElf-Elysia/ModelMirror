import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import McpHubReviewWorkbench, {
  type HubReviewStatus,
} from "./McpHubReviewWorkbench";

function json(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      headers: { "Content-Type": "application/json" },
      status,
    }),
  );
}

const status: HubReviewStatus = {
  enabled: true,
  local_publish_enabled: true,
  signing_key_configured: true,
  sop_version: "anonymous_https_tools_v1",
  max_batch_size: 20,
  max_concurrency: 2,
  active_run_id: "hubreview_" + "1".repeat(32),
  operator_scope: "trusted-local-operator",
  multi_tenant_admin: false,
};

const runId = "hubreview_" + "1".repeat(32);
const itemId = "hubitem_" + "2".repeat(32);
const proposalId = "hubproposal_" + "3".repeat(32);
const proposalDigest = "4".repeat(64);
const evidenceDigest = "5".repeat(64);

function reviewItem(stateName: "awaiting_call_approval" | "awaiting_decision") {
  return {
    item_id: itemId,
    server_name: "io.example/reviewable",
    version: "1.0.0",
    state: stateName,
    current_stage: stateName === "awaiting_decision" ? "cleanup" : "call_proposal",
    evidence_digest: evidenceDigest,
    contract_fingerprint: "",
    error_code: "",
    evidence: {
      snapshot: { origin: "https://review.example.com" },
      effect_proposals: {
        search_primary: "read_candidate",
        search_secondary: "read_candidate",
        admin_delete: "dangerous_candidate",
      },
      tool_schema_digests: {
        search_primary: "6".repeat(64),
        search_secondary: "7".repeat(64),
        admin_delete: "8".repeat(64),
      },
      representative_call: stateName === "awaiting_decision"
        ? {
            tool_name: "search_primary",
            result_digest: "9".repeat(64),
            result_size: 123,
            result_type: "mcp-content",
            assertions: { result_is_object: true, remote_reported_error: false },
          }
        : {},
    },
    proposal: {
      proposal_id: proposalId,
      tool_name: "search_primary",
      arguments: { query: "modelmirror-review" },
      schema_digest: "6".repeat(64),
      proposal_digest: proposalDigest,
      state: stateName === "awaiting_decision" ? "completed" : "proposed",
    },
    events: [],
  };
}

function reviewRun(stateName: "awaiting_call_approval" | "awaiting_decision") {
  return {
    run_id: runId,
    status: "awaiting_operator",
    cancel_requested: false,
    counts: { [stateName]: 1 },
    items: [reviewItem(stateName)],
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("McpHubReviewWorkbench", () => {
  it("requires an explicit evidence acknowledgement before the one-shot call", async () => {
    let callApproved = false;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/call-proposals/${proposalId}/approve`)) {
        callApproved = true;
        return json({ preview: '{"result":"temporary"}' });
      }
      if (url.endsWith("/api/mcp/hub/review-runs")) {
        return json({ items: [reviewRun(callApproved ? "awaiting_decision" : "awaiting_call_approval")] });
      }
      if (url.endsWith("/api/mcp/hub/contracts")) return json({ items: [] });
      throw new Error(`unexpected URL: ${url} ${init?.method || "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <McpHubReviewWorkbench
        onClearSelection={() => undefined}
        onHubChanged={() => undefined}
        selected={[]}
        status={status}
      />,
    );

    const approve = await screen.findByRole("button", { name: "逐次批准代表调用" });
    expect(approve).toBeDisabled();
    expect(screen.getByText(`Tool Schema：${"6".repeat(64)}`)).toBeVisible();
    expect(screen.getByText(/确定性风险建议：read_candidate/)).toBeVisible();

    fireEvent.click(screen.getByRole("checkbox", { name: /我已核对 Origin、Tool Schema/ }));
    expect(approve).toBeEnabled();
    fireEvent.click(approve);

    await waitFor(() => expect(callApproved).toBe(true));
    const approvalCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith(`/call-proposals/${proposalId}/approve`),
    );
    expect(JSON.parse(String((approvalCall?.[1] as RequestInit).body))).toEqual({
      expected_proposal_digest: proposalDigest,
    });
    const preview = await screen.findByText("查看脱敏临时预览（最多 4 KiB）");
    expect(preview.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText(/代表调用已完成：单次执行/)).toBeVisible();
  });

  it("defaults the publish subset to only the representative-called read tool", async () => {
    let decisionBody: Record<string, unknown> | undefined;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/items/${itemId}/decision`)) {
        decisionBody = JSON.parse(String(init?.body));
        return json({ state: "approved" });
      }
      if (url.endsWith("/api/mcp/hub/review-runs")) {
        return json({ items: [reviewRun("awaiting_decision")] });
      }
      if (url.endsWith("/api/mcp/hub/contracts")) return json({ items: [] });
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <McpHubReviewWorkbench
        onClearSelection={() => undefined}
        onHubChanged={() => undefined}
        selected={[]}
        status={status}
      />,
    );

    expect(await screen.findByRole("checkbox", { name: "允许工具 search_primary" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "允许工具 search_secondary" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "允许工具 admin_delete" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "批准所选只读工具" }));

    await waitFor(() => expect(decisionBody).toBeDefined());
    expect(decisionBody).toMatchObject({
      allowed_tools: ["search_primary"],
      tool_effects: { search_primary: "read" },
    });
  });

  it("requires a second explicit action before revoking a contract", async () => {
    let revokeCalls = 0;
    const contractId = "hubct_" + "a".repeat(32);
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/review-runs")) return json({ items: [] });
      if (url.endsWith("/api/mcp/hub/contracts")) {
        return json({
          items: [{
            contract_id: contractId,
            server_name: "io.example/reviewable",
            version: "1.0.0",
            origin: "https://review.example.com",
            contract_fingerprint: "b".repeat(64),
            allowed_tools: ["search_primary"],
            revoked: false,
            collision: false,
          }],
        });
      }
      if (url.endsWith(`/api/mcp/hub/contracts/${contractId}/revoke`)) {
        revokeCalls += 1;
        return json({ contract_id: contractId, revoked: true });
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <McpHubReviewWorkbench
        onClearSelection={() => undefined}
        onHubChanged={() => undefined}
        selected={[]}
        status={{ ...status, active_run_id: null }}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "撤销并断开" }));
    expect(revokeCalls).toBe(0);
    expect(screen.getByText(/撤销后会立即断开对应 Hub 会话/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "保留契约" }));
    expect(screen.queryByRole("button", { name: "确认撤销" })).not.toBeInTheDocument();
  });
});
