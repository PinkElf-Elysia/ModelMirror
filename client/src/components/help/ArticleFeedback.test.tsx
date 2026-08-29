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
    expect(screen.getByText(/感谢反馈/)).toBeInTheDocument();
    // 持久化到 localStorage，重新渲染不再询问
    expect(window.localStorage.getItem("help-feedback:test-article")).toBe("helpful");
  });

  it("remembers a not-helpful choice", async () => {
    const user = userEvent.setup();
    render(<ArticleFeedback slug="test-article" />);
    await user.click(screen.getByRole("button", { name: "没帮助" }));
    expect(window.localStorage.getItem("help-feedback:test-article")).toBe("not-helpful");
    expect(screen.getByText(/感谢反馈/)).toBeInTheDocument();
  });

  it("does not ask again when already answered in a previous visit", () => {
    window.localStorage.setItem("help-feedback:seen-before", "helpful");
    render(<ArticleFeedback slug="seen-before" />);
    expect(screen.queryByText("这篇对你有帮助吗？")).not.toBeInTheDocument();
    expect(screen.getByText(/感谢反馈/)).toBeInTheDocument();
  });

  it("keeps feedback separate per article", async () => {
    const user = userEvent.setup();
    render(<ArticleFeedback slug="article-a" />);
    await user.click(screen.getByRole("button", { name: "没帮助" }));
    expect(window.localStorage.getItem("help-feedback:article-b")).toBeNull();
  });
});
