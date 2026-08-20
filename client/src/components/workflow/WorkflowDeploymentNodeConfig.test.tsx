import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { WorkflowNode, WorkflowNodeData } from "../../types/workflow";
import WorkflowDeploymentNodeConfig, {
  cronExpressionForUi,
  dateTimeLocalValue,
  durationParts,
  parseCronExpressionForUi,
} from "./WorkflowDeploymentNodeConfig";

function renderConfig(data: WorkflowNodeData, currentProjectId?: string) {
  const node = {
    id: "node",
    type: "workflowNode",
    position: { x: 0, y: 0 },
    data,
  } as WorkflowNode;
  const onChange = vi.fn();
  render(
    <WorkflowDeploymentNodeConfig
      contract={null}
      currentProjectId={currentProjectId}
      data={data}
      edges={[]}
      node={node}
      nodes={[node]}
      onChange={onChange}
    />,
  );
  return onChange;
}

describe("WorkflowDeploymentNodeConfig", () => {
  it("turns duration, date, and Cron values into non-technical controls", () => {
    expect(durationParts(3_600)).toEqual({ amount: 1, unit: "hours" });
    expect(dateTimeLocalValue("2026-08-20T09:30:00+08:00")).toBe("2026-08-20T09:30");
    const cron = parseCronExpressionForUi("0 9 * * 1");
    expect(cron).toMatchObject({ pattern: "weekly", hour: 9, weekday: 1 });
    expect(cronExpressionForUi({ ...cron, weekday: 5 })).toBe("0 9 * * 5");
  });

  it("offers common calendar rules instead of exposing Cron by default", () => {
    const onChange = renderConfig({
      kind: "scheduled_start",
      title: "定时启动",
      description: "",
      scheduleType: "cron",
      cronExpression: "*/5 * * * *",
      timezone: "UTC",
      eventVariable: "schedule_event",
    });

    expect(screen.getByLabelText("重复规则")).toHaveValue("minutes");
    expect(screen.queryByLabelText(/Cron 表达式/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("重复规则"), { target: { value: "daily" } });
    expect(onChange).toHaveBeenCalledWith({ cronExpression: "0 9 * * *" });
  });

  it("provides real HTTP entry controls and two global variable outputs", () => {
    const onChange = renderConfig({
      kind: "http_event_entry",
      title: "HTTP 事件入口",
      description: "",
      eventVariable: "http_event",
      bodyVariable: "request_body",
      acceptedContentType: "both",
      maxBodyBytes: 1_048_576,
    });

    expect(screen.getByLabelText("允许的正文格式")).toHaveValue("both");
    expect(screen.getByLabelText(/最大正文大小/)).toHaveValue("1048576");
    expect(screen.getByLabelText("完整事件变量")).toHaveValue("http_event");
    expect(screen.getByLabelText("请求正文变量")).toHaveValue("request_body");
    fireEvent.change(screen.getByLabelText("允许的正文格式"), { target: { value: "json" } });
    expect(onChange).toHaveBeenCalledWith({ acceptedContentType: "json" });
  });

  it("uses readable wait and reply choices", () => {
    const waitChange = renderConfig({
      kind: "suspend_wait",
      title: "挂起等待",
      description: "",
      waitMode: "duration",
      durationSeconds: 60,
      outputVariable: "resume_event",
    });
    expect(screen.getByLabelText("等待方式")).toHaveValue("duration");
    expect(screen.getByLabelText("时长数值")).toHaveValue(1);
    expect(screen.getByLabelText("时长单位")).toHaveValue("minutes");
    expect(waitChange).not.toHaveBeenCalled();
  });

  it("offers searchable failure sources and excludes the current project", async () => {
    const sourceId = `wf_${"a".repeat(32)}`;
    const currentId = `wf_${"b".repeat(32)}`;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            project_id: sourceId,
            title: "订单同步",
            active_version: 3,
            active_trigger_kind: "schedule",
            updated_at: 2,
          },
          {
            project_id: currentId,
            title: "当前处理器",
            active_version: null,
            active_trigger_kind: null,
            updated_at: 1,
          },
        ],
        total: 2,
        limit: 100,
        offset: 0,
      }),
    }));

    const onChange = renderConfig({
      kind: "failure_event_entry",
      title: "失败处置入口",
      description: "",
      sourceProjectIds: [sourceId],
      eventVariable: "failure_event",
    }, currentId);

    await waitFor(() => expect(screen.getByText("订单同步")).toBeInTheDocument());
    expect(screen.queryByText("当前处理器")).not.toBeInTheDocument();
    expect(screen.getByText("已启用 v3")).toBeInTheDocument();
    expect(screen.getByLabelText("失败事件变量")).toHaveValue("failure_event");
    fireEvent.click(screen.getByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith({ sourceProjectIds: [] });
    vi.unstubAllGlobals();
  });
});
