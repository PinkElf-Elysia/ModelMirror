import type {
  WorkflowEdge,
  WorkflowFileColumn,
  WorkflowNode,
  WorkflowNodeData,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import type { ReactNode } from "react";
import type { WorkflowNodeContractProjection } from "./workflowNodeRegistry";
import WorkflowVariableField from "./WorkflowVariableField";


const inputClass =
  "modelmirror-form-control w-full rounded-lg border border-white/10 bg-[#0f1728] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-400 hover:border-white/20 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10";
const compactInputClass = `${inputClass} px-2.5 py-1.5 text-xs`;

function Field({
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
      <span className="mb-1.5 block text-xs font-semibold text-slate-200">{label}</span>
      {children}
      {hint ? <span className="mt-1.5 block text-[11px] leading-5 text-slate-400">{hint}</span> : null}
    </label>
  );
}

function SmallButton({
  children,
  disabled,
  onClick,
}: {
  children: ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className="rounded-md border border-white/15 px-2 py-1 text-[11px] font-medium text-slate-200 transition hover:border-white/30 hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-40"
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  );
}

function VariableShortcut({ onOpen }: { onOpen: () => void }) {
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

const timeOperationOptions = [
  ["now", "获取当前时间"],
  ["to_iso", "统一为标准时间"],
  ["format", "显示为指定格式"],
  ["add", "向后推一段时间"],
  ["subtract", "向前推一段时间"],
  ["difference", "计算两个时间的间隔"],
  ["start_of", "归整到周期开始"],
  ["end_of", "归整到周期结束"],
] as const;

const amountUnits = [
  ["seconds", "秒"],
  ["minutes", "分钟"],
  ["hours", "小时"],
  ["days", "天"],
  ["weeks", "周"],
  ["months", "月"],
  ["years", "年"],
] as const;

const differenceUnits = amountUnits.slice(0, 4);
const boundaryUnits = [
  ["minute", "分钟"],
  ["hour", "小时"],
  ["day", "天"],
  ["week", "周（周一开始）"],
  ["month", "月"],
  ["year", "年"],
] as const;

const commonTimezones = [
  "UTC",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Asia/Singapore",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Australia/Sydney",
];

function TimeConfig({
  contract,
  data,
  declarations,
  edges,
  node,
  nodes,
  onChange,
  onOpenVariableCenter,
}: ConfigProps) {
  const operation = String(data.operation ?? "now");
  const needsInput = operation !== "now";
  const usesAmount = operation === "add" || operation === "subtract";
  const usesBoundary = operation === "start_of" || operation === "end_of";
  const usesDifference = operation === "difference";
  const unitOptions = usesBoundary
    ? boundaryUnits
    : usesDifference
      ? differenceUnits
      : amountUnits;
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.07] px-3 py-2 text-xs leading-5 text-cyan-50">
        未带时区的输入会按下方时区理解。遇到夏令时不存在或重复的本地时间会停止并提示，避免悄悄偏移一小时。
      </div>
      <Field label="要做什么">
        <select
          className={inputClass}
          onChange={(event) => {
            const next = event.target.value;
            const unit = ["start_of", "end_of"].includes(next)
              ? "day"
              : next === "difference"
                ? "days"
                : "days";
            onChange({ operation: next, unit });
          }}
          value={operation}
        >
          {timeOperationOptions.map(([value, label]) => <option className="bg-slate-950" key={value} value={value}>{label}</option>)}
        </select>
      </Field>
      {needsInput ? (
        <Field hint="选择上游或全局变量中的 ISO 日期时间文本。" label="来源时间">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="inputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ inputVariable: value })} value={data.inputVariable ?? ""} />
        </Field>
      ) : null}
      {usesDifference ? (
        <Field hint="结果 = 来源时间 − 对照时间。" label="对照时间">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="rightVariable" node={node} nodes={nodes} onChange={(value) => onChange({ rightVariable: value })} value={data.rightVariable ?? ""} />
        </Field>
      ) : null}
      <Field hint="支持标准 IANA 名称；常用名称可直接选择。" label="时区">
        <input className={inputClass} list={`timezone-${node.id}`} onChange={(event) => onChange({ timezone: event.target.value })} placeholder="例如 Asia/Shanghai" value={data.timezone ?? "UTC"} />
        <datalist id={`timezone-${node.id}`}>{commonTimezones.map((timezone) => <option key={timezone} value={timezone} />)}</datalist>
      </Field>
      {usesAmount ? (
        <div className="grid grid-cols-[1fr_1.2fr] gap-3">
          <Field label="数量">
            <input className={inputClass} max={1000000} min={-1000000} onChange={(event) => onChange({ amount: Number(event.target.value) })} step="any" type="number" value={data.amount ?? 1} />
          </Field>
          <Field label="单位">
            <select className={inputClass} onChange={(event) => onChange({ unit: event.target.value })} value={data.unit ?? "days"}>
              {unitOptions.map(([value, label]) => <option className="bg-slate-950" key={value} value={value}>{label}</option>)}
            </select>
          </Field>
        </div>
      ) : null}
      {usesDifference || usesBoundary ? (
        <Field label={usesDifference ? "结果单位" : "周期单位"}>
          <select className={inputClass} onChange={(event) => onChange({ unit: event.target.value })} value={data.unit ?? (usesDifference ? "days" : "day")}>
            {unitOptions.map(([value, label]) => <option className="bg-slate-950" key={value} value={value}>{label}</option>)}
          </select>
        </Field>
      ) : null}
      {operation === "format" ? (
        <Field hint="选择常用样式即可；需要其他样式时可编辑为 strftime 格式。" label="显示格式">
          <input className={inputClass} list={`time-format-${node.id}`} onChange={(event) => onChange({ formatString: event.target.value })} value={data.formatString ?? "%Y-%m-%d %H:%M:%S"} />
          <datalist id={`time-format-${node.id}`}>
            <option value="%Y-%m-%d %H:%M:%S">年-月-日 时:分:秒</option>
            <option value="%Y-%m-%d">年-月-日</option>
            <option value="%Y年%m月%d日 %H:%M">中文日期和时间</option>
            <option value="%H:%M">时:分</option>
          </datalist>
        </Field>
      ) : null}
      <Field label="结果变量">
        <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="outputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ outputVariable: value })} value={data.outputVariable ?? ""} />
      </Field>
      <div className="flex justify-end"><VariableShortcut onOpen={onOpenVariableCenter} /></div>
    </div>
  );
}

