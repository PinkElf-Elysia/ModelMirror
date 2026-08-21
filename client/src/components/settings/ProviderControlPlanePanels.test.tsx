import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ProviderCatalogPanel from "./ProviderCatalogPanel";
import ProviderControlPlaneOverview from "./ProviderControlPlaneOverview";

describe("Provider Control Plane panels", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders evidence categories without claiming default qualification", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/runtime/environment-summary") {
        return new Response(JSON.stringify({
          llm_gateway_configured: false,
          openrouter_configured: false,
          model_gateway_ready: false,
        }), { status: 200 });
      }
      return new Response(JSON.stringify({
        provider_count: 2,
        online_provider_count: 1,
        discovered_model_count: 3,
        stale_model_count: 1,
        operation_counts: [{ operation: "chat", total: 3, invocable: 1, stale: 1, blocked: 2 }],
        blocking_reason_codes: ["catalog_contains_stale_evidence"],
        default_qualification: "not_evaluated",
      }), { status: 200 });
    });
    render(<ProviderControlPlaneOverview />);
    expect(await screen.findByText("默认数据面资格未评估")).toBeVisible();
    expect(await screen.findByText("尚未配置")).toBeVisible();
    expect(screen.getByText(/受管 Provider 只形成控制面证据/)).toBeVisible();
    expect(screen.getByText(/1 可调用/)).toBeVisible();
    expect(screen.getByText("• 目录包含过期证据")).toBeVisible();
  });

  it("refreshes only the explicit Catalog endpoint and never Chat", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/router/connections") return new Response(JSON.stringify([{ id: "connection-1", name: "newAPI", kind: "newapi", enabled: true, health: "online", model_count: 0 }]), { status: 200 });
      if (url.startsWith("/api/router/catalog/offerings")) return new Response(JSON.stringify({ offerings: [] }), { status: 200 });
      if (url.endsWith("/catalog/refresh") && init?.method === "POST") return new Response(JSON.stringify({ model_count: 2, truncated: false }), { status: 200 });
      return new Response(null, { status: 404 });
    });
    render(<ProviderCatalogPanel csrfToken="csrf-test" />);
    fireEvent.click(await screen.findByRole("button", { name: "刷新目录" }));
    expect(await screen.findByText(/已发现 2 个模型/)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/router/connections/connection-1/catalog/refresh",
      expect.objectContaining({ method: "POST", headers: { "X-ModelMirror-CSRF": "csrf-test" } }),
    );
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/api/chat"))).toBe(false));
  });
});
