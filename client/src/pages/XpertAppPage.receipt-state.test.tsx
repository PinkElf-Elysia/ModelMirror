import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import XpertAppPage from "./XpertAppPage";

const APP_SLUG = "receipt-state-app";
const TOKEN_KEY = `modelmirror-xpert-app:${APP_SLUG}:access`;
const scrollToDescriptor = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  "scrollTo",
);

function jsonResponse(payload: unknown) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
}

function streamResponse() {
  const receipt = {
    contract_version: "modelmirror-provider-workload-routing-v1",
    entry_id: "xpert_app",
    routing_mode: "managed_required",
    run_reference: "workrun-receipt-state",
    status: "passed",
    call_count: 1,
    reason_codes: [],
    calls: [{
      call_sequence: 1,
      model_id: "provider/test-model",
      actual_model: "provider/test-model",
      dispatched: true,
      status: "passed",
      error_code: null,
      prompt_tokens: 1,
      completion_tokens: 1,
      total_tokens: 2,
    }],
  };
  const payload = {
    choices: [{ delta: { content: "done" } }],
    modelmirror: { provider_route_receipts: receipt },
  };
  const body = `data: ${JSON.stringify(payload)}\n\ndata: [DONE]\n\n`;
  return Promise.resolve(new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  }));
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/apps/${APP_SLUG}`]}>
      <Routes>
        <Route element={<XpertAppPage />} path="/apps/:appSlug" />
      </Routes>
    </MemoryRouter>,
  );
}

async function produceReceipt() {
  await screen.findByText("Receipt State App");
  fireEvent.change(screen.getByPlaceholderText("输入消息，Enter 发送"), {
    target: { value: "hello" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await screen.findByText("已纳管");
}

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });
  sessionStorage.setItem(TOKEN_KEY, "mmshare_test");
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === `/api/apps/${APP_SLUG}/manifest`) {
      return jsonResponse({
        object: "xpert.app",
        slug: APP_SLUG,
        name: "Receipt State App",
        description: "state test",
        starters: [],
        version: 1,
        deployment_revision: 1,
        visibility: "unlisted",
      });
    }
    if (
      String(input) === `/api/v1/xpert-apps/${APP_SLUG}/chat/completions`
      && init?.method === "POST"
    ) {
      return streamResponse();
    }
    throw new Error(`Unexpected fetch: ${String(input)}`);
  }));
});

afterEach(() => {
  if (scrollToDescriptor) {
    Object.defineProperty(HTMLElement.prototype, "scrollTo", scrollToDescriptor);
  } else {
    delete (HTMLElement.prototype as { scrollTo?: unknown }).scrollTo;
  }
  sessionStorage.clear();
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("XpertAppPage provider receipt state", () => {
  it("clears the previous receipt with the conversation", async () => {
    renderPage();
    await produceReceipt();

    fireEvent.click(screen.getByRole("button", { name: "清空对话" }));

    expect(screen.queryByText("已纳管")).not.toBeInTheDocument();
    expect(screen.getByText("从一个问题开始")).toBeInTheDocument();
  });

  it("does not restore a receipt after leaving and reopening the share", async () => {
    renderPage();
    await produceReceipt();

    fireEvent.click(screen.getByRole("button", { name: "退出分享" }));
    expect(await screen.findByText("打开未列出的 Agent App")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("mmshare_..."), {
      target: { value: "mmshare_test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "验证访问" }));

    await waitFor(() => expect(screen.getByText("Receipt State App")).toBeInTheDocument());
    expect(screen.queryByText("已纳管")).not.toBeInTheDocument();
  });
});
