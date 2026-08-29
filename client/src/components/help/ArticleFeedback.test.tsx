import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ArticleFeedback } from "./ArticleFeedback";

function mockFetchJson(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

/** GET stats 返回 200，POST 上报返回指定状态（真实后端 POST 成功是 201）。 */
function mockFeedbackFetch(postStatus: number, statsBody: unknown) {
  return vi.fn().mockImplementation((url: string) => {
    if (url === "/api/help/feedback") {
      return Promise.resolve({
        ok: postStatus >= 200 && postStatus < 300,
        status: postStatus,
        json: async () => ({}),
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => statsBody,
    });
  });
}

describe("ArticleFeedback", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("asks for feedback on first render", () => {
    vi.stubGlobal("fetch", mockFetchJson(200, { slug: "test-article", total: 0, helpful: 0 }));
    render(<ArticleFeedback slug="test-article" articleVersion="v1" />);
    expect(screen.getByText("这篇对你有帮助吗？")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "有帮助" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "没帮助" })).toBeInTheDocument();
  });

  it("remembers a helpful choice, stops asking, and reports to backend", async () => {
    const fetchMock = mockFeedbackFetch(201, { slug: "test-article", total: 0, helpful: 0 });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ArticleFeedback slug="test-article" articleVersion="v1" />);
    await user.click(screen.getByRole("button", { name: "有帮助" }));
    // 本机记住，不再询问
    expect(screen.queryByText("这篇对你有帮助吗？")).not.toBeInTheDocument();
    expect(window.localStorage.getItem("help-feedback:test-article")).toBe("helpful");
    // 上报后端
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([url]) => url === "/api/help/feedback");
      expect(post).toBeTruthy();
      const body = JSON.parse((post as unknown as [string, { body: string }])[1].body);
      expect(body.slug).toBe("test-article");
      expect(body.value).toBe("helpful");
      expect(body.article_version).toBe("v1");
      expect(body.anonymous_id).toBeTruthy();
    });
    expect(screen.getByText(/你的意见已发送给团队/)).toBeInTheDocument();
  });

  it("remembers a not-helpful choice", async () => {
    vi.stubGlobal("fetch", mockFeedbackFetch(201, { slug: "test-article", total: 0, helpful: 0 }));
    const user = userEvent.setup();
    render(<ArticleFeedback slug="test-article" articleVersion="v1" />);
    await user.click(screen.getByRole("button", { name: "没帮助" }));
    expect(window.localStorage.getItem("help-feedback:test-article")).toBe("not-helpful");
    expect(await screen.findByText(/你的意见已发送给团队/)).toBeInTheDocument();
  });

  it("shows stats label when data exists", async () => {
    vi.stubGlobal("fetch", mockFetchJson(200, { slug: "test-article", total: 3, helpful: 2 }));
    render(<ArticleFeedback slug="test-article" articleVersion="v1" />);
    expect(await screen.findByText("已收到 3 人评价，2 人认为有帮助")).toBeInTheDocument();
  });

  it("handles 409 duplicate silently", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ total: 0, helpful: 0 }) })
      .mockResolvedValueOnce({ ok: false, status: 409, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ArticleFeedback slug="test-article" articleVersion="v1" />);
    await user.click(screen.getByRole("button", { name: "有帮助" }));
    await waitFor(() => {
      expect(screen.getByText(/你的选择已记录/)).toBeInTheDocument();
    });
  });

  it("degrades gracefully when offline", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ total: 0, helpful: 0 }) })
      .mockRejectedValueOnce(new Error("network"));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ArticleFeedback slug="test-article" articleVersion="v1" />);
    await user.click(screen.getByRole("button", { name: "有帮助" }));
    expect(await screen.findByText(/本次提交未送达/)).toBeInTheDocument();
  });

  it("keeps feedback separate per article", async () => {
    vi.stubGlobal("fetch", mockFetchJson(200, { slug: "article-a", total: 0, helpful: 0 }));
    const user = userEvent.setup();
    render(<ArticleFeedback slug="article-a" articleVersion="v1" />);
    await user.click(screen.getByRole("button", { name: "没帮助" }));
    expect(window.localStorage.getItem("help-feedback:article-b")).toBeNull();
  });

  it("resets state when switching from article A to article B via key change", async () => {
    vi.stubGlobal("fetch", mockFetchJson(200, { slug: "article-a", total: 0, helpful: 0 }));
    const user = userEvent.setup();
    const { rerender } = render(<ArticleFeedback key="article-a" slug="article-a" articleVersion="v1" />);
    await user.click(screen.getByRole("button", { name: "有帮助" }));
    expect(screen.queryByText("这篇对你有帮助吗？")).not.toBeInTheDocument();

    rerender(<ArticleFeedback key="article-b" slug="article-b" articleVersion="v1" />);
    expect(screen.getByText("这篇对你有帮助吗？")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "有帮助" })).toBeInTheDocument();
  });
});
