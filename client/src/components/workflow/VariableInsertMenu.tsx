import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type {
  WorkflowEdge,
  WorkflowNode,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import type { WorkflowNodeContractProjection } from "./workflowNodeRegistry";
import {
  analyzeWorkflowVariables,
  analyzeWorkflowVariablesForField,
  resolveWorkflowVariableFieldTypes,
  type WorkflowVariableAvailability,
  type WorkflowVariableFieldDescriptor,
  type WorkflowVariableValueType,
} from "./workflowVariables";

export interface InsertableVariable {
  name: string;
  label?: string;
  valueType?: WorkflowVariableValueType;
  availability?: WorkflowVariableAvailability;
  disabled?: boolean;
  disabledReason?: string;
  local?: boolean;
}

const availabilityOrder: WorkflowVariableAvailability[] = [
  "available",
  "conditional",
  "unavailable",
  "conflict",
  "inventory",
];

const availabilityLabels: Record<WorkflowVariableAvailability, string> = {
  available: "可用",
  conditional: "条件可用",
  unavailable: "不可用",
  conflict: "冲突",
  inventory: "目录",
};

export function collectWorkflowVariableOptions(
  nodeId: string,
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  descriptor: WorkflowVariableFieldDescriptor,
  contract?: WorkflowNodeContractProjection | null,
  declarations: WorkflowVariableDeclaration[] = [],
): InsertableVariable[] {
  if (!nodeId) return [];
  const selectedNode = nodes.find((candidate) => candidate.id === nodeId);
  const nodeOrder = new Map(nodes.map((node, index) => [node.id, index]));
  const acceptedTypes = new Set(resolveWorkflowVariableFieldTypes(descriptor, contract));
  const globals = (selectedNode
    ? analyzeWorkflowVariablesForField(
        selectedNode,
        nodes,
        edges,
        descriptor,
        declarations,
      )
    : analyzeWorkflowVariables(nodes, edges, nodeId, declarations))
    .sort(
      (left, right) =>
        availabilityOrder.indexOf(left.availability) -
          availabilityOrder.indexOf(right.availability) ||
        (nodeOrder.get(left.sources[0]?.nodeId ?? "") ?? Number.MAX_SAFE_INTEGER) -
          (nodeOrder.get(right.sources[0]?.nodeId ?? "") ?? Number.MAX_SAFE_INTEGER) ||
        left.name.localeCompare(right.name, "zh-CN"),
    )
    .map((variable) => {
      const typeMismatch = !acceptedTypes.has(variable.valueType);
      const unavailable = variable.availability !== "available";
      return {
        name: variable.name,
        label: variable.sources[0]?.nodeTitle ?? "未定义引用",
        valueType: variable.valueType,
        availability: variable.availability,
        disabled: unavailable || typeMismatch,
        disabledReason: typeMismatch
          ? `类型不匹配：字段接受 ${[...acceptedTypes].join(" / ")}，变量为 ${variable.valueType}。`
          : variable.availabilityReason,
      } satisfies InsertableVariable;
    });

  const localVariables = (descriptor.localVariables ?? []).map((local) => {
    const name =
      Number(selectedNode?.data.contractVersion) === 2
        ? local.name === "item"
          ? String(selectedNode?.data.itemVariable ?? "").trim() || local.name
          : local.name === "item_index"
            ? String(selectedNode?.data.indexVariable ?? "").trim() || local.name
            : local.name
        : local.name === "item"
          ? String(selectedNode?.data.iterationVariable ?? "").trim() || local.name
          : local.name;
    const typeMismatch = !acceptedTypes.has(local.valueType);
    return {
      name,
      label: local.label,
      valueType: local.valueType,
      availability: "available" as const,
      disabled: typeMismatch,
      disabledReason: typeMismatch ? "局部变量类型与当前字段不兼容。" : undefined,
      local: true,
    } satisfies InsertableVariable;
  });
  return [...localVariables, ...globals];
}

/** 兼容第一批和旧测试：只返回确定可用的模板变量。 */
export function collectUpstreamInsertableVariables(
  nodeId: string,
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
): InsertableVariable[] {
  if (!nodeId) return [];
  const nodeOrder = new Map(nodes.map((node, index) => [node.id, index]));
  return analyzeWorkflowVariables(nodes, edges, nodeId)
    .filter((variable) => variable.availability === "available")
    .sort(
      (left, right) =>
        (nodeOrder.get(left.sources[0]?.nodeId ?? "") ?? Number.MAX_SAFE_INTEGER) -
        (nodeOrder.get(right.sources[0]?.nodeId ?? "") ?? Number.MAX_SAFE_INTEGER),
    )
    .map((variable) => ({
      name: variable.name,
      label: variable.sources[0]?.nodeTitle,
    }));
}

function groupKey(item: InsertableVariable): WorkflowVariableAvailability {
  if (item.local) return "available";
  return item.availability ?? (item.disabled ? "unavailable" : "available");
}

export default function VariableInsertMenu({
  variables,
  onPick,
  disabled = false,
  label = "选择变量",
  template = true,
  onOpenChange,
}: {
  variables: InsertableVariable[];
  onPick: (name: string) => void;
  disabled?: boolean;
  label?: string;
  template?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  const setMenuOpen = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
    if (!next) {
      setQuery("");
      requestAnimationFrame(() => triggerRef.current?.focus());
    }
  };

  useEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const menuWidth = Math.min(360, window.innerWidth - 24);
    const left = Math.max(
      12,
      Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 12),
    );
    const estimatedHeight = 420;
    const top =
      rect.bottom + estimatedHeight <= window.innerHeight - 12
        ? rect.bottom + 6
        : Math.max(12, rect.top - estimatedHeight - 6);
    setPosition({ left, top });
    requestAnimationFrame(() => searchRef.current?.focus());
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return variables;
    return variables.filter(
      (item) =>
        item.name.toLowerCase().includes(normalized) ||
        (item.label ?? "").toLowerCase().includes(normalized) ||
        (item.disabledReason ?? "").toLowerCase().includes(normalized),
    );
  }, [query, variables]);

  const grouped = useMemo(
    () =>
      availabilityOrder
        .map((availability) => ({
          availability,
          items: filtered.filter((item) => groupKey(item) === availability),
        }))
        .filter((group) => group.items.length > 0),
    [filtered],
  );

  return (
    <>
      <button
        aria-expanded={open}
        aria-haspopup="dialog"
        className="inline-flex min-h-8 shrink-0 items-center justify-center rounded-md border border-brand-300/30 bg-brand-300/10 px-2.5 text-[11px] font-semibold text-brand-100 transition hover:bg-brand-300/20 focus:outline-none focus:ring-2 focus:ring-brand-300/40 disabled:cursor-not-allowed disabled:opacity-50"
        disabled={disabled || variables.length === 0}
        onClick={() => setMenuOpen(!open)}
        ref={triggerRef}
        title={label}
        type="button"
      >
        ＋ {label}
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
            <>
              <button
                aria-label="关闭变量选择器"
                className="fixed inset-0 z-[70] cursor-default bg-transparent"
                onClick={() => setMenuOpen(false)}
                type="button"
              />
              <div
                aria-label="变量选择器"
                aria-modal="true"
                className="fixed z-[71] w-[min(360px,calc(100vw-24px))] overflow-hidden rounded-xl border border-white/12 bg-[#101828] shadow-2xl shadow-ink-950/70"
                role="dialog"
                style={position}
              >
                <div className="border-b border-white/10 p-3">
                  <p className="mb-2 text-xs font-semibold text-slate-200">
                    {template ? "插入模板变量" : "绑定工作流变量"}
                  </p>
                  <input
                    className="modelmirror-form-control w-full rounded-lg border border-white/10 bg-[#0b1324] px-3 py-2 text-sm text-white outline-none placeholder:text-slate-500 focus:border-brand-300/50 focus:ring-2 focus:ring-brand-300/15"
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="搜索名称、来源或状态"
                    ref={searchRef}
                    value={query}
                  />
                </div>
                <div className="max-h-80 overflow-y-auto p-2">
                  {grouped.length === 0 ? (
                    <p className="px-2.5 py-6 text-center text-xs text-slate-500">
                      没有匹配的变量。
                    </p>
                  ) : (
                    grouped.map((group) => (
                      <section className="mb-2 last:mb-0" key={group.availability}>
                        <h4 className="px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                          {availabilityLabels[group.availability]}
                        </h4>
                        <div className="space-y-1">
                          {group.items.map((item) => (
                            <button
                              aria-disabled={item.disabled || undefined}
                              className={`w-full rounded-lg border px-2.5 py-2 text-left transition focus:outline-none focus:ring-2 focus:ring-brand-300/35 ${
                                item.disabled
                                  ? "cursor-not-allowed border-transparent bg-white/[0.025] text-slate-500"
                                  : "border-transparent text-slate-200 hover:border-white/10 hover:bg-white/[0.07]"
                              }`}
                              key={`${item.local ? "local" : "global"}:${item.name}`}
                              onClick={() => {
                                if (item.disabled) return;
                                onPick(item.name);
                                setMenuOpen(false);
                              }}
                              title={item.disabledReason}
                              type="button"
                            >
                              <span className="flex items-center justify-between gap-3">
                                <span className="truncate font-mono text-xs text-brand-100">
                                  {template ? `{{${item.name}}}` : item.name}
                                </span>
                                <span className="shrink-0 text-[10px] text-slate-500">
                                  {item.valueType ?? "unknown"}
                                </span>
                              </span>
                              <span className="mt-1 block truncate text-[11px] text-slate-500">
                                {item.disabled ? item.disabledReason : item.label}
                              </span>
                            </button>
                          ))}
                        </div>
                      </section>
                    ))
                  )}
                </div>
              </div>
            </>,
            document.body,
          )
        : null}
    </>
  );
}
