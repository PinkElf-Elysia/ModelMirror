import { render, screen } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import type { ComponentProps } from "react";
import { describe, expect, it } from "vitest";

import WorkflowNodeCard from "./WorkflowNodeCard";


function renderCard(
  failureAction: "stop" | "error_output",
  contractVersion = 2,
  kind: "http_request" | "knowledge_retrieval" = "http_request",
  runStatus?: "retry_waiting",
) {
  const props = {
    id: "http-1",
    type: "workflowNode",
    data: {
      kind,
      title: "安全 HTTP 请求",
      description: "test",
      contractVersion,
      outputVariable: "http_response",
      failureAction,
      errorVariable: failureAction === "error_output" ? "node_error" : undefined,
      runStatus,
    },
    selected: false,
    dragging: false,
    draggable: true,
    selectable: true,
    connectable: true,
    deletable: true,
    positionAbsoluteX: 0,
    positionAbsoluteY: 0,
    zIndex: 0,
    isConnectable: true,
  } as unknown as ComponentProps<typeof WorkflowNodeCard>;
  render(
    <ReactFlowProvider>
      <WorkflowNodeCard {...props} />
    </ReactFlowProvider>,
  );
}


describe("WorkflowNodeCard structured error output", () => {
  it("shows stable text-labelled success and error handles when enabled", () => {
    renderCard("error_output");

    expect(screen.getByLabelText("连接成功出口")).toBeInTheDocument();
    expect(screen.getByLabelText("连接错误出口")).toHaveAttribute("data-handleid", "error");
    expect(screen.getByText("成功")).toBeInTheDocument();
    expect(screen.getByText("错误")).toBeInTheDocument();
  });

  it("does not leave an error handle behind in stop mode", () => {
    renderCard("stop");
    expect(screen.queryByLabelText("连接错误出口")).not.toBeInTheDocument();
  });

  it.each(["http_request", "knowledge_retrieval"] as const)(
    "does not expose a V2 error handle on legacy %s nodes",
    (kind) => {
      renderCard("error_output", 1, kind);
      expect(screen.queryByLabelText("连接错误出口")).not.toBeInTheDocument();
    },
  );

  it("shows a non-animated retry waiting marker without adding another handle", () => {
    renderCard("error_output", 2, "http_request", "retry_waiting");

    expect(screen.getByLabelText("等待重试")).toHaveAttribute("title", "等待下一次重试");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByLabelText("连接错误出口")).toBeInTheDocument();
  });
});
