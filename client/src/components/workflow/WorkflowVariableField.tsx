import { useMemo, useRef } from "react";

import type {
  WorkflowEdge,
  WorkflowNode,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import type { WorkflowNodeContractProjection } from "./workflowNodeRegistry";
import VariableInsertMenu, {
  collectWorkflowVariableOptions,
} from "./VariableInsertMenu";
import {
  analyzeWorkflowVariables,
  getWorkflowVariableFieldDescriptor,
  resolveWorkflowVariableFieldTypes,
  type WorkflowVariableFieldDescriptor,
} from "./workflowVariables";

const defaultInputClass =
  "modelmirror-form-control w-full rounded-lg border border-white/10 bg-[#0f1728] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10";

function splitBindingList(value: string) {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function variableFieldWarning(
  node: WorkflowNode,
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  descriptor: WorkflowVariableFieldDescriptor,
  value: string,
  contract?: WorkflowNodeContractProjection | null,
  declarations: WorkflowVariableDeclaration[] = [],
) {
  const trimmed = value.trim();
  if (!trimmed) return "";
  if (descriptor.mode === "declaration") {
    if (!/^[A-Za-z_][\w.-]*$/.test(trimmed)) {
      return "变量名格式异常；旧值会保留，但建议使用字母或下划线开头。";
    }
    const declared = analyzeWorkflowVariables(
      nodes,
      edges,
      node.id,
      declarations,
    ).find(
      (variable) => variable.name === trimmed,
    );
    if (declared?.availability === "conflict") return declared.availabilityReason;
    return "";
  }

  const inventory = analyzeWorkflowVariables(nodes, edges, node.id, declarations);
  const acceptedTypes = new Set(
    resolveWorkflowVariableFieldTypes(descriptor, contract),
  );
  const localNames = new Set(
    (descriptor.localVariables ?? []).map((local) =>
      local.name === "item"
        ? String(node.data.iterationVariable ?? "").trim() || local.name
        : local.name,
    ),
  );
  const names = descriptor.mode === "binding-list"
    ? splitBindingList(value)
    : descriptor.mode === "template"
      ? [...value.matchAll(/{{\s*([A-Za-z_][\w.-]*)\s*}}/g)]
          .map((match) => match[1])
          .filter((name) => !localNames.has(name))
      : [trimmed];
  const warnings = names.flatMap((name) => {
    const variable = inventory.find((candidate) => candidate.name === name);
    if (!variable) return [`${name}：未找到变量生产者，旧引用会原样保留。`];
    if (variable.availability !== "available") {
      return [`${name}：${variable.availabilityReason}`];
    }
    if (!acceptedTypes.has(variable.valueType)) {
      return [
        `${name}：类型不匹配；字段接受 ${[...acceptedTypes].join(" / ")}，变量为 ${variable.valueType}。`,
      ];
    }
    return [];
  });
  return warnings.join(" ");
}

export default function WorkflowVariableField({
  node,
  nodes,
  edges,
  fieldName,
  value,
  onChange,
  contract,
  descriptor: descriptorOverride,
  declarations = [],
  multiline = false,
  className = "",
  inputClassName = defaultInputClass,
  placeholder,
  ariaLabel,
}: {
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  fieldName: string;
  value: string;
  onChange: (value: string) => void;
  contract?: WorkflowNodeContractProjection | null;
  descriptor?: WorkflowVariableFieldDescriptor;
  declarations?: WorkflowVariableDeclaration[];
  multiline?: boolean;
  className?: string;
  inputClassName?: string;
  placeholder?: string;
  ariaLabel?: string;
}) {
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const descriptor =
    descriptorOverride ??
    getWorkflowVariableFieldDescriptor(node.data.kind, fieldName);

  const variables = useMemo(
    () =>
      descriptor
        ? collectWorkflowVariableOptions(
            node.id,
            nodes,
            edges,
            descriptor,
            contract,
            declarations,
          )
        : [],
    [contract, declarations, descriptor, edges, node.id, nodes],
  );
  const warning = useMemo(
    () =>
      descriptor
        ? variableFieldWarning(
            node,
            nodes,
            edges,
            descriptor,
            value,
            contract,
            declarations,
          )
        : "",
    [contract, declarations, descriptor, edges, node, nodes, value],
  );

  const pickVariable = (name: string) => {
    if (!descriptor) return;
    if (descriptor.mode === "binding") {
      onChange(name);
    } else if (descriptor.mode === "binding-list") {
      const current = splitBindingList(value);
      if (!current.includes(name)) current.push(name);
      onChange(current.join(", "));
    } else if (descriptor.mode === "template") {
      const control = inputRef.current;
      const start = control?.selectionStart ?? value.length;
      const end = control?.selectionEnd ?? value.length;
      const token = `{{${name}}}`;
      onChange(`${value.slice(0, start)}${token}${value.slice(end)}`);
      requestAnimationFrame(() => {
        control?.focus();
        const cursor = start + token.length;
        control?.setSelectionRange(cursor, cursor);
      });
    }
  };

  const controlClass = `${inputClassName} ${
    descriptor && descriptor.mode !== "declaration" ? "pr-3" : ""
  } ${className}`.trim();
  const control = multiline ? (
    <textarea
      aria-label={ariaLabel}
      className={controlClass}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      ref={(element) => {
        inputRef.current = element;
      }}
      value={value}
    />
  ) : (
    <input
      aria-label={ariaLabel}
      className={controlClass}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      ref={(element) => {
        inputRef.current = element;
      }}
      value={value}
    />
  );

  return (
    <div>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">{control}</div>
        {descriptor && descriptor.mode !== "declaration" ? (
          <VariableInsertMenu
            label={descriptor.mode === "template" ? "插入变量" : "绑定变量"}
            onPick={pickVariable}
            template={descriptor.mode === "template"}
            variables={variables}
          />
        ) : null}
      </div>
      {warning ? (
        <p className="mt-1.5 text-[11px] leading-5 text-amber-200" role="status">
          {warning}
        </p>
      ) : null}
    </div>
  );
}
