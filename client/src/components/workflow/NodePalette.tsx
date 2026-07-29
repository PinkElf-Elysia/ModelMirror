import { useEffect, useMemo, useState } from "react";

import {
  fetchRuntimeMiddlewareNodes,
  type RuntimeMiddlewareNode,
} from "../../types/runtimeMiddleware";
import {
  fetchWorkflowNodeRegistry,
  matchesWorkflowPaletteQuery,
  type WorkflowPaletteItem,
  type WorkflowPalettePlaceholder,
  type WorkflowNodeRegistryResponse,
  workflowNodeRegistryFallback,
} from "./workflowNodeRegistry";

type PaletteTab = "workflow" | "middleware" | "knowledge";

const paletteTabs: Array<{ id: PaletteTab; label: string }> = [
  { id: "workflow", label: "工作流" },
  { id: "middleware", label: "中间件" },
  { id: "knowledge", label: "知识流水线" },
];

const middlewareIconMap: Record<string, string> = {
  Shrink: "CTX",
  Braces: "{}",
  ListTodo: "TODO",
  ListFilter: "SEL",
  Activity: "〽",
  ClipboardList: "▦",
  MessageSquare: "◇",
  Puzzle: "▣",
  Shield: "◇",
};

function MiddlewareIcon({ icon }: { icon: string }) {
  return <span aria-hidden="true">{middlewareIconMap[icon] ?? "▣"}</span>;
}

function paletteCardClass(disabled = false) {
  if (disabled) {
    return "w-full rounded-lg border border-dashed border-white/10 bg-white/[0.025] p-3 text-left opacity-70";
  }
  return "group w-full rounded-lg border border-white/10 bg-white/[0.045] p-3 text-left transition duration-200 hover:-translate-y-0.5 hover:border-hire-300/35 hover:bg-hire-300/10 focus:outline-none focus:ring-2 focus:ring-hire-300/35";
}

function NormalNodeButton({ item }: { item: WorkflowPaletteItem }) {
  return (
    <button
      className={paletteCardClass()}
      draggable
      key={item.kind}
      onDragStart={(event) => {
        event.dataTransfer.setData("application/modelmirror-node", item.kind);
        event.dataTransfer.effectAllowed = "move";
      }}
      type="button"
    >
      <span className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-hire-300/25 bg-hire-300/10 text-[11px] font-semibold text-hire-100">
          {item.icon}
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-white">
            {item.title}
          </span>
          <span className="mt-1 block text-xs leading-5 text-slate-400">
            {item.description}
          </span>
        </span>
      </span>
    </button>
  );
}

function PlaceholderCard({ item }: { item: WorkflowPalettePlaceholder }) {
  return (
    <div aria-disabled="true" className={paletteCardClass(true)}>
      <span className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] text-[10px] font-semibold text-slate-400">
          {item.icon}
        </span>
        <span className="min-w-0">
          <span className="flex items-center gap-2">
            <span className="block text-sm font-semibold text-slate-300">
              {item.title}
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-slate-500">
              {item.statusLabel}
            </span>
          </span>
          <span className="mt-1 block text-xs leading-5 text-slate-500">
            {item.description}
          </span>
        </span>
      </span>
    </div>
  );
}

function EmptyState({ children }: { children: string }) {
  return (
    <p className="rounded-lg border border-dashed border-white/10 bg-white/[0.025] px-3 py-5 text-center text-xs leading-5 text-slate-500">
      {children}
    </p>
  );
}

function RegistryFallbackNotice({ message }: { message: string | null }) {
  if (!message) {
    return null;
  }
  return (
    <p className="rounded-lg border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
      {message}
    </p>
  );
}

