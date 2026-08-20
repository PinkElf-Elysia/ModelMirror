import { useEffect, useMemo, useState } from "react";
import type {
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeData,
} from "../../types/workflow";
import {
  fetchWorkflowProjects,
  type WorkflowProjectSummary,
} from "../../utils/workflowDeployments";
import WorkflowVariableField from "./WorkflowVariableField";
import type { WorkflowNodeContractProjection } from "./workflowNodeRegistry";

type DurationUnit = "seconds" | "minutes" | "hours" | "days";
type CronPattern = "minutes" | "hourly" | "daily" | "weekly" | "monthly" | "custom";

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
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label} hint="自动纳入变量中心">
      <WorkflowVariableField
        ariaLabel={label}
        contract={contract}
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
  data,
  onChange,
}: {
  currentProjectId?: string;
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  contract: WorkflowNodeContractProjection | null;
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

export default function WorkflowDeploymentNodeConfig({
  currentProjectId,
  node,
  nodes,
  edges,
  contract,
  data,
  onChange,
}: {
  currentProjectId?: string;
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  contract: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  if (data.kind === "failure_event_entry") {
    return (
      <FailureEntryConfig
        contract={contract}
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
