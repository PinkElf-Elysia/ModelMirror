import { describe, expect, it } from "vitest";
import { xpertAppProviderReceipt } from "./XpertAppPage";

describe("xpertAppProviderReceipt", () => {
  it("reads the additive sanitized receipt envelope", () => {
    const receipt = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "xpert_app",
      routing_mode: "managed_required",
      run_reference: "workrun-app",
      status: "passed",
      call_count: 1,
      reason_codes: [],
      calls: [],
    };

    expect(xpertAppProviderReceipt({
      modelmirror: { provider_route_receipts: receipt },
    })).toEqual(receipt);
    expect(xpertAppProviderReceipt({ choices: [] })).toBeNull();
  });
});
