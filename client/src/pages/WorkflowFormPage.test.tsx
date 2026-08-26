import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkflowFormPage from "./WorkflowFormPage";

const formId = `form_${"a".repeat(32)}`;
const accessKey = "mmform_test_access_key";

const manifest = {
  formTitle: "内部需求登记",
  formDescription: "请填写本次需求。",
  submitLabel: "提交登记",
  privacyNotice: "内容只用于本次处理。",
  successTitle: "已收到",
  successMessage: "可以关闭页面。",
  theme: "light",
  fields: [
    {
      id: "field_name",
      label: "姓名",
      helpText: "填写联系人姓名",
      placeholder: "示例用户",
      type: "short_text",
      required: true,
      options: [],
    },
    {
      id: "field_amount",
      label: "预算",
      helpText: "",
      placeholder: "",
      type: "number",
      required: false,
      options: [],
    },
    {
      id: "field_consent",
      label: "确认",
      helpText: "",
      placeholder: "我确认以上信息准确",
      type: "boolean",
      required: true,
      options: [],
    },
  ],
  submissionToken: "signed-token",
  expiresInSeconds: 900,
};

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/forms/${formId}`]}>
      <Routes>
        <Route element={<WorkflowFormPage />} path="/forms/:formId" />
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorkflowFormPage", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    window.history.replaceState({}, "", `/forms/${formId}#access=${accessKey}`);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("removes the fragment and submits only to same-origin APIs without cookies", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(manifest))
      .mockResolvedValueOnce(response({ status: "accepted" }, 202));
    const user = userEvent.setup();

    renderPage();

    expect(window.location.hash).toBe("");
    expect(window.sessionStorage.getItem(`modelmirror-workflow-form:${formId}:access`)).toBe(accessKey);
    expect(window.localStorage.length).toBe(0);
    expect(await screen.findByRole("heading", { name: "内部需求登记" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      `/api/workflow-forms/${formId}/manifest`,
      expect.objectContaining({ credentials: "omit", cache: "no-store" }),
    );

    await user.type(screen.getByLabelText(/姓名/), "示例用户");
    await user.click(screen.getByLabelText(/我确认以上信息准确/));
    await user.click(screen.getByRole("button", { name: "提交登记" }));

    await screen.findByRole("heading", { name: "已收到" });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `/api/workflow-forms/${formId}/submissions`,
      expect.objectContaining({
        credentials: "omit",
        method: "POST",
        cache: "no-store",
      }),
    );
    const submission = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
    expect(submission).toEqual({
      submissionToken: "signed-token",
      values: { field_name: "示例用户", field_amount: null, field_consent: true },
    });
    expect(fetchMock.mock.calls.every(([url]) => String(url).startsWith("/api/workflow-forms/"))).toBe(true);
    expect(document.querySelectorAll("img, iframe, script[src], link[href]")).toHaveLength(0);
  });

  it("forgets a rejected capability key and exposes only a generic unavailable state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response({ detail: "hidden" }, 404));

    renderPage();

    expect(await screen.findByRole("heading", { name: "表单暂时不可用" })).toBeInTheDocument();
    expect(screen.queryByText("hidden")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(window.sessionStorage.getItem(`modelmirror-workflow-form:${formId}:access`)).toBeNull();
    });
  });

  it("keeps the capability key after a transient manifest failure and lets the user retry", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("network offline"))
      .mockResolvedValueOnce(response(manifest));
    const user = userEvent.setup();

    renderPage();

    expect(await screen.findByText("网络异常，暂时无法加载表单。请检查连接后重新加载。")).toBeInTheDocument();
    expect(window.sessionStorage.getItem(`modelmirror-workflow-form:${formId}:access`)).toBe(accessKey);
    await user.click(screen.getByRole("button", { name: "重新加载表单" }));

    expect(await screen.findByRole("heading", { name: "内部需求登记" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps working in the current page when session storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Storage disabled", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Storage disabled", "SecurityError");
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response(manifest));

    renderPage();

    expect(window.location.hash).toBe("");
    expect(await screen.findByRole("heading", { name: "内部需求登记" })).toBeInTheDocument();
  });

  it("focuses and describes a required multi-select group after validation fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response({
      ...manifest,
      fields: [
        {
          id: "field_topics",
          label: "关注方向",
          helpText: "至少选择一个方向",
          placeholder: "",
          type: "multi_select",
          required: true,
          options: [
            { id: "option_workflow", value: "workflow", label: "工作流" },
            { id: "option_agent", value: "agent", label: "智能体" },
          ],
        },
      ],
    }));
    const user = userEvent.setup();

    renderPage();
    await user.click(await screen.findByRole("button", { name: "提交登记" }));

    const group = screen.getByRole("group", { name: "关注方向" });
    expect(group).toHaveFocus();
    expect(group).toHaveAttribute("aria-invalid", "true");
    expect(group).toHaveAccessibleDescription("至少选择一个方向 请至少选择一项。");
  });

  it("refreshes an expired token without silently submitting the retained values", async () => {
    const refreshedManifest = { ...manifest, submissionToken: "refreshed-token" };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(manifest))
      .mockResolvedValueOnce(response({ detail: "hidden" }, 404))
      .mockResolvedValueOnce(response(refreshedManifest))
      .mockResolvedValueOnce(response({ status: "accepted" }, 202));
    const user = userEvent.setup();

    renderPage();
    await user.type(await screen.findByLabelText(/姓名/), "保留内容");
    await user.click(screen.getByLabelText(/我确认以上信息准确/));
    await user.click(screen.getByRole("button", { name: "提交登记" }));

    expect(await screen.findByText("提交时效已刷新。请确认内容后再次提交。")).toBeInTheDocument();
    expect(screen.getByLabelText(/姓名/)).toHaveValue("保留内容");
    expect(fetchMock).toHaveBeenCalledTimes(3);

    await user.click(screen.getByRole("button", { name: "提交登记" }));
    expect(await screen.findByRole("heading", { name: "已收到" })).toBeInTheDocument();
    const replay = JSON.parse(String(fetchMock.mock.calls[3][1]?.body));
    expect(replay.submissionToken).toBe("refreshed-token");
    expect(replay.values.field_name).toBe("保留内容");
  });

  it("keeps the key and entered values when token refresh is temporarily offline", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(manifest))
      .mockResolvedValueOnce(response({ detail: "hidden" }, 404))
      .mockRejectedValueOnce(new TypeError("network offline"));
    const user = userEvent.setup();

    renderPage();
    await user.type(await screen.findByLabelText(/姓名/), "保留内容");
    await user.click(screen.getByLabelText(/我确认以上信息准确/));
    await user.click(screen.getByRole("button", { name: "提交登记" }));

    expect(await screen.findByText("提交时效暂时无法刷新。请检查网络后再次提交。")).toBeInTheDocument();
    expect(screen.getByLabelText(/姓名/)).toHaveValue("保留内容");
    expect(window.sessionStorage.getItem(`modelmirror-workflow-form:${formId}:access`)).toBe(accessKey);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
