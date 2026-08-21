import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type WorkflowDefinition, type WorkflowNodeKind } from "../../types/workflow";
import WorkflowEditor from "./WorkflowEditor";
import { type WorkflowNodeRegistryResponse } from "./workflowNodeRegistry";

let registryAvailable = true;
let staticValidation = {
  valid: true,
  issues: [] as Array<Record<string, unknown>>,
  order: ["entry", "agent", "output"],
  node_count: 3,
  edge_count: 2,
};

function registry(): WorkflowNodeRegistryResponse {
  const kinds: WorkflowNodeKind[] = [
    "input",
    "workflow_call_entry",
    "workflow_agent",
    "output",
  ];
  return {
    version: "xpert-workflow-node-registry-v4",
    contract_version: 3,
    contract_checksum: "a".repeat(64),
    tabs: [],
    sections: [
      {
        id: "logic",
        label: "test",
        description: "test",
        items: kinds.map((kind) => ({
          kind: kind as Exclude<WorkflowNodeKind, "runtime_middleware">,
          icon: "T",
          title: kind,
          description: kind,
          enabled: true,
          contract: {
            kind: kind as Exclude<WorkflowNodeKind, "runtime_middleware">,
            contract_status: "complete",
            config_schema: {},
            ports: [],
            edge: {},
            execution: {},
            availability: {
              xpert:
                kind === "workflow_call_entry"
                  ? { state: "deny", message: "denied" }
                  : { state: "allow" },
            },
            resources: [],
            planner: {},
            contract_version: 3,
            checksum: "b".repeat(64),
            compiler_checksum: "c".repeat(64),
          },
        })),
      },
    ],
    knowledge_pipeline: { items: [], placeholders: [] },
  };
}

function invalidXpertDraft(): WorkflowDefinition {
  return {
    id: "xpert-invalid",
    title: "Invalid Xpert",
    updatedAt: "2026-08-20T00:00:00.000Z",
    variables: [
      {
        id: "user-input",
        name: "user_input",
        kind: "input",
        valueType: "text",
      },
    ],
    nodes: [
      {
        id: "entry",
        type: "workflowNode",
        position: { x: 10, y: 20 },
        data: {
          kind: "workflow_call_entry",
          title: "子流程入口",
          description: "",
          eventVariable: "call_event",
        },
      },
      {
        id: "agent",
        type: "workflowNode",
        position: { x: 300, y: 20 },
        data: {
          kind: "workflow_agent",
          title: "Agent",
          description: "",
          modelId: "test-model",
          rolePrompt: "Help",
          taskInput: "{{user_input}}",
          outputVariable: "agent_output",
        },
      },
      {
        id: "output",
        type: "workflowNode",
        position: { x: 600, y: 20 },
        data: {
          kind: "output",
          title: "Output",
          description: "",
          outputVariable: "agent_output",
        },
      },
    ],
    edges: [
      { id: "entry-agent", source: "entry", target: "agent" },
      { id: "agent-output", source: "agent", target: "output" },
    ],
  };
}

function ordinaryWorkflow(): WorkflowDefinition {
  const definition = invalidXpertDraft();
  definition.id = "classic-local";
  definition.variables = [];
  definition.nodes[0] = {
    ...definition.nodes[0],
    data: {
      kind: "input",
      title: "Input",
      description: "",
      variableName: "user_input",
    },
  };
  return definition;
}

function jsonResponse(value: unknown) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("WorkflowEditor Xpert entry repair", () => {
  beforeEach(() => {
    registryAvailable = true;
    staticValidation = {
      valid: true,
      issues: [],
      order: ["entry", "agent", "output"],
      node_count: 3,
      edge_count: 2,
    };
    class TestResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    vi.stubGlobal(
      "fetch",
      vi.fn((request: RequestInfo | URL) => {
        const url = String(request);
        if (url === "/api/workflow/node-registry") {
          return Promise.resolve(
            registryAvailable
              ? jsonResponse(registry())
              : new Response("unavailable", { status: 503 }),
          );
        }
        if (url === "/api/workflow-native/validate") {
          return Promise.resolve(jsonResponse(staticValidation));
        }
        if (url === "/api/runtime/middleware-nodes") return Promise.resolve(jsonResponse([]));
        if (url.startsWith("/api/xperts")) {
          return Promise.resolve(jsonResponse({ items: [], total: 0, version: "test" }));
        }
        if (url === "/api/runtime/client-hosts") return Promise.resolve(jsonResponse({ hosts: [] }));
        if (url === "/api/skills/installed") return Promise.resolve(jsonResponse({ skills: [] }));
        if (url === "/api/workflow/vision-capabilities") {
          return Promise.resolve(jsonResponse({ models: [] }));
        }
        return Promise.resolve(jsonResponse([]));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("repairs only the Xpert copy, supports undo, and saves explicitly", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const source = invalidXpertDraft();
    const sourceSnapshot = structuredClone(source);
    render(
      <MemoryRouter>
        <WorkflowEditor
          initialDefinition={source}
          onSave={onSave}
          saveLabel="保存测试草稿"
          workflowId="xpert-invalid"
        />
      </MemoryRouter>,
    );

    expect(await screen.findByText("独立工作流入口需要转换")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "转换为智能体输入" }));
    await waitFor(() =>
      expect(screen.queryByText("独立工作流入口需要转换")).not.toBeInTheDocument(),
    );

    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    expect(await screen.findByText("独立工作流入口需要转换")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "转换为智能体输入" }));
    fireEvent.click(screen.getByRole("button", { name: "保存测试草稿" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    const saved = onSave.mock.calls[0][0] as WorkflowDefinition;
    expect(saved.nodes[0]).toMatchObject({
      id: "entry",
      position: { x: 10, y: 20 },
      data: { kind: "input", variableName: "user_input" },
    });
    expect(saved.edges).toEqual(source.edges);
    expect(saved.variables).toEqual([]);
    expect(source).toEqual(sourceSnapshot);
  });

  it("fails closed when Registry is unavailable", async () => {
    registryAvailable = false;
    render(
      <MemoryRouter>
        <WorkflowEditor
          initialDefinition={ordinaryWorkflow()}
          workflowId="classic-local"
        />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(
        "节点 Registry 暂不可用，已暂停智能体转换与入口修复。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "转为智能体草稿" }),
    ).toBeDisabled();
    const fetchMock = vi.mocked(fetch);
    expect(
      fetchMock.mock.calls.some(
        ([request, init]) =>
          String(request) === "/api/xperts" && init?.method === "POST",
      ),
    ).toBe(false);
  });

  it("does not create an Xpert when static validation rejects the converted graph", async () => {
    staticValidation = {
      valid: false,
      issues: [
        {
          code: "test_invalid",
          message: "bad graph",
          severity: "error",
          node_id: "agent",
        },
      ],
      order: [],
      node_count: 3,
      edge_count: 2,
    };
    render(
      <MemoryRouter>
        <WorkflowEditor
          initialDefinition={ordinaryWorkflow()}
          workflowId="classic-local"
        />
      </MemoryRouter>,
    );

    const convert = screen.getByRole("button", { name: "转为智能体草稿" });
    await waitFor(() => expect(convert).toBeEnabled());
    fireEvent.click(convert);
    expect(await screen.findByText("• agent：bad graph")).toBeInTheDocument();
    const fetchMock = vi.mocked(fetch);
    expect(
      fetchMock.mock.calls.some(
        ([request, init]) =>
          String(request) === "/api/xperts" && init?.method === "POST",
      ),
    ).toBe(false);
  });
});
