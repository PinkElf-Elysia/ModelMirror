import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ProviderRouteReceiptSummary from "./ProviderRouteReceiptSummary";

describe("ProviderRouteReceiptSummary", () => {
  it("shows redacted per-segment evidence without internal connection details", () => {
    render(
      <ProviderRouteReceiptSummary
        title="DAG 控制面"
        receipts={[
          {
            contract_version: "modelmirror-provider-workload-routing-v1",
            entry_id: "expert_team_dag",
            routing_mode: "managed_required",
            run_reference: "run-1",
            status: "passed",
            call_count: 2,
            reason_codes: [],
            calls: [
              {
                call_sequence: 1,
                model_id: "provider/model",
                dispatched: true,
                status: "passed",
              },
            ],
            connection_id: "must-not-render",
            base_url: "https://must-not-render.example",
          } as never,
        ]}
      />,
    );

    expect(screen.getByText("DAG 控制面：已纳管 · 2 次 Provider 调用")).toBeVisible();
    expect(screen.getByText(/执行片段 1：通过 · 2 次 · provider\/model/)).toBeVisible();
    expect(screen.queryByText(/must-not-render/)).not.toBeInTheDocument();
  });
});
