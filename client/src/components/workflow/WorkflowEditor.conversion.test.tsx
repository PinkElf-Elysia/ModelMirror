import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type WorkflowDefinition, type WorkflowNodeKind } from "../../types/workflow";
import { type RuntimeMiddlewareNode } from "../../types/runtimeMiddleware";
import WorkflowEditor from "./WorkflowEditor";
import WorkflowClassicPage from "../../pages/WorkflowClassicPage";
import { type WorkflowNodeRegistryResponse } from "./workflowNodeRegistry";
import {
  type WorkflowDeploymentSummary,
  type WorkflowFormPublicationSummary,
} from "../../utils/workflowDeployments";

let registryAvailable = true;
let serverWorkflow: WorkflowDefinition | null = null;
let serverActiveDeployment: WorkflowDeploymentSummary | null = null;
let serverFormPublication: WorkflowFormPublicationSummary | null = null;
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
    "form_event_entry",
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
    knowledge_pipeline: {
      items: [
        {
          kind: "knowledge_write_proposal",
          icon: "K",
          title: "知识写入提议",
          description: "提交待审批知识提议。",
          enabled: true,
          metadata: {
            feature_enabled: false,
            feature_disabled_reason: "测试环境开关关闭。",
          },
          contract: {
            kind: "knowledge_write_proposal",
            contract_status: "complete",
            config_schema: {},
            ports: [],
            edge: {},
            execution: {},
            availability: { xpert: { state: "allow" } },
            resources: [],
            planner: {},
            contract_version: 3,
            checksum: "b".repeat(64),
            compiler_checksum: "c".repeat(64),
          },
        },
      ],
      placeholders: [],
    },
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

function WorkflowRouteHarness() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate("/workflow/new")} type="button">
        打开新建工作流
      </button>
      <Routes>
        <Route path="/workflow/:id" element={<WorkflowClassicPage />} />
      </Routes>
    </>
  );
}

function signedFormWorkflow(): WorkflowDefinition {
  return {
    id: "signed-form-editor",
    title: "Signed form editor",
    updatedAt: "2026-08-26T00:00:00.000Z",
    variables: [],
    nodes: [
      {
        id: "form-entry",
        type: "form_event_entry" as "workflowNode",
        position: { x: 10, y: 20 },
        data: {
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
          fields: [
            {
              id: "field_contact",
              outputVariable: "contact",
              label: "联系人",
              helpText: "",
              placeholder: "请输入联系人",
              type: "short_text",
              required: true,
              options: [],
            },
          ] as never,
        },
      },
    ],
    edges: [],
  };
}

