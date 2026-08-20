import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ProviderAdminGate from "./ProviderAdminGate";

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

describe("ProviderAdminGate", () => {
  it("does not misreport a session fetch failure as unconfigured", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network unavailable"));
    render(
      <ProviderAdminGate>{() => <div>protected controls</div>}</ProviderAdminGate>,
    );
    expect(await screen.findByText("无法读取 Provider 管理会话")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("network unavailable");
    expect(screen.queryByText("Provider 管理面尚未配置")).toBeNull();
  });

  it("keeps management locked when pairing is not configured", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({ configured: false, authenticated: false }),
    );
    render(
      <ProviderAdminGate>{() => <div>protected controls</div>}</ProviderAdminGate>,
    );
    expect(await screen.findByText("Provider 管理面尚未配置")).toBeVisible();
    expect(screen.queryByText("protected controls")).toBeNull();
  });

  it("submits the secret once and exposes only the csrf session to children", async () => {
    const requests: Array<{ url: string; body?: string }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      requests.push({ url: String(input), body: init?.body as string | undefined });
      if (!init?.method) {
        return json({ configured: true, authenticated: false });
      }
      return json({
        configured: true,
        authenticated: true,
        expires_at: 12345,
        csrf_token: "csrf-only",
      });
    });
    render(
      <ProviderAdminGate>
        {({ csrfToken }) => <div>unlocked {csrfToken}</div>}
      </ProviderAdminGate>,
    );
    const input = await screen.findByPlaceholderText("输入管理员配对密钥");
    fireEvent.change(input, { target: { value: "one-time-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "开始管理" }));
    await screen.findByText("unlocked csrf-only");
    expect(requests.at(-1)?.body).toContain("one-time-secret");
    expect(screen.queryByPlaceholderText("输入管理员配对密钥")).toBeNull();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("shows the server retry window after pairing rate limiting", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ configured: true, authenticated: false }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: { code: "rate_limited" } }), {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "245" },
        }),
      );
    render(
      <ProviderAdminGate>{() => <div>protected controls</div>}</ProviderAdminGate>,
    );
    const input = await screen.findByPlaceholderText("输入管理员配对密钥");
    fireEvent.change(input, { target: { value: "wrong-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "开始管理" }));
    expect(
      await screen.findByText("配对尝试过多，请在 245 秒后重试。"),
    ).toBeVisible();
  });
});
