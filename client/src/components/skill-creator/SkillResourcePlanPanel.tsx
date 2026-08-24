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
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";

import {
  answerSkillCreatorResourcePlan,
  confirmSkillCreatorResourcePlan,
  generateSkillCreatorResourcePlan,
  patchSkillCreatorResourcePlan,
  SkillCreatorApiError,
  type SkillCreatorSession,
  type SkillCreatorStatus,
  type SkillResourcePlanItem,
  type SkillResourceHookPlanItem,
} from "../../utils/skillCreatorApi";
import SkillTriggerOptimizationPanel, { skillTriggerGateReady } from "./SkillTriggerOptimizationPanel";


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
  onPlanConfirmed?: (session: SkillCreatorSession) => void;
}

function errorMessage(value: unknown, fallback: string) {
  if (value instanceof SkillCreatorApiError && value.status === 409) {
    return "会话或方案已更新，请重新加载页面后再继续。";
  }
  return fallback;
}

function summaryItem(value: string) {
  return value.replace(/^\s*[-•]\s*/, "").trim();
}

export default function SkillResourcePlanPanel({ session, status, onSession, onPlanConfirmed }: Props) {
  const plan = session.resource_plan ?? null;
  const legacyFlow = session.authoring_flow !== "resource";
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [skillName, setSkillName] = useState("");
  const [skillDescription, setSkillDescription] = useState("");
  const [resources, setResources] = useState<SkillResourcePlanItem[]>([]);
  const [hooks, setHooks] = useState<SkillResourceHookPlanItem[]>([]);

  useEffect(() => {
    setSkillName(plan?.skill_name ?? "");
    setSkillDescription(plan?.skill_description ?? "");
    setResources(plan?.resources ?? []);
    setHooks(plan?.hooks ?? []);
    setAnswers(plan?.clarification_answers ?? {});
  }, [plan?.digest]);

  const planDirty = useMemo(() => {
    if (!plan) return false;
    return skillName !== plan.skill_name
      || skillDescription !== plan.skill_description
      || JSON.stringify(resources) !== JSON.stringify(plan.resources)
      || JSON.stringify(hooks) !== JSON.stringify(plan.hooks ?? []);
  }, [hooks, plan, resources, skillDescription, skillName]);
  const resourcePathById = useMemo(
    () => new Map(resources.map((item) => [item.resource_id, item.path])),
    [resources],
  );
  const hookAuthoringEnabled = status.hook_authoring_enabled !== false;
  const hookEditingLocked = plan?.state === "confirmed" || !hookAuthoringEnabled;
  const planEditingLocked = plan?.state === "confirmed"
    || (!hookAuthoringEnabled && hooks.length > 0);
  const triggerGateReady = skillTriggerGateReady(session, status);

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
        hooks,
      }),
      "资源计划修改已保存为新的不可变版本。",
    );
  }

  async function confirmPlan() {
    if (planDirty) {
      setError("请先保存当前修改，再确认资源计划。");
      return;
    }
    if (!triggerGateReady) {
      setError("请先完成下方的触发检查，再确认资源计划。");
      return;
    }
    const updated = await run(
      "confirm",
      () => confirmSkillCreatorResourcePlan(session),
      "资源计划已冻结。后续生成只能使用这些已确认路径和来源。",
    );
    if (updated) onPlanConfirmed?.(updated);
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
    const targetResource = resources[index];
    if (targetResource) {
      setHooks((current) => current.flatMap((hook) => {
        if (hook.script_resource_id !== targetResource.resource_id) return [hook];
        return hook.action === "create" ? [] : [{ ...hook, action: "delete" as const }];
      }));
    }
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

  function updateHook(index: number, changes: Partial<SkillResourceHookPlanItem>) {
    const currentHook = hooks[index];
    const nextScriptId = changes.script_resource_id ?? currentHook?.script_resource_id;
    if (changes.script_resource_id && nextScriptId) {
      setResources((items) => items.map((resource) => (
        resource.resource_id === nextScriptId && resource.action === "keep"
          ? { ...resource, action: "update" }
          : resource
      )));
    }
    setHooks((current) => current.map((item, itemIndex) => (
      itemIndex === index
        ? { ...item, ...changes, action: item.action === "keep" ? "update" : item.action }
        : item
    )));
  }

  function addHook() {
    if (!plan || hooks.length >= 12) return;
    const script = resources.find((item) => item.kind === "script" && item.action !== "delete");
    if (!script) {
      setError("先添加一个 Python 或 JavaScript 脚本，再为它配置 Hook。");
      return;
    }
    if (script.action === "keep") {
      setResources((current) => current.map((item) => (
        item.resource_id === script.resource_id ? { ...item, action: "update" } : item
      )));
    }
    const ordinal = hooks.length + 1;
    setHooks((current) => [
      ...current,
      {
        hook_id: `check_event_${ordinal}`,
        spec_digest: "",
        event: "pre_tool_use",
        mode: "validation",
        tool_names: ["sandbox_write_file"],
        purpose: "在工具执行前验证一项明确、可测试的条件。",
        script_resource_id: script.resource_id,
        source_ids: ["intent"],
        used_by_steps: plan.workflow_steps[0]?.step_id ? [plan.workflow_steps[0].step_id] : [],
        acceptance_checks: ["合法输入通过，违反条件时返回类型化失败结果。"],
        action: "create",
      },
    ]);
  }

  function removeHook(index: number) {
    setHooks((current) => {
      const target = current[index];
      if (!target) return current;
      if (target.action === "create") return current.filter((_, itemIndex) => itemIndex !== index);
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
            <h3 className="text-base font-semibold" id="creator-resource-plan-heading">AI 给出的制作方案</h3>
          </div>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            AI 会先决定是否真的需要脚本、参考资料或输出模板。简单任务可以不增加任何文件，复杂任务才会拆分。
          </p>
        </div>
        {plan ? <span className="rounded-full border border-white/10 bg-ink-950/55 px-3 py-1 text-xs text-slate-300">方案 {plan.revision}</span> : null}
      </div>

      {error ? <p className="mt-4 rounded-md border border-red-400/25 bg-red-400/10 px-3 py-2 text-sm text-red-100" role="alert">{error}</p> : null}
      {notice ? <p className="mt-4 rounded-md border border-emerald-400/20 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100" role="status">{notice}</p> : null}

      {!plan ? (
        <div className="mt-5 rounded-md border border-white/10 bg-ink-950/45 p-4">
          <p className="text-sm text-slate-300">
            {legacyFlow
              ? "这是旧版 Creator 会话。现有提案和草稿保持可读；只有你主动进入资源规划后，才切换到逐资源生成流程。"
              : "先让 AI 给出一份可编辑方案。此时不会生成文件，也不会安装任何内容。"}
          </p>
          <button
            className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={!session.evidence_confirmed || plannerUnavailable || Boolean(busy)}
            onClick={() => void generate()}
            type="button"
          >
            {busy === "generate" ? <LoaderCircle className="animate-spin" aria-hidden="true" size={16} /> : <Sparkles aria-hidden="true" size={16} />}
            {busy === "generate" ? "正在理解需求…" : legacyFlow ? "切换到新流程并生成方案" : "让 AI 生成方案"}
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
          <div className="rounded-lg border border-white/10 bg-ink-950/45 p-4 sm:p-5">
            <p className="text-xs font-semibold text-brand-100">AI 的理解</p>
            <h4 className="mt-2 text-base font-semibold text-white">{skillName || "待命名 Skill"}</h4>
            <p className="mt-2 text-sm leading-6 text-slate-300">{skillDescription}</p>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {resources.length ? resources.map((item) => (
                <div className="rounded-md bg-white/[0.035] p-3" key={item.resource_id}>
                  <p className="text-xs font-semibold text-white">{KIND_LABELS[item.kind]} · {item.action === "create" ? "新增" : ACTION_LABELS[item.action]}</p>
                  <p className="mt-1 text-xs leading-5 text-slate-400">{item.purpose}</p>
                </div>
              )) : <p className="text-sm text-emerald-100">这个任务不需要额外资源，保持简单即可。</p>}
            </div>
            {hooks.length ? (
              <div className="mt-4 border-l-2 border-amber-300/40 pl-3">
                <p className="flex items-center gap-2 text-xs font-semibold text-amber-100">
                  <ShieldCheck aria-hidden="true" size={15} />
                  计划包含 {hooks.filter((item) => item.action !== "delete").length} 个运行 Hook，确认后会执行离线脚本测试
                </p>
                <p className="mt-1 text-xs leading-5 text-slate-400">
                  Hook 只在已确认的事件边界运行，不会改写参数、安装 Skill 或获得网络权限。
                </p>
              </div>
            ) : null}
            <p className="mt-4 text-xs font-semibold text-slate-300">共 {plan.workflow_steps.length} 步执行流程</p>
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <section className="rounded-md border border-white/10 bg-white/[0.025] p-3" aria-labelledby="creator-plan-output-heading">
                <h5 className="text-sm font-semibold text-white" id="creator-plan-output-heading">交付结果</h5>
                <ul className="mt-2 space-y-1.5 text-xs leading-5 text-slate-300">
                  {plan.output_contract.map((item) => <li key={item}>• {summaryItem(item)}</li>)}
                </ul>
              </section>
              <section className="rounded-md border border-white/10 bg-white/[0.025] p-3" aria-labelledby="creator-plan-failure-heading">
                <h5 className="text-sm font-semibold text-white" id="creator-plan-failure-heading">遇到信息不足时</h5>
                <ul className="mt-2 space-y-1.5 text-xs leading-5 text-slate-300">
                  {plan.failure_modes.map((item) => <li key={item}>• {summaryItem(item)}</li>)}
                </ul>
              </section>
            </div>
          </div>

          <details className="rounded-lg border border-white/10 bg-white/[0.02] p-4">
            <summary className="cursor-pointer text-sm font-semibold text-slate-300">查看并调整完整方案（可选）</summary>
            <div className="mt-5 space-y-5">
          <div className="grid gap-4 lg:grid-cols-2">
            <label className="block">
              <span className="text-xs font-semibold text-slate-300">Skill ID</span>
              <input className="mt-2 min-h-11 w-full rounded-md border border-white/10 bg-ink-950/70 px-3 text-sm text-white disabled:opacity-70" disabled={planEditingLocked} onChange={(event) => setSkillName(event.target.value)} value={skillName} />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-300">能力、触发场景与边界</span>
              <textarea className="mt-2 min-h-24 w-full rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 text-sm leading-6 text-white disabled:opacity-70" disabled={planEditingLocked} maxLength={1024} onChange={(event) => setSkillDescription(event.target.value)} value={skillDescription} />
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
                {!planEditingLocked ? <button className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40" disabled={resources.length >= 20} onClick={addResource} type="button"><Plus aria-hidden="true" size={15} />添加必要资源</button> : null}
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
                          <select aria-label={`资源 ${index + 1} 类型`} className="min-h-11 rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" disabled={planEditingLocked} onChange={(event) => changeResourceKind(index, event.target.value as SkillResourcePlanItem["kind"])} value={item.kind}>{Object.entries(KIND_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
                          <select aria-label={`资源 ${index + 1} 操作`} className="min-h-11 rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" disabled={planEditingLocked} onChange={(event) => updateResource(index, { action: event.target.value as SkillResourcePlanItem["action"] })} value={item.action}>{Object.entries(ACTION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
                          <input aria-label={`资源 ${index + 1} 路径`} className="min-h-11 min-w-0 rounded-md border border-white/10 bg-ink-950 px-3 font-mono text-xs text-white" disabled={planEditingLocked} onChange={(event) => updateResource(index, { path: event.target.value })} value={item.path} />
                        </div>
                        <textarea aria-label={`资源 ${index + 1} 用途`} className="min-h-20 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm leading-6 text-white" disabled={planEditingLocked} onChange={(event) => updateResource(index, { purpose: event.target.value })} value={item.purpose} />
                        <div className="flex flex-wrap gap-2 text-[11px] text-slate-400">
                          <span>生成成本：{COST_LABELS[item.generation_cost]}</span>
                          <span>步骤：{item.used_by_steps.join("、") || "未绑定"}</span>
                          <span>来源：{item.source_ids.join("、") || "未绑定"}</span>
                          {item.depends_on.length ? <span>依赖：{item.depends_on.map((value) => resourcePathById.get(value) ?? value).join("、")}</span> : null}
                        </div>
                        <p className="text-xs leading-5 text-slate-400">验收：{item.acceptance_checks.join("；")}</p>
                      </div>
                      {!planEditingLocked ? (
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

          {hookAuthoringEnabled || hooks.length ? <div className="border-t border-white/10 pt-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h4 className="flex items-center gap-2 text-sm font-semibold text-white">
                  <ShieldCheck aria-hidden="true" size={16} />
                  运行 Hook（{hooks.filter((item) => item.action !== "delete").length}）
                </h4>
                <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-400">
                  只有明确需要“运行前检查、运行后验证、阻止危险操作或会话提示”时才添加。普通 Skill 保持为空。
                </p>
              </div>
              {plan.state !== "confirmed" && hookAuthoringEnabled ? (
                <button
                  className="inline-flex min-h-11 items-center gap-2 rounded-full border border-amber-200/25 px-4 py-2 text-xs font-semibold text-amber-100 disabled:opacity-40"
                  disabled={hooks.length >= 12}
                  onClick={addHook}
                  type="button"
                >
                  <Plus aria-hidden="true" size={15} />添加 Hook
                </button>
              ) : null}
            </div>
            {!hookAuthoringEnabled ? (
              <p className="mt-3 rounded-md border border-amber-300/20 bg-amber-300/[0.07] px-3 py-2 text-xs leading-5 text-amber-100">
                Hook V2 当前已关闭。现有计划仅供查看，重新开启后才能修改、确认或构建。
              </p>
            ) : null}
            {hooks.length === 0 ? (
              <p className="mt-3 rounded-md border border-dashed border-white/10 px-3 py-3 text-xs leading-5 text-slate-400">
                当前方案不需要 Hook，不会在工作流运行时额外执行脚本。
              </p>
            ) : (
              <div className="mt-3 space-y-3">
                {hooks.map((hook, index) => {
                  const toolEvent = hook.event === "pre_tool_use" || hook.event === "post_tool_use";
                  const scripts = resources.filter((item) => item.kind === "script" && item.action !== "delete");
                  return (
                    <article className="border-l-2 border-amber-300/35 bg-ink-950/35 py-3 pl-4 pr-3" key={hook.hook_id}>
                      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        <label className="block">
                          <span className="text-[11px] font-semibold text-slate-400">Hook ID</span>
                          <input className="mt-1 min-h-11 w-full rounded-md border border-white/10 bg-ink-950 px-3 font-mono text-xs text-white" disabled={hookEditingLocked} onChange={(event) => updateHook(index, { hook_id: event.target.value })} value={hook.hook_id} />
                        </label>
                        <label className="block">
                          <span className="text-[11px] font-semibold text-slate-400">何时运行</span>
                          <select className="mt-1 min-h-11 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" disabled={hookEditingLocked} onChange={(event) => {
                            const nextEvent = event.target.value as SkillResourceHookPlanItem["event"];
                            updateHook(index, {
                              event: nextEvent,
                              mode: hook.mode === "guard" && nextEvent !== "pre_tool_use" ? "validation" : hook.mode,
                              tool_names: ["pre_tool_use", "post_tool_use"].includes(nextEvent) ? (hook.tool_names.length ? hook.tool_names : ["sandbox_write_file"]) : [],
                            });
                          }} value={hook.event}>
                            <option value="session_start">会话开始</option>
                            <option value="pre_tool_use">工具执行前</option>
                            <option value="post_tool_use">工具执行后</option>
                            <option value="session_end">会话结束</option>
                          </select>
                        </label>
                        <label className="block">
                          <span className="text-[11px] font-semibold text-slate-400">结果策略</span>
                          <select className="mt-1 min-h-11 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" disabled={hookEditingLocked} onChange={(event) => {
                            const nextMode = event.target.value as SkillResourceHookPlanItem["mode"];
                            updateHook(index, { mode: nextMode, event: nextMode === "guard" ? "pre_tool_use" : hook.event, tool_names: nextMode === "guard" && !hook.tool_names.length ? ["sandbox_write_file"] : hook.tool_names });
                          }} value={hook.mode}>
                            <option value="annotation">只提示，不阻断</option>
                            <option value="validation">验证失败时终止节点</option>
                            <option value="guard">工具前拒绝危险调用</option>
                          </select>
                        </label>
                        <label className="block">
                          <span className="text-[11px] font-semibold text-slate-400">绑定脚本</span>
                          <select className="mt-1 min-h-11 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" disabled={hookEditingLocked} onChange={(event) => updateHook(index, { script_resource_id: event.target.value })} value={hook.script_resource_id}>
                            {scripts.map((item) => <option key={item.resource_id} value={item.resource_id}>{item.path}</option>)}
                          </select>
                        </label>
                      </div>
                      {toolEvent ? (
                        <label className="mt-3 block">
                          <span className="text-[11px] font-semibold text-slate-400">精确工具名（逗号分隔）</span>
                          <input className="mt-1 min-h-11 w-full rounded-md border border-white/10 bg-ink-950 px-3 font-mono text-xs text-white" disabled={hookEditingLocked} onChange={(event) => updateHook(index, { tool_names: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} value={hook.tool_names.join(", ")} />
                        </label>
                      ) : null}
                      <label className="mt-3 block">
                        <span className="text-[11px] font-semibold text-slate-400">为什么需要</span>
                        <textarea className="mt-1 min-h-20 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm leading-6 text-white" disabled={hookEditingLocked} onChange={(event) => updateHook(index, { purpose: event.target.value })} value={hook.purpose} />
                      </label>
                      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-400">
                        <span>验收：{hook.acceptance_checks.join("；")}</span>
                        {plan.state !== "confirmed" && hookAuthoringEnabled ? <button className="inline-flex min-h-10 items-center gap-2 rounded-full px-3 text-red-200 hover:bg-red-400/10" onClick={() => removeHook(index)} type="button"><Trash2 aria-hidden="true" size={15} />移除</button> : null}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div> : null}
            </div>
          </details>

          {planDirty ? (
            <div className="rounded-md border border-amber-300/25 bg-amber-300/[0.08] p-4" id="creator-plan-save-before-trigger" role="status">
              <p className="text-sm font-semibold text-amber-100">先保存方案调整</p>
              <p className="mt-1 text-xs leading-5 text-amber-100/75">
                当前名称、描述或资源还有未保存内容。保存后再做触发检查，避免验证旧描述或覆盖你的修改。
              </p>
            </div>
          ) : null}
          <fieldset
            aria-describedby={planDirty ? "creator-plan-save-before-trigger" : undefined}
            className={`m-0 min-w-0 border-0 p-0 ${planDirty || busy ? "opacity-60" : ""}`}
            disabled={planDirty || Boolean(busy)}
          >
            <SkillTriggerOptimizationPanel onSession={onSession} session={session} status={status} />
          </fieldset>

          {plan.state === "confirmed" ? (
            <div className="rounded-md border border-emerald-300/20 bg-emerald-300/[0.08] p-4">
              <p className="flex items-center gap-2 text-sm font-semibold text-emerald-100"><CheckCircle2 aria-hidden="true" size={17} />方案已确认</p>
              <p className="mt-1 text-xs leading-5 text-emerald-100/75">下一步会按这个方案逐项生成，仍不会自动安装。</p>
            </div>
          ) : (
            <div className="flex flex-wrap justify-end gap-3 border-t border-white/10 pt-4">
              <button className="min-h-11 rounded-full border border-white/15 px-5 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={planEditingLocked || !planDirty || Boolean(busy)} onClick={() => void savePlan()} type="button">{busy === "save" ? "正在保存…" : "保存我的调整"}</button>
              <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={planEditingLocked || planDirty || !triggerGateReady || Boolean(busy)} onClick={() => void confirmPlan()} type="button"><CheckCircle2 aria-hidden="true" size={16} />{busy === "confirm" ? "正在确认…" : triggerGateReady ? "确认方案，进入生成" : "先完成触发检查"}</button>
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
