import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import SystemSettingsPage, { resolveNewApiConsoleUrl } from "./SystemSettingsPage";

const adminState = vi.hoisted(() => ({ unlocked: true }));

vi.mock("../components/PageContainer", () => ({ default: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock("../components/settings/ModelServiceConnections", () => ({ default: () => <div>connections</div> }));
vi.mock("../components/settings/MarbleConnectionSettings", () => ({ default: () => <div>marble</div> }));
vi.mock("../components/settings/ProviderCatalogPanel", () => ({ default: () => <div>inventory</div> }));
vi.mock("../components/settings/ProviderControlPlaneOverview", () => ({ default: () => <div>overview panel</div> }));
vi.mock("../components/settings/ProviderWorkloadControlSettings", () => ({ default: ({ view }: { view: string }) => <div>workload {view}</div> }));
vi.mock("../components/settings/SmartRoutingSettings", () => ({ default: () => <div>routing</div> }));
vi.mock("../components/settings/ProviderAdminGate", () => ({ default: ({ children }: { children: (session: { csrfToken: string }) => ReactNode }) => adminState.unlocked ? <>{children({ csrfToken: "test-csrf" })}</> : <div>admin locked</div> }));

function renderPage(entry = "/settings") {
  return render(<MemoryRouter initialEntries={[entry]}><SystemSettingsPage /></MemoryRouter>);
}

describe("SystemSettingsPage", () => {
  afterEach(() => { vi.unstubAllGlobals(); adminState.unlocked = true; });

  it("accepts only safe external console URLs", () => {
    expect(resolveNewApiConsoleUrl(undefined)).toBeNull();
    expect(resolveNewApiConsoleUrl("javascript:alert(1)")).toBeNull();
    expect(resolveNewApiConsoleUrl("https://user:secret@example.com")).toBeNull();
    expect(resolveNewApiConsoleUrl("https://example.com/?token=secret")).toBeNull();
    expect(resolveNewApiConsoleUrl("https://console.example.com/admin")?.host).toBe("console.example.com");
  });

  it("uses URL-backed tabs while preserving every existing settings module", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ newApiWebUrl: "http://127.0.0.1:3000" }) }));
    renderPage();
    expect(screen.getByText("overview panel")).toBeVisible();
    expect(screen.getByText("marble")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /Provider 与 Catalog/ }));
    expect(screen.getByText("connections")).toBeVisible();
    expect(screen.getByText("inventory")).toBeVisible();
    expect(screen.getByText("workload certifications")).toBeVisible();
    const link = await screen.findByRole("link", { name: "在新窗口管理" });
    expect(link).toHaveAttribute("href", "http://127.0.0.1:3000/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(document.querySelector("iframe")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /路由与实验/ }));
    expect(screen.getByText("routing")).toBeVisible();
    expect(screen.getByText("marble")).toBeVisible();
  });

  it("opens the requested section and keeps Marble outside the admin content", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ newApiWebUrl: "" }) }));
    renderPage("/settings?section=routing");
    expect(screen.getByText("routing")).toBeVisible();
    expect(screen.getByText("marble")).toBeVisible();
    expect(screen.queryByText("connections")).toBeNull();
  });

  it("keeps Marble usable when Provider administration is locked", () => {
    adminState.unlocked = false;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ newApiWebUrl: "" }) }));
    renderPage("/settings?section=providers");
    expect(screen.getByText("admin locked")).toBeVisible();
    expect(screen.getByText("marble")).toBeVisible();
    expect(screen.queryByText("connections")).toBeNull();
  });
});
