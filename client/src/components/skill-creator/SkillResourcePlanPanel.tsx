import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  CircleAlert,
  FileCode2,
  FileText,
  LayoutTemplate,
  LoaderCircle,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";

import {
  answerSkillCreatorResourcePlan,
  confirmSkillCreatorResourcePlan,
  generateSkillCreatorResourcePlan,
  patchSkillCreatorResourcePlan,
  type SkillCreatorSession,
  type SkillCreatorStatus,
  type SkillResourcePlanItem,
} from "../../utils/skillCreatorApi";


const KIND_LABELS = {
  script: "脚本",
  reference: "参考资料",
  asset: "输出模板",
} as const;

const ACTION_LABELS = {
  keep: "保留",
  create: "新增",
  update: "更新",
  delete: "删除",
} as const;

const COST_LABELS = {
  low: "低",
  medium: "中",
  high: "高",
} as const;

const KIND_ICONS = {
  script: FileCode2,
  reference: FileText,
  asset: LayoutTemplate,
} as const;

interface Props {
  session: SkillCreatorSession;
  status: SkillCreatorStatus;
  onSession: (session: SkillCreatorSession) => void | Promise<void>;
}

function errorMessage(value: unknown, fallback: string) {
  return value instanceof Error && value.message ? value.message : fallback;
}