function matchesMiddlewareNode(node: RuntimeMiddlewareNode, query: string) {
  if (!query) {
    return true;
  }
  const haystack = [
    node.id,
    node.kind,
    node.title,
    node.description,
    node.category,
    node.icon,
    ...(node.tags ?? []),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

export default function NodePalette() {
  const [activeTab, setActiveTab] = useState<PaletteTab>("workflow");
  const [searchQuery, setSearchQuery] = useState("");
  const [nodeRegistry, setNodeRegistry] = useState<WorkflowNodeRegistryResponse>(
    workflowNodeRegistryFallback,
  );
  const [registryLoading, setRegistryLoading] = useState(false);
  const [registryError, setRegistryError] = useState<string | null>(null);
  const [middlewareNodes, setMiddlewareNodes] = useState<RuntimeMiddlewareNode[]>(
    [],
  );
  const [middlewareLoading, setMiddlewareLoading] = useState(false);
  const [middlewareError, setMiddlewareError] = useState<string | null>(null);

  const normalizedSearch = searchQuery.trim().toLowerCase();

  useEffect(() => {
    let isMounted = true;
    setRegistryLoading(true);
    fetchWorkflowNodeRegistry()
      .then((registry) => {
        if (!isMounted) {
          return;
        }
        setNodeRegistry(registry);
        setRegistryError(null);
      })
      .catch((error) => {
        console.error("Failed to load workflow node registry:", error);
        if (!isMounted) {
          return;
        }
        setNodeRegistry(workflowNodeRegistryFallback);
        setRegistryError("节点注册表加载失败，已使用本地节点库。");
      })
      .finally(() => {
        if (isMounted) {
          setRegistryLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    let isMounted = true;
    setMiddlewareLoading(true);
    fetchRuntimeMiddlewareNodes()
      .then((nodes) => {
        if (!isMounted) {
          return;
        }
        setMiddlewareNodes(nodes.filter((node) => node.enabled));
        setMiddlewareError(null);
      })
      .catch((error) => {
        console.error("Failed to load middleware nodes:", error);
        if (!isMounted) {
          return;
        }
        setMiddlewareError("加载中间件节点失败");
      })
      .finally(() => {
        if (isMounted) {
          setMiddlewareLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  const filteredWorkflowSections = useMemo(
    () =>
      nodeRegistry.sections
        .filter((section) => !section.tab || section.tab === "workflow")
        .map((section) => ({
          ...section,
          items: section.items
            .filter((item) => item.enabled !== false)
            .filter((item) => matchesWorkflowPaletteQuery(item, normalizedSearch)),
          placeholders: (section.placeholders ?? []).filter((item) =>
            matchesWorkflowPaletteQuery(item, normalizedSearch),
          ),
        }))
        .filter(
          (section) =>
            section.items.length > 0 || (section.placeholders ?? []).length > 0,
        ),
    [nodeRegistry.sections, normalizedSearch],
  );

  const filteredMiddlewareNodes = useMemo(
    () =>
      middlewareNodes.filter((node) =>
        matchesMiddlewareNode(node, normalizedSearch),
      ),
    [middlewareNodes, normalizedSearch],
  );

  const filteredKnowledgeItems = useMemo(
    () =>
      nodeRegistry.knowledge_pipeline.items
        .filter((item) => item.enabled !== false)
        .filter((item) => matchesWorkflowPaletteQuery(item, normalizedSearch)),
    [nodeRegistry.knowledge_pipeline.items, normalizedSearch],
  );

  const filteredKnowledgePlaceholders = useMemo(
    () =>
      nodeRegistry.knowledge_pipeline.placeholders.filter((item) =>
        matchesWorkflowPaletteQuery(item, normalizedSearch),
      ),
    [nodeRegistry.knowledge_pipeline.placeholders, normalizedSearch],
  );

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <label className="sr-only" htmlFor="workflow-node-palette-search">
          搜索节点
        </label>
        <input
          className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-hire-300/45 focus:bg-white/[0.06]"
          id="workflow-node-palette-search"
          onChange={(event) => setSearchQuery(event.target.value)}
          placeholder="搜索节点、分类或标签"
          value={searchQuery}
        />
        <div className="grid grid-cols-3 gap-1 rounded-lg border border-white/10 bg-white/[0.035] p-1">
          {paletteTabs.map((tab) => (
            <button
              className={`rounded-md px-2 py-1.5 text-xs font-semibold transition ${
                activeTab === tab.id
                  ? "bg-hire-300/20 text-hire-100 shadow-sm"
                  : "text-slate-400 hover:bg-white/[0.05] hover:text-slate-200"
              }`}
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              type="button"
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {activeTab === "workflow" ? (
        <div className="space-y-4">
          {registryLoading ? (
            <p className="text-[11px] text-slate-500">正在同步节点注册表...</p>
          ) : null}
          <RegistryFallbackNotice message={registryError} />
          {filteredWorkflowSections.length === 0 ? (
            <EmptyState>没有匹配的工作流节点。</EmptyState>
          ) : (
            filteredWorkflowSections.map((section) => (
              <section className="space-y-2" key={section.id}>
                <div>
                  <h3 className="text-sm font-semibold text-slate-200">
                    {section.label}
                  </h3>
                  <p className="mt-0.5 text-xs leading-5 text-slate-500">
                    {section.description}
                  </p>
                </div>
                <div className="space-y-2">
                  {section.items.map((item) => (
                    <NormalNodeButton item={item} key={item.kind} />
                  ))}
                  {(section.placeholders ?? []).map((item) => (
                    <PlaceholderCard item={item} key={item.id} />
                  ))}
                </div>
              </section>
            ))
          )}
        </div>
      ) : null}

      {activeTab === "middleware" ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-200">
              智能体中间件
            </h3>
            {middlewareLoading ? (
              <span className="text-[11px] text-slate-500">加载中...</span>
            ) : null}
          </div>

          {middlewareError ? (
            <p className="rounded-lg border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
              {middlewareError}，工作流节点可继续使用。
            </p>
          ) : null}

          {!middlewareError && filteredMiddlewareNodes.length === 0 ? (
            <EmptyState>
              {middlewareLoading
                ? "正在加载中间件节点。"
                : "没有匹配的中间件节点。"}
            </EmptyState>
          ) : null}

          {!middlewareError && filteredMiddlewareNodes.length > 0 ? (
            <div className="space-y-2">
              {filteredMiddlewareNodes.map((node) => (
                <button
                  className={paletteCardClass()}
                  draggable
                  key={node.id}
                  onDragStart={(event) => {
                    const payload = {
                      kind: "runtime_middleware",
                      runtimeMiddlewareId: node.id,
                      runtimeMiddlewareKind: node.kind,
                      title: node.title,
                      description: node.description,
                      fields: node.fields,
                      metadata: node.metadata ?? {},
                    };
                    const serialized = JSON.stringify(payload);
                    event.dataTransfer.setData(
                      "application/modelmirror-node",
                      serialized,
                    );
                    event.dataTransfer.setData(
                      "application/modelmirror-runtime-middleware",
                      serialized,
                    );
                    event.dataTransfer.effectAllowed = "move";
                  }}
                  type="button"
                >
                  <span className="flex items-start gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-hire-300/25 bg-hire-300/10 text-hire-100">
                      <MiddlewareIcon icon={node.icon} />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold text-white">
                        {node.title}
                      </span>
                      <span className="mt-1 block text-xs leading-5 text-slate-400">
                        {node.description}
                      </span>
                    </span>
                  </span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {activeTab === "knowledge" ? (
        <div className="space-y-4">
          <RegistryFallbackNotice message={registryError} />
          <section className="space-y-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-200">
                引用与检索
              </h3>
              <p className="mt-0.5 text-xs leading-5 text-slate-500">
                当前先暴露可运行的 CitationAnchor 节点。
              </p>
            </div>
            {filteredKnowledgeItems.length === 0 ? (
              <EmptyState>没有匹配的知识引用节点。</EmptyState>
            ) : (
              <div className="space-y-2">
                {filteredKnowledgeItems.map((item) => (
                  <NormalNodeButton item={item} key={item.kind} />
                ))}
              </div>
            )}
          </section>

          <section className="space-y-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-200">
                流水线阶段
              </h3>
              <p className="mt-0.5 text-xs leading-5 text-slate-500">
                智能体资源草稿入口，当前不会直接创建工作流节点。
              </p>
            </div>
            {filteredKnowledgePlaceholders.length === 0 ? (
              <EmptyState>没有匹配的知识流水线条目。</EmptyState>
            ) : (
              <div className="space-y-2">
                {filteredKnowledgePlaceholders.map((item) => (
                  <PlaceholderCard item={item} key={item.id} />
                ))}
              </div>
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
}
