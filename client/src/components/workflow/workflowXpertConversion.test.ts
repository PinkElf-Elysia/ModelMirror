import { describe, expect, it, vi } from "vitest";

import {
  type WorkflowDefinition,
  type WorkflowNode,
  type WorkflowNodeKind,
  type WorkflowVariableDeclaration,
} from "../../types/workflow";
import { type WorkflowNodeRegistryResponse } from "./workflowNodeRegistry";
import {
  analyzeXpertWorkflowConversion,
  validateXpertConversionGraph,
} from "./workflowXpertConversion";

function node(
  id: string,
  kind: WorkflowNodeKind,
  data: Record<string, unknown> = {},
  position = { x: 0, y: 0 },
): WorkflowNode {
  return {
    id,
    type: "workflowNode",
    position,
    data: {
      kind,
      title: id,
      description: `${id} description`,
      ...data,
    },
  };
}

function registry(
  kinds: WorkflowNodeKind[],
  denied: WorkflowNodeKind[] = ["workflow_call_entry"],
): WorkflowNodeRegistryResponse {
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
              xpert: denied.includes(kind)
                ? {
                    state: "deny",
                    code: "deployment_node_xpert_forbidden",
                    message: "独立部署节点不能进入智能体。",
                  }
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

function callableDefinition(
  variables: WorkflowVariableDeclaration[] = [
    {
      id: "message",
      name: "user_input",
      kind: "input",
      valueType: "text",
    },
  ],
): WorkflowDefinition {
  return {
    id: "wf-callable",
    title: "Callable",
    updatedAt: "2026-08-20T00:00:00.000Z",
    variables,
    nodes: [
      node(
        "entry",
        "workflow_call_entry",
        { eventVariable: "call_event" },
        { x: 20, y: 40 },
      ),
      node("agent", "workflow_agent", {
        modelId: "test-model",
        rolePrompt: "Help",
        taskInput: "{{user_input}}",
        outputVariable: "agent_output",
      }),
      node("output", "output", { outputVariable: "agent_output" }),
    ],
    edges: [
      { id: "entry-agent", source: "entry", target: "agent" },
      { id: "agent-output", source: "agent", target: "output" },
    ],
  };
}

const baseRegistryKinds: WorkflowNodeKind[] = [
  "input",
  "workflow_call_entry",
  "workflow_agent",
  "output",
];

describe("analyzeXpertWorkflowConversion", () => {
  it("converts a sole callable entry in an isolated copy and preserves graph identity", () => {
    const source = callableDefinition();
    const snapshot = structuredClone(source);
    const analysis = analyzeXpertWorkflowConversion(
      source,
      registry(baseRegistryKinds),
    );

    expect(analysis.status).toBe("ready");
    expect(analysis.selectedInputVariable).toBe("user_input");
    expect(analysis.outputVariable).toBe("agent_output");
    expect(analysis.definition?.nodes[0]).toMatchObject({
      id: "entry",
      position: { x: 20, y: 40 },
      data: { kind: "input", variableName: "user_input" },
    });
    expect(analysis.definition?.edges).toEqual(source.edges);
    expect(analysis.definition?.variables).toEqual([]);
    expect(source).toEqual(snapshot);
    expect(analysis.definition).not.toBe(source);
  });

  it("prefers user_input but requires a choice when multiple candidates exist", () => {
    const source = callableDefinition([
      {
        id: "message",
        name: "message",
        kind: "input",
        valueType: "text",
      },
      {
        id: "user-input",
        name: "user_input",
        kind: "input",
        valueType: "text",
        defaultValue: "fallback",
      },
    ]);
    const analysis = analyzeXpertWorkflowConversion(
      source,
      registry(baseRegistryKinds),
    );
    expect(analysis.status).toBe("selection_required");
    expect(analysis.selectedInputVariable).toBe("user_input");
    expect(analysis.inputCandidates).toEqual(["user_input", "message"]);

    const selected = analyzeXpertWorkflowConversion(
      source,
      registry(baseRegistryKinds),
      "message",
    );
    expect(selected.status).toBe("ready");
    expect(selected.definition?.variables).toEqual([
      expect.objectContaining({ name: "user_input", defaultValue: "fallback" }),
    ]);
  });

  it("blocks conversion when the call context variable is referenced", () => {
    const source = callableDefinition();
    source.nodes[1].data.taskInput = "Handle {{call_event}} for {{user_input}}";
    const analysis = analyzeXpertWorkflowConversion(
      source,
      registry(baseRegistryKinds),
    );
    expect(analysis.status).toBe("blocked");
    expect(analysis.blockers.join(" ")).toContain("call_event");
    expect(analysis.blockers.join(" ")).toContain("改变语义");
  });

  it("blocks multiple callable entries instead of choosing one silently", () => {
    const source = callableDefinition();
    source.nodes.splice(
      1,
      0,
      node("entry-2", "workflow_call_entry", { eventVariable: "call_event_2" }),
    );
    const analysis = analyzeXpertWorkflowConversion(
      source,
      registry(baseRegistryKinds),
    );
    expect(analysis.status).toBe("blocked");
    expect(analysis.blockers).toContain(
      "工作流包含多个子流程入口，无法确定要转换的入口。",
    );
  });

  it("blocks a selected input when another required input has no default", () => {
    const source = callableDefinition([
      {
        id: "message",
        name: "message",
        kind: "input",
        valueType: "text",
      },
      {
        id: "count",
        name: "count",
        kind: "input",
        valueType: "number",
      },
    ]);
    const analysis = analyzeXpertWorkflowConversion(
      source,
      registry(baseRegistryKinds),
      "message",
    );
    expect(analysis.status).toBe("blocked");
    expect(analysis.blockers).toContain("其他必填输入没有默认值：count。");
  });

  it.each([
    "scheduled_start",
    "http_event_entry",
    "form_event_entry",
    "failure_event_entry",
    "invoke_workflow",
    "http_event_reply",
    "suspend_wait",
  ] satisfies WorkflowNodeKind[])("blocks independent node %s before Xpert creation", (kind) => {
    const source = callableDefinition();
    source.nodes[0] = node("input", "input", { variableName: "user_input" });
    source.nodes.splice(2, 0, node("forbidden", kind));
    source.edges = [];
    const analysis = analyzeXpertWorkflowConversion(
      source,
      registry([...baseRegistryKinds, kind], [kind]),
    );
    expect(analysis.status).toBe("blocked");
    expect(analysis.blockers.join(" ")).toContain("独立发布运行面");
  });

  it("keeps an ordinary single-input workflow unchanged", () => {
    const source = callableDefinition([]);
    source.nodes[0] = node(
      "input",
      "input",
      { variableName: "user_input" },
      { x: 20, y: 40 },
    );
    const analysis = analyzeXpertWorkflowConversion(
      source,
      registry(baseRegistryKinds, []),
    );
    expect(analysis.status).toBe("ready");
    expect(analysis.convertedEntryNodeId).toBeNull();
    expect(analysis.definition).toEqual(source);
    expect(analysis.definition).not.toBe(source);
  });

  it("fails closed when Registry cannot prove a node's Xpert availability", () => {
    const analysis = analyzeXpertWorkflowConversion(
      callableDefinition(),
      registry(["input", "workflow_call_entry", "output"]),
    );
    expect(analysis.status).toBe("blocked");
    expect(analysis.blockers.join(" ")).toContain("Registry 未提供");
  });
});

describe("validateXpertConversionGraph", () => {
  it("sends the converted copy to the static validator", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          valid: true,
          issues: [],
          order: ["entry", "agent", "output"],
          node_count: 3,
          edge_count: 2,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await validateXpertConversionGraph(callableDefinition());
    expect(result.valid).toBe(true);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0];
    const payload = JSON.parse(String(init.body));
    expect(payload.workflow).toMatchObject({
      source: "classic",
      version: "xpert-conversion-v1",
      id: "wf-callable",
    });
    vi.unstubAllGlobals();
  });

  it("honors the Xpert-injected history variable without hiding other errors", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          valid: false,
          issues: [
            {
              code: "missing_workflow_agent_template_variable",
              message:
                "Workflow agent taskInput references undefined variable 'conversation_history'.",
              severity: "error",
              node_id: "agent",
            },
            {
              code: "unrelated_error",
              message: "Keep this error.",
              severity: "error",
              node_id: "agent",
            },
          ],
          order: [],
          node_count: 3,
          edge_count: 2,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await validateXpertConversionGraph(callableDefinition());
    expect(result.valid).toBe(false);
    expect(result.issues.map((issue) => issue.code)).toEqual(["unrelated_error"]);
    vi.unstubAllGlobals();
  });
});
