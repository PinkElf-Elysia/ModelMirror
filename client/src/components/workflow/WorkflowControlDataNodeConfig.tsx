import { useEffect, useState, type ReactNode } from "react";

import type {
  WorkflowAggregateMeasure,
  WorkflowComparisonOperator,
  WorkflowComparisonRule,
  WorkflowComparisonValueType,
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeData,
  WorkflowObjectOperation,
  WorkflowSortKey,
  WorkflowValue,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import type { WorkflowNodeContractProjection } from "./workflowNodeRegistry";
import WorkflowVariableField from "./WorkflowVariableField";


const inputClass =
  "modelmirror-form-control w-full rounded-lg border border-white/10 bg-[#0f1728] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-400 hover:border-white/20 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10";
const compactInputClass = `${inputClass} px-2.5 py-1.5 text-xs`;

const operatorLabels: Record<WorkflowComparisonOperator, string> = {
  equals: "等于",
  not_equals: "不等于",
  gt: "大于",
  gte: "大于或等于",
  lt: "小于",
  lte: "小于或等于",
  contains: "包含",
  in: "属于集合",
  is_null: "为空",
};

const valueTypeLabels: Record<WorkflowComparisonValueType, string> = {
  text: "文本",
  number: "数字",
  boolean: "布尔值",
  null: "空值",
  json: "JSON",
};

function ConfigField({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-slate-200">
        {label}
      </span>
      {children}
      {hint ? (
        <span className="mt-1.5 block text-[11px] leading-5 text-slate-400">
          {hint}
        </span>
      ) : null}
    </label>
  );
}

function SectionHeader({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-white/10 pb-2">
      <div>
        <p className="text-xs font-semibold text-white">{title}</p>
        <p className="mt-1 text-[11px] leading-5 text-slate-400">{detail}</p>
      </div>
      {action}
    </div>
  );
}

function defaultValueForType(type: WorkflowComparisonValueType): WorkflowValue {
  if (type === "number") return 0;
  if (type === "boolean") return true;
  if (type === "null") return null;
  if (type === "json") return [];
  return "";
}

export function comparisonValueText(value: WorkflowValue | undefined) {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function TypedValueEditor({
  rule,
  onChange,
  ariaPrefix,
}: {
  rule: WorkflowComparisonRule;
  onChange: (patch: Partial<WorkflowComparisonRule>) => void;
  ariaPrefix: string;
}) {
  const valueType = rule.valueType ?? "text";
  const [jsonText, setJsonText] = useState(comparisonValueText(rule.value));
  const [jsonError, setJsonError] = useState("");

  useEffect(() => {
    setJsonText(comparisonValueText(rule.value));
    setJsonError("");
  }, [rule.value, valueType]);

  if (rule.operator === "is_null") {
    return <p className="text-[11px] leading-5 text-slate-400">此规则不需要比较值。</p>;
  }

  return (
    <div className="space-y-2">
      <select
        aria-label={`${ariaPrefix} 比较值类型`}
        className={compactInputClass}
        onChange={(event) => {
          const nextType = event.target.value as WorkflowComparisonValueType;
          onChange({ valueType: nextType, value: defaultValueForType(nextType) });
        }}
        value={valueType}
      >
        {Object.entries(valueTypeLabels).map(([value, label]) => (
          <option className="bg-slate-950" key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      {valueType === "text" ? (
        <input
          aria-label={`${ariaPrefix} 比较文本`}
          className={compactInputClass}
          onChange={(event) => onChange({ value: event.target.value })}
          value={typeof rule.value === "string" ? rule.value : ""}
        />
      ) : null}
      {valueType === "number" ? (
        <input
          aria-label={`${ariaPrefix} 比较数字`}
          className={compactInputClass}
          onChange={(event) => {
            const value = Number(event.target.value);
            if (Number.isFinite(value)) onChange({ value });
          }}
          step="any"
          type="number"
          value={typeof rule.value === "number" ? rule.value : 0}
        />
      ) : null}
      {valueType === "boolean" ? (
        <select
          aria-label={`${ariaPrefix} 比较布尔值`}
          className={compactInputClass}
          onChange={(event) => onChange({ value: event.target.value === "true" })}
          value={rule.value === false ? "false" : "true"}
        >
          <option className="bg-slate-950" value="true">是</option>
          <option className="bg-slate-950" value="false">否</option>
        </select>
      ) : null}
      {valueType === "null" ? (
        <p className="text-[11px] leading-5 text-slate-400">比较值固定为空。</p>
      ) : null}
      {valueType === "json" ? (
        <>
          <textarea
            aria-label={`${ariaPrefix} 比较 JSON`}
            className={`${compactInputClass} min-h-20 resize-y font-mono leading-5`}
            onBlur={() => {
              try {
                const parsed = JSON.parse(jsonText) as WorkflowValue;
                onChange({ value: parsed });
                setJsonError("");
              } catch {
                setJsonError("JSON 格式无效，修正后再离开输入框。");
              }
            }}
            onChange={(event) => setJsonText(event.target.value)}
            value={jsonText}
          />
          {jsonError ? (
            <p className="text-[11px] leading-5 text-rose-200" role="alert">
              {jsonError}
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function RuleEditor({
  rule,
  allowField,
  onChange,
  ariaPrefix,
}: {
  rule: WorkflowComparisonRule;
  allowField: boolean;
  onChange: (rule: WorkflowComparisonRule) => void;
  ariaPrefix: string;
}) {
  const patchRule = (patch: Partial<WorkflowComparisonRule>) =>
    onChange({ ...rule, ...patch });
  return (
    <div className="space-y-2">
      {allowField ? (
        <input
          aria-label={`${ariaPrefix} 顶层字段`}
          className={compactInputClass}
          onChange={(event) => patchRule({ field: event.target.value })}
          placeholder="顶层字段，留空表示整项"
          value={rule.field ?? ""}
        />
      ) : null}
      <select
        aria-label={`${ariaPrefix} 比较运算符`}
        className={compactInputClass}
        onChange={(event) => {
          const operator = event.target.value as WorkflowComparisonOperator;
          patchRule(
            operator === "is_null"
              ? { operator, valueType: "null", value: null }
              : operator === "in"
                ? { operator, valueType: "json", value: [] }
                : ["gt", "gte", "lt", "lte"].includes(operator)
                  ? {
                      operator,
                      valueType: "number",
                      value: typeof rule.value === "number" ? rule.value : 0,
                    }
                : { operator },
          );
        }}
        value={rule.operator}
      >
        {Object.entries(operatorLabels).map(([value, label]) => (
          <option className="bg-slate-950" key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      <TypedValueEditor ariaPrefix={ariaPrefix} onChange={patchRule} rule={rule} />
    </div>
  );
}

function SmallButton({
  ariaLabel,
  children,
  disabled,
  onClick,
  title,
}: {
  ariaLabel?: string;
  children: ReactNode;
  disabled?: boolean;
  onClick: () => void;
  title?: string;
}) {
  return (
    <button
      aria-label={ariaLabel}
      className="rounded-md border border-white/15 px-2 py-1 text-[11px] font-medium text-slate-200 transition hover:border-white/30 hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-40"
      disabled={disabled}
      onClick={onClick}
      title={title}
      type="button"
    >
      {children}
    </button>
  );
}

function VariableCenterShortcut({ onOpen }: { onOpen: () => void }) {
  return (
    <button
      className="text-[11px] font-medium text-cyan-200 underline decoration-cyan-300/30 underline-offset-4 hover:text-cyan-100"
      onClick={onOpen}
      type="button"
    >
      管理全局变量
    </button>
  );
}

export default function WorkflowControlDataNodeConfig({
  contract,
  data,
  declarations,
  edges,
  node,
  nodes,
  onChange,
  onOpenVariableCenter,
}: {
  contract?: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  declarations?: WorkflowVariableDeclaration[];
  edges: WorkflowEdge[];
  node: WorkflowNode;
  nodes: WorkflowNode[];
  onChange: (patch: Partial<WorkflowNodeData>) => void;
  onOpenVariableCenter: () => void;
}) {
  const routes = data.routes ?? [];

  if (data.kind === "condition") {
    const isV2 = String(data.contractVersion ?? "1") === "2";
    if (!isV2) {
      return (
        <div className="space-y-4">
          <div className="rounded-lg border border-amber-300/25 bg-amber-300/[0.08] px-3 py-2 text-xs leading-5 text-amber-50">
            这是旧版文本条件，仍可运行和发布。升级后会严格区分文本、数字、布尔值、空值和 JSON。
          </div>
          <ConfigField label="判断变量">
            <WorkflowVariableField
              contract={contract}
              declarations={declarations}
              edges={edges}
              fieldName="conditionVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => onChange({ conditionVariable: value })}
              value={data.conditionVariable ?? ""}
            />
          </ConfigField>
          <ConfigField label="判断方式">
            <select
              className={inputClass}
              onChange={(event) => onChange({ conditionOperator: event.target.value as "equals" | "contains" })}
              value={data.conditionOperator ?? "contains"}
            >
              <option className="bg-slate-950" value="contains">包含</option>
              <option className="bg-slate-950" value="equals">等于</option>
            </select>
          </ConfigField>
          <ConfigField label="比较文本">
            <input className={inputClass} onChange={(event) => onChange({ conditionValue: event.target.value })} value={data.conditionValue ?? ""} />
          </ConfigField>
          <button
            className="w-full rounded-lg bg-cyan-300 px-3 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 focus:outline-none focus:ring-4 focus:ring-cyan-300/20"
            onClick={() => onChange({
              contractVersion: 2,
              inputVariable: data.conditionVariable ?? "user_input",
              field: "",
              operator: data.conditionOperator ?? "contains",
              valueType: "text",
              value: data.conditionValue ?? "",
            })}
            type="button"
          >
            升级为类型化条件
          </button>
        </div>
      );
    }
    const rule: WorkflowComparisonRule = {
      field: data.field ?? "",
      operator: (data.operator as WorkflowComparisonOperator | undefined) ?? "equals",
      valueType: data.valueType ?? "text",
      value: data.value,
    };
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.07] px-3 py-2 text-xs leading-5 text-cyan-50">
          类型不匹配、变量不存在或字段缺失时会停止工作流，不会误走“否”出口。
        </div>
        <ConfigField label="判断变量">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="inputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ inputVariable: value })} value={data.inputVariable ?? ""} />
        </ConfigField>
        <div className="flex justify-end"><VariableCenterShortcut onOpen={onOpenVariableCenter} /></div>
        <SectionHeader detail="字段留空时判断整个变量；填写后只读取对象的一个顶层字段。" title="比较规则" />
        <RuleEditor
          allowField
          ariaPrefix="类型化条件"
          onChange={(next) => onChange({
            field: next.field ?? "",
            operator: next.operator,
            valueType: next.valueType,
            value: next.value,
          })}
          rule={rule}
        />
      </div>
    );
  }

  if (data.kind === "terminate_error") {
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-rose-300/25 bg-rose-300/[0.08] px-3 py-2 text-xs leading-5 text-rose-50">
          到达此节点后执行立即失败，后续节点不会运行。消息只能是固定文本，不允许变量模板。
        </div>
        <ConfigField label="安全错误码" hint="使用大写字母、数字和下划线，最多 64 个字符。">
          <input
            className={inputClass}
            onChange={(event) => onChange({ errorCode: event.target.value.toUpperCase() })}
            value={data.errorCode ?? ""}
          />
        </ConfigField>
        <ConfigField label="错误消息" hint="此消息会进入失败摘要，请勿填写密钥、令牌或请求正文。">
          <textarea
            className={`${inputClass} min-h-24 resize-y leading-6`}
            maxLength={2000}
            onChange={(event) => onChange({ message: event.target.value })}
            value={data.message ?? ""}
          />
        </ConfigField>
      </div>
    );
  }

  if (data.kind === "multi_route") {
    const connectedHandles = new Set(
      edges
        .filter((edge) => edge.source === node.id)
        .map((edge) => edge.sourceHandle ?? ""),
    );
    const updateRoute = (index: number, route: WorkflowComparisonRule) =>
      onChange({ routes: routes.map((item, itemIndex) => itemIndex === index ? route : item) });
    return (
      <div className="space-y-4">
        <ConfigField label="判断变量">
          <WorkflowVariableField
            contract={contract}
            declarations={declarations}
            edges={edges}
            fieldName="inputVariable"
            node={node}
            nodes={nodes}
            onChange={(value) => onChange({ inputVariable: value })}
            value={data.inputVariable ?? ""}
          />
        </ConfigField>
        <div className="flex justify-end"><VariableCenterShortcut onOpen={onOpenVariableCenter} /></div>
        <SectionHeader
          detail="从上到下匹配，首个命中后停止。调整顺序不会改变已连接的出口。"
          title="分派规则"
        />
        <div className="divide-y divide-white/10 rounded-lg border border-white/10 bg-black/10">
          {routes.map((route, index) => {
            const routeId = route.id ?? `route_${index + 1}`;
            const connected = connectedHandles.has(routeId);
            return (
              <div className="space-y-3 p-3" key={routeId}>
                <div className="flex items-center gap-2">
                  <span className="rounded-md bg-amber-300/10 px-2 py-1 font-mono text-[10px] text-amber-100">
                    {routeId}
                  </span>
                  <input
                    aria-label={`${routeId} 标签`}
                    className={`${compactInputClass} min-w-0 flex-1`}
                    onChange={(event) => updateRoute(index, { ...route, label: event.target.value })}
                    value={route.label ?? ""}
                  />
                </div>
                <RuleEditor ariaPrefix={routeId} allowField={false} onChange={(next) => updateRoute(index, next)} rule={route} />
                <div className="flex flex-wrap gap-1.5">
                  <SmallButton disabled={index === 0} onClick={() => {
                    const next = [...routes];
                    [next[index - 1], next[index]] = [next[index], next[index - 1]];
                    onChange({ routes: next });
                  }}>上移</SmallButton>
                  <SmallButton disabled={index === routes.length - 1} onClick={() => {
                    const next = [...routes];
                    [next[index], next[index + 1]] = [next[index + 1], next[index]];
                    onChange({ routes: next });
                  }}>下移</SmallButton>
                  <SmallButton
                    disabled={routes.length <= 2 || connected}
                    onClick={() => onChange({ routes: routes.filter((_, itemIndex) => itemIndex !== index) })}
                    title={connected ? "先删除此出口的画布连线" : undefined}
                  >删除规则</SmallButton>
                  {connected ? <span className="text-[10px] leading-6 text-amber-200">已连线，需先删除连线</span> : null}
                </div>
              </div>
            );
          })}
        </div>
        <SmallButton
          disabled={routes.length >= 8}
          onClick={() => {
            const used = new Set(routes.map((route) => route.id));
            const id = ([1, 2, 3, 4, 5, 6, 7, 8]
              .map((index) => `route_${index}` as WorkflowComparisonRule["id"])
              .find((candidate) => !used.has(candidate)) ?? "route_8");
            onChange({
              routes: [...routes, { id, label: `情况 ${routes.length + 1}`, operator: "equals", valueType: "text", value: "" }],
            });
          }}
        >添加规则</SmallButton>
        <p className="rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2 text-[11px] leading-5 text-slate-300">
          默认出口始终存在。每条规则和默认出口都必须在画布上恰好连接一次。
        </p>
      </div>
    );
  }

  if (data.kind === "list_operation") {
    const filterRules = data.filterRules ?? [];
    const sortKeys = data.sortKeys ?? [];
    const deduplicateFields = data.deduplicateFields ?? [];
    return (
      <div className="space-y-4">
        <ConfigField label="输入列表变量">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="inputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ inputVariable: value })} value={data.inputVariable ?? ""} />
        </ConfigField>
        <div className="flex justify-end"><VariableCenterShortcut onOpen={onOpenVariableCenter} /></div>
        <ConfigField label="列表操作">
          <select className={inputClass} onChange={(event) => onChange({ operator: event.target.value as WorkflowNodeData["operator"] })} value={data.operator ?? "length"}>
            <option className="bg-slate-950" value="length">计算长度</option>
            <option className="bg-slate-950" value="join">拼接文本</option>
            <option className="bg-slate-950" value="first">取第一项</option>
            <option className="bg-slate-950" value="last">取最后一项</option>
            <option className="bg-slate-950" value="filter">筛选</option>
            <option className="bg-slate-950" value="sort">排序</option>
            <option className="bg-slate-950" value="deduplicate">去重</option>
            <option className="bg-slate-950" value="take">保留前几项</option>
            <option className="bg-slate-950" value="skip">跳过前几项</option>
            <option className="bg-slate-950" value="slice">按位置截取</option>
          </select>
        </ConfigField>
        {data.operator === "join" ? (
          <ConfigField label="拼接分隔符"><input className={inputClass} onChange={(event) => onChange({ joinSeparator: event.target.value })} value={data.joinSeparator ?? ""} /></ConfigField>
        ) : null}
        {data.operator === "filter" ? (
          <div className="space-y-3">
            <SectionHeader detail="对象数组支持顶层字段，留空字段可筛选标量数组。" title="筛选规则" />
            <select className={inputClass} onChange={(event) => onChange({ filterMode: event.target.value as "all" | "any" })} value={data.filterMode ?? "all"}>
              <option className="bg-slate-950" value="all">满足全部规则</option>
              <option className="bg-slate-950" value="any">满足任一规则</option>
            </select>
            <div className="divide-y divide-white/10 rounded-lg border border-white/10 bg-black/10">
              {filterRules.map((rule, index) => (
                <div className="space-y-2 p-3" key={`filter-${index}`}>
                  <RuleEditor ariaPrefix={`筛选规则 ${index + 1}`} allowField onChange={(next) => onChange({ filterRules: filterRules.map((item, itemIndex) => itemIndex === index ? next : item) })} rule={rule} />
                  <SmallButton disabled={filterRules.length <= 1} onClick={() => onChange({ filterRules: filterRules.filter((_, itemIndex) => itemIndex !== index) })}>删除规则</SmallButton>
                </div>
              ))}
            </div>
            <SmallButton disabled={filterRules.length >= 10} onClick={() => onChange({ filterRules: [...filterRules, { field: "", operator: "equals", valueType: "text", value: "" }] })}>添加规则</SmallButton>
          </div>
        ) : null}
        {data.operator === "sort" ? (
          <div className="space-y-3">
            <SectionHeader detail="最多三个排序键，空值位置不受升降序影响。" title="排序键" />
            <div className="divide-y divide-white/10 rounded-lg border border-white/10 bg-black/10">
              {sortKeys.map((key, index) => (
                <div className="space-y-2 p-3" key={`sort-${index}`}>
                  <input aria-label={`排序键 ${index + 1} 字段`} className={compactInputClass} onChange={(event) => onChange({ sortKeys: sortKeys.map((item, itemIndex) => itemIndex === index ? { ...item, field: event.target.value } : item) })} placeholder="顶层字段，标量数组留空" value={key.field} />
                  <div className="flex gap-2">
                    <select aria-label={`排序键 ${index + 1} 方向`} className={compactInputClass} onChange={(event) => onChange({ sortKeys: sortKeys.map((item, itemIndex) => itemIndex === index ? { ...item, direction: event.target.value as WorkflowSortKey["direction"] } : item) })} value={key.direction}><option className="bg-slate-950" value="asc">升序</option><option className="bg-slate-950" value="desc">降序</option></select>
                    <select aria-label={`排序键 ${index + 1} 空值位置`} className={compactInputClass} onChange={(event) => onChange({ sortKeys: sortKeys.map((item, itemIndex) => itemIndex === index ? { ...item, nulls: event.target.value as WorkflowSortKey["nulls"] } : item) })} value={key.nulls}><option className="bg-slate-950" value="first">空值在前</option><option className="bg-slate-950" value="last">空值在后</option></select>
                  </div>
                  <SmallButton disabled={sortKeys.length <= 1} onClick={() => onChange({ sortKeys: sortKeys.filter((_, itemIndex) => itemIndex !== index) })}>删除排序键</SmallButton>
                </div>
              ))}
            </div>
            <SmallButton disabled={sortKeys.length >= 3} onClick={() => onChange({ sortKeys: [...sortKeys, { field: "", direction: "asc", nulls: "last" }] })}>添加排序键</SmallButton>
          </div>
        ) : null}
        {data.operator === "deduplicate" ? (
          <div className="space-y-3">
            <SectionHeader detail="不添加字段时按完整元素去重，添加字段时保留第一次出现的对象。" title="去重字段" />
            {deduplicateFields.map((field, index) => (
              <div className="flex gap-2" key={`dedupe-${index}`}>
                <input aria-label={`去重字段 ${index + 1}`} className={compactInputClass} onChange={(event) => onChange({ deduplicateFields: deduplicateFields.map((item, itemIndex) => itemIndex === index ? event.target.value : item) })} placeholder="顶层字段" value={field} />
                <SmallButton onClick={() => onChange({ deduplicateFields: deduplicateFields.filter((_, itemIndex) => itemIndex !== index) })}>删除</SmallButton>
              </div>
            ))}
            <SmallButton disabled={deduplicateFields.length >= 5} onClick={() => onChange({ deduplicateFields: [...deduplicateFields, ""] })}>添加字段</SmallButton>
          </div>
        ) : null}
        {data.operator === "take" || data.operator === "skip" ? (
          <ConfigField
            hint="从 0 到 10,000。该操作只接受真正的 JSON 数组。"
            label={data.operator === "take" ? "保留数量" : "跳过数量"}
          >
            <input
              className={inputClass}
              max={10000}
              min={0}
              onChange={(event) => onChange({ count: Number(event.target.value) })}
              type="number"
              value={data.count ?? 10}
            />
          </ConfigField>
        ) : null}
        {data.operator === "slice" ? (
          <div className="grid grid-cols-2 gap-3">
            <ConfigField hint="从 0 开始计数。" label="起始位置">
              <input className={inputClass} max={10000} min={0} onChange={(event) => onChange({ startIndex: Number(event.target.value) })} type="number" value={data.startIndex ?? 0} />
            </ConfigField>
            <ConfigField hint="不包含此位置。" label="结束位置">
              <input className={inputClass} max={10000} min={0} onChange={(event) => onChange({ endIndex: Number(event.target.value) })} type="number" value={data.endIndex ?? 10} />
            </ConfigField>
          </div>
        ) : null}
        <ConfigField label="输出变量">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="outputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ outputVariable: value })} value={data.outputVariable ?? ""} />
        </ConfigField>
      </div>
    );
  }

  if (data.kind === "object_transform") {
    const operations = data.operations ?? [];
    const updateOperation = (
      index: number,
      patch: Partial<WorkflowObjectOperation>,
    ) =>
      onChange({
        operations: operations.map((operation, operationIndex) =>
          operationIndex === index ? { ...operation, ...patch } : operation,
        ),
      });
    const moveOperation = (from: number, to: number) => {
      const next = [...operations];
      [next[from], next[to]] = [next[to], next[from]];
      onChange({ operations: next });
    };
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.07] px-3 py-2 text-xs leading-5 text-cyan-50">
          操作会从上到下依次执行，只处理顶层字段。删除、重命名或保留不存在的字段会明确报错，不会静默丢数据。
        </div>
        <ConfigField label="输入对象变量">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="inputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ inputVariable: value })} value={data.inputVariable ?? ""} />
        </ConfigField>
        <ConfigField label="输出对象变量">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="outputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ outputVariable: value })} value={data.outputVariable ?? ""} />
        </ConfigField>
        <div className="flex justify-end"><VariableCenterShortcut onOpen={onOpenVariableCenter} /></div>
        <div className="space-y-3">
          <SectionHeader detail="最多 20 步；调整顺序不会改变步骤 ID。" title="转换步骤" />
          <div className="divide-y divide-white/10 rounded-lg border border-white/10 bg-black/10">
            {operations.map((operation, index) => {
              const binding = operation.binding ?? {
                source: "literal" as const,
                valueType: "text" as const,
                value: "",
              };
              return (
                <div className="space-y-3 p-3" key={operation.id}>
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-slate-200">步骤 {index + 1}</p>
                    <div className="flex gap-1">
                      <SmallButton disabled={index === 0} onClick={() => moveOperation(index, index - 1)}>上移</SmallButton>
                      <SmallButton disabled={index === operations.length - 1} onClick={() => moveOperation(index, index + 1)}>下移</SmallButton>
                      <SmallButton disabled={operations.length <= 1} onClick={() => onChange({ operations: operations.filter((_, operationIndex) => operationIndex !== index) })}>删除</SmallButton>
                    </div>
                  </div>
                  <select
                    aria-label={`转换步骤 ${index + 1} 操作`}
                    className={compactInputClass}
                    onChange={(event) => {
                      const next = event.target.value as WorkflowObjectOperation["operation"];
                      updateOperation(index, {
                        operation: next,
                        sourceField: next === "rename" ? operation.sourceField ?? "" : undefined,
                        targetField: ["set", "set_default", "rename", "remove"].includes(next) ? operation.targetField ?? "" : undefined,
                        fields: next === "keep_only" ? operation.fields ?? [""] : undefined,
                        binding: ["set", "set_default"].includes(next) ? binding : undefined,
                      });
                    }}
                    value={operation.operation}
                  >
                    <option className="bg-slate-950" value="set">设置字段</option>
                    <option className="bg-slate-950" value="set_default">缺失时设置默认值</option>
                    <option className="bg-slate-950" value="rename">重命名字段</option>
                    <option className="bg-slate-950" value="remove">删除字段</option>
                    <option className="bg-slate-950" value="keep_only">只保留所选字段</option>
                  </select>
                  {operation.operation === "rename" ? (
                    <input aria-label={`转换步骤 ${index + 1} 来源字段`} className={compactInputClass} onChange={(event) => updateOperation(index, { sourceField: event.target.value })} placeholder="原字段名" value={operation.sourceField ?? ""} />
                  ) : null}
                  {["set", "set_default", "rename", "remove"].includes(operation.operation) ? (
                    <input aria-label={`转换步骤 ${index + 1} 目标字段`} className={compactInputClass} onChange={(event) => updateOperation(index, { targetField: event.target.value })} placeholder={operation.operation === "rename" ? "新字段名" : "字段名"} value={operation.targetField ?? ""} />
                  ) : null}
                  {["set", "set_default"].includes(operation.operation) ? (
                    <div className="space-y-2 rounded-lg border border-white/10 bg-white/[0.025] p-2.5">
                      <select aria-label={`转换步骤 ${index + 1} 值来源`} className={compactInputClass} onChange={(event) => updateOperation(index, { binding: event.target.value === "variable" ? { source: "variable", variable: "" } : { source: "literal", valueType: "text", value: "" } })} value={binding.source}>
                        <option className="bg-slate-950" value="literal">固定值</option>
                        <option className="bg-slate-950" value="variable">工作流变量</option>
                      </select>
                      {binding.source === "variable" ? (
                        <WorkflowVariableField declarations={declarations} edges={edges} fieldName="bindingVariable" node={node} nodes={nodes} onChange={(value) => updateOperation(index, { binding: { source: "variable", variable: value } })} value={binding.variable ?? ""} />
                      ) : (
                        <TypedValueEditor
                          ariaPrefix={`转换步骤 ${index + 1}`}
                          onChange={(patch) => updateOperation(index, { binding: { source: "literal", valueType: patch.valueType ?? binding.valueType ?? "text", value: patch.value } })}
                          rule={{ operator: "equals", valueType: binding.valueType ?? "text", value: binding.value }}
                        />
                      )}
                    </div>
                  ) : null}
                  {operation.operation === "keep_only" ? (
                    <div className="space-y-2">
                      {(operation.fields ?? [""]).map((field, fieldIndex) => (
                        <div className="flex gap-2" key={`${operation.id}-field-${fieldIndex}`}>
                          <input aria-label={`转换步骤 ${index + 1} 保留字段 ${fieldIndex + 1}`} className={compactInputClass} onChange={(event) => updateOperation(index, { fields: (operation.fields ?? [""]).map((item, itemIndex) => itemIndex === fieldIndex ? event.target.value : item) })} placeholder="顶层字段" value={field} />
                          <SmallButton disabled={(operation.fields ?? []).length <= 1} onClick={() => updateOperation(index, { fields: (operation.fields ?? []).filter((_, itemIndex) => itemIndex !== fieldIndex) })}>删除</SmallButton>
                        </div>
                      ))}
                      <SmallButton disabled={(operation.fields ?? []).length >= 50} onClick={() => updateOperation(index, { fields: [...(operation.fields ?? []), ""] })}>添加字段</SmallButton>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
          <SmallButton
            disabled={operations.length >= 20}
            onClick={() => {
              const used = new Set(operations.map((operation) => operation.id));
              const id = Array.from({ length: 20 }, (_, index) => `operation_${index + 1}`).find((candidate) => !used.has(candidate)) ?? "operation_20";
              onChange({ operations: [...operations, { id, operation: "set", targetField: "", binding: { source: "literal", valueType: "text", value: "" } }] });
            }}
          >添加步骤</SmallButton>
        </div>
      </div>
    );
  }

  if (data.kind === "data_aggregate") {
    const groups = data.groupByFields ?? [];
    const measures = data.measures ?? [];
    return (
      <div className="space-y-4">
        <ConfigField label="输入对象数组">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="inputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ inputVariable: value })} value={data.inputVariable ?? ""} />
        </ConfigField>
        <ConfigField label="输出变量">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="outputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ outputVariable: value })} value={data.outputVariable ?? ""} />
        </ConfigField>
        <div className="flex justify-end"><VariableCenterShortcut onOpen={onOpenVariableCenter} /></div>
        <div className="space-y-3">
          <SectionHeader detail="留空表示汇总全部行，最多三个顶层字段。" title="分组字段" />
          {groups.map((field, index) => (
            <div className="flex gap-2" key={`group-${index}`}>
              <input aria-label={`分组字段 ${index + 1}`} className={compactInputClass} onChange={(event) => onChange({ groupByFields: groups.map((item, itemIndex) => itemIndex === index ? event.target.value : item) })} placeholder="顶层字段" value={field} />
              <SmallButton onClick={() => onChange({ groupByFields: groups.filter((_, itemIndex) => itemIndex !== index) })}>删除</SmallButton>
            </div>
          ))}
          <SmallButton disabled={groups.length >= 3} onClick={() => onChange({ groupByFields: [...groups, ""] })}>添加分组字段</SmallButton>
        </div>
        <div className="space-y-3">
          <SectionHeader detail="计数统计行数，其他度量会忽略缺失值和空值。" title="度量" />
          <div className="divide-y divide-white/10 rounded-lg border border-white/10 bg-black/10">
            {measures.map((measure, index) => {
              const updateMeasure = (patch: Partial<WorkflowAggregateMeasure>) => onChange({ measures: measures.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item) });
              return (
                <div className="space-y-2 p-3" key={`measure-${index}`}>
                  <input aria-label={`度量 ${index + 1} 输出字段`} className={compactInputClass} onChange={(event) => updateMeasure({ outputField: event.target.value })} placeholder="输出字段" value={measure.outputField} />
                  <select aria-label={`度量 ${index + 1} 操作`} className={compactInputClass} onChange={(event) => updateMeasure({ operation: event.target.value as WorkflowAggregateMeasure["operation"], sourceField: event.target.value === "count" ? undefined : measure.sourceField ?? "" })} value={measure.operation}>
                    <option className="bg-slate-950" value="count">计数</option><option className="bg-slate-950" value="sum">求和</option><option className="bg-slate-950" value="avg">平均值</option><option className="bg-slate-950" value="min">最小值</option><option className="bg-slate-950" value="max">最大值</option>
                  </select>
                  {measure.operation !== "count" ? <input aria-label={`度量 ${index + 1} 来源字段`} className={compactInputClass} onChange={(event) => updateMeasure({ sourceField: event.target.value })} placeholder="数字来源字段" value={measure.sourceField ?? ""} /> : null}
                  <SmallButton disabled={measures.length <= 1} onClick={() => onChange({ measures: measures.filter((_, itemIndex) => itemIndex !== index) })}>删除度量</SmallButton>
                </div>
              );
            })}
          </div>
          <SmallButton
            disabled={measures.length >= 10}
            onClick={() => {
              const usedOutputFields = new Set(measures.map((measure) => measure.outputField));
              const outputField = Array.from(
                { length: 10 },
                (_, index) => `measure_${index + 1}`,
              ).find((candidate) => !usedOutputFields.has(candidate)) ?? "measure_10";
              onChange({
                measures: [...measures, { outputField, operation: "count" }],
              });
            }}
          >添加度量</SmallButton>
        </div>
      </div>
    );
  }

  if (data.kind === "data_merge") {
    const mergeMode = data.mergeMode ?? "append";
    const keyFields = data.keyFields ?? [];
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.07] px-3 py-2 text-xs leading-5 text-cyan-50">
          节点会等待“左侧数据”和“右侧数据”两条路径都到达后执行。请让每个入口只连接一条线，并选择该路径实际生成的变量。
        </div>
        <ConfigField label="合流方式">
          <select
            className={inputClass}
            onChange={(event) => {
              const nextMode = event.target.value as "append" | "keyed_join";
              onChange({
                mergeMode: nextMode,
                keyFields: nextMode === "append"
                  ? []
                  : keyFields.length > 0 ? keyFields : ["id"],
              });
            }}
            value={mergeMode}
          >
            <option className="bg-slate-950" value="append">顺序拼接两个数组</option>
            <option className="bg-slate-950" value="keyed_join">按字段匹配两侧记录</option>
          </select>
        </ConfigField>
        <div className="grid gap-3 sm:grid-cols-2">
          <ConfigField
            hint="必须由连接到左侧入口的路径生成，或来自全局输入/常量。"
            label="左侧数组变量"
          >
            <WorkflowVariableField
              contract={contract}
              declarations={declarations}
              edges={edges}
              fieldName="leftVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => onChange({ leftVariable: value })}
              value={data.leftVariable ?? ""}
            />
          </ConfigField>
          <ConfigField
            hint="必须由连接到右侧入口的路径生成，或来自全局输入/常量。"
            label="右侧数组变量"
          >
            <WorkflowVariableField
              contract={contract}
              declarations={declarations}
              edges={edges}
              fieldName="rightVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => onChange({ rightVariable: value })}
              value={data.rightVariable ?? ""}
            />
          </ConfigField>
        </div>
        <div className="flex justify-end"><VariableCenterShortcut onOpen={onOpenVariableCenter} /></div>
        {mergeMode === "keyed_join" ? (
          <div className="space-y-3">
            <SectionHeader
              detail="使用 1 至 3 个两侧共有的顶层字段组成键。每侧键必须唯一，只输出能一对一匹配的记录。"
              title="匹配键"
            />
            {keyFields.map((field, index) => (
              <div className="flex gap-2" key={`merge-key-${index}`}>
                <input
                  aria-label={`合流匹配键 ${index + 1}`}
                  className={compactInputClass}
                  onChange={(event) => onChange({
                    keyFields: keyFields.map((item, itemIndex) =>
                      itemIndex === index ? event.target.value : item
                    ),
                  })}
                  placeholder="例如 id"
                  value={field}
                />
                <SmallButton
                  ariaLabel={`删除合流匹配键 ${index + 1}`}
                  disabled={keyFields.length <= 1}
                  onClick={() => onChange({
                    keyFields: keyFields.filter((_, itemIndex) => itemIndex !== index),
                  })}
                >
                  删除
                </SmallButton>
              </div>
            ))}
            <SmallButton
              disabled={keyFields.length >= 3}
              onClick={() => onChange({ keyFields: [...keyFields, ""] })}
            >
              添加匹配键
            </SmallButton>
          </div>
        ) : (
          <p className="text-xs leading-5 text-slate-300">
            输出按左侧数组、右侧数组的顺序拼接；不会修改两侧原变量。
          </p>
        )}
        <ConfigField label="合流结果变量">
          <WorkflowVariableField
            contract={contract}
            declarations={declarations}
            edges={edges}
            fieldName="outputVariable"
            node={node}
            nodes={nodes}
            onChange={(value) => onChange({ outputVariable: value })}
            value={data.outputVariable ?? ""}
          />
        </ConfigField>
      </div>
    );
  }

  if (data.kind === "dataset_compare") {
    const keyFields = data.keyFields ?? [];
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.07] px-3 py-2 text-xs leading-5 text-cyan-50">
          两侧必须是对象数组。键在各自数据集中必须唯一，文本“1”和数字 1 会被视为不同键。
        </div>
        <ConfigField label="变更前数据">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="leftVariable" node={node} nodes={nodes} onChange={(value) => onChange({ leftVariable: value })} value={data.leftVariable ?? ""} />
        </ConfigField>
        <ConfigField label="变更后数据">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="rightVariable" node={node} nodes={nodes} onChange={(value) => onChange({ rightVariable: value })} value={data.rightVariable ?? ""} />
        </ConfigField>
        <div className="flex justify-end"><VariableCenterShortcut onOpen={onOpenVariableCenter} /></div>
        <div className="space-y-3">
          <SectionHeader detail="使用 1 至 3 个顶层字段组成稳定键，例如 id，或 tenant_id + id。" title="匹配键" />
          {keyFields.map((field, index) => (
            <div className="flex gap-2" key={`dataset-key-${index}`}>
              <input aria-label={`匹配键 ${index + 1}`} className={compactInputClass} onChange={(event) => onChange({ keyFields: keyFields.map((item, itemIndex) => itemIndex === index ? event.target.value : item) })} placeholder="顶层字段" value={field} />
              <SmallButton ariaLabel={`删除数据集匹配键 ${index + 1}`} disabled={keyFields.length <= 1} onClick={() => onChange({ keyFields: keyFields.filter((_, itemIndex) => itemIndex !== index) })}>删除</SmallButton>
            </div>
          ))}
          <SmallButton disabled={keyFields.length >= 3} onClick={() => onChange({ keyFields: [...keyFields, ""] })}>添加匹配键</SmallButton>
        </div>
        <label className="flex items-start gap-2 rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2 text-xs leading-5 text-slate-200">
          <input
            checked={Boolean(data.includeUnchanged)}
            className="mt-1"
            onChange={(event) => onChange({ includeUnchanged: event.target.checked })}
            type="checkbox"
          />
          <span>在结果中复制未变化记录。关闭时仍会统计数量，可显著减小输出。</span>
        </label>
        <ConfigField label="对照结果变量">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="outputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ outputVariable: value })} value={data.outputVariable ?? ""} />
        </ConfigField>
      </div>
    );
  }

  return null;
}