const formatOptions = [
  ["plain_text", "纯文本（TXT）", "适合简单正文"],
  ["markdown", "Markdown", "适合带标题和列表的报告"],
  ["json", "JSON", "完整保留类型化数据"],
  ["csv", "CSV 表格", "对象数组导出为通用表格"],
  ["pdf", "PDF 文档", "适合固定版式阅读"],
  ["docx", "Word 文档", "适合继续编辑"],
  ["xlsx", "Excel 工作簿", "对象数组导出为单工作表"],
] as const;

function FileOutputConfig({
  contract,
  data,
  declarations,
  edges,
  node,
  nodes,
  onChange,
  onOpenVariableCenter,
}: ConfigProps) {
  const format = String(data.format ?? "markdown");
  const columns = data.columns ?? [];
  const tabular = format === "csv" || format === "xlsx";
  const document = format === "pdf" || format === "docx";
  const updateColumn = (index: number, patch: Partial<WorkflowFileColumn>) =>
    onChange({ columns: columns.map((column, columnIndex) => columnIndex === index ? { ...column, ...patch } : column) });
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.07] px-3 py-2 text-xs leading-5 text-cyan-50">
        文件会保存到当前工作流或私有智能体会话，变量中只返回安全元数据。相同运行与节点重复执行会复用同一产物。
      </div>
      <Field label="要写入的变量">
        <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="inputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ inputVariable: value })} value={data.inputVariable ?? ""} />
      </Field>
      <Field label="文件格式">
        <div className="grid grid-cols-2 gap-2">
          {formatOptions.map(([value, label, detail]) => (
            <button
              className={`rounded-lg border px-3 py-2 text-left transition ${format === value ? "border-cyan-300/45 bg-cyan-300/10" : "border-white/10 bg-white/[0.025] hover:border-white/20"}`}
              key={value}
              onClick={() => onChange({ format: value })}
              type="button"
            >
              <span className="block text-xs font-semibold text-white">{label}</span>
              <span className="mt-1 block text-[10px] leading-4 text-slate-400">{detail}</span>
            </button>
          ))}
        </div>
      </Field>
      <Field hint="系统会自动补上与格式匹配的扩展名。支持 {{变量}}，但不能包含文件夹路径。" label="文件名">
        <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="filenameTemplate" node={node} nodes={nodes} onChange={(value) => onChange({ filenameTemplate: value })} placeholder="例如 月度报告-{{report_month}}" value={data.filenameTemplate ?? ""} />
      </Field>
      {document ? (
        <Field hint="支持 {{变量}}；该标题会显示在文档顶部。" label="文档标题">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="titleTemplate" node={node} nodes={nodes} onChange={(value) => onChange({ titleTemplate: value })} value={data.titleTemplate ?? ""} />
        </Field>
      ) : null}
      {tabular ? (
        <div className="space-y-3">
          <div>
            <p className="text-xs font-semibold text-white">表格列</p>
            <p className="mt-1 text-[11px] leading-5 text-slate-400">按固定顺序选择对象的顶层字段；缺失字段会写为空值，嵌套对象不会被隐式展开。</p>
          </div>
          <div className="divide-y divide-white/10 rounded-lg border border-white/10 bg-black/10">
            {columns.map((column, index) => (
              <div className="grid grid-cols-[1fr_1fr_auto] gap-2 p-3" key={column.id}>
                <input aria-label={`表格列 ${index + 1} 字段`} className={compactInputClass} onChange={(event) => updateColumn(index, { field: event.target.value })} placeholder="对象字段" value={column.field} />
                <input aria-label={`表格列 ${index + 1} 标题`} className={compactInputClass} onChange={(event) => updateColumn(index, { label: event.target.value })} placeholder="列标题" value={column.label} />
                <SmallButton disabled={columns.length <= 1} onClick={() => onChange({ columns: columns.filter((_, columnIndex) => columnIndex !== index) })}>删除</SmallButton>
              </div>
            ))}
          </div>
          <SmallButton
            disabled={columns.length >= 200}
            onClick={() => {
              const used = new Set(columns.map((column) => column.id));
              const id = Array.from({ length: 200 }, (_, index) => `column_${index + 1}`).find((candidate) => !used.has(candidate)) ?? `column_${columns.length + 1}`;
              onChange({ columns: [...columns, { id, field: "", label: "" }] });
            }}
          >添加列</SmallButton>
        </div>
      ) : null}
      <Field label="文件信息变量">
        <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="outputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ outputVariable: value })} value={data.outputVariable ?? ""} />
      </Field>
      <div className="flex justify-end"><VariableShortcut onOpen={onOpenVariableCenter} /></div>
    </div>
  );
}

interface ConfigProps {
  contract?: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  declarations?: WorkflowVariableDeclaration[];
  edges: WorkflowEdge[];
  node: WorkflowNode;
  nodes: WorkflowNode[];
  onChange: (patch: Partial<WorkflowNodeData>) => void;
  onOpenVariableCenter: () => void;
}

export default function WorkflowFileDataNodeConfig(props: ConfigProps) {
  if (props.data.kind === "time_tool" && String(props.data.contractVersion ?? "1") === "2") {
    return <TimeConfig {...props} />;
  }
  if (props.data.kind === "file_output") {
    return <FileOutputConfig {...props} />;
  }
  return null;
}