function knowledgeProposalWorkflow(): WorkflowDefinition {
  return {
    id: "knowledge-proposal-editor",
    title: "Knowledge proposal editor",
    updatedAt: "2026-08-26T00:00:00.000Z",
    variables: [
      { id: "content", name: "content", kind: "input", valueType: "text" },
    ],
    nodes: [
      {
        id: "proposal",
        type: "workflowNode",
        position: { x: 10, y: 20 },
        data: {
          kind: "knowledge_write_proposal",
          title: "知识写入提议",
          description: "提交待审批知识提议。",
          contractVersion: 1,
          knowledgeBaseId: "kb_writable",
          titleTemplate: "公告：{{content}}",
          contentVariable: "content",
          tags: ["公告"],
          outputVariable: "proposal_receipt",
        },
      },
    ],
    edges: [],
  };
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
    window.localStorage.clear();
    registryAvailable = true;
    serverWorkflow = null;
    serverActiveDeployment = null;
    serverFormPublication = null;
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
        if (url.startsWith("/api/workflows/wf_") && serverWorkflow) {
          return Promise.resolve(jsonResponse({
            project_id: serverWorkflow.id,
            title: serverWorkflow.title,
            draft: serverWorkflow,
            draft_revision: 1,
            active_version: 1,
            active_deployment: serverActiveDeployment,
            form_publication: serverFormPublication,
            published_versions: [],
            created_at: 0,
            updated_at: 0,
          }));
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
        if (url === "/api/workflow/resource-options?kind=knowledge_base") {
          return Promise.resolve(jsonResponse({
            items: [
              {
                id: "kb_writable",
                name: "产品公告",
                corpus_locked: false,
                provisioning_status: "ready",
              },
              {
                id: "kb_locked",
                name: "锁定基线",
                corpus_locked: true,
                provisioning_status: "ready",
              },
            ],
          }));
        }
        return Promise.resolve(jsonResponse([]));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requires an explicit choice before opening a recovered local draft", async () => {
    const storedDraft = ordinaryWorkflow();
    storedDraft.id = "draft";
    storedDraft.title = "上次的客户分流";
    window.localStorage.setItem(
      "modelmirror-workflow:draft",
      JSON.stringify(storedDraft),
    );

    render(
      <MemoryRouter>
        <WorkflowEditor workflowId="draft" />
      </MemoryRouter>,
    );

    const recoveryDialog = await screen.findByRole("dialog", {
      name: "发现一个未发布的本地草稿",
    });
    expect(recoveryDialog).toHaveTextContent("上次的客户分流");
    expect(recoveryDialog).toHaveTextContent("3 个节点和 2 条连线");
    expect(recoveryDialog.contains(document.activeElement)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "恢复本地草稿" }));

    await waitFor(() => expect(recoveryDialog).not.toBeInTheDocument());
    expect(screen.getByDisplayValue("上次的客户分流")).toBeInTheDocument();
  });

  it("starts from the default workflow without deleting the recovered draft", async () => {
    const storedDraft = ordinaryWorkflow();
    storedDraft.id = "draft";
    storedDraft.title = "需要保留的本地草稿";
    const serializedDraft = JSON.stringify(storedDraft);
    window.localStorage.setItem("modelmirror-workflow:draft", serializedDraft);

    render(
      <MemoryRouter>
        <WorkflowEditor workflowId="draft" />
      </MemoryRouter>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "使用默认工作流新建" }),
    );

    expect(
      screen.queryByRole("dialog", { name: "发现一个未发布的本地草稿" }),
    ).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("新建 AI 流水线")).toBeInTheDocument();
    expect(window.localStorage.getItem("modelmirror-workflow:draft")).toBe(
      serializedDraft,
    );
  });

  it("remounts when SPA navigation changes a saved workflow into a new draft", async () => {
    serverWorkflow = ordinaryWorkflow();
    serverWorkflow.id = "wf_existing";
    serverWorkflow.title = "已保存的工作流";
    const storedDraft = ordinaryWorkflow();
    storedDraft.id = "draft";
    storedDraft.title = "等待恢复的本地草稿";
    window.localStorage.setItem(
      "modelmirror-workflow:draft",
      JSON.stringify(storedDraft),
    );

    render(
      <MemoryRouter initialEntries={["/workflow/wf_existing"]}>
        <WorkflowRouteHarness />
      </MemoryRouter>,
    );

    expect(await screen.findByDisplayValue("已保存的工作流")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开新建工作流" }));

    const recoveryDialog = await screen.findByRole("dialog", {
      name: "发现一个未发布的本地草稿",
    });
    expect(recoveryDialog).toHaveTextContent("等待恢复的本地草稿");
    expect(screen.queryByDisplayValue("已保存的工作流")).not.toBeInTheDocument();
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

  it("preserves signed-form field variables in the full editor", async () => {
    render(
      <MemoryRouter>
        <WorkflowEditor
          initialDefinition={signedFormWorkflow()}
          workflowId="signed-form-editor"
        />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByText("表单提交入口"));

    expect(await screen.findByLabelText("输出变量")).toHaveValue("contact");
  });

  it("preserves signed-form field variables after loading a server draft", async () => {
    serverWorkflow = signedFormWorkflow();
    serverWorkflow.id = "wf_signed_form_editor";
    render(
      <MemoryRouter>
        <WorkflowEditor workflowId="wf_signed_form_editor" />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByText("表单提交入口"));

    expect(await screen.findByLabelText("输出变量")).toHaveValue("contact");
  });

  it("keeps form publication actions behind one compact status control", async () => {
    serverWorkflow = signedFormWorkflow();
    serverWorkflow.id = "wf_signed_form_editor";
    serverFormPublication = {
      form_id: "form_test",
      project_id: "wf_signed_form_editor",
      version: 1,
      deployment_id: "deploy_test",
      form_key_prefix: "mmform_test",
      active: true,
      updated_at: 1,
    };
    serverActiveDeployment = {
      deployment_id: "deploy_test",
      project_id: "wf_signed_form_editor",
      version: 1,
      trigger_kind: "form",
      active: true,
      form_publication: serverFormPublication,
    };

    render(
      <MemoryRouter>
        <WorkflowEditor workflowId="wf_signed_form_editor" />
      </MemoryRouter>,
    );

    const menu = await screen.findByTestId("form-publication-menu");
    const trigger = screen.getByLabelText("表单发布设置");
    expect(trigger).toHaveTextContent("表单 v1");
    expect(trigger).toHaveTextContent("已启用");
    expect(trigger).not.toHaveTextContent("mmform_test");
    expect(menu).not.toHaveAttribute("open");

    fireEvent.click(trigger);

    expect(menu).toHaveAttribute("open");
    expect(screen.getByText("固定版本 v1 · 密钥 mmform_test")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "发布新版本并切换" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "轮换分享链接" })).toBeVisible();
    expect(screen.getByRole("button", { name: "停用表单" })).toBeVisible();
  });

  it("makes deterministic knowledge proposals configurable without raw JSON", async () => {
    render(
      <MemoryRouter>
        <WorkflowEditor
          initialDefinition={knowledgeProposalWorkflow()}
          workflowId="knowledge-proposal-editor"
        />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByText("知识写入提议"));

    expect(await screen.findByText(/正文会持久保存到 Knowledge Inbox/)).toBeVisible();
    expect(screen.getByText(/当前功能开关关闭/)).toHaveTextContent("测试环境开关关闭");
    expect(screen.getByLabelText("写入目标")).toHaveValue("kb_writable");
    expect(screen.getByLabelText("正文变量（必须是文本）")).toHaveValue("content");
    expect(screen.getByLabelText("标签 1")).toHaveValue("公告");
    expect(screen.getByRole("link", { name: "打开 Knowledge Inbox" })).toHaveAttribute(
      "href",
      "/rag/kb_writable/inbox",
    );
    expect(screen.getByRole("option", { name: /锁定基线/ })).toBeDisabled();
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
