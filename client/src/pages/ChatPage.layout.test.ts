import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AUTO_ROUTING_GUIDANCE,
  CHAT_COMPOSER_COLUMN_CLASSES,
  CHAT_MESSAGE_COLUMN_CLASSES,
  CHAT_SHELL_HEADER_CLASSES,
  ChatRouteReceiptCard,
  ProviderChatCanaryControl,
  directAudioNativeOutputConflictReason,
  requiresManagedChatAudioShapeSeparation,
  shouldShowBatchServingSettings,
  skillActivationContentUrl,
} from "./ChatPage";

afterEach(() => {
  vi.unstubAllGlobals();
});

const BREAKPOINTS: Record<string, number> = {
  md: 768,
  lg: 1024,
};

function activePositionClasses(classNames: string, viewportWidth: number) {
  return classNames.split(/\s+/).flatMap((token) => {
    const [prefix, className] = token.includes(":")
      ? token.split(":", 2)
      : [null, token];
    if (!prefix) return [className];
    return viewportWidth >= BREAKPOINTS[prefix] ? [className] : [];
  });
}

describe("ChatPage conversation-first shell", () => {
  it("keeps direct audio input mutually exclusive with native audio output", () => {
    expect(
      directAudioNativeOutputConflictReason(true, false),
    ).toBeUndefined();
    expect(
      directAudioNativeOutputConflictReason(false, true),
    ).toBeUndefined();
    expect(directAudioNativeOutputConflictReason(true, true)).toContain(
      "暂不同时生成原生语音回答",
    );
    expect(
      requiresManagedChatAudioShapeSeparation([
        { feature_enabled: false, status: "managed_required" },
        { feature_enabled: true, status: "legacy" },
      ]),
    ).toBe(false);
    expect(
      requiresManagedChatAudioShapeSeparation([
        { feature_enabled: true, status: "degraded_required" },
      ]),
    ).toBe(true);
  });

  it("renders managed Chat Audio receipts without exposing internal routing details", () => {
    render(
      createElement(ChatRouteReceiptCard, {
        receipt: {
          contract_version: "modelmirror-provider-workload-routing-v1",
          entry_id: "chat_audio_output",
          routing_mode: "managed_required",
          run_reference: "run-managed-audio",
          status: "passed",
          call_count: 1,
          reason_codes: [],
          calls: [
            {
              call_sequence: 1,
              model_id: "provider/audio-model",
              actual_model: "provider/audio-model",
              dispatched: true,
              status: "passed",
            },
          ],
          connection_id: "must-not-render",
          base_url: "https://must-not-render.example",
        } as never,
      }),
    );

    expect(
      screen.getByText("Chat 音频输出控制面：已纳管 · 1 次 Provider 调用"),
    ).toBeVisible();
    expect(screen.getByText(/provider\/audio-model/)).toBeVisible();
    expect(screen.queryByText(/must-not-render/)).not.toBeInTheDocument();
    expect(screen.queryByText("成本暂不可用")).not.toBeInTheDocument();
  });

  it("preserves the legacy Chat route receipt card", () => {
    render(
      createElement(ChatRouteReceiptCard, {
        receipt: {
          requested_model: "provider/text-model",
          actual_model: "provider/text-model",
          provider: "legacy-provider",
          cost_kind: "unavailable",
          request_id: "legacy-request",
        },
      }),
    );

    expect(screen.getByText("路由回执")).toBeVisible();
    expect(screen.getByText("legacy-provider")).toBeVisible();
    expect(screen.getByText("成本暂不可用")).toBeVisible();
    expect(screen.queryByText(/已纳管/)).not.toBeInTheDocument();
  });

  it("keeps one compact header and bounded message/composer columns at every viewport", () => {
    expect(activePositionClasses(CHAT_SHELL_HEADER_CLASSES, 390)).toEqual(
      expect.arrayContaining(["sticky", "top-0", "h-16"]),
    );
    expect(CHAT_MESSAGE_COLUMN_CLASSES).toContain("max-w-[920px]");
    expect(CHAT_COMPOSER_COLUMN_CLASSES).toContain("max-w-[1000px]");
    expect(CHAT_SHELL_HEADER_CLASSES).not.toContain("top-24");
  });

  it("requests Skill content through the server activation gate", () => {
    expect(skillActivationContentUrl("skill id/with spaces")).toBe(
      "/api/skills/skill%20id%2Fwith%20spaces/content?purpose=activate",
    );
  });

  it("keeps Auto routing guidance and excludes inherited batch settings", () => {
    expect(AUTO_ROUTING_GUIDANCE).toContain("可在“设置”中更改路由设置。");
    expect(shouldShowBatchServingSettings(true, true)).toBe(false);
    expect(shouldShowBatchServingSettings(false, true)).toBe(true);
    expect(shouldShowBatchServingSettings(false, false)).toBe(false);
  });

  it("keeps manual newAPI canary in page memory and reconfirms after model changes", async () => {
    let sequence = 0;
    vi.stubGlobal("crypto", {
      randomUUID: () => `page-session-${++sequence}`,
    });
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            contract_version: "modelmirror-provider-chat-canary-v1",
            feature_enabled: true,
            available: true,
            gateway: "newapi_canary",
            model_id: "provider/model",
            reason_code: "available",
            consent_revision: "provider-chat-canary-consent-v1",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onChange = vi.fn();
    const { rerender } = render(
      createElement(ProviderChatCanaryControl, {
        blockedReason: "",
        disabled: false,
        modelId: "provider/model-a",
        onChange,
      }),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "开启试运行" }),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "失败后不会自动重放到第二个 Provider",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "确认当前会话" }));
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith({
        enabled: true,
        sessionId: expect.stringContaining("page-session-"),
      }),
    );
    const firstSession = onChange.mock.calls.at(-1)?.[0].sessionId;

    rerender(
      createElement(ProviderChatCanaryControl, {
        blockedReason: "",
        disabled: false,
        modelId: "provider/model-b",
        onChange,
      }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "开启试运行" }),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认当前会话" }));
    const secondSession = onChange.mock.calls.at(-1)?.[0].sessionId;
    expect(secondSession).not.toBe(firstSession);

    rerender(
      createElement(ProviderChatCanaryControl, {
        blockedReason: "MCP 工具已启用",
        disabled: false,
        modelId: "provider/model-b",
        onChange,
      }),
    );
    await screen.findByText("已关闭 newAPI 试运行：MCP 工具已启用");
    expect(onChange).toHaveBeenLastCalledWith({
      enabled: false,
      sessionId: secondSession,
    });
  });
});
