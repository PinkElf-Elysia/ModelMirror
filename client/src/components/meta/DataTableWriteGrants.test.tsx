import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import DataTableWriteGrants, {
  normalizeDataTableWriteCatalog,
  validateDataTableWriteGrants,
  type DataTableWriteCatalogItem,
} from "./DataTableWriteGrants";
import { type DataTableWriteGrant } from "./metaAuthoring";

vi.mock("../workflow/WorkflowEditor", () => ({ default: () => null }));

import MetaPlannerV2 from "./MetaPlannerV2";

const CATALOG: DataTableWriteCatalogItem[] = [
  {
    table_id: "table-orders",
    name: "订单表",
    schema_version: 4,
    fields: [
      { name: "status", label: "状态", data_type: "string" },
      { name: "owner_id", label: "负责人", data_type: "string" },
    ],
  },
];

function Harness() {
  const [grants, setGrants] = useState<DataTableWriteGrant[]>([]);
  return (
    <>
      <DataTableWriteGrants
        catalog={CATALOG}
        grants={grants}
        onChange={setGrants}
      />
      <output aria-label="当前写授权">{JSON.stringify(grants)}</output>
    </>
  );
}

function currentGrants() {
  return JSON.parse(screen.getByLabelText("当前写授权").textContent ?? "[]");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Agent Table 逐表写授权", () => {
  it("只从安全字段摘要构造目录，不保留记录或默认值", () => {
    const catalog = normalizeDataTableWriteCatalog([
      {
        table_id: "table-orders",
        name: "订单表",
        active_schema_version: 4,
        records: [{ status: "private" }],
        fields: [
          {
            name: "status",
            label: "状态",
            data_type: "string",
            default_value: "private-default",
            required: true,
          },
          { name: "record_id", label: "记录 ID", data_type: "string" },
          { name: "created_at", label: "创建时间", data_type: "datetime" },
          { name: "updated_at", label: "更新时间", data_type: "datetime" },
          { name: "revision", label: "修订", data_type: "integer" },
          { name: "客户名称", label: "客户名称", data_type: "string" },
        ],
      },
    ]);

    expect(catalog).toEqual([
      {
        table_id: "table-orders",
        name: "订单表",
        schema_version: 4,
        fields: [
          { name: "status", label: "状态", data_type: "string" },
        ],
      },
    ]);
    expect(JSON.stringify(catalog)).not.toContain("private");
    expect(JSON.stringify(catalog)).not.toContain("default_value");
  });

  it("首次选择表后仍要求显式选择操作、字段和影响上限", () => {
    render(<Harness />);

    expect(screen.queryByText("允许操作")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "写授权表：订单表" }));

    expect(currentGrants()).toEqual([
      {
        table_id: "table-orders",
        operations: [],
        writable_fields: [],
        max_affected_rows: 1,
      },
    ]);
    expect(screen.getByRole("checkbox", {
      name: "table-orders 操作：新增 insert",
    })).not.toBeChecked();
    expect(screen.getByRole("checkbox", {
      name: "table-orders 操作：更新 update",
    })).not.toBeChecked();
    expect(screen.getByRole("checkbox", {
      name: "table-orders 操作：删除 delete",
    })).not.toBeChecked();
    expect(screen.getByRole("spinbutton", {
      name: "table-orders 单次影响行数上限",
    })).toHaveValue(1);
    expect(screen.getByText("请为 table-orders 选择至少一个写操作。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", {
      name: "table-orders 操作：更新 update",
    }));
    expect(screen.getByRole("checkbox", {
      name: "table-orders 字段：status，类型：string",
    })).toBeEnabled();
    expect(
      screen.getByText("新增或更新 table-orders 前必须选择至少一个可写字段。"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", {
      name: "table-orders 字段：status，类型：string",
    }));
    fireEvent.change(screen.getByRole("spinbutton", {
      name: "table-orders 单次影响行数上限",
    }), { target: { value: "8" } });

    expect(currentGrants()).toEqual([
      {
        table_id: "table-orders",
        operations: ["update"],
        writable_fields: ["status"],
        max_affected_rows: 8,
      },
    ]);
    expect(
      screen.getByText("该表写授权已完整，仍需单独勾选对应写节点。"),
    ).toBeInTheDocument();
  });

  it("直接显示 Schema 字段类型并纳入可访问名称", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("checkbox", { name: "写授权表：订单表" }));

    expect(screen.getAllByText("string")).toHaveLength(2);
    expect(screen.getByRole("checkbox", {
      name: /table-orders 字段：status.*string/,
    })).toBeInTheDocument();
  });

  it("允许 Delete-only 授权保持空字段且不自动扩大影响上限", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("checkbox", { name: "写授权表：订单表" }));
    fireEvent.click(screen.getByRole("checkbox", {
      name: "table-orders 操作：删除 delete",
    }));

    expect(currentGrants()).toEqual([
      {
        table_id: "table-orders",
        operations: ["delete"],
        writable_fields: [],
        max_affected_rows: 1,
      },
    ]);
    expect(screen.getByRole("checkbox", {
      name: "table-orders 字段：status，类型：string",
    })).toBeDisabled();
    expect(
      screen.getByText("该表写授权已完整，仍需单独勾选对应写节点。"),
    ).toBeInTheDocument();
  });

  it("拒绝目录外表、目录外字段和不完整写授权", () => {
    expect(validateDataTableWriteGrants([
      {
        table_id: "table-private",
        operations: ["delete"],
        writable_fields: [],
        max_affected_rows: 1,
      },
    ], CATALOG)).toContain("不在当前安全目录");
    expect(validateDataTableWriteGrants([
      {
        table_id: "table-orders",
        operations: ["update"],
        writable_fields: ["private_field"],
        max_affected_rows: 1,
      },
    ], CATALOG)).toContain("目录外字段");
    expect(validateDataTableWriteGrants([
      {
        table_id: "table-orders",
        operations: ["insert"],
        writable_fields: [],
        max_affected_rows: 1,
      },
    ], CATALOG)).toContain("必须选择至少一个可写字段");
    expect(validateDataTableWriteGrants([
      {
        table_id: "table-orders",
        operations: ["update"],
        writable_fields: ["record_id"],
        max_affected_rows: 1,
      },
    ], CATALOG)).toContain("无效业务字段名");
  });

  it("最多授权 20 张表，达到上限后仍允许取消已选表", () => {
    const catalog = Array.from({ length: 21 }, (_, index) => ({
      table_id: `table-${index + 1}`,
      name: `Table ${index + 1}`,
      schema_version: 1,
      fields: [{ name: "status", label: "状态", data_type: "string" }],
    }));
    const grants: DataTableWriteGrant[] = catalog.slice(0, 20).map((table) => ({
      table_id: table.table_id,
      operations: ["delete"],
      writable_fields: [],
      max_affected_rows: 1,
    }));

    expect(validateDataTableWriteGrants([
      ...grants,
      {
        table_id: "table-21",
        operations: ["delete"],
        writable_fields: [],
        max_affected_rows: 1,
      },
    ], catalog)).toContain("最多允许 20 张表");

    render(
      <DataTableWriteGrants
        catalog={catalog}
        grants={grants}
        onChange={() => undefined}
      />,
    );

    expect(screen.getByText("已选 20/20 张表")).toBeInTheDocument();
    expect(screen.getByText("已达到 20 张写授权表上限。")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "写授权表：Table 1" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "写授权表：Table 21" })).toBeDisabled();
  });

  it("没有已发布 Schema 时禁用写表选择", () => {
    render(
      <DataTableWriteGrants
        catalog={[{ ...CATALOG[0], schema_version: null }]}
        grants={[]}
        onChange={() => undefined}
      />,
    );

    expect(screen.getByRole("checkbox", {
      name: "写授权表：订单表",
    })).toBeDisabled();
    expect(screen.getByText(/无已发布 Schema/)).toBeInTheDocument();
  });

  it("只在逐表授权后开放写节点，并把 grant 原样放入独立 scope", async () => {
    let generationBody: Record<string, unknown> | null = null;
    const jsonResponse = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/meta-agent/capabilities") {
          return jsonResponse({
            version: "evoagentx-meta-planner-capabilities-v10",
            ir_version: 3,
            supported_ir_versions: [2, 3],
            snapshot_hash: "snapshot-v10",
            generated_at: 1,
            nodes: [
              { kind: "data_table_insert", title: "新增数据" },
              { kind: "data_table_update", title: "更新数据" },
              { kind: "data_table_delete", title: "删除数据" },
            ],
            middleware: [],
            external_xperts: [],
            knowledge_bases: [],
            data_tables: [
              {
                id: "table-orders",
                name: "订单表",
                active_schema_version: 4,
                fields: [
                  { name: "status", type: "string" },
                  { name: "owner_id", type: "string" },
                ],
              },
            ],
            toolsets: [],
            plugins: [],
            prompt_profiles: [],
            vision_models: [],
            default_scope: {
              allowed_node_kinds: [
                "data_table_insert",
                "data_table_update",
                "data_table_delete",
              ],
              data_table_ids: ["table-orders"],
              data_table_write_grants: [
                {
                  table_id: "table-orders",
                  operations: ["delete"],
                  writable_fields: [],
                  max_affected_rows: 100,
                },
              ],
            },
          });
        }
        if (url.startsWith("/api/xperts?")) {
          return jsonResponse({ items: [], total: 0 });
        }
        if (url.startsWith("/api/runtime/authoring-proposals?")) {
          return jsonResponse({ items: [] });
        }
        if (
          url === "/api/meta-agent/generate-xpert-candidate" &&
          init?.method === "POST"
        ) {
          generationBody = JSON.parse(String(init.body));
          return jsonResponse({ detail: "测试请求已记录。" }, 422);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <MetaPlannerV2 />
      </MemoryRouter>,
    );

    const updateNode = await screen.findByRole("checkbox", {
      name: "节点：更新数据",
    });
    expect(updateNode).not.toBeChecked();
    expect(updateNode).toBeDisabled();
    expect(screen.getByRole("checkbox", {
      name: "Agent Table 查询：订单表",
    })).not.toBeChecked();

    fireEvent.click(screen.getByRole("checkbox", { name: "写授权表：订单表" }));
    fireEvent.click(screen.getByRole("checkbox", {
      name: "table-orders 操作：更新 update",
    }));
    fireEvent.click(screen.getByRole("checkbox", {
      name: "table-orders 字段：status，类型：string",
    }));

    expect(updateNode).toBeEnabled();
    expect(screen.getByRole("checkbox", {
      name: "节点：新增数据",
    })).toBeDisabled();
    fireEvent.click(updateNode);
    fireEvent.change(
      screen.getByPlaceholderText(
        "例如：构建一个负责研究、事实核查与审稿协作的智能体",
      ),
      { target: { value: "请生成一个受限更新订单状态的智能体。" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "生成候选智能体" }));

    await waitFor(() => expect(generationBody).not.toBeNull());
    expect(generationBody).toMatchObject({
      scope: {
        allowed_node_kinds: ["data_table_update"],
        data_table_ids: [],
        data_table_write_grants: [
          {
            table_id: "table-orders",
            operations: ["update"],
            writable_fields: ["status"],
            max_affected_rows: 1,
          },
        ],
      },
    });
  });
});
