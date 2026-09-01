import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WorkflowEdge, WorkflowNode, WorkflowNodeData } from "../../types/workflow";
import WorkflowFailureRoutingConfig, {
  type WorkflowRetryAvailability,
} from "./WorkflowFailureRoutingConfig";


function Harness({
  edges = [],
  initial,
  retryAvailability = {
    registryStatus: "ready",
    resourceStatus: "ready",
    featureEnabled: true,
    eligible: true,
  },
}: {
  edges?: WorkflowEdge[];
  initial: WorkflowNodeData;
  retryAvailability?: WorkflowRetryAvailability;
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
      retryAvailability={retryAvailability}
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

  it("configures bounded transient retry before the existing failure action", () => {
    render(<Harness initial={base} />);

    fireEvent.click(screen.getByText("失败处理"));
    expect(screen.getByLabelText("自动重试")).toHaveValue("none");
    fireEvent.change(screen.getByLabelText("自动重试"), {
      target: { value: "transient" },
    });

    expect(screen.getByLabelText("最多尝试次数（含首次）")).toHaveValue("2");
    expect(screen.getByText(/第 2 次前等待 5 秒/)).toBeInTheDocument();
    expect(screen.getByText(/不会保存前序节点生成的结果/)).toBeInTheDocument();
    expect(screen.getByText(/发布前会检查这项约束/)).toBeInTheDocument();
    expect(screen.getByText(/408、429、502、503、504/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("最多尝试次数（含首次）"), {
      target: { value: "3" },
    });
    expect(screen.getByText(/临时故障最多尝试 3 次/)).toBeInTheDocument();
    expect(screen.getByLabelText("重试仍未成功时")).toHaveValue("stop");
  });

  it("keeps an ineligible retry configuration visible instead of silently clearing it", () => {
    render(
      <Harness
        initial={{
          ...base,
          method: "POST",
          retryMode: "transient",
          maxAttempts: 3,
        }}
      />,
    );

    expect(screen.getByText(/自动重试配置需修正/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("失败处理"));
    expect(screen.getByLabelText("自动重试")).toHaveValue("transient");
    expect(screen.getByRole("alert")).toHaveTextContent(/只允许固定 GET 请求/);
    expect(screen.getByRole("alert")).toHaveTextContent(/不会静默关闭/);
  });

  it("does not show retry qualification blockers while retry is disabled", () => {
    render(
      <Harness
        initial={{ ...base, method: "POST", retryMode: "none" }}
        retryAvailability={{
          registryStatus: "error",
          featureEnabled: false,
          eligible: false,
          ineligibleReason: "不应在关闭状态显示。",
        }}
      />,
    );

    expect(screen.getByText("节点失败时终止工作流。")).toBeInTheDocument();
    expect(screen.queryByText(/自动重试配置需修正/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("失败处理"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("explains feature-gate and resource qualification without blocking draft editing", () => {
    render(
      <Harness
        initial={{
          ...base,
          kind: "knowledge_retrieval",
          retryMode: "transient",
        }}
        retryAvailability={{
          registryStatus: "ready",
          resourceStatus: "ready",
          featureEnabled: false,
          featureDisabledReason: "WORKFLOW_NODE_RETRIES_ENABLED=false",
          eligible: false,
          ineligibleReason: "活动版本使用远程重排。",
        }}
      />,
    );

    expect(screen.getByText(/自动重试当前不可运行/)).toHaveTextContent(
      /WORKFLOW_NODE_RETRIES_ENABLED=false/,
    );
    fireEvent.click(screen.getByText("失败处理"));
    expect(screen.getByText(/当前环境未开启节点重试/)).toBeInTheDocument();
    expect(
      screen.getAllByText(/WORKFLOW_NODE_RETRIES_ENABLED=false/).length,
    ).toBeGreaterThan(0);
    expect(screen.getByRole("alert")).toHaveTextContent(/活动版本使用远程重排/);
  });

  it("keeps retry qualification unknown when registry metadata cannot be loaded", () => {
    render(
      <Harness
        initial={{ ...base, retryMode: "transient" }}
        retryAvailability={{ registryStatus: "error" }}
      />,
    );

    expect(screen.getByText(/自动重试资格尚未确认/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("失败处理"));
    expect(screen.getByRole("status")).toHaveTextContent(/节点目录暂不可用/);
    expect(screen.getByRole("status")).toHaveTextContent(/服务端阻止/);
  });

  it("keeps qualification unknown when a ready registry omits the feature flag", () => {
    render(
      <Harness
        initial={{ ...base, retryMode: "transient" }}
        retryAvailability={{ registryStatus: "ready" }}
      />,
    );

    expect(screen.getByText(/自动重试资格尚未确认/)).toBeInTheDocument();
  });

  it("keeps knowledge qualification unknown when the resource omits retry eligibility", () => {
    render(
      <Harness
        initial={{ ...base, kind: "knowledge_retrieval", retryMode: "transient" }}
        retryAvailability={{
          registryStatus: "ready",
          resourceStatus: "ready",
          featureEnabled: true,
        }}
      />,
    );

    expect(screen.getByText(/自动重试资格尚未确认/)).toBeInTheDocument();
  });

  it("clarifies that capture-all HTTP statuses are normal results", () => {
    render(
      <Harness
        initial={{
          ...base,
          retryMode: "transient",
          statusPolicy: "capture_all",
        }}
      />,
    );

    fireEvent.click(screen.getByText("失败处理"));
    expect(screen.getByText(/非 2xx 响应属于正常结果/)).toBeInTheDocument();
  });
});
