import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ProviderRouteReceiptSummary from "./ProviderRouteReceiptSummary";

describe("ProviderRouteReceiptSummary", () => {
  it("shows only redacted managed call evidence", () => {
    render(
      <ProviderRouteReceiptSummary
        receipt={{
          contract_version: "modelmirror-provider-workload-routing-v1",
          entry_id: "meta_agent",
          routing_mode: "managed_required",
          run_reference: "workrun_1234567890abcdef",
          status: "passed",
          call_count: 2,
          reason_codes: [],
          calls: [
            {
              call_sequence: 1,
              model_id: "provider/meta-model",
              actual_model: "provider/meta-model",
              status: "passed",
              total_tokens: 12,
            },
            {
              call_sequence: 2,
              model_id: "provider/meta-model",
              actual_model: "provider/meta-model",
              status: "passed",
              total_tokens: 18,
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("已纳管")).toBeInTheDocument();
    expect(screen.getByText("2 次模型调用")).toBeInTheDocument();
    fireEvent.click(screen.getByText("2 次模型调用"));
    expect(screen.getByText("调用 1")).toBeInTheDocument();
    expect(screen.getByText("18 tokens")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("connection_id");
    expect(document.body.textContent).not.toContain("api_key");
    expect(document.body.textContent).not.toContain("private prompt");
  });

  it("does not describe a blocked preflight as a model call", () => {
    render(
      <ProviderRouteReceiptSummary
        receipt={{
          contract_version: "modelmirror-provider-workload-routing-v1",
          entry_id: "meta_agent",
          routing_mode: "managed_required",
          run_reference: "workrun_preflight_only",
          status: "failed",
          call_count: 0,
          reason_codes: ["provider_workload_binding_missing"],
          calls: [
            {
              call_sequence: 1,
              model_id: "provider/unbound-model",
              dispatched: false,
              status: "failed",
              error_code: "provider_workload_binding_missing",
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("0 次模型调用")).toBeInTheDocument();
    expect(screen.getByText("0 个精确模型")).toBeInTheDocument();
    fireEvent.click(screen.getByText("0 次模型调用"));
    expect(screen.getByText("预检 1")).toBeInTheDocument();
    expect(screen.getByText("未派发")).toBeInTheDocument();
    expect(screen.queryByText("调用 1")).not.toBeInTheDocument();
  });
});
