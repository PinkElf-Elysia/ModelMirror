import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type WorkflowNodeData } from "../../types/workflow";
import WorkflowTypedDataNodeConfig from "./WorkflowTypedDataNodeConfig";

const table = {
  table_id: "table_tasks",
  name: "审核任务",
  description: "待审核内容",
  status: "published",
  draft_revision: 2,
  active_schema_version: 1,
  fields: [],
  created_at: 1,
  updated_at: 2,
};

const detail = {
  table,
  schema_versions: [
    {
      table_id: table.table_id,
      version: 1,
      draft_revision: 2,
      checksum: "sha256:test",
      published_at: 2,
      fields: [
        {
          field_id: "field_status",
          name: "status",
          label: "状态",
          description: "任务状态",
          data_type: "string",
          required: true,
          has_default: false,
          default_value: null,
        },
      ],
    },
  ],
  record_count: 3,
};

function Harness({ initial }: { initial: WorkflowNodeData }) {
  const [data, setData] = useState(initial);
  return (
    <MemoryRouter>
      <WorkflowTypedDataNodeConfig
        data={data}
        onChange={(patch) => setData((current) => ({ ...current, ...patch }))}
      />
    </MemoryRouter>
  );
}

function mockAgentTables() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.includes(`/api/data-tables/${table.table_id}`)
        ? detail
        : { items: [table], count: 1, backend: "sqlite" };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WorkflowTypedDataNodeConfig", () => {
  it("loads a published table and exposes query schema configuration", async () => {
    mockAgentTables();
    render(
      <Harness
        initial={{
          kind: "data_table_query",
          title: "查询数据表",
          description: "query",
          tableId: "",
          versionPolicy: "latest",
          selectFields: [],
          filter: {},
          sort: [],
          limit: 20,
          returnMode: "list",
          outputVariable: "table_records",
        }}
      />,
    );

    const tableSelect = await screen.findByLabelText("已发布数据表");
    fireEvent.change(tableSelect, { target: { value: table.table_id } });

    await waitFor(() => {
      expect(screen.getByText(/当前配置：Schema v1/)).toBeInTheDocument();
    });
    expect(screen.getByText("返回字段")).toBeInTheDocument();
    expect(screen.getByText("查询条件")).toBeInTheDocument();
    expect(screen.getByText("状态")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /打开当前数据表/ })).toHaveAttribute(
      "href",
      `/data-tables/${table.table_id}`,
    );
  });

  it("exposes typed field bindings and mandatory filters for update nodes", async () => {
    mockAgentTables();
    render(
      <Harness
        initial={{
          kind: "data_table_update",
          title: "更新数据",
          description: "update",
          tableId: table.table_id,
          versionPolicy: "latest",
          filter: {},
          valueBindings: {},
          outputVariable: "update_result",
        }}
      />,
    );

    await screen.findByText(/当前配置：Schema v1/);
    expect(screen.getByText("更新字段绑定")).toBeInTheDocument();
    expect(screen.getByText("必填安全条件")).toBeInTheDocument();
    expect(screen.getByText(/不允许全表操作/)).toBeInTheDocument();
  });
});