export default function SkillResourcePlanPanel({ session, status, onSession }: Props) {
  const plan = session.resource_plan ?? null;
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [skillName, setSkillName] = useState("");
  const [skillDescription, setSkillDescription] = useState("");
  const [resources, setResources] = useState<SkillResourcePlanItem[]>([]);

  useEffect(() => {
    setSkillName(plan?.skill_name ?? "");
    setSkillDescription(plan?.skill_description ?? "");
    setResources(plan?.resources ?? []);
    setAnswers(plan?.clarification_answers ?? {});
  }, [plan?.digest]);

  const planDirty = useMemo(() => {
    if (!plan) return false;
    return skillName !== plan.skill_name
      || skillDescription !== plan.skill_description
      || JSON.stringify(resources) !== JSON.stringify(plan.resources);
  }, [plan, resources, skillDescription, skillName]);
  const resourcePathById = useMemo(
    () => new Map(resources.map((item) => [item.resource_id, item.path])),
    [resources],
  );

  async function run(label: string, operation: () => Promise<SkillCreatorSession>, success: string) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const updated = await operation();
      await onSession(updated);
      setNotice(success);
      return updated;
    } catch (caught) {
      setError(errorMessage(caught, "资源计划操作失败。"));
      return null;
    } finally {
      setBusy("");
    }
  }

  async function generate() {
    await run(
      "generate",
      () => generateSkillCreatorResourcePlan(session),
      plan ? "资源计划已按最新定义重新生成。" : "资源计划已生成，请先检查再确认。",
    );
  }

  async function saveAnswersAndRegenerate() {
    if (!plan) return;
    const missing = plan.clarifications.find((item) => !answers[item.question_id]?.trim());
    if (missing) {
      setError(`请先回答：${missing.question}`);
      return;
    }
    setBusy("answers");
    setError("");
    setNotice("");
    try {
      const answered = await answerSkillCreatorResourcePlan(session, answers);
      const updated = await generateSkillCreatorResourcePlan(answered);
      await onSession(updated);
      setNotice("回答已保存，资源计划已重新生成。请检查新的范围与资源。 ");
    } catch (caught) {
      setError(errorMessage(caught, "澄清回答保存失败。"));
    } finally {
      setBusy("");
    }
  }

  async function savePlan() {
    if (!plan) return;
    await run(
      "save",
      () => patchSkillCreatorResourcePlan(session, {
        skill_name: skillName.trim(),
        skill_description: skillDescription.trim(),
        resources,
      }),
      "资源计划修改已保存为新的不可变版本。",
    );
  }

  async function confirmPlan() {
    if (planDirty) {
      setError("请先保存当前修改，再确认资源计划。");
      return;
    }
    await run(
      "confirm",
      () => confirmSkillCreatorResourcePlan(session),
      "资源计划已冻结。后续生成只能使用这些已确认路径和来源。",
    );
  }

  function updateResource(index: number, changes: Partial<SkillResourcePlanItem>) {
    setResources((current) => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, ...changes } : item
    )));
  }

  function changeResourceKind(index: number, kind: SkillResourcePlanItem["kind"]) {
    const roots = { script: "scripts", reference: "references", asset: "assets" } as const;
    const current = resources[index];
    if (!current) return;
    const basename = current.path.split("/").at(-1)?.replace(/\.(?:py|js|md|txt)$/i, "") || `resource-${index + 1}`;
    const extension = kind === "script" ? ".py" : ".md";
    updateResource(index, { kind, path: `${roots[kind]}/${basename}${extension}` });
  }

  function addResource() {
    if (!plan || resources.length >= 20) return;
    const ordinal = resources.length + 1;
    const firstStep = plan.workflow_steps[0]?.step_id;
    setResources((current) => [
      ...current,
      {
        resource_id: `draft-resource-${ordinal}`,
        spec_digest: "",
        kind: "reference",
        action: "create",
        generation_cost: "medium",
        path: `references/resource-${ordinal}.md`,
        purpose: "保存主流程不应重复展开、但执行时需要按需读取的规则。",
        source_ids: ["intent"],
        used_by_steps: firstStep ? [firstStep] : [],
        depends_on: [],
        acceptance_checks: ["资源为 UTF-8 文本，并完整支持计划中绑定的执行步骤。"],
      },
    ]);
  }

  function moveResource(index: number, offset: number) {
    setResources((current) => {
      const nextIndex = index + offset;
      if (nextIndex < 0 || nextIndex >= current.length) return current;
      const next = [...current];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
  }

  function removeResource(index: number) {
    setResources((current) => {
      const target = current[index];
      if (!target) return current;
      if (target.action === "create") {
        return current.filter((_, itemIndex) => itemIndex !== index);
      }
      return current.map((item, itemIndex) => (
        itemIndex === index ? { ...item, action: "delete" } : item
      ));
    });
  }

  const plannerUnavailable = !status.resource_planner_available;

  return (
    <section className="mt-5 rounded-lg border border-brand-300/20 bg-brand-300/[0.05] p-4 sm:p-5" aria-labelledby="creator-resource-plan-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-brand-100">
            <Sparkles aria-hidden="true" size={17} />
            <h3 className="text-base font-semibold" id="creator-resource-plan-heading">资源规划</h3>
          </div>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            先决定哪些步骤需要脚本、参考资料或输出模板。规划阶段不会写草稿，也不会为了显得完整而强行增加文件。
          </p>
        </div>
        {plan ? (
          <span className="rounded-full border border-white/10 bg-ink-950/55 px-3 py-1 text-xs text-slate-300">
            plan r{plan.revision} · {plan.digest.slice(0, 10)}
          </span>
        ) : null}
      </div>

      {error ? <p className="mt-4 rounded-md border border-red-400/25 bg-red-400/10 px-3 py-2 text-sm text-red-100" role="alert">{error}</p> : null}
      {notice ? <p className="mt-4 rounded-md border border-emerald-400/20 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100" role="status">{notice}</p> : null}

      {!plan ? (
        <div className="mt-5 rounded-md border border-white/10 bg-ink-950/45 p-4">
          <p className="text-sm text-slate-300">素材确认后，让固定 Creator 助手只生成一份可编辑计划，不生成任何文件。</p>
          <button
            className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={!session.evidence_confirmed || plannerUnavailable || Boolean(busy)}
            onClick={() => void generate()}
            type="button"
          >
            {busy === "generate" ? <LoaderCircle className="animate-spin" aria-hidden="true" size={16} /> : <Sparkles aria-hidden="true" size={16} />}
            {busy === "generate" ? "正在分析需求…" : "生成资源计划"}
          </button>
          {plannerUnavailable ? <p className="mt-3 text-xs text-amber-200">模型网关未配置，暂时不能生成资源计划。</p> : null}
        </div>
      ) : null}

      {plan?.stale ? (
        <div className="mt-5 rounded-md border border-amber-300/25 bg-amber-300/10 p-4">
          <p className="flex items-center gap-2 text-sm font-semibold text-amber-100"><CircleAlert aria-hidden="true" size={16} />用途或草稿已变化，当前计划已过期。</p>
          <button className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-full border border-amber-200/30 px-4 py-2 text-sm font-semibold text-amber-100 disabled:opacity-40" disabled={plannerUnavailable || Boolean(busy)} onClick={() => void generate()} type="button"><RefreshCw aria-hidden="true" size={15} />按最新内容重新规划</button>
        </div>
      ) : null}

      {plan && !plan.stale && plan.state === "needs_input" ? (
        <div className="mt-5 space-y-4">
          <div className="rounded-md border border-amber-300/20 bg-amber-300/[0.08] p-4">
            <p className="font-semibold text-amber-100">需要先补充 {plan.clarifications.length} 项关键信息</p>
            <p className="mt-1 text-xs leading-5 text-amber-100/75">缺少依据时不会生成虚构的规则文件。回答只作为本次 Creator 会话的可信素材。</p>
          </div>
          {plan.clarifications.map((item) => (
            <label className="block rounded-md border border-white/10 bg-ink-950/45 p-4" key={item.question_id}>
              <span className="text-sm font-semibold text-white">{item.question}</span>
              <span className="mt-1 block text-xs leading-5 text-slate-400">原因：{item.reason}</span>
              <textarea
                className="mt-3 min-h-28 w-full resize-y rounded-md border border-white/10 bg-ink-950/80 px-3 py-2 text-sm leading-6 text-white focus:border-brand-300/50 focus:outline-none"
                maxLength={32_000}
                onChange={(event) => setAnswers((current) => ({ ...current, [item.question_id]: event.target.value }))}
                value={answers[item.question_id] ?? ""}
              />
            </label>
          ))}
          <div className="flex justify-end">
            <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void saveAnswersAndRegenerate()} type="button">{busy === "answers" ? <LoaderCircle className="animate-spin" aria-hidden="true" size={16} /> : <RefreshCw aria-hidden="true" size={16} />}{busy === "answers" ? "正在重新规划…" : "保存回答并重新规划"}</button>
          </div>
        </div>
      ) : null}

      {plan && !plan.stale && plan.state === "needs_regeneration" ? (
        <div className="mt-5 rounded-md border border-white/10 bg-ink-950/45 p-4">
          <p className="text-sm text-slate-300">澄清回答已保存，需要据此生成新的资源计划。</p>
          <button className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={plannerUnavailable || Boolean(busy)} onClick={() => void generate()} type="button"><RefreshCw aria-hidden="true" size={15} />重新规划</button>
        </div>
      ) : null}

      {plan && !plan.stale && (plan.state === "ready" || plan.state === "confirmed") ? (
        <div className="mt-5 space-y-5">
          <div className="grid gap-4 lg:grid-cols-2">
            <label className="block">
              <span className="text-xs font-semibold text-slate-300">Skill ID</span>
              <input className="mt-2 min-h-11 w-full rounded-md border border-white/10 bg-ink-950/70 px-3 text-sm text-white disabled:opacity-70" disabled={plan.state === "confirmed"} onChange={(event) => setSkillName(event.target.value)} value={skillName} />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-300">能力、触发场景与边界</span>
              <textarea className="mt-2 min-h-24 w-full rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 text-sm leading-6 text-white disabled:opacity-70" disabled={plan.state === "confirmed"} maxLength={1024} onChange={(event) => setSkillDescription(event.target.value)} value={skillDescription} />
            </label>
          </div>

          <div>
            <h4 className="text-sm font-semibold text-white">工作流</h4>
            <ol className="mt-3 space-y-2">
              {plan.workflow_steps.map((step, index) => <li className="flex gap-3 rounded-md border border-white/8 bg-ink-950/40 px-3 py-2 text-sm text-slate-300" key={step.step_id}><span className="font-semibold text-brand-100">{index + 1}</span><span>{step.instruction}</span></li>)}
            </ol>
          </div>

          <div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-sm font-semibold text-white">计划资源（{resources.length}）</h4>
              <div className="flex flex-wrap items-center gap-2">
                {resources.length === 0 ? <span className="text-xs text-emerald-200">已判断无需附加资源</span> : null}
                {plan.state !== "confirmed" ? <button className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40" disabled={resources.length >= 20} onClick={addResource} type="button"><Plus aria-hidden="true" size={15} />添加必要资源</button> : null}
              </div>
            </div>
            <div className="mt-3 space-y-3">
              {resources.map((item, index) => {
                const Icon = KIND_ICONS[item.kind];
                return (
                  <article className="rounded-md border border-white/10 bg-ink-950/45 p-4" key={item.resource_id}>
                    <div className="flex flex-wrap items-start gap-3">
                      <span className="rounded-md bg-white/[0.06] p-2 text-brand-100"><Icon aria-hidden="true" size={17} /></span>
                      <div className="min-w-0 flex-1 space-y-3">
                        <div className="grid gap-3 sm:grid-cols-[130px_120px_minmax(0,1fr)]">
                          <select aria-label={`资源 ${index + 1} 类型`} className="min-h-11 rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" disabled={plan.state === "confirmed"} onChange={(event) => changeResourceKind(index, event.target.value as SkillResourcePlanItem["kind"])} value={item.kind}>{Object.entries(KIND_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
                          <select aria-label={`资源 ${index + 1} 操作`} className="min-h-11 rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" disabled={plan.state === "confirmed"} onChange={(event) => updateResource(index, { action: event.target.value as SkillResourcePlanItem["action"] })} value={item.action}>{Object.entries(ACTION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
                          <input aria-label={`资源 ${index + 1} 路径`} className="min-h-11 min-w-0 rounded-md border border-white/10 bg-ink-950 px-3 font-mono text-xs text-white" disabled={plan.state === "confirmed"} onChange={(event) => updateResource(index, { path: event.target.value })} value={item.path} />
                        </div>
                        <textarea aria-label={`资源 ${index + 1} 用途`} className="min-h-20 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm leading-6 text-white" disabled={plan.state === "confirmed"} onChange={(event) => updateResource(index, { purpose: event.target.value })} value={item.purpose} />
                        <div className="flex flex-wrap gap-2 text-[11px] text-slate-400">
                          <span>生成成本：{COST_LABELS[item.generation_cost]}</span>
                          <span>步骤：{item.used_by_steps.join("、") || "未绑定"}</span>
                          <span>来源：{item.source_ids.join("、") || "未绑定"}</span>
                          {item.depends_on.length ? <span>依赖：{item.depends_on.map((value) => resourcePathById.get(value) ?? value).join("、")}</span> : null}
                        </div>
                        <p className="text-xs leading-5 text-slate-400">验收：{item.acceptance_checks.join("；")}</p>
                      </div>
                      {plan.state !== "confirmed" ? (
                        <div className="flex gap-1">
                          <button aria-label="上移资源" className="rounded-md p-2 text-slate-300 hover:bg-white/10 disabled:opacity-30" disabled={index === 0} onClick={() => moveResource(index, -1)} type="button"><ArrowUp aria-hidden="true" size={16} /></button>
                          <button aria-label="下移资源" className="rounded-md p-2 text-slate-300 hover:bg-white/10 disabled:opacity-30" disabled={index === resources.length - 1} onClick={() => moveResource(index, 1)} type="button"><ArrowDown aria-hidden="true" size={16} /></button>
                          <button aria-label="移除资源" className="rounded-md p-2 text-red-200 hover:bg-red-400/10" onClick={() => removeResource(index)} type="button"><Trash2 aria-hidden="true" size={16} /></button>
                        </div>
                      ) : null}
                    </div>
                  </article>
                );
              })}
            </div>
          </div>

          {plan.state === "confirmed" ? (
            <div className="rounded-md border border-emerald-300/20 bg-emerald-300/[0.08] p-4">
              <p className="flex items-center gap-2 text-sm font-semibold text-emerald-100"><CheckCircle2 aria-hidden="true" size={17} />资源计划已确认</p>
              <p className="mt-1 text-xs leading-5 text-emerald-100/75">路径、来源和生成顺序已经冻结。当前阶段不会提前写入草稿或安装目录。</p>
            </div>
          ) : (
            <div className="flex flex-wrap justify-end gap-3 border-t border-white/10 pt-4">
              <button className="min-h-11 rounded-full border border-white/15 px-5 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={!planDirty || Boolean(busy)} onClick={() => void savePlan()} type="button">{busy === "save" ? "正在保存…" : "保存计划修改"}</button>
              <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={planDirty || Boolean(busy)} onClick={() => void confirmPlan()} type="button"><CheckCircle2 aria-hidden="true" size={16} />{busy === "confirm" ? "正在确认…" : "确认并冻结资源计划"}</button>
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
