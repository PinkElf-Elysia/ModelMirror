import { Check, Download, History, LoaderCircle, RefreshCw, Save, ShieldCheck, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import {
  answerSkillCreatorEvolutionPlan,
  confirmSkillCreatorEvolutionPlan,
  generateSkillCreatorEvolutionPlan,
  installSkillCreatorDraft,
  iterateSkillCreatorDraft,
  updateSkillCreatorEvolutionPlan,
  type SkillCreatorDraft,
  type SkillCreatorProposal,
  type SkillCreatorSession,
  type SkillEvaluationRun,
  type SkillEvolutionPlan,
} from "../../utils/skillCreatorApi";

function currentEvolutionPlan(session: SkillCreatorSession): SkillEvolutionPlan | null {
  const value = session.evolution_plan;
  return value && "plan_id" in value && value.state !== "stale" ? value : null;
}

export default function SkillCreatorFinish({
  session,
  draft,
  run,
  proposal,
  onProposal,
  onReload,
  onGoToBuild,
  onError,
  onNotice,
}: {
  session: SkillCreatorSession;
  draft: SkillCreatorDraft;
  run: SkillEvaluationRun | null;
  proposal: SkillCreatorProposal | null;
  onProposal: (proposal: SkillCreatorProposal, session?: SkillCreatorSession) => Promise<void> | void;
  onReload: () => Promise<void>;
  onGoToBuild?: () => void;
  onError: (error: unknown, fallback: string) => void;
  onNotice: (message: string) => void;
}) {
  const [busy, setBusy] = useState("");
  const [evolutionPlan, setEvolutionPlan] = useState<SkillEvolutionPlan | null>(() => currentEvolutionPlan(session));
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const qualityStatus = session.quality_status ?? draft.quality_status ?? "not_evaluated";
  const installState = session.install_state ?? draft.install_state ?? "not_installed";
  const accepted = qualityStatus === "accepted";
  const waived = qualityStatus === "eval_waived";
  const canInstall = (accepted || waived) && session.current_digest === draft.content_digest && installState !== "current";
  const evolutionEnabled = Boolean(session.regression_governance?.enabled);

  useEffect(() => {
    setEvolutionPlan(currentEvolutionPlan(session));
  }, [session.evolution_plan]);

  async function iterate() {
    if (!run) return;
    setBusy("iterate");
    try {
      const result = await iterateSkillCreatorDraft(session, draft, run);
      await onProposal(result.proposal, result.session);
      onNotice("生成助手已根据已保存反馈提交更新提案。批准后会形成新 revision，并使旧评测过期。");
    } catch (error) {
      onError(error, "改进提案生成失败。");
    } finally {
      setBusy("");
    }
  }

  async function generateEvolutionPlan() {
    if (!run) return;
    setBusy("evolution-generate");
    try {
      const next = await generateSkillCreatorEvolutionPlan(session, draft, run);
      setEvolutionPlan(next);
      setAnswers(next.clarification_answers ?? {});
      onNotice(next.state === "needs_input" ? "进化计划需要补充少量信息。" : "资源级进化计划已生成，请检查后确认。");
    } catch (error) {
      onError(error, "进化计划生成失败。");
    } finally {
      setBusy("");
    }
  }

  async function saveEvolutionAnswers() {
    if (!evolutionPlan) return;
    setBusy("evolution-answers");
    try {
      const next = await answerSkillCreatorEvolutionPlan(session, draft, evolutionPlan, answers);
      setEvolutionPlan(next);
      onNotice("澄清答案已冻结；请重新生成计划以应用答案。");
    } catch (error) {
      onError(error, "进化计划答案保存失败。");
    } finally {
      setBusy("");
    }
  }

  async function saveEvolutionActions() {
    if (!evolutionPlan) return;
    setBusy("evolution-save");
    try {
      const next = await updateSkillCreatorEvolutionPlan(session, draft, evolutionPlan, { actions: evolutionPlan.actions });
      setEvolutionPlan(next);
      onNotice("进化动作已保存为新的不可变 revision。");
    } catch (error) {
      onError(error, "进化动作保存失败。");
    } finally {
      setBusy("");
    }
  }

  async function confirmEvolution() {
    if (!evolutionPlan) return;
    setBusy("evolution-confirm");
    try {
      const result = await confirmSkillCreatorEvolutionPlan(session, draft, evolutionPlan);
      setEvolutionPlan(result.evolution_plan);
      await onReload();
      onGoToBuild?.();
      onNotice("改进方案已确认。接下来只重新生成需要变化的内容。");
    } catch (error) {
      onError(error, "进化计划确认失败。");
    } finally {
      setBusy("");
    }
  }

  async function install() {
    if (!canInstall) return;
    setBusy("install");
    try {
      await installSkillCreatorDraft(draft);
      await onReload();
      onNotice("Skill 已安装到工作区。评测完成不会自动安装，本次安装由你单独确认。");
    } catch (error) {
      onError(error, "Skill 安装失败。");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="mt-5 space-y-5">
      <section className="rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6" aria-labelledby="creator-iteration-heading">
        <div className="flex items-start gap-3">
          <RefreshCw aria-hidden="true" className="mt-0.5 shrink-0 text-brand-100" size={20} />
          <div>
            <h2 className="text-lg font-semibold text-white" id="creator-iteration-heading">按你的反馈继续改进</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">AI 只根据你刚保存的反馈找出需要变化的内容。它会先给出改进方案，得到确认后才重新生成。</p>
          </div>
        </div>
        {session.review_state === "revise" ? (
          <div className="mt-5 rounded-lg bg-amber-300/[0.07] p-4">
            <p className="text-xs font-semibold text-amber-100">你选择了：还要修改</p>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-amber-50/85">{session.review_feedback || run?.reviews?.at(-1)?.feedback || "反馈已保存在评测记录中。"}</p>
            {evolutionEnabled ? (
              <div className="mt-4 space-y-4">
                {!evolutionPlan ? (
                  <button className="inline-flex min-h-11 items-center gap-2 rounded-md bg-brand-200 px-4 py-2.5 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={!run || !session.resource_plan || Boolean(busy)} onClick={() => void generateEvolutionPlan()} type="button"><Sparkles aria-hidden="true" size={15} />{busy === "evolution-generate" ? "正在分析问题…" : "生成改进方案"}</button>
                ) : (
                  <div className="space-y-4 rounded-lg border border-white/10 bg-black/10 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-semibold text-white">AI 建议这样修改</p>
                      <span className="rounded-full bg-white/[0.06] px-2.5 py-1 text-xs text-slate-300">方案 {evolutionPlan.revision}</span>
                    </div>
                    {evolutionPlan.diagnoses.length ? <ul className="space-y-2 text-sm leading-6 text-slate-300">{evolutionPlan.diagnoses.map((diagnosis) => <li className="rounded-md bg-white/[0.035] p-3" key={`${diagnosis.case_id}-${diagnosis.summary}`}><span className="font-semibold text-white">{diagnosis.case_id}</span>：{diagnosis.summary}</li>)}</ul> : null}
                    {evolutionPlan.clarifications.length && evolutionPlan.state === "needs_input" ? (
                      <div className="space-y-3">{evolutionPlan.clarifications.map((question) => <label className="block" key={question.question_id}><span className="text-xs font-semibold text-amber-100">{question.question}</span><span className="mt-1 block text-xs text-slate-500">{question.reason}</span><textarea className="mt-2 min-h-20 w-full rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white" maxLength={4_000} onChange={(event) => setAnswers((current) => ({ ...current, [question.question_id]: event.target.value }))} value={answers[question.question_id] ?? ""} /></label>)}<button className="inline-flex items-center gap-2 rounded-md border border-white/15 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={evolutionPlan.clarifications.some((question) => !answers[question.question_id]?.trim()) || Boolean(busy)} onClick={() => void saveEvolutionAnswers()} type="button"><Save aria-hidden="true" size={14} />保存全部答案</button></div>
                    ) : null}
                    {evolutionPlan.actions.length ? (
                      <div className="space-y-2">{evolutionPlan.actions.map((action, index) => <div className="grid gap-3 rounded-md bg-white/[0.035] p-3 sm:grid-cols-[120px_minmax(0,1fr)]" key={action.action_id}><select className="rounded-md border border-white/10 bg-ink-950 px-2 py-2 text-sm text-white" disabled={evolutionPlan.state !== "ready"} onChange={(event) => setEvolutionPlan((current) => current ? { ...current, actions: current.actions.map((item, currentIndex) => currentIndex === index ? { ...item, action: event.target.value as typeof item.action } : item) } : current)} value={action.action}><option value="keep">保留</option><option value="update">更新</option><option value="create">新增</option><option value="delete">删除</option></select><div className="min-w-0"><p className="break-all font-mono text-xs text-white">{action.path}</p><p className="mt-1 text-xs leading-5 text-slate-400">{action.expected_improvement}</p></div></div>)}</div>
                    ) : null}
                    {evolutionPlan.overfitting_risks.length ? <div className="rounded-md bg-rose-300/[0.07] p-3 text-xs leading-5 text-rose-50"><span className="font-semibold">需要留意：</span>{evolutionPlan.overfitting_risks.join("；")}</div> : null}
                    <div className="flex flex-wrap gap-2">
                      {evolutionPlan.state === "ready" ? <><button className="inline-flex min-h-11 items-center gap-2 rounded-md border border-white/15 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void saveEvolutionActions()} type="button"><Save aria-hidden="true" size={14} />保存调整</button><button className="inline-flex min-h-11 items-center gap-2 rounded-md bg-emerald-300 px-3 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void confirmEvolution()} type="button"><Check aria-hidden="true" size={14} />确认方案并继续生成</button></> : null}
                      {evolutionPlan.state === "needs_regeneration" ? <button className="inline-flex items-center gap-2 rounded-md bg-brand-200 px-3 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void generateEvolutionPlan()} type="button"><RefreshCw aria-hidden="true" size={14} />根据答案重新生成</button> : null}
                      {evolutionPlan.state === "confirmed" ? <span className="inline-flex items-center gap-2 text-sm font-semibold text-emerald-100"><Check aria-hidden="true" size={14} />已转换为资源计划，返回资源构建阶段继续</span> : null}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <button className="mt-4 inline-flex items-center gap-2 rounded-md bg-brand-200 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={!run || Boolean(busy) || proposal?.status === "pending"} onClick={() => void iterate()} type="button">
                {busy === "iterate" ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={15} /> : <Sparkles aria-hidden="true" size={15} />}
                {proposal?.status === "pending" ? "更新提案待评审" : busy === "iterate" ? "正在生成…" : "根据反馈生成改进提案"}
              </button>
            )}
          </div>
        ) : (
          <p className="mt-5 rounded-lg bg-white/[0.025] p-4 text-sm leading-6 text-slate-400">评审选择“需要修改”后，可在这里生成下一版提案。任何内容修改都会让旧评测变为过期。</p>
        )}
        {session.resource_build ? (
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.025] p-4">
            <p className="text-xs font-semibold text-slate-300">内容生成进度</p>
            <p className="mt-2 text-sm text-slate-400">已确认 {session.resource_build.resources.filter((item) => item.state === "accepted").length}/{session.resource_build.resources.length} 项辅助内容；最终使用说明 {session.resource_build.skill_markdown_digest ? "已生成" : "待生成"}。</p>
          </div>
        ) : null}
      </section>

      {session.regression_governance ? (
        <details className="rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6">
          <summary className="cursor-pointer text-sm font-semibold text-slate-300">查看版本与评测历史</summary>
        <section className="mt-5" aria-labelledby="creator-history-heading">
          <div className="flex items-start gap-3">
            <History aria-hidden="true" className="mt-0.5 shrink-0 text-brand-100" size={20} />
            <div><h2 className="text-lg font-semibold text-white" id="creator-history-heading">版本与评测历史</h2><p className="mt-2 text-sm leading-6 text-slate-400">历史记录不会被覆盖，只有当前版本的接受结果可以用于安装。</p></div>
          </div>
          <div className="mt-5 grid gap-3 lg:grid-cols-2">
            <div className="rounded-lg bg-white/[0.025] p-4"><h3 className="text-sm font-semibold text-white">草稿 revision</h3><ul className="mt-3 space-y-2">{session.regression_governance.revisions.slice().reverse().map((item) => <li className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400" key={item.revision}><span>revision {item.revision} · {item.content_digest.slice(0, 12)}…</span><span>{item.is_current ? "当前" : item.is_previous ? "进化前" : item.is_installed ? "已安装基线" : "历史"}</span></li>)}</ul></div>
            <div className="rounded-lg bg-white/[0.025] p-4"><h3 className="text-sm font-semibold text-white">最近评测</h3><ul className="mt-3 space-y-2">{session.regression_governance.runs.slice(0, 8).map((item) => <li className="text-xs leading-5 text-slate-400" key={item.run_id}><span className="font-mono text-slate-300">{item.run_id.slice(0, 18)}…</span> · rev {item.draft_revision} · {item.target_count} 侧 · {item.status}{item.comparison_counts ? ` · 改善 ${item.comparison_counts.improved ?? 0} / 退化 ${item.comparison_counts.regressed ?? 0}` : ""}</li>)}</ul></div>
          </div>
        </section></details>
      ) : null}

      <section className={`rounded-lg p-5 sm:p-6 ${waived ? "border border-amber-300/25 bg-amber-300/[0.055]" : accepted || installState === "current" ? "border border-emerald-300/20 bg-emerald-300/[0.045]" : "border border-white/10 bg-surface-900/80"}`} aria-labelledby="creator-install-heading">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex items-start gap-3">
            <ShieldCheck aria-hidden="true" className={`mt-0.5 shrink-0 ${waived ? "text-amber-100" : accepted || installState === "current" ? "text-emerald-100" : "text-slate-500"}`} size={22} />
            <div>
              <h2 className="text-lg font-semibold text-white" id="creator-install-heading">最后一步：安装</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
                {installState === "current"
                  ? "当前摘要已安装。后续编辑只会生成新 revision，不会静默替换已安装版本。"
                  : accepted
                    ? "当前摘要的全部核心与回归用例已经人工接受。安装仍是一次独立的全局写入。"
                    : waived
                      ? "当前摘要记录了人工评测豁免。它没有获得行为对照结论，安装前请再次确认风险。"
                      : "当前摘要尚未获得有效的评测接受或人工豁免，不能安装。"}
              </p>
            </div>
          </div>
          <span className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-semibold ${installState === "current" ? "bg-emerald-300/15 text-emerald-100" : accepted ? "bg-emerald-300/10 text-emerald-100" : waived ? "bg-amber-300/10 text-amber-100" : "bg-white/[0.055] text-slate-400"}`}>
            {installState === "current" ? "已安装当前版本" : accepted ? "评测已接受" : waived ? "人工豁免" : qualityStatus === "outdated" ? "评测已过期" : "质量门未通过"}
          </span>
        </div>

        {waived && (session.quality_reason || draft.quality_decision?.reason) ? (
          <div className="mt-4 rounded-lg bg-black/15 p-4 text-sm leading-6 text-amber-50/85"><span className="font-semibold text-amber-100">豁免原因：</span>{session.quality_reason || draft.quality_decision?.reason}</div>
        ) : null}

        <div className="mt-5 flex flex-col gap-3 border-t border-white/10 pt-5 sm:flex-row sm:items-center sm:justify-between">
          <details className="text-xs text-slate-500"><summary className="cursor-pointer">查看版本信息</summary><p className="mt-2 break-all font-mono">版本 {draft.revision} · {draft.content_digest}</p></details>
          {installState === "current" ? (
            <span className="inline-flex items-center gap-2 text-sm font-semibold text-emerald-100"><Check aria-hidden="true" size={16} />已安装为 {session.installed_skill_id || draft.installed_skill_id || draft.name}</span>
          ) : (
            <button className={`inline-flex items-center justify-center gap-2 rounded-md px-4 py-2.5 text-sm font-semibold transition disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500 ${waived ? "border border-amber-200/30 bg-amber-200/10 text-amber-50 hover:bg-amber-200/15" : "bg-emerald-300 text-ink-950 hover:bg-emerald-200"}`} disabled={!canInstall || Boolean(busy)} onClick={() => void install()} type="button">
              {busy === "install" ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={15} /> : <Download aria-hidden="true" size={15} />}
              {busy === "install" ? "正在安装…" : accepted ? "确认安装当前版本" : waived ? "确认安装人工豁免版本" : "通过质量门后可安装"}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
