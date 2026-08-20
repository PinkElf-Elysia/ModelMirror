import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SystemSettingsPage, { resolveNewApiConsoleUrl } from "./SystemSettingsPage";

vi.mock("../components/PageContainer", () => ({
  default: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("../components/settings/ModelServiceConnections", () => ({
  default: () => <div>connections</div>,
}));
vi.mock("../components/settings/MarbleConnectionSettings", () => ({
  default: () => <div>marble</div>,
}));
vi.mock("../components/settings/SmartRoutingSettings", () => ({
  default: () => <div>routing</div>,
}));
vi.mock("../components/settings/ProviderAdminGate", () => ({
  default: ({
    children,
  }: {
    children: (session: { csrfToken: string }) => ReactNode;
  }) => <>{children({ csrfToken: "test-csrf" })}</>,
}));

describe("SystemSettingsPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("accepts only safe external console URLs", () => {
    expect(resolveNewApiConsoleUrl(undefined)).toBeNull();
    expect(resolveNewApiConsoleUrl("javascript:alert(1)")).toBeNull();
    expect(resolveNewApiConsoleUrl("https://user:secret@example.com")).toBeNull();
    expect(resolveNewApiConsoleUrl("https://example.com/?token=secret")).toBeNull();
    expect(resolveNewApiConsoleUrl("https://console.example.com/admin")?.host).toBe(
      "console.example.com",
    );
  });

  it("loads a safe runtime management URL without embedding the console", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ newApiWebUrl: "http://127.0.0.1:3000" }),
      }),
    );

    render(<SystemSettingsPage />);
    const link = await screen.findByRole("link", { name: "在新窗口管理" });
    expect(link).toHaveAttribute("href", "http://127.0.0.1:3000/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("外部管理入口已配置")).toBeVisible();
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("shows an actionable runtime configuration instruction when unset", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ newApiWebUrl: "" }),
      }),
    );

    render(<SystemSettingsPage />);
    expect(await screen.findByText("外部管理入口未配置")).toBeVisible();
    expect(screen.getByText(/请在 client 服务环境中配置后重启前端容器/)).toBeVisible();
    expect(document.querySelector("iframe")).toBeNull();
    expect(screen.queryByRole("link", { name: "在新窗口管理" })).toBeNull();
  });
});
