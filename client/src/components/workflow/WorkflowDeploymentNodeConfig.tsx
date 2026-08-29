import { useEffect, useMemo, useState } from "react";
import type {
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeData,
  WorkflowFormField,
  WorkflowFormFieldType,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import {
  fetchWorkflowProject,
  fetchWorkflowProjects,
  fetchWorkflowVersionInterface,
  inspectWorkflowRss,
  inspectWorkflowEmail,
  type WorkflowInterfaceInput,
  type WorkflowProjectSummary,
  type WorkflowVersionInterface,
} from "../../utils/workflowDeployments";
import WorkflowVariableField from "./WorkflowVariableField";
import type { WorkflowNodeContractProjection } from "./workflowNodeRegistry";
import type { WorkflowVariableValueType } from "./workflowVariables";

type DurationUnit = "seconds" | "minutes" | "hours" | "days";
type CronPattern = "minutes" | "hourly" | "daily" | "weekly" | "monthly" | "custom";
type EmailCredentialSummary = {
  credential_id: string;
  name: string;
  kind: string;
  status: string;
  masked_value: string;
};

const DURATION_FACTORS: Record<DurationUnit, number> = {
  seconds: 1,
  minutes: 60,
  hours: 3_600,
  days: 86_400,
};

const COMMON_TIMEZONES = [
  { value: "UTC", label: "UTC（协调世界时）" },
  { value: "Asia/Shanghai", label: "中国标准时间（上海）" },
  { value: "Asia/Hong_Kong", label: "香港时间" },
  { value: "Asia/Tokyo", label: "日本时间（东京）" },
  { value: "Asia/Singapore", label: "新加坡时间" },
  { value: "America/Phoenix", label: "美国山地时间（凤凰城，无夏令时）" },
  { value: "America/Los_Angeles", label: "美国太平洋时间（洛杉矶）" },
  { value: "America/New_York", label: "美国东部时间（纽约）" },
  { value: "Europe/London", label: "英国时间（伦敦）" },
  { value: "Europe/Berlin", label: "欧洲中部时间（柏林）" },
] as const;

const COMMON_STATUS_CODES = [
  { value: 200, label: "200 成功" },
  { value: 201, label: "201 已创建" },
  { value: 202, label: "202 已接受，稍后处理" },
  { value: 204, label: "204 成功，无返回内容" },
  { value: 400, label: "400 请求内容有误" },
  { value: 401, label: "401 未通过身份验证" },
  { value: 403, label: "403 无权访问" },
  { value: 404, label: "404 未找到资源" },
  { value: 409, label: "409 状态冲突" },
  { value: 422, label: "422 内容无法处理" },
  { value: 429, label: "429 请求过于频繁" },
  { value: 500, label: "500 工作流执行失败" },
  { value: 503, label: "503 服务暂不可用" },
] as const;

const inputClass =
  "modelmirror-form-control w-full rounded-lg border border-white/10 bg-[#0f1728] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10";

function numberOr(value: unknown, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function durationParts(value: unknown): { amount: number; unit: DurationUnit } {
  const seconds = Math.max(1, Math.round(numberOr(value, 1)));
  if (seconds % DURATION_FACTORS.days === 0) {
    return { amount: seconds / DURATION_FACTORS.days, unit: "days" };
  }
  if (seconds % DURATION_FACTORS.hours === 0) {
    return { amount: seconds / DURATION_FACTORS.hours, unit: "hours" };
  }
  if (seconds % DURATION_FACTORS.minutes === 0) {
    return { amount: seconds / DURATION_FACTORS.minutes, unit: "minutes" };
  }
  return { amount: seconds, unit: "seconds" };
}

function durationSeconds(amount: number, unit: DurationUnit, maximum: number) {
  return Math.min(maximum, Math.max(1, Math.round(amount * DURATION_FACTORS[unit])));
}

function pad2(value: number) {
  return String(value).padStart(2, "0");
}

export interface CronUiValue {
  pattern: CronPattern;
  intervalMinutes: number;
  minute: number;
  hour: number;
  weekday: number;
  monthday: number;
}

export function parseCronExpressionForUi(expression: unknown): CronUiValue {
  const raw = String(expression ?? "").trim();
  const fields = raw.split(/\s+/);
  const fallback: CronUiValue = {
    pattern: "custom",
    intervalMinutes: 5,
    minute: 0,
    hour: 9,
    weekday: 1,
    monthday: 1,
  };
  if (fields.length !== 5) return fallback;
  const [minute, hour, monthday, month, weekday] = fields;
  const interval = minute.match(/^\*\/(\d+)$/);
  if (interval && hour === "*" && monthday === "*" && month === "*" && weekday === "*") {
    return { ...fallback, pattern: "minutes", intervalMinutes: Number(interval[1]) };
  }
  if (/^\d+$/.test(minute) && hour === "*" && monthday === "*" && month === "*" && weekday === "*") {
    return { ...fallback, pattern: "hourly", minute: Number(minute) };
  }
  if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && monthday === "*" && month === "*" && weekday === "*") {
    return { ...fallback, pattern: "daily", minute: Number(minute), hour: Number(hour) };
  }
  if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && monthday === "*" && month === "*" && /^[0-6]$/.test(weekday)) {
    return { ...fallback, pattern: "weekly", minute: Number(minute), hour: Number(hour), weekday: Number(weekday) };
  }
  if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && /^\d+$/.test(monthday) && month === "*" && weekday === "*") {
    return { ...fallback, pattern: "monthly", minute: Number(minute), hour: Number(hour), monthday: Number(monthday) };
  }
  return fallback;
}

export function cronExpressionForUi(value: CronUiValue) {
  if (value.pattern === "minutes") return `*/${value.intervalMinutes} * * * *`;
  if (value.pattern === "hourly") return `${value.minute} * * * *`;
  if (value.pattern === "daily") return `${value.minute} ${value.hour} * * *`;
  if (value.pattern === "weekly") return `${value.minute} ${value.hour} * * ${value.weekday}`;
  if (value.pattern === "monthly") return `${value.minute} ${value.hour} ${value.monthday} * *`;
  return "*/5 * * * *";
}

function defaultCronExpression(pattern: CronPattern) {
  if (pattern === "minutes") return "*/5 * * * *";
  if (pattern === "hourly") return "0 * * * *";
  if (pattern === "daily") return "0 9 * * *";
  if (pattern === "weekly") return "0 9 * * 1";
  if (pattern === "monthly") return "0 9 1 * *";
  return "*/5 * * * *";
}

export function dateTimeLocalValue(value: unknown) {
  const raw = String(value ?? "").trim();
  const match = raw.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})/);
  return match?.[1] ?? "";
}

function deviceTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3 rounded-xl border border-white/10 bg-white/[0.025] p-3">
      <div>
        <h3 className="text-sm font-semibold text-white">{title}</h3>
        <p className="mt-1 text-xs leading-5 text-slate-400">{description}</p>
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-slate-200">{label}</span>
      {hint ? <span className="ml-2 text-[11px] font-normal text-slate-500">{hint}</span> : null}
      <div className="mt-2">{children}</div>
    </label>
  );
}

function TimezoneField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const common = COMMON_TIMEZONES.some((item) => item.value === value);
  return (
    <Field label="使用的时区" hint="会自动处理夏令时">
      <div className="space-y-2">
        <select
          className={inputClass}
          onChange={(event) => {
            if (event.target.value === "__device") onChange(deviceTimezone());
            else if (event.target.value === "__custom") onChange("");
            else onChange(event.target.value);
          }}
          value={common ? value : value ? "__custom" : "__custom"}
        >
          <option value="__device">使用当前设备时区（{deviceTimezone()}）</option>
          {COMMON_TIMEZONES.map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
          <option value="__custom">其他 IANA 时区…</option>
        </select>
        {!common ? (
          <input
            aria-label="自定义 IANA 时区"
            className={inputClass}
            onChange={(event) => onChange(event.target.value)}
            placeholder="例如 Asia/Shanghai"
            value={value}
          />
        ) : null}
      </div>
    </Field>
  );
}

function DurationField({
  value,
  minimumSeconds,
  maximumSeconds,
  onChange,
}: {
  value: unknown;
  minimumSeconds: number;
  maximumSeconds: number;
  onChange: (value: number) => void;
}) {
  const parts = durationParts(value);
  const minimumAmount = parts.unit === "seconds" ? minimumSeconds : 1;
  const maximumAmount = Math.max(1, Math.floor(maximumSeconds / DURATION_FACTORS[parts.unit]));
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_8rem] gap-2">
      <input
        aria-label="时长数值"
        className={inputClass}
        max={maximumAmount}
        min={minimumAmount}
        onChange={(event) =>
          onChange(durationSeconds(Number(event.target.value), parts.unit, maximumSeconds))
        }
        type="number"
        value={parts.amount}
      />
      <select
        aria-label="时长单位"
        className={inputClass}
        onChange={(event) => {
          const unit = event.target.value as DurationUnit;
          const currentSeconds = numberOr(value, minimumSeconds);
          const amount = Math.max(1, Math.round(currentSeconds / DURATION_FACTORS[unit]));
          onChange(Math.max(minimumSeconds, durationSeconds(amount, unit, maximumSeconds)));
        }}
        value={parts.unit}
      >
        <option value="seconds">秒</option>
        <option value="minutes">分钟</option>
        <option value="hours">小时</option>
        <option value="days">天</option>
      </select>
    </div>
  );
}

function GlobalVariableField({
  label,
  hint,
  fieldName,
  value,
  node,
  nodes,
  edges,
  contract,
  declarations = [],
  onChange,
}: {
  label: string;
  hint: string;
  fieldName: string;
  value: string;
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  contract: WorkflowNodeContractProjection | null;
  declarations?: WorkflowVariableDeclaration[];
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label} hint="自动纳入变量中心">
      <WorkflowVariableField
        ariaLabel={label}
        contract={contract}
        declarations={declarations}
        edges={edges}
        fieldName={fieldName}
        node={node}
        nodes={nodes}
        onChange={onChange}
        placeholder={hint}
        value={value}
      />
      <p className="mt-1.5 text-[11px] leading-5 text-slate-500">{hint}</p>
    </Field>
  );
}

