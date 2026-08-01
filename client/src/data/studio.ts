export type StudioAppKind = "workflow" | "dataset" | "chat" | "agent";

export interface StudioAppRecord {
  id: string;
  name: string;
  kind: StudioAppKind;
  icon: string;
  status: "stable" | "fallback" | "planned";
  description: string;
  href: string;
  tags: string[];
}

export interface StudioActivityRecord {
  id: string;
  title: string;
  detail: string;
  at: string;
}

export interface StudioState {
  apps: StudioAppRecord[];
  activities: StudioActivityRecord[];
}

export function createInitialStudioState(): StudioState {
  const now = new Date().toISOString();

  return {
    apps: [
      {
        id: "workflow-canvas",
        name: "工作流（经典自研）",
        kind: "workflow",
        icon: "🛠️",
        status: "stable",
        description:
          "模镜自研的 React Flow 工作流编辑器，支持节点拖拽、静态图校验和本地运行。",
        href: "/workflow",
        tags: ["工作流", "稳定", "本地"],
      },
      {
        id: "local-rag-datasets",
        name: "本地 RAG 知识库",
        kind: "dataset",
        icon: "📚",
        status: "stable",
        description: "通过本地知识库提供文档上传、切分、检索测试和 RAG 问答。",
        href: "/rag",
        tags: ["RAG", "知识库"],
      },
      {
        id: "workflow-native",
        name: "自研工作流（实验）",
        kind: "workflow",
        icon: "WN",
        status: "planned",
        description:
          "独立的 native 实验线，节点类型更丰富，支持条件分支、变量聚合、参数提取等能力。",
        href: "/workflow-native",
        tags: ["实验", "validate", "非 iframe"],
      },
      {
        id: "newapi-gateway",
        name: "newAPI 网关（占位）",
        kind: "agent",
        icon: "🔌",
        status: "planned",
        description:
          "外部网关与 API Key 统一管理入口，后续工作流与对话可通过它接入更多服务商。",
        href: "/settings",
        tags: ["设置", "网关", "占位"],
      },
    ],
    activities: [
      {
        id: "activity-workflow-refactor",
        title: "工作流已切换为经典自研画布为默认路径",
        detail: "工作流默认使用经典自研画布；外部网关与 API Key 管理迁移到 /settings。",
        at: now,
      },
      {
        id: "activity-native-nodes",
        title: "原生工作流节点持续扩展",
        detail: "mcp_tool / time_tool / template_transform 等节点陆续到位，与 MCP 板块联动。",
        at: now,
      },
    ],
  };
}

export function readStudioState(): StudioState {
  return createInitialStudioState();
}
