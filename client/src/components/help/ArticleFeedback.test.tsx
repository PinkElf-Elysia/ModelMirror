import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ArticleFeedback } from "./ArticleFeedback";

describe("ArticleFeedback", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("asks for feedback on first render", () => {
    render(<ArticleFeedback slug="test-article" />);
    expect(screen.getByText("这篇对你有帮助吗？")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "有帮助" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "没帮助" })).toBeInTheDocument();
  });

  it("remembers a helpful choice and stops asking", async () => {
    const user = userEvent.setup();
    render(<ArticleFeedback slug="test-article" />);
    await user.click(screen.getByRole("button", { name: "有帮助" }));
    expect(screen.queryByText("这篇对你有帮助吗？")).not.toBeInTheDocument();
    expect(screen.getByText(/仅保存在本机浏览器/)).toBeInTheDocument();
    // 持久化到 localStorage，重新渲染不再询问
    expect(window.localStorage.getItem("help-feedback:test-article")).toBe("helpful");
  });

  it("remembers a not-helpful choice", async () => {
    const user = userEvent.setup();
    render(<ArticleFeedback slug="test-article" />);
    await user.click(screen.getByRole("button", { name: "没帮助" }));
    expect(window.localStorage.getItem("help-feedback:test-article")).toBe("not-helpful");
    expect(screen.getByText(/仅保存在本机浏览器/)).toBeInTheDocument();
  });

  it("does not ask again when already answered in a previous visit", () => {
    window.localStorage.setItem("help-feedback:seen-before", "helpful");
    render(<ArticleFeedback slug="seen-before" />);
    expect(screen.queryByText("这篇对你有帮助吗？")).not.toBeInTheDocument();
    expect(screen.getByText(/仅保存在本机浏览器/)).toBeInTheDocument();
  });

  it("keeps feedback separate per article", async () => {
    const user = userEvent.setup();
    render(<ArticleFeedback slug="article-a" />);
    await user.click(screen.getByRole("button", { name: "没帮助" }));
    expect(window.localStorage.getItem("help-feedback:article-b")).toBeNull();
  });

  it("resets state when switching from article A to article B via key change", async () => {
    const user = userEvent.setup();
    // 模拟 HelpArticlePage 用 key={article.slug} 渲染：路由从 A 切到 B 时组件重建
    const { rerender } = render(<ArticleFeedback key="article-a" slug="article-a" />);
    await user.click(screen.getByRole("button", { name: "有帮助" }));
    expect(screen.queryByText("这篇对你有帮助吗？")).not.toBeInTheDocument();

    // 切换到文章 B（新 key，组件重建）——必须重新询问，不复用 A 的状态
    rerender(<ArticleFeedback key="article-b" slug="article-b" />);
    expect(screen.getByText("这篇对你有帮助吗？")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "有帮助" })).toBeInTheDocument();
  });
});
