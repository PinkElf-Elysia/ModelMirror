import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  LoaderCircle,
  Plus,
  RefreshCw,
  Sparkles,
  Target,
  Trash2,
} from "lucide-react";

import {
  confirmSkillCreatorTriggerDescription,
  confirmSkillCreatorTriggerSuite,
  evaluateSkillCreatorTriggerDescription,
  generateSkillCreatorTriggerSuite,
  optimizeSkillCreatorTriggerDescriptions,
  readSkillCreatorSession,
  saveSkillCreatorTriggerSuite,
  SkillCreatorApiError,
  type SkillCreatorSession,
  type SkillCreatorStatus,
} from "../../utils/skillCreatorApi";

interface Props {
  session: SkillCreatorSession;
  status: SkillCreatorStatus;
  onSession: (session: SkillCreatorSession) => void | Promise<void>;
}

const ERROR_COPY: Record<string, { message: string; action: string }> = {
  skill_trigger_suite_required: {
    message: "需要先确认哪些任务应该触发、哪些相似任务不该触发。",
    action: "补全并确认测试边界",
  },
  skill_trigger_suite_stale: {
    message: "需求或 Skill 名称已经变化，原测试边界不再适用。",
    action: "按当前需求重新生成",
  },
  skill_trigger_suite_invalid: {
    message: "测试边界不完整或存在重复。正例和反例都至少需要 2 条。",
    action: "检查用例后重新保存",
  },
  skill_trigger_optimizer_unconfigured: {
    message: "模型网关未配置，仍可手工填写用例和描述并在本地验证。",
    action: "改用手工验证",
  },
  skill_trigger_optimizer_invalid: {
    message: "AI 没有返回可用的触发描述，现有方案未被修改。",
    action: "重试或手工填写描述",
  },
  skill_trigger_description_invalid: {
    message: "描述需要是一行清晰文字，说明能力、使用时机和关键边界。",
    action: "修改描述后重新验证",
  },
  skill_trigger_evaluation_failed: {
    message: "当前描述还不能稳定命中目标任务并避开相似任务。",
    action: "换一个描述再验证",
  },
  skill_trigger_receipt_stale: {
    message: "目录或排序版本已变化，需要按当前索引重新验证。",
    action: "重新验证当前描述",
  },
  skill_trigger_gate_required: {
    message: "触发验证尚未完成，资源计划暂时不能确认。",
    action: "完成当前触发检查",
  },
  skill_trigger_index_unavailable: {
    message: "本地 Skill 索引暂不可用，无法给出可信的触发结论。",
    action: "恢复索引后重新加载",
  },
};

function presentError(value: unknown, fallback: string) {
  if (value instanceof SkillCreatorApiError) {
    const known = ERROR_COPY[value.code];
    if (known) return `${known.message} ${known.action}。`;
    if (value.status === 409) return "会话或方案已在其他位置更新。请先复制未保存内容，再重新加载继续。";
    return fallback;
  }
  return fallback;
}

function minimumCases(values: string[]) {
  return values.map((item) => item.trim()).filter(Boolean).length >= 2;
}

function rankLabel(rank: number | null | undefined) {
  return rank == null ? "未进入 Top 24" : `第 ${rank} 名`;
}

