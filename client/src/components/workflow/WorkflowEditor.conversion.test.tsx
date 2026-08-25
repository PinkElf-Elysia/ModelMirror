import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type WorkflowDefinition, type WorkflowNodeKind } from "../../types/workflow";
import { type RuntimeMiddlewareNode } from "../../types/runtimeMiddleware";
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

function legacyVariableAggregatorWorkflow(): WorkflowDefinition {
  const definition = ordinaryWorkflow();
  definition.id = "legacy-variable-aggregator";
  definition.variables = [
    { id: "customer", name: "customer", kind: "input", valueType: "json" },
    { id: "order", name: "order", kind: "input", valueType: "json" },
  ];
  definition.nodes.splice(1, 0, {
    id: "legacy-pack",
    type: "workflowNode",
    position: { x: 180, y: 20 },
    data: {
      kind: "variable_aggregator",
      title: "旧变量聚合",
      description: "兼容配置",
      variableNames: "customer, order",
      outputTemplate: "",
      outputVariable: "bundle",
    },
  });
  definition.edges = [
    { id: "entry-pack", source: "entry", target: "legacy-pack" },
    { id: "pack-agent", source: "legacy-pack", target: "agent" },
    ...definition.edges.filter((edge) => edge.source === "agent"),
  ];
  return definition;
}

function legacyTemplateAggregatorWorkflow(): WorkflowDefinition {
  const definition = legacyVariableAggregatorWorkflow();
  definition.id = "legacy-template-aggregator";
  const legacy = definition.nodes.find((node) => node.id === "legacy-pack")!;
  legacy.data.title = "旧模板聚合";
  legacy.data.outputTemplate = "## {name}\n{value}\n";
  return definition;
}

function jsonResponse(value: unknown) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function skillRuntimeMiddleware(): RuntimeMiddlewareNode {
  return {
    id: "skills_runtime",
    kind: "runtime_middleware.skills_runtime",
    title: "Skill 执行指导",
    description: "要求 Agent 实际读取选定 Skill。",
    category: "tool",
    icon: "BookOpenCheck",
    enabled: true,
    fields: [
      { name: "skill_ids", label: "选择必用 Skill", type: "textarea" },
      {
        name: "auto_discover",
        label: "允许发现其他已安装 Skill",
        type: "boolean",
        default: false,
      },
      {
        name: "catalog_search",
        label: "允许按需检索已核验 Skill 目录",
        type: "boolean",
        default: false,
      },
    ],
  };
}

function workflowWithSkillRuntime(): WorkflowDefinition {
  const definition = ordinaryWorkflow();
  definition.nodes.push({
    id: "skills-runtime",
    type: "workflowNode",
    position: { x: 300, y: 260 },
    data: {
      kind: "runtime_middleware",
      title: "Skill 执行指导",
      description: "要求 Agent 实际读取选定 Skill。",
      runtimeMiddlewareId: "skills_runtime",
      runtimeMiddlewareKind: "runtime_middleware.skills_runtime",
      runtimeMiddlewareConfig: {
        skill_ids: "pdf, tdd",
        auto_discover: true,
        catalog_search: true,
      },
    },
  });
  definition.edges.push({
    id: "bind-skills-runtime",
    source: "skills-runtime",
    target: "agent",
    sourceHandle: "middleware-binding",
    targetHandle: "middleware",
  });
  return definition;
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
        if (url === "/api/runtime/middleware-nodes") {
          return Promise.resolve(jsonResponse([skillRuntimeMiddleware()]));
        }
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

  it("recognizes global variables in the legacy aggregator migration UI", async () => {
    render(
      <MemoryRouter>
        <WorkflowEditor
          initialDefinition={legacyVariableAggregatorWorkflow()}
          workflowId="legacy-variable-aggregator"
        />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByText("旧变量聚合"));

    expect(
      await screen.findByRole("button", { name: "迁移为变量打包 V2" }),
    ).toBeEnabled();
    expect(screen.queryByText(/未找到变量生产者/)).not.toBeInTheDocument();
  });

  it("keeps global variable references recognized after template migration", async () => {
    render(
      <MemoryRouter>
        <WorkflowEditor
          initialDefinition={legacyTemplateAggregatorWorkflow()}
          workflowId="legacy-template-aggregator"
        />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByText("旧模板聚合"));
    fireEvent.click(
      await screen.findByRole("button", { name: "迁移为变量赋值 V2" }),
    );

    expect(await screen.findByText("变量赋值")).toBeInTheDocument();
    expect(screen.queryByText(/未找到变量生产者/)).not.toBeInTheDocument();
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

  it("recognizes global declarations in workflow agent templates without hiding unknown references", async () => {
    const definition = ordinaryWorkflow();
    definition.variables = [
      {
        id: "user-input",
        name: "user_input",
        kind: "input",
        valueType: "text",
      },
    ];
    definition.nodes[1] = {
      ...definition.nodes[1],
      data: {
        ...definition.nodes[1].data,
        taskInput: "{{user_input}}",
        promptSuffix: "Use {{missing_context}} when available.",
      },
    };

    render(
      <MemoryRouter>
        <WorkflowEditor
          initialDefinition={definition}
          workflowId="classic-global-variable"
        />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByTestId("rf__node-agent"));

    expect(
      await screen.findByText(/missing_context：未找到变量生产者/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/user_input：未找到变量生产者/),
    ).not.toBeInTheDocument();
  });

  it("edits required Skills as removable tags and keeps discovery advanced", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <WorkflowEditor
          initialDefinition={workflowWithSkillRuntime()}
          onSave={onSave}
          saveLabel="保存测试草稿"
          workflowId="classic-skill-runtime"
        />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByTestId("rf__node-skills-runtime"));
    expect(
      await screen.findByRole("button", { name: "移除必用 Skill：pdf" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "移除必用 Skill：tdd" })).toBeVisible();
    expect(screen.queryByText("允许发现其他已安装 Skill")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "展开高级选项" }));
    expect((await screen.findAllByText("允许发现其他已安装 Skill")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "移除必用 Skill：pdf" }));
    fireEvent.click(screen.getByRole("button", { name: "保存测试草稿" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    const saved = onSave.mock.calls[0][0] as WorkflowDefinition;
    expect(
      saved.nodes.find((node) => node.id === "skills-runtime")?.data
        .runtimeMiddlewareConfig,
    ).toMatchObject({
      skill_ids: "tdd",
      auto_discover: true,
      catalog_search: true,
    });
  });
});
