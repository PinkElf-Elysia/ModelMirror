import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  WorkflowNode,
  WorkflowNodeData,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import WorkflowDeploymentNodeConfig, {
  JsonLiteralInput,
  cronExpressionForUi,
  dateTimeLocalValue,
  durationParts,
  parseJsonLiteralForUi,
  parseCronExpressionForUi,
} from "./WorkflowDeploymentNodeConfig";

function renderConfig(
  data: WorkflowNodeData,
  currentProjectId?: string,
  declarations: WorkflowVariableDeclaration[] = [],
) {
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
      declarations={declarations}
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
    expect(parseJsonLiteralForUi('{"enabled":true}')).toEqual({
      valid: true,
      value: { enabled: true },
    });
    expect(parseJsonLiteralForUi("{unfinished")).toEqual({ valid: false });
  });

  it("keeps incomplete JSON local and only saves a valid typed value", () => {
    const onChange = vi.fn();
    render(
      <JsonLiteralInput
        inputName="payload"
        onChange={onChange}
        value={{ enabled: false }}
      />,
    );

    const input = screen.getByLabelText("payload 固定 JSON 值");
    fireEvent.change(input, { target: { value: "{unfinished" } });
    expect(screen.getByRole("alert")).toHaveTextContent("当前内容尚未保存");
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: '{"enabled":true}' } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(onChange).toHaveBeenCalledWith({ enabled: true });
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

  it("configures native form fields with stable IDs, variables, and local previews", () => {
    const onChange = renderConfig({
      kind: "form_event_entry",
      title: "表单提交入口",
      description: "",
      contractVersion: 1,
      formTitle: "需求登记",
      formDescription: "请填写需求。",
      submitLabel: "提交",
      privacyNotice: "仅用于本次处理。",
      successTitle: "已收到",
      successMessage: "可以关闭页面。",
      theme: "light",
      eventVariable: "form_event",
      submissionVariable: "form_submission",
      fields: [{
        id: "field_contact",
        outputVariable: "contact",
        label: "联系人",
        helpText: "",
        placeholder: "请输入联系人",
        type: "short_text",
        required: true,
        options: [],
      }] as never,
    });

    expect(screen.getByLabelText("表单标题")).toHaveValue("需求登记");
    expect(screen.getByText("field_contact")).toBeInTheDocument();
    expect(screen.getByLabelText("字段类型")).toHaveValue("short_text");
    expect(screen.getByLabelText("输出变量")).toHaveValue("contact");
    expect(screen.getByLabelText("事件元数据变量")).toHaveValue("form_event");
    expect(screen.getByLabelText("完整提交对象变量")).toHaveValue("form_submission");
    expect(screen.getByRole("button", { name: "移动端" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("字段类型"), { target: { value: "single_select" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      fields: [expect.objectContaining({
        id: "field_contact",
        type: "single_select",
        options: [
          expect.objectContaining({ value: "option_1" }),
          expect.objectContaining({ value: "option_2" }),
        ],
      })],
    }));
  });

  it("checks a public RSS source and keeps polling controls non-technical", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        format: "atom1",
        feedTitle: "Release notes",
        itemCount: 2,
        items: [{
          title: "Version 2",
          publishedAt: "2026-08-27T08:00:00Z",
          link: "https://example.test/releases/2",
        }],
      }),
    }));
    const onChange = renderConfig({
      kind: "rss_event_entry",
      title: "RSS/Atom 订阅入口",
      description: "",
      contractVersion: 1,
      feedUrl: "https://example.test/feed.xml",
      pollIntervalMinutes: 15,
      eventVariable: "rss_event",
      itemVariable: "rss_item",
    });

    expect(screen.getByLabelText("Feed 地址")).toHaveValue("https://example.test/feed.xml");
    expect(screen.getByLabelText("检查频率")).toHaveValue("15");
    expect(screen.getByText(/首次启用只记录当前条目/)).toBeInTheDocument();
    expect(screen.getByLabelText("事件元数据变量")).toHaveValue("rss_event");
    expect(screen.getByLabelText("完整条目变量")).toHaveValue("rss_item");
    fireEvent.click(screen.getByRole("button", { name: "检查订阅源" }));
    await waitFor(() => expect(screen.getByText(/Release notes/)).toBeInTheDocument());
    expect(screen.getByText(/Version 2/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("检查频率"), { target: { value: "60" } });
    expect(onChange).toHaveBeenCalledWith({ pollIntervalMinutes: 60 });
    vi.unstubAllGlobals();
  });

  it("explains a rejected private RSS address without exposing the backend message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({
        detail: "Local, private, and reserved RSS targets are forbidden.",
      }),
    }));
    renderConfig({
      kind: "rss_event_entry",
      title: "RSS/Atom 订阅入口",
      description: "",
      contractVersion: 1,
      feedUrl: "https://127.0.0.1/private-feed.xml",
      pollIntervalMinutes: 15,
      eventVariable: "rss_event",
      itemVariable: "rss_item",
    });

    fireEvent.click(screen.getByRole("button", { name: "检查订阅源" }));
    await waitFor(() => expect(screen.getByText(/此地址指向本机、内网或保留网络/)).toBeInTheDocument());
    expect(screen.queryByText(/reserved RSS targets/i)).not.toBeInTheDocument();
    vi.unstubAllGlobals();
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

  it("configures template batches with scoped item and zero-based index variables", () => {
    const onChange = renderConfig({
      kind: "iteration",
      title: "批量处理",
      description: "",
      contractVersion: 2,
      mode: "template_map",
      inputVariable: "orders",
      itemVariable: "order",
      indexVariable: "order_index",
      itemTemplate: "{{order_index}}：{{order}}",
      outputVariable: "mapped_orders",
    }, undefined, [{
      id: "constant-orders",
      name: "orders",
      kind: "constant",
      valueType: "json",
      defaultValue: [],
    }]);

    expect(screen.getByLabelText("批量处理方式")).toHaveValue("template_map");
    expect(screen.getByLabelText("数组变量")).toHaveValue("orders");
    expect(screen.getByLabelText("当前项变量")).toHaveValue("order");
    expect(screen.getByLabelText(/序号变量/)).toHaveValue("order_index");
    expect(screen.getByLabelText(/每项输出模板/)).toHaveValue("{{order_index}}：{{order}}");
    expect(screen.getByText(/不会静默截断/)).toBeInTheDocument();
    expect(screen.queryByText(/orders：未找到变量生产者/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("批量处理方式"), {
      target: { value: "workflow_map" },
    });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      mode: "workflow_map",
      itemVariable: "order",
      indexVariable: "order_index",
      timeoutSeconds: 60,
    }));
  });

  it("warns immediately when a batch local shadows a workflow variable", () => {
    renderConfig({
      kind: "iteration",
      title: "批量处理",
      description: "",
      contractVersion: 2,
      mode: "template_map",
      inputVariable: "orders",
      itemVariable: "orders",
      indexVariable: "order_index",
      itemTemplate: "{{orders}}",
      outputVariable: "mapped_orders",
    }, undefined, [{
      id: "constant-orders",
      name: "orders",
      kind: "constant",
      valueType: "json",
      defaultValue: [],
    }]);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "局部变量不能与输入、输出或全局变量重名",
    );
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

  it("explains callable inputs and exposes the call context variable", () => {
    renderConfig({
      kind: "workflow_call_entry",
      title: "子流程入口",
      description: "",
      eventVariable: "call_event",
    });

    expect(screen.getByLabelText("调用事件变量")).toHaveValue("call_event");
    expect(screen.getByText(/不会生成公开 URL/)).toBeInTheDocument();
    expect(screen.getByText(/全局变量中心添加类型化外部输入/)).toBeInTheDocument();
  });

  it("loads an active fixed target and creates its typed binding table", async () => {
    const targetId = `wf_${"c".repeat(32)}`;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/interface")) {
        return {
          ok: true,
          json: async () => ({
            project_id: targetId,
            version: 2,
            active: true,
            trigger_kind: "call",
            node_contract_checksum: "contract",
            definition_checksum: "definition",
            inputs: [
              { name: "message", value_type: "text", required: true, has_default: false },
              { name: "count", value_type: "number", required: false, has_default: true, default_value: 1 },
            ],
            output: { type: "text" },
          }),
        } as Response;
      }
      if (url === `/api/workflows/${targetId}`) {
        return {
          ok: true,
          json: async () => ({
            project_id: targetId,
            title: "文本清洗",
            draft: {},
            draft_revision: 1,
            active_version: 2,
            active_deployment: null,
            published_versions: [
              { project_id: targetId, version: 2, trigger_kind: "call" },
              { project_id: targetId, version: 1, trigger_kind: "call" },
            ],
            created_at: 1,
            updated_at: 2,
          }),
        } as Response;
      }
      return {
        ok: true,
        json: async () => ({
          items: [{
            project_id: targetId,
            title: "文本清洗",
            active_version: 2,
            active_trigger_kind: "call",
            updated_at: 2,
          }],
          total: 1,
          limit: 100,
          offset: 0,
        }),
      } as Response;
    }));

    const onChange = renderConfig({
      kind: "invoke_workflow",
      title: "调用已发布工作流",
      description: "",
      targetProjectId: targetId,
      targetVersion: 2,
      inputBindings: {},
      resultVariable: "workflow_result",
      timeoutSeconds: 60,
    });

    await waitFor(() => expect(screen.getByText("message")).toBeInTheDocument());
    expect(screen.getByLabelText("目标工作流")).toHaveValue(targetId);
    expect(screen.getByLabelText(/固定发布版本/)).toHaveValue("2");
    expect(screen.getByPlaceholderText("选择或输入上游变量")).toHaveValue("message");
    expect(screen.getByText("count")).toBeInTheDocument();
    expect(onChange).toHaveBeenCalledWith({
      inputBindings: {
        message: { source: "variable", variable: "message" },
      },
    });
    vi.unstubAllGlobals();
  });

  it("shows an actionable error when batch mode has no item binding", async () => {
    const targetId = `wf_${"d".repeat(32)}`;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/interface")) {
        return {
          ok: true,
          json: async () => ({
            project_id: targetId,
            version: 3,
            active: true,
            trigger_kind: "call",
            node_contract_checksum: "contract",
            definition_checksum: "definition",
            inputs: [
              { name: "message", value_type: "text", required: true, has_default: false },
            ],
            output: { type: "text" },
          }),
        } as Response;
      }
      if (url === `/api/workflows/${targetId}`) {
        return {
          ok: true,
          json: async () => ({
            project_id: targetId,
            title: "逐项清洗",
            draft: {},
            draft_revision: 1,
            active_version: 3,
            active_deployment: null,
            published_versions: [
              { project_id: targetId, version: 3, trigger_kind: "call" },
            ],
            created_at: 1,
            updated_at: 2,
          }),
        } as Response;
      }
      return {
        ok: true,
        json: async () => ({
          items: [{
            project_id: targetId,
            title: "逐项清洗",
            active_version: 3,
            active_trigger_kind: "call",
            updated_at: 2,
          }],
          total: 1,
          limit: 100,
          offset: 0,
        }),
      } as Response;
    }));

    renderConfig({
      kind: "iteration",
      title: "批量处理",
      description: "",
      contractVersion: 2,
      mode: "workflow_map",
      inputVariable: "items",
      itemVariable: "item",
      indexVariable: "item_index",
      targetProjectId: targetId,
      targetVersion: 3,
      inputBindings: { message: { source: "index" } },
      outputVariable: "batch_receipts",
      timeoutSeconds: 60,
    });

    await waitFor(() => expect(screen.getByText("message")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent("一个且仅一个");
    expect(screen.getByText(/最多 32 项/)).toBeInTheDocument();
    expect(screen.getByText(/目标工作流的最终文本/)).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
