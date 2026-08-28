import { Check, ChevronDown, LoaderCircle, RefreshCw, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { WorkflowRunEvent } from "../../types/workflow";
import type { XpertConversationMessage } from "../../types/xpert";
import { createSkillCreatorSession, SkillCreatorApiError } from "../../utils/skillCreatorApi";
import {
  analyzeSkillExperienceCandidate,
  createSkillExperienceCandidate,
  decideSkillExperienceCandidate,
  dismissSkillExperienceCandidate,
  findSkillExperienceCandidate,
  promoteSkillExperienceCandidate,
  readSkillExperienceCandidate,
  readSkillExperienceStatus,
  selectSkillExperienceEvidence,
  SkillExperienceApiError,
  sourceMatchesCandidate,
  updateSkillExperienceBrief,
  type DistilledSkillBrief,
  type SkillExperienceCandidate,
  type SkillExperienceEvidencePreview,
  type SkillExperienceSource,
} from "../../utils/skillExperienceApi";

export type SkillCreatorCaptureSource = SkillExperienceSource;

type BriefDraft = {
  suggestion: DistilledSkillBrief["suggestion"];
  recommendationReason: string;
  noSkillReason: NonNullable<DistilledSkillBrief["no_skill_reason"]> | "";
  intent: string;
  positiveExamples: string;
  negativeExamples: string;
  expectedOutput: string;
  successCriteria: string;
  reusableSteps: string;
  failureBoundaries: string;
  resourceClues: string;
  overfittingRisk: string;
};

const NO_SKILL_COPY: Record<string, string> = {
  one_off_task: "这只是一次性任务",
  preference_or_environment_fact: "这是偏好或环境事实",
  insufficient_evidence: "证据不足，暂时无法泛化",
  already_covered: "已有 Skill 已覆盖",
  cannot_generalize: "做法无法稳定泛化",
};

const SUGGESTION_COPY = {
  create: "建议新建 Skill",
  update: "建议更新现有 Skill",
  no_skill: "不建议沉淀",
} as const;

function cleanId(value: string | null | undefined) {
  return value?.trim() ?? "";
}

function lines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function briefToDraft(brief: DistilledSkillBrief): BriefDraft {
  return {
    suggestion: brief.suggestion,
    recommendationReason: brief.recommendation_reason,
    noSkillReason: brief.no_skill_reason ?? "",
    intent: brief.intent,
    positiveExamples: brief.positive_examples.join("\n"),
    negativeExamples: brief.negative_examples.join("\n"),
    expectedOutput: brief.expected_output,
    successCriteria: brief.success_criteria.join("\n"),
    reusableSteps: brief.reusable_steps.join("\n"),
    failureBoundaries: brief.failure_boundaries.join("\n"),
    resourceClues: brief.resource_clues.join("\n"),
    overfittingRisk: brief.overfitting_risk,
  };
}

function sameBriefDraft(draft: BriefDraft, brief: DistilledSkillBrief) {
  return JSON.stringify(draft) === JSON.stringify(briefToDraft(brief));
}

function reusableBriefReady(brief: DistilledSkillBrief) {
  return Boolean(
    brief.intent.trim()
    && brief.recommendation_reason.trim()
    && brief.expected_output.trim()
    && brief.overfitting_risk.trim()
    && brief.positive_examples.length >= 2
    && brief.negative_examples.length >= 2
    && brief.success_criteria.length > 0
    && brief.reusable_steps.length > 0
    && brief.failure_boundaries.length > 0
  );
}

export function xpertMessageCaptureSource(
  message: XpertConversationMessage,
  xpertId: string,
  conversationId: string,
): SkillCreatorCaptureSource | null {
  const taskId = cleanId(message.source_task_id);
  const runId = cleanId(message.source_run_id);
  const messageId = cleanId(message.message_id);
  const cleanXpertId = cleanId(xpertId);
  const cleanConversationId = cleanId(conversationId);
  if (
    message.role !== "assistant" || !taskId || !runId || !messageId
    || !cleanXpertId || !cleanConversationId
  ) return null;
  return {
    sourceKind: "xpert_chat",
    taskId,
    runId,
    xpertId: cleanXpertId,
    conversationId: cleanConversationId,
    messageId,
  };
}

export function completedWorkflowCaptureSource(
  events: WorkflowRunEvent[],
  taskId: string | null,
  runId: string | null,
  isRunning: boolean,
): SkillCreatorCaptureSource | null {
  const cleanTaskId = cleanId(taskId);
  const cleanRunId = cleanId(runId);
  if (isRunning || !cleanTaskId || !cleanRunId) return null;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.event === "workflow_end") {
      return { sourceKind: "workflow_classic", taskId: cleanTaskId, runId: cleanRunId };
    }
    if (["error", "human_intervention_pending", "runtime_approval_pending", "client_tool_waiting"].includes(event.event)) return null;
  }
  return null;
}

