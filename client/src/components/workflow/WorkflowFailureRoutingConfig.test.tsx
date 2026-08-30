import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WorkflowEdge, WorkflowNode, WorkflowNodeData } from "../../types/workflow";
import WorkflowFailureRoutingConfig from "./WorkflowFailureRoutingConfig";


function Harness({
  edges = [],
  initial,
}: {
  edges?: WorkflowEdge[];
  initial: WorkflowNodeData;
}) {
  const [data, setData] = useState(initial);
  const node: WorkflowNode = {
    id: "http-1",
    type: "workflowNode",
    position: { x: 0, y: 0 },
    data,
  };
  return (
    <WorkflowFailureRoutingConfig
      data={data}
      edges={edges}
      node={node}
      nodes={[node]}
      onChange={(patch) => setData((current) => ({ ...current, ...patch }))}
    />
  );
}


const base: WorkflowNodeData = {
  kind: "http_request",
  title: "安全 HTTP 请求",
  description: "test",
  contractVersion: 2,
  outputVariable: "http_response",
  failureAction: "stop",
};


describe("WorkflowFailureRoutingConfig", () => {
  it("keeps failure handling folded and creates a safe default error variable", () => {
    render(<Harness initial={base} />);

    const details = screen.getByText("失败处理").closest("details");
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("失败处理"));
    expect(details).toHaveAttribute("open");
    fireEvent.change(screen.getByLabelText("发生运行故障时"), {
      target: { value: "error_output" },
    });

    expect(screen.getByLabelText("错误结果变量")).toHaveValue("node_error");
    expect(screen.getByText(/红色出口连接一个处理步骤/)).toBeInTheDocument();
    expect(screen.getByText(/凭据、权限、安全策略/)).toBeInTheDocument();
  });

  it("does not disable an error output while its edge still exists", () => {
    const edges: WorkflowEdge[] = [{
      id: "error-edge",
      source: "http-1",
      sourceHandle: "error",
      target: "handler",
    }];
    render(
      <Harness
        edges={edges}
        initial={{ ...base, failureAction: "error_output", errorVariable: "node_error" }}
      />,
    );

    fireEvent.click(screen.getByText("失败处理"));
    expect(screen.getByRole("option", { name: "终止工作流" })).toBeDisabled();
    expect(screen.getByLabelText("发生运行故障时")).toHaveValue("error_output");
    expect(screen.getByRole("status")).toHaveTextContent(/请先删除红色错误连线/);
  });
});
