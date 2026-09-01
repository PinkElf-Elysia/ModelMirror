import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WorkflowExecutionSummary } from "../../utils/workflowDeployments";
import WorkflowDeploymentRetryStatus, {
  workflowDeploymentRetryEvents,
} from "./WorkflowDeploymentRetryStatus";

const execution: WorkflowExecutionSummary = {
  execution_id: "wfx_test",
  project_id: "wf_test",
  version: 3,
  trigger_kind: "schedule",
  occurrence_key: "schedule:test",
  status: "waiting",
  wait_kind: "node_retry",
  resume_at: 1_780_000_000,
  trigger_summary: {
    retry_events: [
      {
        event: "node_retry_scheduled",
        node_id: "http-1",
        attempt: 2,
        max_attempts: 3,
        resume_at: 1_780_000_000,
        error_code: "HTTP_STATUS_503",
        classification: "transient",
      },
      {
        event: "node_retry_started",
        node_id: "http-1",
        attempt: 2,
        max_attempts: 3,
      },
    ],
  },
  created_at: 1,
  updated_at: 2,
};

describe("WorkflowDeploymentRetryStatus", () => {
  it("shows a localized wait state and bounded safe retry history", () => {
    render(<WorkflowDeploymentRetryStatus execution={execution} />);

    expect(screen.getByText("等待自动重试")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "部署重试历史" })).toHaveTextContent(
      "第 2/3 次尝试已排队",
    );
    expect(screen.getByRole("list", { name: "部署重试历史" })).toHaveTextContent(
      "第 2/3 次尝试已开始",
    );
    expect(screen.getByText("HTTP_STATUS_503")).toBeInTheDocument();
    expect(screen.getByText("临时故障")).toBeInTheDocument();
  });

  it("drops malformed or unsafe retry summaries instead of rendering them", () => {
    const unsafe: WorkflowExecutionSummary = {
      ...execution,
      trigger_summary: {
        retry_events: [
          {
            event: "node_retry_scheduled",
            node_id: "<img src=x onerror=alert(1)>",
            attempt: "2",
            max_attempts: 3,
            error_code: "secret response body",
          },
          {
            event: "node_retry_started",
            node_id: "http-1",
            attempt: true,
            max_attempts: 3,
          },
          { event: "unknown", node_id: "http-1", attempt: 1, max_attempts: 1 },
        ],
      },
    };

    expect(workflowDeploymentRetryEvents(unsafe)).toEqual([]);
  });

  it("does not render an invalid top-level resume timestamp", () => {
    const invalid: WorkflowExecutionSummary = {
      ...execution,
      wait_kind: null,
      resume_at: Number.POSITIVE_INFINITY,
      trigger_summary: {},
    };

    const { container, rerender } = render(
      <WorkflowDeploymentRetryStatus execution={invalid} />,
    );
    expect(container).toBeEmptyDOMElement();

    rerender(
      <WorkflowDeploymentRetryStatus
        execution={{ ...invalid, resume_at: "1780000000" as unknown as number }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