function legacySessionPayload(source: SkillCreatorCaptureSource) {
  return source.sourceKind === "xpert_chat"
    ? {
        mode: "run" as const,
        source_kind: "xpert_chat" as const,
        source_task_id: source.taskId,
        source_run_id: source.runId,
        source_xpert_id: source.xpertId,
        source_conversation_id: source.conversationId,
        source_message_id: source.messageId,
      }
    : {
        mode: "run" as const,
        source_kind: "workflow_classic" as const,
        source_task_id: source.taskId,
        source_run_id: source.runId,
      };
}

type CaptureProps = {
  enabled: boolean;
  source: SkillCreatorCaptureSource;
  label?: string;
  busyLabel?: string;
  initialCandidate?: SkillExperienceCandidate | null;
  onCandidateChange?: (candidate: SkillExperienceCandidate) => void;
};

export default function SkillCreatorCaptureButton(props: CaptureProps) {
  // A different trusted run must not retain the previous candidate or editable brief.
  return <SourceExperienceCapture key={JSON.stringify(props.source)} {...props} />;
}

function SourceExperienceCapture({
  enabled,
  source,
  label = "沉淀为 Skill",
  busyLabel = "正在读取运行经验...",
  initialCandidate = null,
  onCandidateChange,
}: CaptureProps) {
  const navigate = useNavigate();
  const [candidate, setCandidate] = useState<SkillExperienceCandidate | null>(
    initialCandidate && sourceMatchesCandidate(source, initialCandidate) ? initialCandidate : null,
  );
  const [preview, setPreview] = useState<SkillExperienceEvidencePreview | null>(null);
  const [experienceEnabled, setExperienceEnabled] = useState<boolean | null>(null);
  const [modelCallsEnabled, setModelCallsEnabled] = useState(false);
  const [restoring, setRestoring] = useState(enabled);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<Set<string>>(new Set());
  const [showEvidence, setShowEvidence] = useState(true);
  const [showEditor, setShowEditor] = useState(false);
  const [briefDraft, setBriefDraft] = useState<BriefDraft | null>(null);
  const [decisionMode, setDecisionMode] = useState<"create" | "update" | "dismiss">("create");
  const [targetSkillId, setTargetSkillId] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [newBoundary, setNewBoundary] = useState("");
  const sourceIdentity = useMemo(() => JSON.stringify(source), [source]);
  const eligibleUpdates = candidate?.overlaps.filter((item) => item.update_target_eligible) ?? [];
  const majorOverlaps = candidate?.overlaps.filter((item) => item.major_overlap) ?? [];

  function acceptCandidate(value: SkillExperienceCandidate) {
    setCandidate(value);
    onCandidateChange?.(value);
  }

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    setRestoring(true);
    setError("");
    void readSkillExperienceStatus()
      .then(async (status) => {
        if (!active) return;
        setExperienceEnabled(status.enabled);
        setModelCallsEnabled(status.model_calls_enabled);
        if (!status.enabled) return;
        if (!status.available) {
          setError("运行经验或已安装 Skill 暂时无法读取。请恢复服务端存储后重试。");
          return;
        }
        const existing = initialCandidate && sourceMatchesCandidate(source, initialCandidate)
          ? initialCandidate
          : await findSkillExperienceCandidate(source);
        if (!active || !existing) return;
        const hydrated = await readSkillExperienceCandidate(existing.candidate_id);
        if (!active) return;
        acceptCandidate(hydrated.candidate);
        setPreview(hydrated.evidence_preview);
      })
      .catch((caught) => {
        if (!active) return;
        if (caught instanceof SkillExperienceApiError && caught.code === "skill_experience_disabled") {
          setExperienceEnabled(false);
        } else {
          setExperienceEnabled(true);
          setError(caught instanceof Error ? caught.message : "运行经验或已安装 Skill 暂时无法读取。");
        }
      })
      .finally(() => {
        if (active) setRestoring(false);
      });
    return () => { active = false; };
  // sourceIdentity intentionally represents all trusted source fields.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, sourceIdentity]);

  useEffect(() => {
    if (!preview) return;
    const saved = new Set(candidate?.selected_evidence.map((item) => item.evidence_id) ?? []);
    setSelectedEvidence(saved.size > 0 ? saved : new Set(
      preview.candidates.filter((item) => item.default_selected).map((item) => item.candidate_id),
    ));
    setShowEvidence(saved.size === 0);
  }, [candidate?.candidate_id, candidate?.selected_evidence, preview]);

  useEffect(() => {
    const brief = candidate?.brief;
    if (!brief) return;
    setBriefDraft(briefToDraft(brief));
    setShowEditor(!brief.complete || candidate.analysis_attempt?.executor_mode === "manual");
    const hasUpdate = candidate.overlaps.some((item) => item.update_target_eligible);
    setDecisionMode(brief.suggestion === "no_skill" ? "dismiss" : brief.suggestion === "update" && hasUpdate ? "update" : "create");
    setTargetSkillId(candidate.overlaps.find((item) => item.update_target_eligible)?.installed_skill_id ?? "");
  }, [candidate?.brief?.digest]);

  useEffect(() => {
    if (candidate?.state !== "analyzing") return;
    let active = true;
    let timer = 0;
    const poll = async () => {
      try {
        const response = await readSkillExperienceCandidate(candidate.candidate_id);
        if (!active) return;
        acceptCandidate(response.candidate);
        setPreview(response.evidence_preview);
        setError("");
        if (response.candidate.state === "analyzing") {
          timer = window.setTimeout(() => void poll(), 800);
        }
      } catch (caught) {
        if (!active) return;
        setError(caught instanceof Error ? caught.message : "分析结果读取失败。");
        timer = window.setTimeout(() => void poll(), 1_600);
      }
    };
    timer = window.setTimeout(() => void poll(), 800);
    return () => { active = false; window.clearTimeout(timer); };
  }, [candidate?.candidate_id, candidate?.revision, candidate?.state]);

  if (!enabled) return null;

  async function legacyCapture() {
    setBusy("capture"); setError("");
    try {
      const session = await createSkillCreatorSession(legacySessionPayload(source));
      navigate(`/skills/create/${encodeURIComponent(session.session_id)}`);
    } catch (caught) {
      setError(caught instanceof SkillCreatorApiError ? caught.message : "这次运行暂时无法沉淀，请刷新后重试。");
    } finally { setBusy(""); }
  }

  async function captureExperience() {
    if (experienceEnabled === false) { await legacyCapture(); return; }
    setBusy("capture"); setError("");
    try {
      const response = await createSkillExperienceCandidate(source);
      acceptCandidate(response.candidate);
      setPreview(response.evidence_preview);
      setExperienceEnabled(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "这次运行暂时无法沉淀，请稍后重试。");
    } finally { setBusy(""); }
  }

  async function saveEvidence() {
    if (!candidate || !preview) return;
    if (selectedEvidence.size === 0) { setError("请至少选择一项脱敏素材。"); return; }
    setBusy("evidence"); setError("");
    try {
      acceptCandidate(await selectSkillExperienceEvidence(candidate, preview, [...selectedEvidence]));
      setShowEvidence(false);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "素材确认失败。"); }
    finally { setBusy(""); }
  }

  async function analyze() {
    if (!candidate) return;
    setBusy("analysis"); setError("");
    try { acceptCandidate(await analyzeSkillExperienceCandidate(candidate)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "运行经验分析失败。"); }
    finally { setBusy(""); }
  }

  async function saveBrief() {
    if (!candidate || !briefDraft) return;
    setBusy("brief"); setError("");
    try {
      const updated = await updateSkillExperienceBrief(candidate, {
        suggestion: briefDraft.suggestion,
        recommendation_reason: briefDraft.recommendationReason.trim(),
        no_skill_reason: briefDraft.suggestion === "no_skill" ? briefDraft.noSkillReason || null : null,
        intent: briefDraft.intent.trim(),
        positive_examples: lines(briefDraft.positiveExamples),
        negative_examples: lines(briefDraft.negativeExamples),
        expected_output: briefDraft.expectedOutput.trim(),
        success_criteria: lines(briefDraft.successCriteria),
        reusable_steps: lines(briefDraft.reusableSteps),
        failure_boundaries: lines(briefDraft.failureBoundaries),
        resource_clues: lines(briefDraft.resourceClues),
        overfitting_risk: briefDraft.overfittingRisk.trim(),
      });
      acceptCandidate(updated);
      setShowEditor(!updated.brief?.complete);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "提炼结果保存失败。"); }
    finally { setBusy(""); }
  }

  async function promote(value: SkillExperienceCandidate) {
    const response = await promoteSkillExperienceCandidate(value);
    acceptCandidate(response.candidate);
    navigate(response.route || `/skills/create/${encodeURIComponent(response.creator_session_id)}?step=2`);
  }

  async function resumePromotion() {
    if (!candidate) return;
    setBusy("promotion"); setError("");
    try { await promote(candidate); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Creator 会话创建失败。"); }
    finally { setBusy(""); }
  }

  async function confirmDecision() {
    if (!candidate?.brief) return;
    if (!candidate.brief.complete || (briefDraft && !sameBriefDraft(briefDraft, candidate.brief))) {
      if (decisionMode === "dismiss") { await dismissBeforeAnalysis(); return; }
      setError("请先保存并补全提炼结果，再继续。"); setShowEditor(true); return;
    }
    if (decisionMode === "update" && !targetSkillId) { setError("请选择要更新的 Creator Skill。"); return; }
    const needsBoundary = decisionMode === "create" && majorOverlaps.some((item) => item.best_rank <= 3);
    if (needsBoundary && !newBoundary.trim()) { setError("请说明新 Skill 与已有能力的适用边界。"); return; }
    if (decisionMode !== "dismiss" && candidate.brief.suggestion === "no_skill" && !overrideReason.trim()) {
      setError("请说明为什么仍要继续沉淀。"); return;
    }
    setBusy("decision"); setError("");
    try {
      const decided = await decideSkillExperienceCandidate(candidate, {
        decision: decisionMode,
        target_skill_id: decisionMode === "update" ? targetSkillId : undefined,
        override_reason: overrideReason.trim() || undefined,
        new_boundary: decisionMode === "create" ? newBoundary.trim() || undefined : undefined,
      });
      acceptCandidate(decided);
      if (decisionMode !== "dismiss") await promote(decided);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "沉淀决定保存失败。"); }
    finally { setBusy(""); }
  }

  async function dismissBeforeAnalysis() {
    if (!candidate) return;
    setBusy("dismiss"); setError("");
    try { acceptCandidate(await dismissSkillExperienceCandidate(candidate)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "暂不处理失败。"); }
    finally { setBusy(""); }
  }

  if (restoring || experienceEnabled === null) {
    return <div aria-label="正在恢复运行经验" className="min-h-11 w-full animate-pulse rounded-lg border border-white/10 bg-white/[0.035] motion-reduce:animate-none" />;
  }

  if (!candidate) {
    return (
      <div className="w-full rounded-lg border border-emerald-300/20 bg-emerald-300/[0.055] p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-emerald-100">把成功做法变成可复用 Skill</p>
            <p className="mt-1 text-xs leading-5 text-slate-400">先预览脱敏素材；分析、创建和安装都不会自动发生。</p>
          </div>
          <button className="inline-flex min-h-10 shrink-0 items-center justify-center gap-2 rounded-full bg-emerald-200 px-4 text-sm font-semibold text-ink-950 transition hover:bg-emerald-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-200 disabled:cursor-wait disabled:opacity-60" disabled={Boolean(busy)} onClick={() => void captureExperience()} type="button">
            <Sparkles aria-hidden="true" size={15} />{busy ? busyLabel : label}
          </button>
        </div>
        {error ? <p className="mt-3 text-xs leading-5 text-rose-200" role="alert">{error}</p> : null}
      </div>
    );
  }

  if (candidate.state === "dismissed" || candidate.state === "archived") {
    return <div className="w-full rounded-lg border border-white/10 bg-white/[0.025] px-4 py-3 text-xs text-slate-400">这次运行已标记为暂不沉淀。</div>;
  }

  if (candidate.state === "stale" || candidate.state === "failed") {
    return (
      <div className="w-full rounded-lg border border-rose-300/25 bg-rose-300/[0.07] p-4" role="alert">
        <p className="text-sm font-semibold text-rose-100">这次运行经验已失效</p>
        <p className="mt-1 text-xs leading-5 text-slate-300">来源运行或版本已经变化，不能用旧证据继续创建 Skill。</p>
        <button className="mt-3 inline-flex items-center gap-2 rounded-full border border-rose-200/25 px-3 py-2 text-xs font-semibold text-rose-100" onClick={() => window.location.reload()} type="button"><RefreshCw size={13} />重新加载</button>
      </div>
    );
  }

  if (candidate.state === "promoted" && candidate.promotion) {
    return (
      <div className="w-full rounded-lg border border-emerald-300/25 bg-emerald-300/[0.07] p-4">
        <p className="flex items-center gap-2 text-sm font-semibold text-emerald-100"><Check aria-hidden="true" size={15} />经验已交给 Creator</p>
        <p className="mt-1 text-xs leading-5 text-slate-400">需求和边界已经预填，下一步由你确认资源计划。</p>
        <button className="mt-3 rounded-full bg-emerald-200 px-4 py-2 text-xs font-semibold text-ink-950" onClick={() => navigate(candidate.promotion?.route || `/skills/create/${encodeURIComponent(candidate.promotion?.session_id || "")}?step=2`)} type="button">打开 Creator</button>
      </div>
    );
  }

  if (candidate.state === "promotion_ready") {
    return (
      <div className="w-full rounded-lg border border-brand-300/25 bg-brand-300/[0.07] p-4">
        <p className="text-sm font-semibold text-brand-100">沉淀方案已确认</p>
        <p className="mt-1 text-xs leading-5 text-slate-400">Creator 会话尚未创建，可安全重试，不会生成重复会话。</p>
        <button className="mt-3 rounded-full bg-brand-200 px-4 py-2 text-xs font-semibold text-ink-950 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void resumePromotion()} type="button">{busy === "promotion" ? "正在创建…" : "创建并打开 Creator"}</button>
        {error ? <p className="mt-3 text-xs text-rose-200" role="alert">{error}</p> : null}
      </div>
    );
  }

  if (candidate.state === "analyzing") {
    return (
      <div className="w-full rounded-lg border border-brand-300/20 bg-brand-300/[0.055] p-4" aria-live="polite">
        <p className="flex items-center gap-2 text-sm font-semibold text-brand-100"><LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={15} />正在提炼可复用做法</p>
        <p className="mt-1 text-xs leading-5 text-slate-400">只使用你确认的脱敏素材。刷新页面不会重复调用。</p>
        {error ? <p className="mt-3 text-xs text-rose-200" role="alert">{error}</p> : null}
      </div>
    );
  }

  if (candidate.state === "captured") {
    const evidenceSaved = candidate.selected_evidence.length > 0;
    return (
      <div className="w-full overflow-hidden rounded-lg border border-emerald-300/20 bg-surface-900/90">
        <div className="p-4">
          <p className="text-sm font-semibold text-white">确认可用于沉淀的素材</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">内容已由服务端脱敏。最终输出默认不选中，完整对话、参数和附件不会外发。</p>
        </div>
        {preview && (showEvidence || !evidenceSaved) ? (
          <div className="border-t border-white/10 p-4">
            <div className="grid gap-2 sm:grid-cols-2">
              {preview.candidates.map((item) => (
                <label className={`flex min-w-0 cursor-pointer gap-3 rounded-md border p-3 ${selectedEvidence.has(item.candidate_id) ? "border-brand-300/35 bg-brand-300/[0.08]" : "border-white/10 bg-white/[0.025]"}`} key={item.candidate_id}>
                  <input checked={selectedEvidence.has(item.candidate_id)} className="mt-1 h-4 w-4 shrink-0 accent-cyan-300" onChange={() => setSelectedEvidence((current) => {
                    const next = new Set(current);
                    if (next.has(item.candidate_id)) next.delete(item.candidate_id); else next.add(item.candidate_id);
                    return next;
                  })} type="checkbox" />
                  <span className="min-w-0">
                    <span className="text-xs font-semibold text-slate-100">{item.title}</span>
                    <span className="mt-1 block max-h-24 overflow-y-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-400">{item.summary}</span>
                    {item.kind === "final_output_excerpt" ? <span className="mt-1 block text-[10px] text-amber-100">默认关闭，勾选后才用于本次分析</span> : null}
                  </span>
                </label>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <button className="text-xs font-semibold text-slate-400 hover:text-white" disabled={Boolean(busy)} onClick={() => void dismissBeforeAnalysis()} type="button">暂不处理</button>
              <button className="rounded-full bg-brand-200 px-4 py-2 text-xs font-semibold text-ink-950 disabled:opacity-50" disabled={busy === "evidence" || selectedEvidence.size === 0} onClick={() => void saveEvidence()} type="button">{busy === "evidence" ? "正在保存…" : "确认这些素材"}</button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3 border-t border-white/10 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-xs font-semibold text-emerald-100">已确认 {candidate.selected_evidence.length} 项脱敏素材</p>
              <p className="mt-1 text-[11px] leading-5 text-slate-400">{modelCallsEnabled ? "点击后会把这些摘要发送给当前模型 Provider。" : "未配置模型，将生成可编辑的手工提纲，不会外发。"}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button className="rounded-full border border-white/15 px-3 py-2 text-xs font-semibold text-slate-300" onClick={() => setShowEvidence(true)} type="button">修改素材</button>
              <button className="rounded-full bg-brand-200 px-4 py-2 text-xs font-semibold text-ink-950 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void analyze()} type="button">{busy === "analysis" ? "正在开始…" : modelCallsEnabled ? "分析并预填" : "生成可编辑提纲"}</button>
            </div>
          </div>
        )}
        {error ? <p className="border-t border-white/10 px-4 py-3 text-xs text-rose-200" role="alert">{error}</p> : null}
      </div>
    );
  }

  const brief = candidate.brief;
  if (!brief || !briefDraft) return null;
  const briefDirty = !sameBriefDraft(briefDraft, brief);
  const manualMode = candidate.analysis_attempt?.executor_mode === "manual";
  const needsBoundary = decisionMode === "create" && majorOverlaps.some((item) => item.best_rank <= 3);
  const noSkillSuggested = brief.suggestion === "no_skill";
  const showReusableBrief = !noSkillSuggested || decisionMode !== "dismiss";
  const promotionReady = reusableBriefReady(brief);
  const summaryTitle = noSkillSuggested
    ? NO_SKILL_COPY[brief.no_skill_reason ?? ""] || "这次运行不适合沉淀为 Skill"
    : brief.intent || "请补全这项做法的可复用目标";
  const summaryReason = noSkillSuggested
    ? brief.recommendation_reason || "这次运行没有形成值得长期维护的可复用流程。"
    : brief.recommendation_reason;

  return (
    <div className="w-full overflow-hidden rounded-lg border border-brand-300/20 bg-surface-900/90">
      <div className="p-4 sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs font-semibold text-brand-100">{SUGGESTION_COPY[brief.suggestion]}</p>
            <p className="mt-1 text-sm font-semibold text-white">{summaryTitle}</p>
            <p className="mt-2 text-xs leading-5 text-slate-400">{summaryReason}</p>
          </div>
          <span className={`w-fit rounded-full px-2.5 py-1 text-[11px] font-semibold ${brief.complete ? "bg-emerald-300/10 text-emerald-100" : "bg-amber-300/10 text-amber-100"}`}>{noSkillSuggested ? "判断完成" : brief.complete ? "提纲完整" : "需要补全"}</span>
        </div>
        {manualMode ? <p className="mt-3 rounded-md border border-amber-300/20 bg-amber-300/[0.06] px-3 py-2 text-xs leading-5 text-amber-50">未使用外部模型。目标和步骤已从脱敏素材提取，请补充正反例、输出和验收条件。</p> : null}
        {showReusableBrief ? <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="rounded-md border border-white/10 bg-white/[0.025] p-3"><p className="text-[11px] font-semibold text-slate-300">可复用步骤</p><p className="mt-1 text-xs leading-5 text-slate-400">{brief.reusable_steps.slice(0, 3).join("；") || "待补充"}</p></div>
          <div className="rounded-md border border-white/10 bg-white/[0.025] p-3"><p className="text-[11px] font-semibold text-slate-300">预期结果</p><p className="mt-1 text-xs leading-5 text-slate-400">{brief.expected_output || "待补充"}</p></div>
        </div> : null}
        {majorOverlaps.length > 0 ? (
          <details className="mt-4 rounded-md border border-white/10 bg-white/[0.02] px-3 py-2">
            <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-semibold text-slate-300">可能已有 Skill 覆盖 <ChevronDown aria-hidden="true" size={14} /></summary>
            <ul className="mt-2 space-y-1 text-xs text-slate-400">{majorOverlaps.slice(0, 6).map((item) => <li key={item.candidate_id}>{item.name} · Top {item.best_rank}{item.update_target_eligible ? " · 可更新" : " · 仅供参考"}</li>)}</ul>
          </details>
        ) : null}
        {showReusableBrief ? <button className="mt-4 inline-flex items-center gap-2 text-xs font-semibold text-brand-100" onClick={() => setShowEditor((value) => !value)} type="button">{showEditor ? "收起详细提纲" : "检查或修改提纲"}<ChevronDown aria-hidden="true" className={showEditor ? "rotate-180" : ""} size={14} /></button> : null}
      </div>

      {showEditor ? (
        <div className="grid gap-4 border-t border-white/10 p-4 sm:p-5 lg:grid-cols-2">
          <label className="block lg:col-span-2"><span className="text-xs font-semibold text-slate-200">可复用目标</span><textarea className="mt-2 min-h-24 w-full rounded-md border border-white/10 bg-ink-950/70 p-3 text-sm text-white" maxLength={2000} onChange={(event) => setBriefDraft({ ...briefDraft, intent: event.target.value })} value={briefDraft.intent} /></label>
          <label className="block"><span className="text-xs font-semibold text-slate-200">应该使用它的例子（每行一个，至少 2 条）</span><textarea className="mt-2 min-h-28 w-full rounded-md border border-white/10 bg-ink-950/70 p-3 text-xs leading-5 text-white" onChange={(event) => setBriefDraft({ ...briefDraft, positiveExamples: event.target.value })} value={briefDraft.positiveExamples} /></label>
          <label className="block"><span className="text-xs font-semibold text-slate-200">相似但不该使用的例子（每行一个，至少 2 条）</span><textarea className="mt-2 min-h-28 w-full rounded-md border border-white/10 bg-ink-950/70 p-3 text-xs leading-5 text-white" onChange={(event) => setBriefDraft({ ...briefDraft, negativeExamples: event.target.value })} value={briefDraft.negativeExamples} /></label>
          <label className="block"><span className="text-xs font-semibold text-slate-200">预期输出</span><textarea className="mt-2 min-h-24 w-full rounded-md border border-white/10 bg-ink-950/70 p-3 text-xs leading-5 text-white" onChange={(event) => setBriefDraft({ ...briefDraft, expectedOutput: event.target.value })} value={briefDraft.expectedOutput} /></label>
          <label className="block"><span className="text-xs font-semibold text-slate-200">成功标准（每行一个）</span><textarea className="mt-2 min-h-24 w-full rounded-md border border-white/10 bg-ink-950/70 p-3 text-xs leading-5 text-white" onChange={(event) => setBriefDraft({ ...briefDraft, successCriteria: event.target.value })} value={briefDraft.successCriteria} /></label>
          <label className="block"><span className="text-xs font-semibold text-slate-200">可复用步骤（每行一个）</span><textarea className="mt-2 min-h-24 w-full rounded-md border border-white/10 bg-ink-950/70 p-3 text-xs leading-5 text-white" onChange={(event) => setBriefDraft({ ...briefDraft, reusableSteps: event.target.value })} value={briefDraft.reusableSteps} /></label>
          <label className="block"><span className="text-xs font-semibold text-slate-200">失败边界（每行一个）</span><textarea className="mt-2 min-h-24 w-full rounded-md border border-white/10 bg-ink-950/70 p-3 text-xs leading-5 text-white" onChange={(event) => setBriefDraft({ ...briefDraft, failureBoundaries: event.target.value })} value={briefDraft.failureBoundaries} /></label>
          <label className="block lg:col-span-2"><span className="text-xs font-semibold text-slate-200">避免过拟合</span><textarea className="mt-2 min-h-20 w-full rounded-md border border-white/10 bg-ink-950/70 p-3 text-xs leading-5 text-white" onChange={(event) => setBriefDraft({ ...briefDraft, overfittingRisk: event.target.value })} value={briefDraft.overfittingRisk} /></label>
          <div className="flex justify-end lg:col-span-2"><button className="rounded-full border border-brand-300/30 bg-brand-300/10 px-4 py-2 text-xs font-semibold text-brand-100 disabled:opacity-50" disabled={!briefDirty || busy === "brief"} onClick={() => void saveBrief()} type="button">{busy === "brief" ? "正在保存…" : "保存提纲"}</button></div>
        </div>
      ) : null}

      <div className="border-t border-white/10 p-4 sm:p-5">
        <p className="text-xs font-semibold text-slate-200">你决定如何处理</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-3" role="group" aria-label="沉淀方式">
          {(["create", "update", "dismiss"] as const).map((mode) => (
            <button aria-pressed={decisionMode === mode} className={`min-h-11 rounded-md border px-3 text-xs font-semibold ${decisionMode === mode ? "border-brand-300/40 bg-brand-300/10 text-brand-100" : "border-white/10 text-slate-400"}`} disabled={mode === "update" && eligibleUpdates.length === 0} key={mode} onClick={() => { setDecisionMode(mode); if (noSkillSuggested && mode !== "dismiss") setShowEditor(true); }} type="button">{mode === "create" ? "新建 Skill" : mode === "update" ? "更新 Creator Skill" : "这次不沉淀"}</button>
          ))}
        </div>
        {decisionMode === "update" ? <label className="mt-3 block"><span className="text-xs text-slate-400">选择可编辑的 Creator Skill</span><select className="mt-2 min-h-11 w-full rounded-md border border-white/10 bg-ink-950/80 px-3 text-sm text-white" onChange={(event) => setTargetSkillId(event.target.value)} value={targetSkillId}>{eligibleUpdates.map((item) => <option key={item.candidate_id} value={item.installed_skill_id || ""}>{item.name}</option>)}</select></label> : null}
        {needsBoundary ? <label className="mt-3 block"><span className="text-xs text-slate-400">与已有 Skill 的新适用边界</span><textarea className="mt-2 min-h-20 w-full rounded-md border border-white/10 bg-ink-950/70 p-3 text-xs text-white" onChange={(event) => setNewBoundary(event.target.value)} placeholder="说明为什么仍需新建，以及它只处理哪些不同场景。" value={newBoundary} /></label> : null}
        {decisionMode !== "dismiss" && brief.suggestion === "no_skill" ? <label className="mt-3 block"><span className="text-xs text-slate-400">仍要继续的原因</span><textarea className="mt-2 min-h-20 w-full rounded-md border border-white/10 bg-ink-950/70 p-3 text-xs text-white" onChange={(event) => setOverrideReason(event.target.value)} value={overrideReason} /></label> : null}
        {brief.suggestion === "no_skill" && brief.no_skill_reason ? <p className="mt-3 text-xs text-amber-100">分析理由：{NO_SKILL_COPY[brief.no_skill_reason] || brief.no_skill_reason}</p> : null}
        <div className="mt-4 flex justify-end"><button className={`rounded-full px-5 py-2.5 text-sm font-semibold disabled:opacity-50 ${decisionMode === "dismiss" ? "border border-white/15 text-slate-200" : "bg-hire-300 text-ink-950"}`} disabled={Boolean(busy) || (decisionMode !== "dismiss" && (!promotionReady || briefDirty))} onClick={() => void confirmDecision()} type="button">{busy === "decision" || busy === "dismiss" ? "正在确认…" : decisionMode === "dismiss" ? "确认暂不沉淀" : "确认并打开 Creator"}</button></div>
        {error ? <p className="mt-3 text-xs leading-5 text-rose-200" role="alert">{error}</p> : null}
      </div>
    </div>
  );
}
