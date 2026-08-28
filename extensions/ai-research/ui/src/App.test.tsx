import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { App } from "./App";

const system = {
  status: "ready",
  checks: [
    { id: "controlLedger", status: "ready", required: true },
    { id: "worker", status: "ready", required: true },
    { id: "tracking", status: "ready", required: true },
    { id: "inspectView", status: "ready", required: false },
  ],
  checkedAt: "2026-08-24T00:00:00Z",
  literatureCapability: {
    status: "ready",
    serviceStatus: "ready",
    sessionStatus: "locked",
    profileStatus: "ready",
    modelBridgeStatus: "ready",
    username: null,
    scientificClaim: "none",
  },
};
const moduleInfo = {
  moduleId: "modelmirror-ai-research",
  moduleVersion: "0.3.0-v0.1",
  apiVersion: "v1",
  workerProtocolVersion: 1,
  fixtures: ["success", "task_error", "long_running_cancel"],
  runtimes: { localDeepResearch: "1.10.6" },
  capabilities: { fixtureExecution: true, literatureResearch: true },
  capabilityClaims: {
    fixtureExecution: { enabled: true, claimLevel: "harness_only", packStatus: "fixture_only" },
    literatureResearch: { enabled: true, scientificClaim: "none", acceptanceState: "pending_live_acceptance", workflowSource: "local_deep_research" },
  },
  links: { mlflow: "http://127.0.0.1:8791", inspectView: "http://127.0.0.1:8793", localDeepResearch: "http://127.0.0.1:8792" },
  limitations: ["literature workflow only; scientificClaim=none"],
};
const createBodies: string[] = [];

beforeEach(() => {
  createBodies.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (init?.method === "POST" && path.endsWith("/api/v1/runs")) {
      createBodies.push(String(init.body));
      return new Response(JSON.stringify({ detail: "模拟响应丢失" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      });
    }
    const body = path.includes("/api/v1/module")
      ? moduleInfo
      : path.includes("/system")
      ? system
      : path.includes("/summary")
        ? { total: 0, phases: { queued: 0, running: 0, terminal: 0 }, outcomes: { success: 0, task_error: 0, cancelled: 0, infrastructure_error: 0 }, evidenceStates: { pending: 0, synced: 0, failed: 0 }, updatedAt: null }
        : { items: [], nextCursor: null };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("overview presents the real literature path without a scientific claim", async () => {
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  expect(screen.getByRole("heading", { name: "继续你的文献研究" })).toBeInTheDocument();
  expect(screen.getByText(/scientificClaim=none/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "创建研究项目" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("工程夹具与证据框架")).toBeInTheDocument());
});

test("system separates fixture claims from pending literature acceptance", async () => {
  render(<MemoryRouter initialEntries={["/system"]}><App /></MemoryRouter>);
  expect(await screen.findByText("harness_only")).toBeInTheDocument();
  expect(screen.getByText("fixture_only")).toBeInTheDocument();
  expect(screen.getByText("Local Deep Research", { selector: "dd" })).toBeInTheDocument();
  expect(screen.getByText("功能已实现，真实环境验收待完成")).toBeInTheDocument();
  expect(screen.getByText("none，结果需人工复核")).toBeInTheDocument();
});

test("illegal run filter is presented as all", async () => {
  render(<MemoryRouter initialEntries={["/runs?phase=illegal"]}><App /></MemoryRouter>);
  expect(screen.getByLabelText("阶段")).toHaveValue("");
  await waitFor(() => expect(screen.getByText("没有匹配当前条件的运行")).toBeInTheDocument());
});

test("search input follows browser history URL changes", async () => {
  const router = createMemoryRouter([{ path: "*", element: <App /> }], {
    initialEntries: ["/runs?q=first", "/runs?q=second"],
    initialIndex: 1,
  });
  render(<RouterProvider router={router} />);
  expect(screen.getByLabelText("搜索")).toHaveValue("second");
  await router.navigate(-1);
  await waitFor(() => expect(screen.getByLabelText("搜索")).toHaveValue("first"));
});

test("run search has an explicit submit action and updates the URL", async () => {
  const user = userEvent.setup();
  const router = createMemoryRouter([{ path: "*", element: <App /> }], {
    initialEntries: ["/runs?phase=running"],
  });
  render(<RouterProvider router={router} />);

  await user.type(screen.getByLabelText("搜索"), "FixtureTaskError");
  await user.click(screen.getByRole("button", { name: "搜索" }));

  await waitFor(() => {
    expect(router.state.location.search).toContain("q=FixtureTaskError");
    expect(router.state.location.search).toContain("phase=running");
  });
});

test("cancel confirmation receives focus and Escape returns it", async () => {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const path = String(input);
    const body = path.endsWith("/api/v1/runs/ar0_active")
      ? {
          runId: "ar0_active",
          fixtureId: "inspect-smoke-v1",
          caseId: "long_running_cancel",
          tenantId: "local",
          projectId: "local",
          actorId: "local",
          phase: "running",
          outcome: null,
          inspectStatus: "started",
          cancelRequested: false,
          cancelApplied: false,
          evidenceState: "pending",
          errorType: null,
          errorMessage: null,
          replayVerified: false,
          mlflowRunId: null,
          createdAt: "2026-08-24T00:00:00Z",
          startedAt: "2026-08-24T00:00:01Z",
          cancelRequestedAt: null,
          cancelAppliedAt: null,
          terminalAt: null,
          evidenceSyncedAt: null,
          updatedAt: "2026-08-24T00:00:01Z",
        }
      : system;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/runs/ar0_active"]}><App /></MemoryRouter>);
  const trigger = await screen.findByRole("button", { name: "请求取消" });

  await user.click(trigger);

  const confirm = screen.getByRole("button", { name: "确认取消" });
  expect(confirm).toHaveFocus();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(trigger).toHaveFocus());
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

test("mobile navigation closes on Escape and returns focus to its toggle", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/runs"]}><App /></MemoryRouter>);
  const open = screen.getByRole("button", { name: "打开导航" });

  open.focus();
  await user.keyboard("{Enter}");
  expect(screen.getByRole("button", { name: "关闭导航" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  const runLink = screen.getByRole("link", { name: "工程夹具" });
  runLink.focus();
  await user.keyboard("{Escape}");

  const closed = screen.getByRole("button", { name: "打开导航" });
  await waitFor(() => expect(closed).toHaveFocus());
  expect(closed).toHaveAttribute("aria-expanded", "false");
});

test("a failed create retry reuses the same idempotency key", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/runs"]}><App /></MemoryRouter>);
  const create = screen.getByRole("button", { name: "创建并查看" });
  await waitFor(() => expect(create).toBeEnabled());
  await user.click(create);
  await screen.findByText("模拟响应丢失");
  await user.click(screen.getByRole("button", { name: "创建并查看" }));
  await waitFor(() => expect(createBodies).toHaveLength(2));
  expect(JSON.parse(createBodies[0]).idempotencyKey).toBe(
    JSON.parse(createBodies[1]).idempotencyKey,
  );
});
