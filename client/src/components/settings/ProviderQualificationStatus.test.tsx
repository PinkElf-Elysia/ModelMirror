import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProviderQualificationStatus } from "./ProviderQualificationStatus";

afterEach(cleanup);

describe("ProviderQualificationStatus", () => {
  it("does not invent qualification when an older server omits the field", () => {
    const { container } = render(<ProviderQualificationStatus />);
    expect(container.textContent).toBe("");
  });
  it("explains unknown history without exposing internal identity", () => {
    render(<ProviderQualificationStatus qualification={{ valid: false,
      reason_code: "provider_chat_certification_expiry_unknown", series_id: "private-series" }} />);
    expect(screen.getByText(/历史到期时间未知/)).toBeInTheDocument();
    expect(screen.queryByText(/private-series/)).not.toBeInTheDocument();
    expect(screen.queryByText("当前资格有效")).not.toBeInTheDocument();
  });
  it("shows the saved expiry and continuity, never calls certification", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<ProviderQualificationStatus qualification={{ valid: true,
      reason_code: "qualified", expires_at: "2026-11-01T00:00:00Z",
      renewal_reason: "provider_qualification_renewed" }} />);
    expect(screen.getByText("当前资格有效")).toBeInTheDocument();
    expect(screen.getByText(/同配置按时续期/)).toBeInTheDocument();
    expect(document.querySelector("time")?.getAttribute("datetime")).toBe("2026-11-01T00:00:00Z");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
  it.each([
    ["provider_chat_certification_expired", "资格已过期"],
    ["provider_chat_certification_invalidated", "资格已失效"],
    ["provider_chat_certification_not_passed", "本次认证未形成有效资格"],
  ])("keeps %s distinct", (reason_code, text) => {
    render(<ProviderQualificationStatus qualification={{ valid: false, reason_code, expires_at: "invalid" }} />);
    expect(screen.getByText(new RegExp(text))).toBeInTheDocument();
    expect(document.querySelector("time")).toBeNull();
  });
});