export default function SkillTriggerOptimizationPanel({ session, status, onSession }: Props) {
  const plan = session.resource_plan ?? null;
  const suite = session.trigger_suite ?? null;
  const attempt = session.trigger_attempt ?? null;
  const receipt = session.trigger_receipt ?? null;
  const enabled = status.trigger_optimization_enabled === true;
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reloadSuggested, setReloadSuggested] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [positiveCases, setPositiveCases] = useState<string[]>([]);
  const [negativeCases, setNegativeCases] = useState<string[]>([]);
  const [description, setDescription] = useState("");
  const [caseDirty, setCaseDirty] = useState(false);
  const positiveExamplesKey = JSON.stringify(session.positive_examples);
  const negativeExamplesKey = JSON.stringify(session.near_miss_examples);

  useEffect(() => {
    const positives = suite?.cases.filter((item) => item.kind === "should_trigger").map((item) => item.text)
      ?? session.positive_examples;
    const negatives = suite?.cases.filter((item) => item.kind === "should_not_trigger").map((item) => item.text)
      ?? session.near_miss_examples;
    setPositiveCases(positives.length ? positives : [""]);
    setNegativeCases(negatives.length ? negatives : [""]);
    setCaseDirty(false);
  }, [negativeExamplesKey, positiveExamplesKey, suite?.suite_digest]);

  useEffect(() => {
    setDescription(plan?.skill_description ?? "");
  }, [plan?.skill_description]);

  const gateReady = Boolean(
    status.trigger_store_available
      && session.trigger_required
      && receipt?.passed
      && attempt?.state === "confirmed"
      && !session.trigger_stale_reason
  );
  const counts = useMemo(() => {
    const results = receipt?.case_results ?? [];
    const positives = results.filter((item) => item.kind === "should_trigger");
    const negatives = results.filter((item) => item.kind === "should_not_trigger");
    return {
      positivePassed: positives.filter((item) => item.passed).length,
      positiveTotal: positives.length,
      negativePassed: negatives.filter((item) => item.passed).length,
      negativeTotal: negatives.length,
    };
  }, [receipt]);

  if (!enabled || !plan || plan.stale || !["ready", "confirmed"].includes(plan.state)) return null;

  async function run(
    label: string,
    operation: () => Promise<SkillCreatorSession>,
    success: string,
  ) {
    setBusy(label);
    setError("");
    setNotice("");
    setReloadSuggested(false);
    try {
      const updated = await operation();
      await onSession(updated);
      setNotice(success);
      return updated;
    } catch (caught) {
      setError(presentError(caught, "触发检查失败，请重试。"));
      setReloadSuggested(
        caught instanceof SkillCreatorApiError
          && (
            caught.status === 409
            || caught.code === "skill_trigger_receipt_stale"
            || caught.code === "skill_trigger_index_unavailable"
          ),
      );
      return null;
    } finally {
      setBusy("");
    }
  }

  async function reloadSession() {
    setBusy("reload");
    setError("");
    try {
      await onSession(await readSkillCreatorSession(session.session_id));
      setReloadSuggested(false);
      setNotice("已加载服务端最新状态。");
    } catch (caught) {
      setError(presentError(caught, "重新加载失败，请稍后重试。"));
    } finally {
      setBusy("");
    }
  }

  async function generateSuite() {
    await run(
      "suite-generate",
      () => generateSkillCreatorTriggerSuite(session),
      "AI 已提出测试边界，请确认它们是否符合你的真实使用场景。",
    );
  }

  async function saveSuite() {
    if (!minimumCases(positiveCases) || !minimumCases(negativeCases)) {
      setNotice("");
      setReloadSuggested(false);
      setError("“应该触发”和“不该触发”都至少需要 2 条非空用例。");
      return;
    }
    const existingSmoke = suite?.cases.filter((item) => item.kind === "exact_name_smoke") ?? [];
    const cases = [
      ...positiveCases.filter((item) => item.trim()).map((text) => ({ kind: "should_trigger" as const, text })),
      ...negativeCases.filter((item) => item.trim()).map((text) => ({ kind: "should_not_trigger" as const, text })),
      ...existingSmoke.map((item) => ({ kind: item.kind, text: item.text })),
    ];
    const updated = await run(
      "suite-save",
      () => saveSkillCreatorTriggerSuite(
        session,
        cases,
        suite ? "用户调整触发测试边界。" : "用户手工定义触发测试边界。",
      ),
      "测试边界已保存。",
    );
    if (updated) setManualOpen(false);
  }

  async function confirmSuite() {
    if (caseDirty) {
      setNotice("");
      setReloadSuggested(false);
      setError("输入框中还有未保存的修改，请先保存测试边界。");
      return;
    }
    await run(
      "suite-confirm",
      () => confirmSkillCreatorTriggerSuite(session),
      "测试边界已冻结。下一步验证 Skill 描述。",
    );
  }

  function updateCase(kind: "positive" | "negative", index: number, value: string) {
    const setter = kind === "positive" ? setPositiveCases : setNegativeCases;
    setter((current) => current.map((item, itemIndex) => itemIndex === index ? value : item));
    setCaseDirty(true);
  }

  function removeCase(kind: "positive" | "negative", index: number) {
    const setter = kind === "positive" ? setPositiveCases : setNegativeCases;
    setter((current) => current.filter((_, itemIndex) => itemIndex !== index));
    setCaseDirty(true);
  }

  function addCase(kind: "positive" | "negative") {
    const setter = kind === "positive" ? setPositiveCases : setNegativeCases;
    setter((current) => current.length >= 6 ? current : [...current, ""]);
    setCaseDirty(true);
  }

  const suiteConfirmed = suite?.state === "confirmed";
  const canEditSuite = plan.state !== "confirmed" && !suiteConfirmed;
  const suiteStale = [
    "skill_trigger_suite_required",
    "skill_trigger_suite_stale",
    "definition_changed",
    "skill_name_changed",
  ].includes(session.trigger_stale_reason ?? "") && Boolean(suite?.state === "confirmed");
  const descriptionStale = [
    "description_unconfirmed",
    "description_failed",
    "skill_trigger_receipt_stale",
  ].includes(session.trigger_stale_reason ?? "");
  const indexUnavailable = session.trigger_stale_reason === "skill_trigger_index_unavailable";
  const primaryNext = indexUnavailable
    ? "recover-index"
    : !session.trigger_required || !suite || suiteStale
    ? "suite"
    : !suiteConfirmed
      ? "confirm-suite"
      : !attempt
        ? "description"
        : attempt.state !== "confirmed"
          ? "confirm-description"
          : descriptionStale
            ? "description"
            : "done";

  return (
    <section className="border-t border-white/10 pt-5" aria-labelledby="creator-trigger-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-white">
            <Target aria-hidden="true" size={17} />
            <h4 className="text-sm font-semibold" id="creator-trigger-heading">先确认什么时候该使用这个 Skill</h4>
          </div>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-400">
            用真实需求和相似任务检查描述，避免该出现时找不到、不该出现时频繁打扰。
          </p>
        </div>
        <span className={`rounded-full border px-3 py-1 text-xs ${gateReady ? "border-emerald-300/20 text-emerald-100" : "border-amber-300/20 text-amber-100"}`}>
          {!session.trigger_required ? "尚未启用" : gateReady ? "已通过" : "待完成"}
        </span>
      </div>

      {error ? <div className="mt-4 rounded-md border border-red-400/25 bg-red-400/10 px-3 py-2" role="alert"><p className="text-sm leading-6 text-red-100">{error}</p>{reloadSuggested ? <button className="mt-2 inline-flex min-h-11 items-center gap-2 rounded-full border border-red-200/25 px-3 text-xs font-semibold text-red-50 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void reloadSession()} type="button"><RefreshCw aria-hidden="true" size={14} />重新加载会话</button> : null}</div> : null}
      {notice ? <p className="mt-4 rounded-md border border-emerald-400/20 bg-emerald-400/10 px-3 py-2 text-sm leading-6 text-emerald-100" role="status">{notice}</p> : null}
      {!status.trigger_store_available ? (
        <p className="mt-4 rounded-md border border-red-400/25 bg-red-400/10 px-3 py-2 text-sm leading-6 text-red-100" role="alert">
          {session.trigger_required
            ? "触发验证 Store 暂不可用。为避免错误放行，当前方案不能确认；恢复后请重新加载。"
            : "触发验证 Store 暂不可用。当前旧流程仍可继续；恢复后才能主动启用触发验证。"}
        </p>
      ) : null}

      {primaryNext === "recover-index" ? (
        <div className="mt-4 rounded-md border border-amber-300/25 bg-amber-300/[0.08] p-4" role="alert">
          <p className="flex items-center gap-2 text-sm font-semibold text-amber-100">
            <CircleAlert aria-hidden="true" size={16} />
            本地 Skill 索引暂不可用
          </p>
          <p className="mt-1 text-xs leading-5 text-amber-100/75">
            当前描述不能形成可信触发凭据。恢复索引后重新加载会话，系统会按最新候选重新验证。
          </p>
          <button className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-full border border-amber-200/30 px-4 text-xs font-semibold text-amber-50 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void reloadSession()} type="button">
            <RefreshCw aria-hidden="true" size={14} />
            {busy === "reload" ? "正在重新加载…" : "重新加载会话"}
          </button>
        </div>
      ) : null}

      {primaryNext === "suite" && !(plan.state === "confirmed" && !session.trigger_required) ? (
        <div className="mt-4 rounded-md border border-brand-300/20 bg-brand-300/[0.04] p-4">
          <p className="text-sm text-slate-200">
            {suiteStale
              ? "需求或 Skill 名称已变化，需要按当前内容重新确认测试边界。"
              : session.trigger_required
              ? "先生成一组正反例，再由你确认边界。"
              : "这是旧会话，现有流程不会被追溯阻断。你可以主动启用触发验证。"}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {status.trigger_optimizer_available ? (
              <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={Boolean(busy) || !status.trigger_store_available} onClick={() => void generateSuite()} type="button">
                {busy === "suite-generate" ? <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" size={16} /> : <Sparkles aria-hidden="true" size={16} />}
                {suiteStale ? "重新生成测试边界" : session.trigger_required ? "AI 提出测试边界" : "启用并生成测试边界"}
              </button>
            ) : null}
            <button className="min-h-11 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={Boolean(busy) || !status.trigger_store_available} onClick={() => setManualOpen(true)} type="button">
              手工填写
            </button>
          </div>
          {!status.trigger_optimizer_available ? <p className="mt-2 text-xs leading-5 text-amber-100">模型网关未配置。手工填写至少 2 条正例、2 条反例后，仍可使用同一套本地排序验证。</p> : null}
        </div>
      ) : null}

      {plan.state === "confirmed" && !session.trigger_required ? (
        <p className="mt-4 rounded-md border border-white/10 bg-ink-950/25 p-4 text-xs leading-5 text-slate-400">
          这个旧会话的资源计划已经冻结，不会被追溯阻断。如需启用触发验证，请复制为新会话后再调整。
        </p>
      ) : null}

      {(manualOpen || (suite && !suiteConfirmed)) && canEditSuite ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          {([
            ["positive", "应该触发", positiveCases, "例如：根据故障日志整理可追溯的事故复盘"],
            ["negative", "不该触发", negativeCases, "例如：把一段普通文字改得更通顺"],
          ] as const).map(([kind, title, values, placeholder]) => (
            <fieldset className="min-w-0 rounded-md border border-white/10 bg-ink-950/35 p-4" key={kind}>
              <legend className="px-1 text-sm font-semibold text-white">{title}（{values.filter((item) => item.trim()).length}/2–6）</legend>
              <div className="mt-2 space-y-2">
                {values.map((value, index) => (
                  <div className="flex min-w-0 items-start gap-2" key={`${kind}-${index}`}>
                    <textarea aria-label={`${title}用例 ${index + 1}`} className="min-h-20 min-w-0 flex-1 resize-y rounded-md border border-white/10 bg-ink-950/75 px-3 py-2 text-sm leading-6 text-white focus:border-brand-300/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300/40 disabled:opacity-50" disabled={Boolean(busy)} maxLength={500} onChange={(event) => updateCase(kind, index, event.target.value)} placeholder={placeholder} value={value} />
                    <button aria-label={`删除${title}用例 ${index + 1}`} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md p-2 text-slate-400 hover:bg-white/10 hover:text-red-100 disabled:opacity-30" disabled={Boolean(busy) || values.length <= 1} onClick={() => removeCase(kind, index)} type="button"><Trash2 aria-hidden="true" size={15} /></button>
                  </div>
                ))}
              </div>
              <button className="mt-2 inline-flex min-h-11 items-center gap-2 rounded-full px-3 text-xs font-semibold text-brand-100 hover:bg-white/5 disabled:opacity-30" disabled={Boolean(busy) || values.length >= 6} onClick={() => addCase(kind)} type="button"><Plus aria-hidden="true" size={14} />再加一条</button>
            </fieldset>
          ))}
          <div className="flex flex-wrap justify-end gap-2 lg:col-span-2">
            <button className="min-h-11 rounded-full border border-white/15 px-5 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={Boolean(busy) || (Boolean(suite) && !caseDirty)} onClick={() => void saveSuite()} type="button">{busy === "suite-save" ? "正在保存…" : "保存测试边界"}</button>
            {suite ? <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={caseDirty || Boolean(busy)} onClick={() => void confirmSuite()} type="button"><CheckCircle2 aria-hidden="true" size={16} />{busy === "suite-confirm" ? "正在确认…" : "边界没问题，继续"}</button> : null}
          </div>
        </div>
      ) : null}

      {suiteConfirmed && primaryNext === "description" ? (
        <div className="mt-4 rounded-md border border-brand-300/20 bg-brand-300/[0.04] p-4">
          <p className="text-sm text-slate-200">{descriptionStale ? "目录或描述已变化，需要重新验证当前触发效果。" : "测试边界已确认。现在用生产 Finder 与 Router 排序验证一条清晰描述。"}</p>
          {status.trigger_optimizer_available ? <div className="mt-3 flex flex-wrap gap-2">
            <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void run("optimize", () => optimizeSkillCreatorTriggerDescriptions(session), "已生成并实测描述候选。") } type="button">{busy === "optimize" ? <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" size={16} /> : <Sparkles aria-hidden="true" size={16} />}{busy === "optimize" ? "正在实测…" : "AI 优化并实测描述"}</button>
            <button className="min-h-11 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white" disabled={Boolean(busy)} onClick={() => setManualOpen(true)} type="button">手工验证描述</button>
          </div> : null}
          {!status.trigger_optimizer_available ? <p className="mt-2 text-xs text-amber-100">无模型也可直接验证当前描述。</p> : null}
        </div>
      ) : null}

      {suiteConfirmed && (manualOpen || (!status.trigger_optimizer_available && !attempt)) && (attempt?.state !== "confirmed" || descriptionStale) ? (
        <div className="mt-4 rounded-md border border-white/10 bg-ink-950/35 p-4">
          <label className="block">
            <span className="text-sm font-semibold text-white">Skill 描述</span>
            <span className="mt-1 block text-xs leading-5 text-slate-400">用一行说明它能做什么、什么时候用，以及不适用的边界（最多 600 字符）。</span>
            <textarea className="mt-3 min-h-24 w-full resize-y rounded-md border border-white/10 bg-ink-950/75 px-3 py-2 text-sm leading-6 text-white focus:border-brand-300/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300/40 disabled:opacity-50" disabled={Boolean(busy)} maxLength={600} onChange={(event) => setDescription(event.target.value)} value={description} />
          </label>
          <div className="mt-3 flex justify-end">
            <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={!description.trim() || Boolean(busy)} onClick={() => void run("evaluate", () => evaluateSkillCreatorTriggerDescription(session, description), "描述已完成本地验证。") } type="button">{busy === "evaluate" ? <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" size={16} /> : <Target aria-hidden="true" size={16} />}{busy === "evaluate" ? "正在验证…" : "验证这条描述"}</button>
          </div>
        </div>
      ) : null}

      {attempt && attempt.state !== "confirmed" ? (
        <div className="mt-4 space-y-3">
          <p className="text-sm font-semibold text-white">选择一条已通过的描述</p>
          {attempt.candidates.map((candidate, index) => {
            const recommended = candidate.description_digest === attempt.recommended_description_digest;
            return (
              <article className={`min-w-0 rounded-md border p-4 ${candidate.passed ? "border-emerald-300/30 bg-emerald-300/[0.04]" : "border-red-300/25 bg-red-300/[0.035]"}`} key={candidate.description_digest}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <p className="min-w-0 flex-1 break-words text-sm leading-6 text-slate-200">{candidate.description}</p>
                  <span className={`rounded-full px-2.5 py-1 text-[11px] ${candidate.passed ? "bg-emerald-300/10 text-emerald-100" : "bg-red-300/10 text-red-100"}`}>{recommended ? "推荐 · " : ""}{candidate.passed ? "通过" : "未通过"}</span>
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                  <p className="text-xs text-slate-400">最弱正例名次 {candidate.worst_positive_rank || "—"} · 反例安全距离 {candidate.negative_safety_distance}</p>
                  {candidate.passed ? <button aria-label={`采用第 ${index + 1} 条描述`} className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-4 py-2 text-xs font-semibold text-ink-950 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void run("confirm-description", () => confirmSkillCreatorTriggerDescription(session, candidate.description_digest), "推荐描述已写入方案，触发检查通过。") } type="button">{busy === "confirm-description" ? <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" size={15} /> : null}{busy === "confirm-description" ? "正在采用…" : "采用这条描述"}{busy !== "confirm-description" ? <ChevronRight aria-hidden="true" size={15} /> : null}</button> : null}
                </div>
              </article>
            );
          })}
          {!attempt.candidates.some((item) => item.passed) ? <p className="rounded-md border border-amber-300/20 bg-amber-300/[0.07] px-3 py-2 text-xs leading-5 text-amber-100">没有候选通过。请手工改写描述后重新验证，或重新运行 AI 优化；资源计划尚未被修改。</p> : null}
          <button className="min-h-11 rounded-full px-3 text-xs font-semibold text-brand-100 hover:bg-white/5" onClick={() => setManualOpen(true)} type="button">手工改写并重试</button>
        </div>
      ) : null}

      {attempt?.state === "confirmed" && receipt ? (
        <div className={`mt-4 rounded-md border p-4 ${receipt.passed && !session.trigger_stale_reason ? "border-emerald-300/30 bg-emerald-300/[0.04]" : "border-amber-300/30 bg-amber-300/[0.04]"}`}>
          <p className="flex items-center gap-2 text-sm font-semibold text-white">
            {receipt.passed && !session.trigger_stale_reason ? <CheckCircle2 className="text-emerald-200" aria-hidden="true" size={17} /> : <CircleAlert className="text-amber-200" aria-hidden="true" size={17} />}
            {receipt.passed && !session.trigger_stale_reason ? "触发检查通过" : "需要重新验证"}
          </p>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            应该触发：{counts.positivePassed}/{counts.positiveTotal} 命中 · 不该触发：{counts.negativePassed}/{counts.negativeTotal} 避开
          </p>
          {session.trigger_stale_reason ? <p className="mt-2 text-xs leading-5 text-amber-100">{ERROR_COPY[session.trigger_stale_reason]?.message ?? "当前验证凭据已过期。"}</p> : null}
        </div>
      ) : null}

      {receipt ? (
        <details className="mt-4 border-t border-white/10 pt-3">
          <summary className="flex min-h-11 cursor-pointer items-center text-xs font-semibold text-slate-400 hover:text-slate-200">查看排名与竞争候选（诊断）</summary>
          <div className="mt-3 space-y-3">
            {receipt.case_results.map((result) => {
              const testCase = suite?.cases.find((item) => item.case_id === result.case_id);
              const terms = [...result.finder.reasons, ...result.router.reasons].flatMap((item) => item.matched_terms);
              return (
                <article className="min-w-0 rounded-md border border-white/8 bg-ink-950/30 p-3" key={result.case_id}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className="min-w-0 flex-1 break-words text-xs leading-5 text-slate-300">{testCase?.text ?? result.case_id}</p>
                    <span className={result.passed ? "text-xs text-emerald-200" : "text-xs text-red-200"}>{result.passed ? "通过" : "未通过"}</span>
                  </div>
                  <p className="mt-2 text-[11px] leading-5 text-slate-500">Finder {rankLabel(result.finder.rank_top_24)} · Router {rankLabel(result.router.rank_top_24)}{terms.length ? ` · 匹配：${[...new Set(terms)].slice(0, 8).join("、")}` : ""}</p>
                  {result.finder.competitors.length || result.router.competitors.length ? <p className="text-[11px] leading-5 text-slate-500">主要竞争：{[...result.finder.competitors, ...result.router.competitors].slice(0, 6).map((item) => item.candidate_id).join("、")}</p> : null}
                </article>
              );
            })}
          </div>
        </details>
      ) : null}
    </section>
  );
}

export function skillTriggerGateReady(session: SkillCreatorSession, status: SkillCreatorStatus) {
  if (!status.trigger_optimization_enabled) return true;
  if (!session.trigger_required) return true;
  if (status.trigger_store_available !== true) return false;
  return Boolean(
    session.trigger_attempt?.state === "confirmed"
      && session.trigger_receipt?.passed
      && !session.trigger_stale_reason,
  );
}