function FailureEntryConfig({
  currentProjectId,
  node,
  nodes,
  edges,
  contract,
  declarations = [],
  data,
  onChange,
}: {
  currentProjectId?: string;
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  contract: WorkflowNodeContractProjection | null;
  declarations?: WorkflowVariableDeclaration[];
  data: WorkflowNodeData;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const [projects, setProjects] = useState<WorkflowProjectSummary[]>([]);
  const [search, setSearch] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reloadToken, setReloadToken] = useState(0);
  const selectedIds = data.sourceProjectIds ?? [];

  useEffect(() => {
    let cancelled = false;
    async function loadProjects() {
      setIsLoading(true);
      setLoadError("");
      try {
        const collected: WorkflowProjectSummary[] = [];
        let offset = 0;
        let total = 0;
        do {
          const page = await fetchWorkflowProjects({ limit: 100, offset });
          collected.push(...page.items);
          total = page.total;
          offset += page.items.length;
        } while (offset < total && offset < 1_000);
        if (!cancelled) setProjects(collected);
      } catch (error) {
        if (!cancelled) {
          setProjects([]);
          setLoadError(
            error instanceof Error ? error.message : "工作流目录暂时不可用。",
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    void loadProjects();
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  const availableProjects = useMemo(
    () => projects.filter((project) => project.project_id !== currentProjectId),
    [currentProjectId, projects],
  );
  const filteredProjects = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    if (!query) return availableProjects;
    return availableProjects.filter(
      (project) =>
        project.title.toLocaleLowerCase().includes(query) ||
        project.project_id.toLocaleLowerCase().includes(query),
    );
  }, [availableProjects, search]);
  const knownProjectIds = new Set(availableProjects.map((project) => project.project_id));
  const unavailableSelectedIds = selectedIds.filter((id) => !knownProjectIds.has(id));

  const toggleProject = (projectId: string) => {
    if (selectedIds.includes(projectId)) {
      onChange({ sourceProjectIds: selectedIds.filter((id) => id !== projectId) });
      return;
    }
    if (selectedIds.length >= 50) return;
    onChange({ sourceProjectIds: [...selectedIds, projectId] });
  };

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-50">
        只接收启用之后产生的独立工作流失败；事件会移除正文、变量、凭据、认证信息和堆栈，也不会递归触发失败处理器。
      </div>
      <Section
        title="监听哪些工作流"
        description="按项目跟随其当前启用版本。每个来源同时只能绑定一个已启用的失败处理器。"
      >
        <div className="flex items-center justify-between gap-3 text-xs">
          <span className="text-slate-400">已选择 {selectedIds.length} / 50</span>
          <button
            className="text-cyan-200 transition hover:text-cyan-100"
            onClick={() => setReloadToken((value) => value + 1)}
            type="button"
          >
            刷新目录
          </button>
        </div>
        {!selectedIds.length ? (
          <p className="rounded-lg border border-rose-300/20 bg-rose-300/[0.06] px-3 py-2 text-xs leading-5 text-rose-100">
            至少选择一个来源工作流后才能发布。
          </p>
        ) : null}
        <input
          aria-label="搜索来源工作流"
          className={inputClass}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="按名称或工作流 ID 搜索"
          type="search"
          value={search}
        />
        {isLoading ? (
          <p className="rounded-lg border border-white/10 bg-black/15 px-3 py-4 text-center text-xs text-slate-400">
            正在读取工作流目录…
          </p>
        ) : loadError ? (
          <div className="space-y-2">
            <p className="rounded-lg border border-rose-300/20 bg-rose-300/[0.06] px-3 py-3 text-xs leading-5 text-rose-100">
              {loadError} 已保存的选择不会被自动清除。
            </p>
            {selectedIds.map((projectId) => (
              <button
                className="flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-black/15 px-3 py-2 text-left"
                key={projectId}
                onClick={() => toggleProject(projectId)}
                type="button"
              >
                <span className="min-w-0 truncate font-mono text-[11px] text-slate-400">
                  {projectId}
                </span>
                <span className="shrink-0 text-xs text-rose-200">移除</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
            {unavailableSelectedIds.map((projectId) => (
              <label
                className="flex cursor-pointer items-start gap-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.06] p-3"
                key={projectId}
              >
                <input
                  checked
                  className="mt-0.5 h-4 w-4 accent-rose-300"
                  onChange={() => toggleProject(projectId)}
                  type="checkbox"
                />
                <span className="min-w-0">
                  <span className="block text-xs font-semibold text-amber-100">
                    来源暂不可用
                  </span>
                  <span className="mt-1 block break-all font-mono text-[11px] text-slate-500">
                    {projectId}
                  </span>
                </span>
              </label>
            ))}
            {filteredProjects.map((project) => {
              const selected = selectedIds.includes(project.project_id);
              const selectionLimitReached = selectedIds.length >= 50 && !selected;
              return (
                <label
                  className={`flex items-start gap-3 rounded-lg border p-3 transition ${
                    selectionLimitReached
                      ? "cursor-not-allowed border-white/5 bg-white/[0.015] opacity-50"
                      : "cursor-pointer border-white/10 bg-black/15 hover:border-white/20"
                  }`}
                  key={project.project_id}
                >
                  <input
                    checked={selected}
                    className="mt-0.5 h-4 w-4 accent-rose-300"
                    disabled={selectionLimitReached}
                    onChange={() => toggleProject(project.project_id)}
                    type="checkbox"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-semibold text-slate-100">
                        {project.title}
                      </span>
                      <span
                        className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${
                          project.active_version
                            ? "bg-emerald-300/10 text-emerald-200"
                            : "bg-slate-400/10 text-slate-400"
                        }`}
                      >
                        {project.active_version ? `已启用 v${project.active_version}` : "未启用"}
                      </span>
                    </span>
                    <span className="mt-1 block truncate font-mono text-[11px] text-slate-500">
                      {project.project_id}
                    </span>
                  </span>
                </label>
              );
            })}
            {!filteredProjects.length && !unavailableSelectedIds.length ? (
              <p className="rounded-lg border border-dashed border-white/10 px-3 py-5 text-center text-xs leading-5 text-slate-500">
                {search ? "没有匹配的工作流。" : "还没有其他服务端工作流可供选择。"}
              </p>
            ) : null}
          </div>
        )}
      </Section>
      <Section
        title="失败事件变量"
        description="后续节点可读取来源项目、固定版本、运行 ID、失败时间和脱敏错误摘要。"
      >
        <GlobalVariableField
          contract={contract}
          declarations={declarations}
          edges={edges}
          fieldName="eventVariable"
          hint="例如 failure_event"
          label="失败事件变量"
          node={node}
          nodes={nodes}
          onChange={(eventVariable) => onChange({ eventVariable })}
          value={String(data.eventVariable ?? "")}
        />
      </Section>
    </div>
  );
}

function WorkflowCallEntryConfig({
  node,
  nodes,
  edges,
  contract,
  declarations = [],
  data,
  onChange,
}: {
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  contract: WorkflowNodeContractProjection | null;
  declarations?: WorkflowVariableDeclaration[];
  data: WorkflowNodeData;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-indigo-300/25 bg-indigo-300/10 px-3 py-2 text-xs leading-5 text-indigo-50">
        该入口只接受其他已发布工作流的内部同步调用，不会生成公开 URL；输入直接使用全局变量中心里的“外部输入”声明。
      </div>
      <Section
        title="调用上下文"
        description="写入父执行、根执行、调用节点和固定版本等安全上下文，不包含调用输入正文。"
      >
        <GlobalVariableField
          contract={contract}
          declarations={declarations}
          edges={edges}
          fieldName="eventVariable"
          hint="例如 call_event"
          label="调用事件变量"
          node={node}
          nodes={nodes}
          onChange={(eventVariable) => onChange({ eventVariable })}
          value={String(data.eventVariable ?? "")}
        />
      </Section>
      <p className="rounded-lg border border-white/10 bg-black/15 px-3 py-2 text-xs leading-5 text-slate-400">
        请在全局变量中心添加类型化外部输入和默认值。常量只属于本工作流，调用方无法覆盖。
      </p>
    </div>
  );
}

function literalText(value: unknown, valueType: WorkflowInterfaceInput["value_type"]) {
  if (valueType === "json") {
    try {
      return JSON.stringify(value ?? null, null, 2);
    } catch {
      return "null";
    }
  }
  return String(value ?? "");
}

export function parseJsonLiteralForUi(raw: string):
  | { valid: true; value: unknown }
  | { valid: false } {
  try {
    return { valid: true, value: JSON.parse(raw) as unknown };
  } catch {
    return { valid: false };
  }
}

export function JsonLiteralInput({
  inputName,
  value,
  onChange,
}: {
  inputName: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const serialized = literalText(value, "json");
  const [draft, setDraft] = useState(serialized);
  const [error, setError] = useState("");

  useEffect(() => {
    setDraft(serialized);
    setError("");
  }, [serialized]);

  return (
    <div>
      <textarea
        aria-invalid={Boolean(error)}
        aria-label={`${inputName} 固定 JSON 值`}
        className={`${inputClass} min-h-20 font-mono`}
        onChange={(event) => {
          const raw = event.target.value;
          setDraft(raw);
          const parsed = parseJsonLiteralForUi(raw);
          if (!parsed.valid) {
            setError("请输入合法 JSON；字符串需要使用双引号。当前内容尚未保存。");
            return;
          }
          setError("");
          onChange(parsed.value);
        }}
        value={draft}
      />
      {error ? (
        <p className="mt-1.5 text-[11px] leading-5 text-rose-200" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function InvokeWorkflowConfig({
  currentProjectId,
  batchMode = false,
  contract,
  declarations = [],
  node,
  nodes,
  edges,
  data,
  onChange,
}: {
  currentProjectId?: string;
  batchMode?: boolean;
  contract: WorkflowNodeContractProjection | null;
  declarations?: WorkflowVariableDeclaration[];
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  data: WorkflowNodeData;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const [projects, setProjects] = useState<WorkflowProjectSummary[]>([]);
  const [versions, setVersions] = useState<number[]>([]);
  const [targetInterface, setTargetInterface] = useState<WorkflowVersionInterface | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [search, setSearch] = useState("");
  const targetProjectId = String(data.targetProjectId ?? "");
  const targetVersion = Number(data.targetVersion || 0);
  const bindings = (data.inputBindings && typeof data.inputBindings === "object"
    ? data.inputBindings
    : {}) as Record<string, { source?: string; variable?: string; value?: unknown }>;

  useEffect(() => {
    let cancelled = false;
    async function loadCallableProjects() {
      const collected: WorkflowProjectSummary[] = [];
      let offset = 0;
      let total = 0;
      do {
        const response = await fetchWorkflowProjects({
          limit: 100,
          offset,
          activeOnly: true,
          triggerKind: "call",
        });
        collected.push(...response.items);
        total = response.total;
        offset += response.items.length;
      } while (offset < total && offset < 1_000);
      return collected;
    }
    void loadCallableProjects()
      .then((items) => {
        if (!cancelled) {
          setProjects(items.filter((item) => item.project_id !== currentProjectId));
          setLoadError("");
        }
      })
      .catch((error) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : "无法读取可调用工作流。");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [currentProjectId]);

  useEffect(() => {
    let cancelled = false;
    if (!targetProjectId) {
      setVersions([]);
      setTargetInterface(null);
      return () => { cancelled = true; };
    }
    void fetchWorkflowProject(targetProjectId)
      .then((project) => {
        if (cancelled) return;
        setVersions(project.published_versions.map((item) => item.version));
        if (!targetVersion && project.active_version) {
          onChange({ targetVersion: project.active_version, inputBindings: {} });
        }
      })
      .catch((error) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : "无法读取目标版本。");
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetProjectId, targetVersion]);

  useEffect(() => {
    let cancelled = false;
    if (!targetProjectId || targetVersion < 1) {
      setTargetInterface(null);
      return () => { cancelled = true; };
    }
    void fetchWorkflowVersionInterface(targetProjectId, targetVersion)
      .then((value) => {
        if (cancelled) return;
        setTargetInterface(value);
        const nextBindings = Object.fromEntries(
          value.inputs.flatMap((input) => {
            const existing = bindings[input.name];
            if (existing) return [[input.name, existing]];
            if (batchMode && input.name === value.inputs[0]?.name) {
              return [[input.name, { source: "item" }]];
            }
            if (!input.required) return [];
            return [[input.name, { source: "variable", variable: input.name }]];
          }),
        );
        if (JSON.stringify(nextBindings) !== JSON.stringify(bindings)) {
          onChange({ inputBindings: nextBindings });
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setTargetInterface(null);
          setLoadError(error instanceof Error ? error.message : "无法读取目标接口。");
        }
      });
    return () => { cancelled = true; };
    // bindings are reconciled only when the selected interface changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchMode, targetProjectId, targetVersion]);

  const filteredProjects = projects.filter((project) => {
    const query = search.trim().toLocaleLowerCase();
    return !query || project.title.toLocaleLowerCase().includes(query) || project.project_id.includes(query);
  });
  const selectedProject = projects.find((project) => project.project_id === targetProjectId);
  const batchItemBindingCount = Object.values(bindings).filter(
    (binding) => binding?.source === "item",
  ).length;
  const updateBinding = (name: string, binding?: Record<string, unknown>) => {
    const next = { ...bindings };
    if (batchMode && binding?.source === "item") {
      for (const [otherName, otherBinding] of Object.entries(next)) {
        if (otherName === name || otherBinding?.source !== "item") continue;
        const targetInput = targetInterface?.inputs.find(
          (item) => item.name === otherName,
        );
        if (targetInput?.required) {
          next[otherName] = { source: "variable", variable: otherName };
        } else {
          delete next[otherName];
        }
      }
    }
    if (binding) next[name] = binding;
    else delete next[name];
    onChange({ inputBindings: next });
  };

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-indigo-300/25 bg-indigo-300/10 px-3 py-2 text-xs leading-5 text-indigo-50">
        {batchMode
          ? "每个数组项按顺序调用同一个固定版本，最多 32 项。任一项失败后，后续项不会启动。"
          : "仅调用当前已启用的精确版本；不会跟随“最新版”。失败、超时或取消会使当前工作流失败。"}
      </div>
      <Section title="固定目标" description="先选择使用子流程入口且当前已启用的工作流，再固定它的发布版本。">
        <input aria-label="搜索可调用工作流" className={inputClass} onChange={(event) => setSearch(event.target.value)} placeholder="按名称或工作流 ID 搜索" type="search" value={search} />
        <Field label="目标工作流">
          <select
            className={inputClass}
            disabled={loading}
            onChange={(event) => onChange({ targetProjectId: event.target.value, targetVersion: "", inputBindings: {} })}
            value={targetProjectId}
          >
            <option value="">请选择已启用的可调用工作流</option>
            {filteredProjects.map((project) => <option key={project.project_id} value={project.project_id}>{project.title} · v{project.active_version}</option>)}
          </select>
        </Field>
        {targetProjectId && !selectedProject ? <p className="text-xs text-amber-200">已保存的目标当前不在可调用目录中，请重新选择。</p> : null}
        <Field label="固定发布版本" hint="只有当前启用版本可运行">
          <select className={inputClass} disabled={!targetProjectId} onChange={(event) => onChange({ targetVersion: Number(event.target.value), inputBindings: {} })} value={targetVersion || ""}>
            <option value="">请选择版本</option>
            {versions.map((version) => <option disabled={version !== selectedProject?.active_version} key={version} value={version}>v{version}{version === selectedProject?.active_version ? " · 当前启用" : " · 未启用"}</option>)}
          </select>
        </Field>
        {loadError ? <p className="rounded-lg border border-rose-300/20 bg-rose-300/[0.06] px-3 py-2 text-xs text-rose-100">{loadError}</p> : null}
      </Section>
      {targetInterface ? (
        <Section
          title="输入绑定"
          description={batchMode
            ? "必须把一个目标输入绑定到当前批次项；其他输入可使用序号、上游变量、固定值或目标默认值。"
            : "目标的外部输入会自动列出；可绑定上游变量或填写类型化固定值。"}
        >
          {!targetInterface.active || targetInterface.trigger_kind !== "call" ? <p className="text-xs text-rose-200">该固定版本当前不可调用。</p> : null}
          {!targetInterface.inputs.length ? (
            <p className={batchMode ? "text-xs text-rose-200" : "text-xs text-slate-400"}>
              {batchMode
                ? "该目标没有声明外部输入，无法接收当前批次项。"
                : "目标没有声明外部输入。"}
            </p>
          ) : null}
          {batchMode && targetInterface.inputs.length > 0 && batchItemBindingCount !== 1 ? (
            <p className="rounded-lg border border-rose-300/20 bg-rose-300/[0.06] px-3 py-2 text-xs text-rose-100" role="alert">
              请选择一个且仅一个目标输入作为“当前批次项”。
            </p>
          ) : null}
          {targetInterface.inputs.map((input) => {
            const binding = bindings[input.name];
            const source = binding?.source
              ?? (batchMode && input === targetInterface.inputs[0]
                ? "item"
                : input.required ? "variable" : "default");
            const expectedTypes: WorkflowVariableValueType[] = input.value_type === "json" ? ["json", "unknown"] : [input.value_type, "unknown"];
            return (
              <div className="space-y-2 rounded-lg border border-white/10 bg-black/15 p-3" key={input.name}>
                <div className="flex items-center justify-between gap-3">
                  <span className="font-mono text-xs text-slate-100">{input.name}</span>
                  <span className="text-[10px] text-slate-500">{input.value_type}{input.required ? " · 必填" : " · 有默认值"}</span>
                </div>
                <select className={inputClass} onChange={(event) => {
                  const mode = event.target.value;
                  if (mode === "default") updateBinding(input.name);
                  else if (mode === "item" || mode === "index") updateBinding(input.name, { source: mode });
                  else if (mode === "variable") updateBinding(input.name, { source: "variable", variable: input.name });
                  else updateBinding(input.name, { source: "literal", value: input.default_value ?? (input.value_type === "boolean" ? false : input.value_type === "number" ? 0 : input.value_type === "json" ? null : "") });
                }} value={source}>
                  {!input.required ? <option value="default">使用目标默认值</option> : null}
                  {batchMode ? <option value="item">当前批次项（必须且仅一个）</option> : null}
                  {batchMode ? <option value="index">当前序号（从 0 开始）</option> : null}
                  <option value="variable">绑定上游变量</option>
                  <option value="literal">填写固定值</option>
                </select>
                {source === "variable" ? (
                  <WorkflowVariableField
                    descriptor={{ nodeKind: batchMode ? "iteration" : "invoke_workflow", field: "inputBindings", mode: "binding", fallbackTypes: expectedTypes }}
                    declarations={declarations}
                    edges={edges}
                    fieldName="inputBindings"
                    node={node}
                    nodes={nodes}
                    onChange={(variable) => updateBinding(input.name, { source: "variable", variable })}
                    placeholder="选择或输入上游变量"
                    value={String(binding?.variable ?? input.name)}
                  />
                ) : null}
                {source === "literal" ? (
                  input.value_type === "boolean" ? (
                    <select className={inputClass} onChange={(event) => updateBinding(input.name, { source: "literal", value: event.target.value === "true" })} value={String(Boolean(binding?.value))}><option value="false">false</option><option value="true">true</option></select>
                  ) : input.value_type === "number" ? (
                    <input className={inputClass} onChange={(event) => updateBinding(input.name, { source: "literal", value: Number(event.target.value) })} type="number" value={Number(binding?.value ?? 0)} />
                  ) : input.value_type === "json" ? (
                    <JsonLiteralInput
                      inputName={input.name}
                      onChange={(value) => updateBinding(input.name, { source: "literal", value })}
                      value={binding?.value}
                    />
                  ) : (
                    <input className={inputClass} onChange={(event) => updateBinding(input.name, { source: "literal", value: event.target.value })} value={literalText(binding?.value, input.value_type)} />
                  )
                ) : null}
                {input.description ? <p className="text-[11px] leading-5 text-slate-500">{input.description}</p> : null}
              </div>
            );
          })}
        </Section>
      ) : null}
      <Section
        title="运行结果"
        description={batchMode
          ? "全部完成后一次性写入安全回执数组；不会额外附带原始批次项，但会保留目标工作流的最终文本。"
          : "子流程完成后写入固定版本、父子执行关系和最终文本结果。"}
      >
        <GlobalVariableField
          contract={contract}
          declarations={declarations}
          edges={edges}
          fieldName={batchMode ? "outputVariable" : "resultVariable"}
          hint={batchMode ? "例如 batch_receipts" : "例如 workflow_result"}
          label={batchMode ? "回执数组变量" : "结果变量"}
          node={node}
          nodes={nodes}
          onChange={(value) => onChange(batchMode ? { outputVariable: value } : { resultVariable: value })}
          value={String(batchMode ? data.outputVariable ?? "" : data.resultVariable ?? "")}
        />
        <Field label="最长等待时间" hint="1–60 秒">
          <input className={inputClass} max={60} min={1} onChange={(event) => onChange({ timeoutSeconds: Number(event.target.value) })} type="number" value={Number(data.timeoutSeconds ?? 60)} />
        </Field>
      </Section>
    </div>
  );
}

function IterationV2Config({
  currentProjectId,
  node,
  nodes,
  edges,
  contract,
  declarations = [],
  data,
  onChange,
}: {
  currentProjectId?: string;
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  contract: WorkflowNodeContractProjection | null;
  declarations?: WorkflowVariableDeclaration[];
  data: WorkflowNodeData;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const mode = data.mode === "workflow_map" ? "workflow_map" : "template_map";
  const itemVariable = String(data.itemVariable ?? "item");
  const indexVariable = String(data.indexVariable ?? "item_index");
  const inputVariable = String(data.inputVariable ?? "").trim();
  const outputVariable = String(data.outputVariable ?? "").trim();
  const declaredVariables = new Set(declarations.map((item) => item.name.trim()));
  const localNames = [itemVariable.trim(), indexVariable.trim()];
  const localVariableConflicts = Array.from(new Set(localNames))
    .filter((name) => name && (
      localNames[0] === localNames[1]
      || name === inputVariable
      || name === outputVariable
      || declaredVariables.has(name)
    ));
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50">
        输入必须是真正的 JSON 数组。安全模板最多处理 10000 项；固定子流程最多处理 32 项，均不会静默截断。
      </div>
      <Section title="处理方式" description="本地模板适合轻量文本整理；固定子流程适合每项都需要完整工作流能力的任务。">
        <select
          aria-label="批量处理方式"
          className={inputClass}
          onChange={(event) => {
            const nextMode = event.target.value as "template_map" | "workflow_map";
            onChange({
              mode: nextMode,
              itemVariable: itemVariable || "item",
              indexVariable: indexVariable || "item_index",
              timeoutSeconds: Number(data.timeoutSeconds ?? 60),
            });
          }}
          value={mode}
        >
          <option value="template_map">安全模板：逐项生成文本</option>
          <option value="workflow_map">固定子流程：逐项顺序调用</option>
        </select>
      </Section>
      <Section title="输入数组" description="选择上游的 JSON 数组变量；文本和逗号列表不会自动转换。">
        <GlobalVariableField
          contract={contract}
          declarations={declarations}
          edges={edges}
          fieldName="inputVariable"
          hint="例如 orders 或 records"
          label="数组变量"
          node={node}
          nodes={nodes}
          onChange={(inputVariable) => onChange({ inputVariable })}
          value={String(data.inputVariable ?? "")}
        />
      </Section>
      {localVariableConflicts.length > 0 ? (
        <p
          className="rounded-lg border border-rose-300/30 bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100"
          role="alert"
        >
          局部变量不能与输入、输出或全局变量重名：{localVariableConflicts.join("、")}。
          {mode === "workflow_map" ? "请切换到安全模板后修改变量名，再切回固定子流程。" : ""}
        </p>
      ) : null}
      {mode === "template_map" ? (
        <>
          <Section title="单项模板" description="item 和 item_index 只在此节点内部可用，不会写入全局变量。">
            <div className="grid grid-cols-2 gap-2">
              <Field label="当前项变量">
                <input
                  className={inputClass}
                  onChange={(event) => onChange({ itemVariable: event.target.value })}
                  placeholder="item"
                  value={itemVariable}
                />
              </Field>
              <Field label="序号变量" hint="从 0 开始">
                <input
                  className={inputClass}
                  onChange={(event) => onChange({ indexVariable: event.target.value })}
                  placeholder="item_index"
                  value={indexVariable}
                />
              </Field>
            </div>
            <Field label="每项输出模板" hint="支持插入上游变量和两个局部变量">
              <WorkflowVariableField
                className="min-h-28 resize-none leading-6"
                contract={contract}
                declarations={declarations}
                descriptor={{
                  nodeKind: "iteration",
                  field: "itemTemplate",
                  mode: "template",
                  fallbackTypes: ["text", "number", "boolean", "json", "unknown"],
                  localVariables: [
                    { name: itemVariable, label: "当前批次项", valueType: "unknown" },
                    { name: indexVariable, label: "当前序号", valueType: "number" },
                  ],
                }}
                edges={edges}
                fieldName="itemTemplate"
                multiline
                node={node}
                nodes={nodes}
                onChange={(itemTemplate) => onChange({ itemTemplate })}
                value={String(data.itemTemplate ?? "")}
              />
            </Field>
          </Section>
          <Section title="输出数组" description="全部项目成功后一次性写入真正的字符串数组；失败时不会写入部分结果。">
            <GlobalVariableField
              contract={contract}
              declarations={declarations}
              edges={edges}
              fieldName="outputVariable"
              hint="例如 mapped_items"
              label="结果变量"
              node={node}
              nodes={nodes}
              onChange={(outputVariable) => onChange({ outputVariable })}
              value={String(data.outputVariable ?? "")}
            />
          </Section>
        </>
      ) : (
        <InvokeWorkflowConfig
          batchMode
          contract={contract}
          declarations={declarations}
          currentProjectId={currentProjectId}
          data={data}
          edges={edges}
          node={node}
          nodes={nodes}
          onChange={onChange}
        />
      )}
    </div>
  );
}

const FORM_FIELD_LABELS: Record<WorkflowFormFieldType, string> = {
  short_text: "短文本",
  long_text: "长文本",
  email: "邮箱",
  number: "数字",
  boolean: "确认勾选",
  date: "日期",
  single_select: "单选",
  multi_select: "多选",
};

function stableFormId(prefix: "field" | "option") {
  return `${prefix}_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
}

function safeVariableCandidate(label: string, fallback: string) {
  const ascii = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 56);
  return /^[a-z_]/.test(ascii) ? ascii : fallback;
}

function FormEntryPreview({
  data,
  fields,
  viewport,
}: {
  data: WorkflowNodeData;
  fields: WorkflowFormField[];
  viewport: "desktop" | "mobile";
}) {
  const dark = data.theme === "dark";
  return (
    <div className={`mx-auto transition-[max-width] duration-200 ${viewport === "mobile" ? "max-w-[320px]" : "max-w-xl"}`}>
      <div className={`rounded-xl p-5 ${dark ? "bg-slate-950 text-slate-100" : "bg-slate-100 text-slate-950"}`}>
        <div className={`rounded-xl p-5 ${dark ? "border border-white/10 bg-slate-900" : "border border-slate-200 bg-white"}`}>
          <h4 className="text-lg font-semibold">{String(data.formTitle || "未命名表单")}</h4>
          {data.formDescription ? <p className={`mt-2 text-xs leading-5 ${dark ? "text-slate-300" : "text-slate-600"}`}>{String(data.formDescription)}</p> : null}
          <div className="mt-5 space-y-4">
            {fields.map((field) => (
              <div key={field.id}>
                <p className="text-xs font-semibold">{field.label}{field.required ? <span className="ml-1 text-rose-500">*</span> : null}</p>
                <div className={`mt-1.5 min-h-9 rounded-lg border px-3 py-2 text-xs ${dark ? "border-white/15 bg-slate-950 text-slate-400" : "border-slate-300 bg-white text-slate-500"}`}>
                  {field.type === "boolean" ? field.placeholder || "确认此项" : field.placeholder || FORM_FIELD_LABELS[field.type]}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-5 inline-flex min-h-9 items-center rounded-lg bg-cyan-700 px-4 text-xs font-semibold text-white">{String(data.submitLabel || "提交")}</div>
        </div>
      </div>
    </div>
  );
}

function FormEventEntryConfig({
  node,
  nodes,
  edges,
  contract,
  declarations,
  data,
  onChange,
}: {
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  contract: WorkflowNodeContractProjection | null;
  declarations: WorkflowVariableDeclaration[];
  data: WorkflowNodeData;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const fields = (data.fields ?? []) as unknown as WorkflowFormField[];
  const [previewViewport, setPreviewViewport] = useState<"desktop" | "mobile">("desktop");
  const updateFields = (next: WorkflowFormField[]) => onChange({
    fields: next as unknown as WorkflowNodeData["fields"],
  });
  const variableReferenced = (variable: string) => nodes.some((candidate) => (
    candidate.id !== node.id
    && JSON.stringify(candidate.data).includes(variable)
  ));
  const updateField = (index: number, patch: Partial<WorkflowFormField>) => {
    const current = fields[index];
    if (
      patch.outputVariable
      && patch.outputVariable !== current.outputVariable
      && variableReferenced(current.outputVariable)
      && !window.confirm(`变量 ${current.outputVariable} 已被下游节点引用。确认改名后，请同步检查这些引用。`)
    ) return;
    updateFields(fields.map((field, fieldIndex) => fieldIndex === index ? { ...field, ...patch } : field));
  };
  const removeField = (index: number) => {
    const field = fields[index];
    const downstream = variableReferenced(field.outputVariable);
    const message = downstream
      ? `字段“${field.label}”的变量 ${field.outputVariable} 已被下游引用。删除后这些节点会校验失败，仍要删除吗？`
      : `删除字段“${field.label}”？此操作可通过撤销恢复。`;
    if (window.confirm(message)) updateFields(fields.filter((_, fieldIndex) => fieldIndex !== index));
  };
  const addField = () => {
    if (fields.length >= 30) return;
    let variable = `field_${fields.length + 1}`;
    const used = new Set(fields.map((field) => field.outputVariable));
    while (used.has(variable)) variable = `field_${fields.length + 1}_${used.size}`;
    updateFields([
      ...fields,
      {
        id: stableFormId("field"),
        outputVariable: variable,
        label: `字段 ${fields.length + 1}`,
        helpText: "",
        placeholder: "",
        type: "short_text",
        required: false,
        options: [],
      },
    ]);
  };

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-xs leading-5 text-emerald-50">
        启用后由模镜同源发布签名表单。链接密钥只显示一次，提交者不会看到工作流状态或结果。
      </div>
      <Section title="页面内容" description="所有文案均为固定纯文本，不解析 HTML、脚本或变量模板。">
        <Field label="表单标题"><input className={inputClass} maxLength={120} onChange={(event) => onChange({ formTitle: event.target.value })} value={String(data.formTitle ?? "")} /></Field>
        <Field label="表单说明"><textarea className={`${inputClass} min-h-20 resize-y`} maxLength={1000} onChange={(event) => onChange({ formDescription: event.target.value })} value={String(data.formDescription ?? "")} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="提交按钮"><input className={inputClass} maxLength={40} onChange={(event) => onChange({ submitLabel: event.target.value })} value={String(data.submitLabel ?? "")} /></Field>
          <Field label="页面主题"><select className={inputClass} onChange={(event) => onChange({ theme: event.target.value as "light" | "dark" })} value={data.theme ?? "light"}><option value="light">浅色</option><option value="dark">深色</option></select></Field>
        </div>
        <Field label="隐私说明" hint="说明数据用途，不要填写链接"><textarea className={`${inputClass} min-h-16 resize-y`} maxLength={1000} onChange={(event) => onChange({ privacyNotice: event.target.value })} value={String(data.privacyNotice ?? "")} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="成功标题"><input className={inputClass} maxLength={120} onChange={(event) => onChange({ successTitle: event.target.value })} value={String(data.successTitle ?? "")} /></Field>
          <Field label="成功说明"><input className={inputClass} maxLength={1000} onChange={(event) => onChange({ successMessage: event.target.value })} value={String(data.successMessage ?? "")} /></Field>
        </div>
      </Section>

      <Section title="提交字段" description="字段 ID 和选项值保持稳定；重排或改标签不会改变下游语义。">
        <div className="space-y-3">
          {fields.map((field, index) => {
            const selectField = field.type === "single_select" || field.type === "multi_select";
            return (
              <div className="rounded-xl border border-white/10 bg-black/15 p-3" key={field.id}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-white">{index + 1}. {field.label || "未命名字段"}</p>
                    <p className="mt-1 font-mono text-[11px] text-slate-500">{field.id}</p>
                  </div>
                  <div className="flex gap-1">
                    <button className="rounded-md border border-white/10 px-2 py-1 text-xs text-slate-300 disabled:opacity-30" disabled={index === 0} onClick={() => updateFields(fields.map((item, itemIndex) => itemIndex === index - 1 ? fields[index] : itemIndex === index ? fields[index - 1] : item))} type="button">上移</button>
                    <button className="rounded-md border border-white/10 px-2 py-1 text-xs text-slate-300 disabled:opacity-30" disabled={index === fields.length - 1} onClick={() => updateFields(fields.map((item, itemIndex) => itemIndex === index + 1 ? fields[index] : itemIndex === index ? fields[index + 1] : item))} type="button">下移</button>
                    <button className="rounded-md border border-rose-300/20 px-2 py-1 text-xs text-rose-200 disabled:opacity-30" disabled={fields.length <= 1} onClick={() => removeField(index)} type="button">删除</button>
                  </div>
                </div>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <Field label="字段标签"><input className={inputClass} maxLength={120} onBlur={(event) => { if (!field.outputVariable) updateField(index, { outputVariable: safeVariableCandidate(event.target.value, `field_${index + 1}`) }); }} onChange={(event) => updateField(index, { label: event.target.value })} value={field.label} /></Field>
                  <Field label="字段类型"><select className={inputClass} onChange={(event) => { const type = event.target.value as WorkflowFormFieldType; updateField(index, { type, options: ["single_select", "multi_select"].includes(type) && field.options.length < 2 ? [{ id: stableFormId("option"), value: "option_1", label: "选项 1" }, { id: stableFormId("option"), value: "option_2", label: "选项 2" }] : ["single_select", "multi_select"].includes(type) ? field.options : [] }); }} value={field.type}>{Object.entries(FORM_FIELD_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
                </div>
                <div className="mt-3">
                  <GlobalVariableField contract={contract} declarations={declarations} edges={edges} fieldName={`fields.${index}.outputVariable`} hint="例如 contact_email" label="输出变量" node={node} nodes={nodes} onChange={(outputVariable) => updateField(index, { outputVariable })} value={field.outputVariable} />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="输入提示"><input className={inputClass} maxLength={200} onChange={(event) => updateField(index, { placeholder: event.target.value })} value={field.placeholder} /></Field>
                  <Field label="帮助说明"><input className={inputClass} maxLength={500} onChange={(event) => updateField(index, { helpText: event.target.value })} value={field.helpText} /></Field>
                </div>
                <label className="mt-3 flex min-h-10 items-center gap-2 text-sm text-slate-200"><input checked={field.required} className="h-4 w-4 accent-cyan-500" onChange={(event) => updateField(index, { required: event.target.checked })} type="checkbox" />提交前必须完成</label>
                {selectField ? (
                  <div className="mt-3 space-y-2 border-t border-white/10 pt-3">
                    <p className="text-xs font-semibold text-slate-200">选项</p>
                    {field.options.map((option, optionIndex) => (
                      <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]" key={option.id}>
                        <input aria-label={`选项 ${optionIndex + 1} 标签`} className={inputClass} maxLength={120} onChange={(event) => updateField(index, { options: field.options.map((item, itemIndex) => itemIndex === optionIndex ? { ...item, label: event.target.value } : item) })} placeholder="显示标签" value={option.label} />
                        <input aria-label={`选项 ${optionIndex + 1} 稳定值`} className={`${inputClass} font-mono`} maxLength={64} onChange={(event) => updateField(index, { options: field.options.map((item, itemIndex) => itemIndex === optionIndex ? { ...item, value: event.target.value } : item) })} placeholder="stable_value" value={option.value} />
                        <button className="rounded-lg border border-rose-300/20 px-3 text-xs text-rose-200 disabled:opacity-30" disabled={field.options.length <= 2} onClick={() => updateField(index, { options: field.options.filter((_, itemIndex) => itemIndex !== optionIndex) })} type="button">删除</button>
                      </div>
                    ))}
                    <button className="rounded-lg border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200 disabled:opacity-30" disabled={field.options.length >= 20} onClick={() => updateField(index, { options: [...field.options, { id: stableFormId("option"), value: `option_${field.options.length + 1}`, label: `选项 ${field.options.length + 1}` }] })} type="button">添加选项</button>
                  </div>
                ) : null}
              </div>
            );
          })}
          <button className="w-full rounded-lg border border-dashed border-cyan-300/30 px-3 py-3 text-sm font-semibold text-cyan-100 disabled:opacity-40" disabled={fields.length >= 30} onClick={addField} type="button">添加字段（{fields.length}/30）</button>
        </div>
      </Section>

      <Section title="运行变量" description="事件对象只含安全元数据；提交对象和每个字段值仅存在于本次执行内存。">
        <GlobalVariableField contract={contract} declarations={declarations} edges={edges} fieldName="eventVariable" hint="例如 form_event" label="事件元数据变量" node={node} nodes={nodes} onChange={(eventVariable) => onChange({ eventVariable })} value={String(data.eventVariable ?? "")} />
        <GlobalVariableField contract={contract} declarations={declarations} edges={edges} fieldName="submissionVariable" hint="例如 form_submission" label="完整提交对象变量" node={node} nodes={nodes} onChange={(submissionVariable) => onChange({ submissionVariable })} value={String(data.submissionVariable ?? "")} />
      </Section>

      <Section title="本地预览" description="预览不会创建分享链接、调用 API 或保存填写内容。">
        <div className="flex gap-2">
          <button className={`rounded-lg px-3 py-2 text-xs font-semibold ${previewViewport === "desktop" ? "bg-cyan-500/20 text-cyan-100" : "border border-white/10 text-slate-300"}`} onClick={() => setPreviewViewport("desktop")} type="button">桌面</button>
          <button className={`rounded-lg px-3 py-2 text-xs font-semibold ${previewViewport === "mobile" ? "bg-cyan-500/20 text-cyan-100" : "border border-white/10 text-slate-300"}`} onClick={() => setPreviewViewport("mobile")} type="button">移动端</button>
        </div>
        <FormEntryPreview data={data} fields={fields} viewport={previewViewport} />
      </Section>
    </div>
  );
}

function RssEventEntryConfig({
  node,
  nodes,
  edges,
  contract,
  declarations,
  data,
  featureEnabled,
  featureDisabledReason,
  onChange,
}: {
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  contract: WorkflowNodeContractProjection | null;
  declarations: WorkflowVariableDeclaration[];
  data: WorkflowNodeData;
  featureEnabled: boolean;
  featureDisabledReason: string;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState("");
  const [checkResult, setCheckResult] = useState<Awaited<ReturnType<typeof inspectWorkflowRss>> | null>(null);
  const interval = Number(data.pollIntervalMinutes ?? 15);
  const commonInterval = [5, 15, 30, 60, 360, 1440].includes(interval)
    ? String(interval)
    : "custom";

  async function inspect() {
    setChecking(true);
    setCheckError("");
    setCheckResult(null);
    try {
      setCheckResult(await inspectWorkflowRss(String(data.feedUrl ?? "")));
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      setCheckError(
        /local, private|reserved RSS targets|metadata RSS targets/i.test(message)
          ? "此地址指向本机、内网或保留网络，不能访问。请使用无需登录的公网 HTTPS 订阅地址。"
          : message || "订阅源检查失败。",
      );
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="space-y-4">
      {!featureEnabled ? (
        <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50" role="status">
          当前环境尚未开启真实 RSS 检查和订阅。可先完成配置与静态发布；启用前请管理员开启 WORKFLOW_RSS_TRIGGERS_ENABLED。
          {featureDisabledReason ? <span className="sr-only">{featureDisabledReason}</span> : null}
        </div>
      ) : null}
      <div className="rounded-lg border border-orange-300/25 bg-orange-300/10 px-3 py-2 text-xs leading-5 text-orange-50">
        首次启用只记录当前条目作为基线，不补跑历史内容；之后每个新条目各启动一次工作流。
      </div>
      <Section title="订阅源" description="仅支持无需登录的公网 HTTPS RSS 2.0 或 Atom 1.0 地址。">
        <Field label="Feed 地址" hint="固定 HTTPS 地址；不能使用变量、密钥参数或内网地址。">
          <input
            aria-label="Feed 地址"
            className={inputClass}
            maxLength={2048}
            onChange={(event) => {
              onChange({ feedUrl: event.target.value });
              setCheckResult(null);
              setCheckError("");
            }}
            placeholder="https://example.com/feed.xml"
            type="url"
            value={String(data.feedUrl ?? "")}
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-[1fr_8rem]">
          <Field label="检查频率">
            <select
              aria-label="检查频率"
              className={inputClass}
              onChange={(event) => {
                if (event.target.value !== "custom") {
                  onChange({ pollIntervalMinutes: Number(event.target.value) });
                } else if ([5, 15, 30, 60, 360, 1440].includes(interval)) {
                  onChange({ pollIntervalMinutes: 90 });
                }
              }}
              value={commonInterval}
            >
              <option value="5">每 5 分钟</option>
              <option value="15">每 15 分钟</option>
              <option value="30">每 30 分钟</option>
              <option value="60">每小时</option>
              <option value="360">每 6 小时</option>
              <option value="1440">每天</option>
              <option value="custom">自定义</option>
            </select>
          </Field>
          {commonInterval === "custom" ? (
            <Field label="分钟">
              <input
                className={inputClass}
                max={1440}
                min={5}
                onChange={(event) => onChange({ pollIntervalMinutes: Number(event.target.value) })}
                type="number"
                value={String(data.pollIntervalMinutes ?? 15)}
              />
            </Field>
          ) : null}
        </div>
        <button
          className="rounded-lg border border-cyan-300/30 bg-cyan-300/10 px-3 py-2 text-xs font-semibold text-cyan-100 transition hover:bg-cyan-300/20 disabled:opacity-40"
          disabled={checking || !String(data.feedUrl ?? "").trim()}
          onClick={() => void inspect()}
          type="button"
        >
          {checking ? "正在安全检查…" : "检查订阅源"}
        </button>
        {checkError ? <p className="text-xs leading-5 text-rose-200">{checkError}</p> : null}
        {checkResult ? (
          <div className="rounded-lg border border-emerald-300/20 bg-emerald-300/10 p-3 text-xs text-emerald-50">
            <p className="font-semibold">{checkResult.feedTitle || "未命名订阅源"} · {checkResult.format === "atom1" ? "Atom 1.0" : "RSS 2.0"} · {checkResult.itemCount} 条</p>
            {checkResult.items.length ? (
              <ul className="mt-2 space-y-1 text-emerald-100/80">
                {checkResult.items.map((item, index) => (
                  <li key={`${item.link ?? item.title ?? "item"}-${index}`}>• {item.title || "无标题条目"}{item.publishedAt ? ` · ${item.publishedAt}` : ""}</li>
                ))}
              </ul>
            ) : <p className="mt-2 text-emerald-100/75">源格式有效，但当前没有条目。</p>}
          </div>
        ) : null}
      </Section>
      <Section title="运行变量" description="事件变量只保存安全元数据；条目变量包含标题、链接、摘要和正文，均视为不可信外部输入。">
        <GlobalVariableField contract={contract} declarations={declarations} edges={edges} fieldName="eventVariable" hint="例如 rss_event" label="事件元数据变量" node={node} nodes={nodes} onChange={(eventVariable) => onChange({ eventVariable })} value={String(data.eventVariable ?? "")} />
        <GlobalVariableField contract={contract} declarations={declarations} edges={edges} fieldName="itemVariable" hint="例如 rss_item" label="完整条目变量" node={node} nodes={nodes} onChange={(itemVariable) => onChange({ itemVariable })} value={String(data.itemVariable ?? "")} />
      </Section>
    </div>
  );
}

function EmailEventEntryConfig({
  node,
  nodes,
  edges,
  contract,
  declarations,
  data,
  featureEnabled,
  featureDisabledReason,
  onChange,
}: {
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  contract: WorkflowNodeContractProjection | null;
  declarations: WorkflowVariableDeclaration[];
  data: WorkflowNodeData;
  featureEnabled: boolean;
  featureDisabledReason: string;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const [credentials, setCredentials] = useState<EmailCredentialSummary[]>([]);
  const [loadingCredentials, setLoadingCredentials] = useState(false);
  const [creating, setCreating] = useState(false);
  const [credentialName, setCredentialName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [checking, setChecking] = useState(false);
  const [notice, setNotice] = useState("");
  const [checkResult, setCheckResult] = useState<Awaited<ReturnType<typeof inspectWorkflowEmail>> | null>(null);
  const interval = Number(data.pollIntervalMinutes ?? 15);
  const commonInterval = [5, 15, 30, 60, 360, 1440].includes(interval) ? String(interval) : "custom";

  function emailInspectFailureMessage(error: unknown) {
    const detail = error instanceof Error ? error.message.toLowerCase() : "";
    if (detail.includes("disabled")) {
      return "当前环境尚未开启真实邮箱检查，请联系管理员开启后重试。";
    }
    if (detail.includes("credential") || detail.includes("authentication")) {
      return "邮箱凭据无效或已失效，请检查用户名、应用密码，或重新选择凭据。";
    }
    if (detail.includes("tls") || detail.includes("certificate")) {
      return "无法建立可信的 IMAPS 连接，请确认服务器支持 993 端口并使用有效 TLS 证书。";
    }
    if (
      detail.includes("local")
      || detail.includes("private")
      || detail.includes("reserved")
      || detail.includes("metadata")
      || detail.includes("ip address")
      || detail.includes("hostname is invalid")
      || detail.includes("fixed public hostname")
      || detail.includes("ascii hostname")
      || detail.includes("rebinding")
    ) {
      return "此服务器不是可安全访问的公网 IMAP 域名，请检查域名后重试。";
    }
    if (
      detail.includes("dns")
      || detail.includes("connection")
      || detail.includes("connect")
      || detail.includes("timeout")
      || detail.includes("timed out")
    ) {
      return "暂时无法连接此邮件服务器，请检查域名和网络后重试。";
    }
    return "邮箱检查失败，请核对服务器和凭据后重试。";
  }

  async function loadCredentials() {
    setLoadingCredentials(true);
    try {
      const response = await fetch("/api/runtime/credentials");
      if (!response.ok) throw new Error("凭据列表加载失败。");
      const payload = await response.json() as { credentials?: EmailCredentialSummary[] };
      setCredentials((payload.credentials ?? []).filter((item) => item.kind === "generic" && item.status === "active"));
      setNotice("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "凭据列表加载失败。");
    } finally {
      setLoadingCredentials(false);
    }
  }

  useEffect(() => { void loadCredentials(); }, []);

  async function createCredential() {
    if (!credentialName.trim() || !username.trim() || !password) {
      setNotice("请填写凭据名称、邮箱用户名和应用密码。");
      return;
    }
    setLoadingCredentials(true);
    try {
      const response = await fetch("/api/runtime/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: credentialName.trim(),
          kind: "generic",
          value: JSON.stringify({ username: username.trim(), password }),
        }),
      });
      if (!response.ok) throw new Error("邮箱凭据保存失败。");
      const created = await response.json() as EmailCredentialSummary;
      onChange({ credentialId: created.credential_id });
      setCredentialName(""); setUsername(""); setPassword(""); setCreating(false);
      await loadCredentials();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "邮箱凭据保存失败。");
    } finally {
      setLoadingCredentials(false);
    }
  }

  async function inspect() {
    setChecking(true); setNotice(""); setCheckResult(null);
    try {
      setCheckResult(await inspectWorkflowEmail(String(data.host ?? ""), String(data.credentialId ?? "")));
    } catch (error) {
      setNotice(emailInspectFailureMessage(error));
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="space-y-4">
      {!featureEnabled ? <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50" role="status">当前环境尚未开启真实邮箱检查和订阅。可先完成配置与静态发布；启用前请管理员开启 WORKFLOW_IMAP_TRIGGERS_ENABLED。{featureDisabledReason ? <span className="sr-only">{featureDisabledReason}</span> : null}</div> : null}
      <div className="rounded-lg border border-sky-300/25 bg-sky-300/10 px-3 py-2 text-xs leading-5 text-sky-50">首次启用只记录 INBOX 当前最高邮件作为基线，不补跑历史邮件；检查始终只读，不会标记已读、移动或删除邮件。</div>
      <div className="rounded-lg border border-violet-300/20 bg-violet-300/[0.08] px-3 py-2 text-xs leading-5 text-violet-50">手动测试会读取当前最新一封邮件并真实执行下游节点，但不会创建订阅或改变 UID 基线。</div>
      <Section title="邮箱连接" description="首版仅支持公网 IMAPS 993、INBOX、用户名与应用密码。">
        <Field label="IMAP 服务器" hint="填写固定公网域名，不含 imaps://、端口或变量。"><input aria-label="IMAP 服务器" className={inputClass} maxLength={253} onChange={(event) => { onChange({ host: event.target.value }); setCheckResult(null); }} placeholder="imap.example.com" value={String(data.host ?? "")} /></Field>
        <div className="space-y-2">
          <div className="flex gap-2"><select aria-label="邮箱凭据" className={inputClass} disabled={loadingCredentials} onChange={(event) => { onChange({ credentialId: event.target.value }); setCheckResult(null); }} value={String(data.credentialId ?? "")}><option value="">选择已加密凭据</option>{credentials.map((item) => <option key={item.credential_id} value={item.credential_id}>{item.name} · 已加密</option>)}</select><button className="rounded-lg border border-white/15 px-3 text-xs text-slate-200" disabled={loadingCredentials} onClick={() => void loadCredentials()} type="button">刷新</button></div>
          <div className="flex flex-wrap gap-3 text-[11px]"><button className="font-medium text-cyan-200 underline underline-offset-4" onClick={() => setCreating((value) => !value)} type="button">{creating ? "取消创建" : "创建邮箱凭据"}</button><a className="text-slate-300 underline underline-offset-4" href="/toolsets">打开凭据中心</a></div>
          {creating ? <div className="space-y-2 rounded-lg border border-white/10 bg-black/10 p-3"><input className={inputClass} onChange={(event) => setCredentialName(event.target.value)} placeholder="凭据名称，如 公告收件箱" value={credentialName} /><input autoComplete="username" className={inputClass} onChange={(event) => setUsername(event.target.value)} placeholder="邮箱用户名" value={username} /><input autoComplete="new-password" className={inputClass} onChange={(event) => setPassword(event.target.value)} placeholder="应用密码" type="password" value={password} /><button className="rounded-md bg-cyan-300 px-3 py-2 text-xs font-semibold text-slate-950 disabled:opacity-50" disabled={loadingCredentials} onClick={() => void createCredential()} type="button">加密保存并选用</button></div> : null}
        </div>
        <div className="grid gap-3 sm:grid-cols-[1fr_8rem]"><Field label="检查频率"><select aria-label="邮箱检查频率" className={inputClass} onChange={(event) => event.target.value === "custom" ? onChange({ pollIntervalMinutes: 90 }) : onChange({ pollIntervalMinutes: Number(event.target.value) })} value={commonInterval}><option value="5">每 5 分钟</option><option value="15">每 15 分钟</option><option value="30">每 30 分钟</option><option value="60">每小时</option><option value="360">每 6 小时</option><option value="1440">每天</option><option value="custom">自定义</option></select></Field>{commonInterval === "custom" ? <Field label="分钟"><input className={inputClass} max={1440} min={5} onChange={(event) => onChange({ pollIntervalMinutes: Number(event.target.value) })} type="number" value={interval} /></Field> : null}</div>
        <button className="rounded-lg border border-cyan-300/30 bg-cyan-300/10 px-3 py-2 text-xs font-semibold text-cyan-100 disabled:opacity-40" disabled={checking || !String(data.host ?? "").trim() || !String(data.credentialId ?? "")} onClick={() => void inspect()} type="button">{checking ? "正在只读检查…" : "检查邮箱"}</button>
        {notice ? <p className="text-xs leading-5 text-rose-200" role="alert">{notice}</p> : null}
        {checkResult ? <div className="rounded-lg border border-emerald-300/20 bg-emerald-300/10 p-3 text-xs text-emerald-50"><p className="font-semibold">INBOX 可访问 · UIDVALIDITY {checkResult.uidValidity} · 当前 {checkResult.messageCount} 封邮件</p>{checkResult.items.length ? <ul className="mt-2 space-y-1 text-emerald-100/80">{checkResult.items.map((item, index) => <li key={`${item.subject}-${index}`}>• {item.subject || "无主题邮件"}{item.from[0]?.address ? ` · ${item.from[0].address}` : ""}{item.sentAt ? ` · ${item.sentAt}` : ""}</li>)}</ul> : <p className="mt-2">当前 INBOX 没有邮件。</p>}</div> : null}
      </Section>
      <Section title="运行变量" description="事件只含 message key 等安全元数据；邮件元数据和带固定不可信边界的纯文本只存在于本次执行内存。">
        <GlobalVariableField contract={contract} declarations={declarations} edges={edges} fieldName="eventVariable" hint="例如 email_event" label="事件元数据变量" node={node} nodes={nodes} onChange={(eventVariable) => onChange({ eventVariable })} value={String(data.eventVariable ?? "")} />
        <GlobalVariableField contract={contract} declarations={declarations} edges={edges} fieldName="messageVariable" hint="例如 email_message" label="邮件元数据变量" node={node} nodes={nodes} onChange={(messageVariable) => onChange({ messageVariable })} value={String(data.messageVariable ?? "")} />
        <GlobalVariableField contract={contract} declarations={declarations} edges={edges} fieldName="contentVariable" hint="例如 email_content" label="安全纯文本变量" node={node} nodes={nodes} onChange={(contentVariable) => onChange({ contentVariable })} value={String(data.contentVariable ?? "")} />
      </Section>
      <div className="rounded-lg border border-rose-300/20 bg-rose-300/[0.07] px-3 py-2 text-xs leading-5 text-rose-50">邮件内容是不可信外部输入。连接 Agent 或 HTTP 节点时，内容可能被发送给模型或外部服务；建议加入内容策略，并按业务需要启用邮箱地址、电话号码和疑似凭据规则。</div>
    </div>
  );
}

export default function WorkflowDeploymentNodeConfig({
  currentProjectId,
  node,
  nodes,
  edges,
  contract,
  declarations = [],
  data,
  featureEnabled = true,
  featureDisabledReason = "",
  onChange,
}: {
  currentProjectId?: string;
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  contract: WorkflowNodeContractProjection | null;
  declarations?: WorkflowVariableDeclaration[];
  data: WorkflowNodeData;
  featureEnabled?: boolean;
  featureDisabledReason?: string;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  if (data.kind === "form_event_entry") {
    return (
      <FormEventEntryConfig
        contract={contract}
        data={data}
        declarations={declarations}
        edges={edges}
        node={node}
        nodes={nodes}
        onChange={onChange}
      />
    );
  }

  if (data.kind === "rss_event_entry") {
    return (
      <RssEventEntryConfig
        contract={contract}
        data={data}
        declarations={declarations}
        edges={edges}
        featureDisabledReason={featureDisabledReason}
        featureEnabled={featureEnabled}
        node={node}
        nodes={nodes}
        onChange={onChange}
      />
    );
  }

  if (data.kind === "email_event_entry") {
    return <EmailEventEntryConfig contract={contract} data={data} declarations={declarations} edges={edges} featureDisabledReason={featureDisabledReason} featureEnabled={featureEnabled} node={node} nodes={nodes} onChange={onChange} />;
  }

  if (data.kind === "failure_event_entry") {
    return (
      <FailureEntryConfig
        contract={contract}
        declarations={declarations}
        currentProjectId={currentProjectId}
        data={data}
        edges={edges}
        node={node}
        nodes={nodes}
        onChange={onChange}
      />
    );
  }

  if (data.kind === "workflow_call_entry") {
    return (
      <WorkflowCallEntryConfig
        contract={contract}
        declarations={declarations}
        data={data}
        edges={edges}
        node={node}
        nodes={nodes}
        onChange={onChange}
      />
    );
  }

  if (data.kind === "invoke_workflow") {
    return (
      <InvokeWorkflowConfig
        contract={contract}
        declarations={declarations}
        currentProjectId={currentProjectId}
        data={data}
        edges={edges}
        node={node}
        nodes={nodes}
        onChange={onChange}
      />
    );
  }

  if (data.kind === "iteration" && Number(data.contractVersion) === 2) {
    return (
      <IterationV2Config
        contract={contract}
        declarations={declarations}
        currentProjectId={currentProjectId}
        data={data}
        edges={edges}
        node={node}
        nodes={nodes}
        onChange={onChange}
      />
    );
  }

  if (data.kind === "scheduled_start") {
    const scheduleType = data.scheduleType ?? "interval";
    const cron = parseCronExpressionForUi(data.cronExpression);
    const updateCron = (patch: Partial<CronUiValue>) => {
      const next = { ...cron, ...patch };
      onChange({ cronExpression: cronExpressionForUi(next) });
    };
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50">
          只有已发布并启用的独立工作流会按此计划运行；同一时刻尚未结束时会跳过重复启动。
        </div>
        <Section title="什么时候启动" description="选择一次、固定间隔或常用日历规则，无需手写 Cron。">
          <Field label="运行方式">
            <select
              className={inputClass}
              onChange={(event) => onChange({ scheduleType: event.target.value as WorkflowNodeData["scheduleType"] })}
              value={scheduleType}
            >
              <option value="once">只运行一次</option>
              <option value="interval">每隔一段时间</option>
              <option value="cron">按日历重复</option>
            </select>
          </Field>
          {scheduleType === "once" ? (
            <Field label="运行日期和时间">
              <input
                className={inputClass}
                onChange={(event) => onChange({ onceAt: event.target.value })}
                type="datetime-local"
                value={dateTimeLocalValue(data.onceAt)}
              />
            </Field>
          ) : null}
          {scheduleType === "interval" ? (
            <Field label="运行间隔" hint="最短 30 秒">
              <DurationField
                maximumSeconds={31_536_000}
                minimumSeconds={30}
                onChange={(intervalSeconds) => onChange({ intervalSeconds })}
                value={data.intervalSeconds ?? 30}
              />
            </Field>
          ) : null}
          {scheduleType === "cron" ? (
            <div className="space-y-3">
              <Field label="重复规则">
                <select
                  className={inputClass}
                  onChange={(event) => {
                    const pattern = event.target.value as CronPattern;
                    if (pattern === "custom") onChange({ cronExpression: "0 9 * * 1-5" });
                    else onChange({ cronExpression: defaultCronExpression(pattern) });
                  }}
                  value={cron.pattern}
                >
                  <option value="minutes">每隔几分钟</option>
                  <option value="hourly">每小时</option>
                  <option value="daily">每天</option>
                  <option value="weekly">每周</option>
                  <option value="monthly">每月</option>
                  <option value="custom">自定义 Cron（高级）</option>
                </select>
              </Field>
              {cron.pattern === "minutes" ? (
                <Field label="间隔分钟数">
                  <select className={inputClass} onChange={(event) => updateCron({ intervalMinutes: Number(event.target.value) })} value={cron.intervalMinutes}>
                    {[1, 5, 10, 15, 30].map((value) => <option key={value} value={value}>每 {value} 分钟</option>)}
                  </select>
                </Field>
              ) : null}
              {cron.pattern === "hourly" ? (
                <Field label="每小时的第几分钟">
                  <input className={inputClass} max={59} min={0} onChange={(event) => updateCron({ minute: Number(event.target.value) })} type="number" value={cron.minute} />
                </Field>
              ) : null}
              {["daily", "weekly", "monthly"].includes(cron.pattern) ? (
                <Field label="运行时间">
                  <input
                    className={inputClass}
                    onChange={(event) => {
                      const [hour, minute] = event.target.value.split(":").map(Number);
                      updateCron({ hour, minute });
                    }}
                    type="time"
                    value={`${pad2(cron.hour)}:${pad2(cron.minute)}`}
                  />
                </Field>
              ) : null}
              {cron.pattern === "weekly" ? (
                <Field label="星期几">
                  <select className={inputClass} onChange={(event) => updateCron({ weekday: Number(event.target.value) })} value={cron.weekday}>
                    <option value={1}>星期一</option><option value={2}>星期二</option><option value={3}>星期三</option><option value={4}>星期四</option><option value={5}>星期五</option><option value={6}>星期六</option><option value={0}>星期日</option>
                  </select>
                </Field>
              ) : null}
              {cron.pattern === "monthly" ? (
                <Field label="每月几号">
                  <input className={inputClass} max={31} min={1} onChange={(event) => updateCron({ monthday: Number(event.target.value) })} type="number" value={cron.monthday} />
                </Field>
              ) : null}
              {cron.pattern === "custom" ? (
                <Field label="Cron 表达式" hint="分 时 日 月 周，共五段">
                  <input className={`${inputClass} font-mono`} onChange={(event) => onChange({ cronExpression: event.target.value })} placeholder="例如 0 9 * * 1" value={data.cronExpression ?? ""} />
                </Field>
              ) : null}
            </div>
          ) : null}
          {scheduleType !== "interval" ? <TimezoneField onChange={(timezone) => onChange({ timezone })} value={String(data.timezone ?? "UTC")} /> : null}
        </Section>
        <Section title="启动数据" description="每次启动都会生成时间、时区和唯一 occurrence key，供后续节点使用。">
          <GlobalVariableField contract={contract} edges={edges} fieldName="eventVariable" hint="例如 schedule_event" label="计划事件变量" node={node} nodes={nodes} onChange={(eventVariable) => onChange({ eventVariable })} value={String(data.eventVariable ?? "")} />
        </Section>
      </div>
    );
  }

  if (data.kind === "http_event_entry") {
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-xs leading-5 text-cyan-50">
          地址和私有密钥会在工作流启用后生成。请求必须携带幂等键，认证信息不会写入变量或运行记录。
        </div>
        <Section title="接收请求" description="R1 固定使用私有 POST；这里配置允许的数据格式和大小。">
          <div className="rounded-lg border border-white/10 bg-black/15 px-3 py-2 font-mono text-xs text-slate-300">
            POST /api/workflow-hooks/（启用后生成）
          </div>
          <Field label="允许的正文格式">
            <select
              className={inputClass}
              onChange={(event) => onChange({ acceptedContentType: event.target.value as WorkflowNodeData["acceptedContentType"] })}
              value={data.acceptedContentType ?? "both"}
            >
              <option value="both">JSON 或纯文本</option>
              <option value="json">仅 JSON</option>
              <option value="text">仅纯文本</option>
            </select>
          </Field>
          <Field label="最大正文大小" hint="服务端硬上限 1 MiB">
            <select className={inputClass} onChange={(event) => onChange({ maxBodyBytes: Number(event.target.value) })} value={Number(data.maxBodyBytes ?? 1_048_576)}>
              <option value={65_536}>64 KiB</option>
              <option value={262_144}>256 KiB</option>
              <option value={1_048_576}>1 MiB</option>
            </select>
          </Field>
        </Section>
        <Section title="保存到变量" description="变量会出现在全局变量中心，可被后续节点直接选择或插入模板。">
          <GlobalVariableField contract={contract} edges={edges} fieldName="eventVariable" hint="完整事件对象：正文、格式、接收时间和 occurrence key" label="完整事件变量" node={node} nodes={nodes} onChange={(eventVariable) => onChange({ eventVariable })} value={String(data.eventVariable ?? "")} />
          <GlobalVariableField contract={contract} edges={edges} fieldName="bodyVariable" hint="只保存请求正文，便于后续节点直接使用；留空则不单独创建" label="请求正文变量" node={node} nodes={nodes} onChange={(bodyVariable) => onChange({ bodyVariable })} value={String(data.bodyVariable ?? "")} />
        </Section>
      </div>
    );
  }

  if (data.kind === "suspend_wait") {
    const waitMode = data.waitMode ?? "duration";
    const untilInputMode = data.untilInputMode ?? (String(data.untilTemplate ?? "").includes("{{") ? "template" : "fixed");
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-violet-300/25 bg-violet-300/10 px-3 py-2 text-xs leading-5 text-violet-50">
          等待期间不占用 Worker；进程重启后会从持久化状态恢复，最长可等待 30 天。
        </div>
        <Section title="等待条件" description="选择等待一段时长，或在指定日期时间继续。">
          <Field label="等待方式">
            <select className={inputClass} onChange={(event) => onChange({ waitMode: event.target.value as WorkflowNodeData["waitMode"] })} value={waitMode}>
              <option value="duration">等待一段时间</option>
              <option value="until">等待到指定时间</option>
            </select>
          </Field>
          {waitMode === "duration" ? (
            <Field label="等待时长" hint="最长 30 天">
              <DurationField maximumSeconds={2_592_000} minimumSeconds={1} onChange={(durationSeconds) => onChange({ durationSeconds })} value={data.durationSeconds ?? 60} />
            </Field>
          ) : (
            <div className="space-y-3">
              <Field label="时间来源">
                <select
                  className={inputClass}
                  onChange={(event) => {
                    const mode = event.target.value as WorkflowNodeData["untilInputMode"];
                    onChange({ untilInputMode: mode, untilTemplate: mode === "template" ? "{{resume_at}}" : "" });
                  }}
                  value={untilInputMode}
                >
                  <option value="fixed">直接选择日期和时间</option>
                  <option value="template">从全局变量或模板读取</option>
                </select>
              </Field>
              {untilInputMode === "fixed" ? (
                <Field label="继续日期和时间">
                  <input className={inputClass} onChange={(event) => onChange({ untilTemplate: event.target.value })} type="datetime-local" value={dateTimeLocalValue(data.untilTemplate)} />
                </Field>
              ) : (
                <Field label="时间变量或模板" hint="结果应为 ISO 日期时间">
                  <WorkflowVariableField className="font-mono" contract={contract} edges={edges} fieldName="untilTemplate" node={node} nodes={nodes} onChange={(untilTemplate) => onChange({ untilTemplate })} placeholder="例如 {{resume_at}}" value={String(data.untilTemplate ?? "")} />
                </Field>
              )}
              <TimezoneField onChange={(untilTimezone) => onChange({ untilTimezone })} value={String(data.untilTimezone ?? "UTC")} />
            </div>
          )}
        </Section>
        <Section title="恢复数据" description="恢复时会写入计划恢复时间和实际恢复时间。">
          <GlobalVariableField contract={contract} edges={edges} fieldName="outputVariable" hint="例如 resume_event" label="恢复事件变量" node={node} nodes={nodes} onChange={(outputVariable) => onChange({ outputVariable })} value={String(data.outputVariable ?? "")} />
        </Section>
      </div>
    );
  }

  if (data.kind === "http_event_reply") {
    const statusCode = numberOr(data.statusCode, 200);
    const commonStatus = COMMON_STATUS_CODES.some((item) => item.value === statusCode);
    const hasBody = statusCode !== 204;
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-xs leading-5 text-emerald-50">
          该节点必须位于 HTTP 入口工作流末尾；30 秒内到达时，请求方会同步收到此回执。
        </div>
        <Section title="返回状态" description="优先选择常用状态；只有特殊集成才需要自定义状态码。">
          <Field label="处理结果">
            <select
              className={inputClass}
              onChange={(event) => {
                if (event.target.value === "custom") {
                  onChange({ statusCode: 299 });
                  return;
                }
                const nextStatus = Number(event.target.value);
                onChange(nextStatus === 204 ? { statusCode: nextStatus, bodyTemplate: "" } : { statusCode: nextStatus });
              }}
              value={commonStatus ? statusCode : "custom"}
            >
              {COMMON_STATUS_CODES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              <option value="custom">自定义状态码…</option>
            </select>
          </Field>
          {!commonStatus ? (
            <Field label="自定义 HTTP 状态码" hint="200–599">
              <input className={inputClass} max={599} min={200} onChange={(event) => onChange({ statusCode: Number(event.target.value) })} type="number" value={statusCode} />
            </Field>
          ) : null}
        </Section>
        {hasBody ? (
          <Section title="返回内容" description="正文可以插入全局变量；选择 JSON 时，渲染结果必须是合法 JSON。">
            <Field label="正文格式">
              <select className={inputClass} onChange={(event) => onChange({ responseBodyType: event.target.value as WorkflowNodeData["responseBodyType"] })} value={data.responseBodyType ?? "json"}>
                <option value="json">JSON</option>
                <option value="text">纯文本</option>
              </select>
            </Field>
            <Field label="回执正文" hint="可从全局变量中心插入">
              <WorkflowVariableField className="min-h-28 resize-y font-mono leading-6" contract={contract} edges={edges} fieldName="bodyTemplate" multiline node={node} nodes={nodes} onChange={(bodyTemplate) => onChange({ bodyTemplate })} placeholder={data.responseBodyType === "text" ? "例如：请求已收到" : "例如：{\"ok\": true}"} value={String(data.bodyTemplate ?? "")} />
            </Field>
          </Section>
        ) : (
          <p className="rounded-lg border border-white/10 bg-white/[0.025] px-3 py-2 text-xs leading-5 text-slate-400">204 表示成功但不返回正文。</p>
        )}
      </div>
    );
  }

  return null;
}
