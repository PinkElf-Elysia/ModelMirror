import {
  ArrowLeft,
  ArrowRight,
  Check,
  ClipboardCheck,
  FileEdit,
  FlaskConical,
  Lightbulb,
  LoaderCircle,
  LockKeyhole,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import SkillCreatorFinish from "../components/skill-creator/SkillCreatorFinish";
import SkillEvaluationDesigner from "../components/skill-creator/SkillEvaluationDesigner";
import SkillEvaluationReview from "../components/skill-creator/SkillEvaluationReview";
import SkillPackageEditor from "../components/skill-creator/SkillPackageEditor";
import SkillProposalReview from "../components/skill-creator/SkillProposalReview";
import SkillResourceBuildPanel from "../components/skill-creator/SkillResourceBuildPanel";
import SkillResourcePlanPanel from "../components/skill-creator/SkillResourcePlanPanel";
import { useSkillCreatorStatus } from "../hooks/useSkillCreatorStatus";
import {
  approveSkillCreatorProposal,
  copySkillCreatorSession,
  createBlankSkillCreatorDraft,
  generateSkillCreatorProposal,
  previewSkillCreatorSource,
  readSkillCreatorEvaluation,
  readSkillCreatorDraft,
  readSkillCreatorProposal,
  readSkillCreatorSession,
  rejectSkillCreatorProposal,
  saveSkillCreatorDraft,
  selectSkillCreatorEvidence,
  SkillCreatorApiError,
  updateSkillCreatorSession,
  type SkillCreatorDraft,
  type SkillCreatorEvidenceCandidate,
  type SkillCreatorProposal,
  type SkillCreatorSession,
  type SkillCreatorSourcePreview,
  type SkillEvaluationRun,
  type SkillPackageIssue,
  type SkillPackagePayload,
} from "../utils/skillCreatorApi";

const LEGACY_STEPS = [
  { title: "说出需求", detail: "一句话也可以", icon: Lightbulb },
  { title: "确认方案", detail: "让 AI 先规划", icon: ClipboardCheck },
  { title: "生成内容", detail: "逐项检查结果", icon: FileEdit },
  { title: "试一试", detail: "准备真实任务", icon: FlaskConical },
  { title: "对比结果", detail: "判断是否更好", icon: ShieldCheck },
  { title: "改进并安装", detail: "做最后决定", icon: Sparkles },
] as const;

type CreatorStep = {
  title: string;
  detail: string;
  icon: (typeof LEGACY_STEPS)[number]["icon"];
};

const RESOURCE_STEPS: readonly CreatorStep[] = LEGACY_STEPS.map((step, index) => (
  index === 1
    ? { ...step, title: "确认方案", detail: "素材与资源计划" }
    : index === 2
      ? { ...step, title: "生成内容", detail: "资源与最终说明" }
    : step
));

type HydrateOptions = {
  preserveActiveStep?: boolean;
};

type LoadSessionOptions = HydrateOptions & {
  background?: boolean;
};

const EVIDENCE_LABELS: Record<SkillCreatorEvidenceCandidate["kind"], string> = {
  intent_summary: "目标摘要",
  successful_steps: "成功步骤",
  tool_names: "工具名称",
  user_correction: "用户修正",
  io_shape: "输入输出结构",
  final_output_excerpt: "最终输出片段",
};

function evidenceLabel(
  candidate: SkillCreatorEvidenceCandidate,
  sourceKind?: SkillCreatorSession["source_kind"],
) {
  if (candidate.kind === "final_output_excerpt" && sourceKind === "workflow_classic") {
    return "工作流生成的需求分析";
  }
  return EVIDENCE_LABELS[candidate.kind];
}

function evidenceSummary(candidate: SkillCreatorEvidenceCandidate) {
  if (candidate.kind === "io_shape") {
    try {
      const payload = JSON.parse(candidate.summary) as {
        inputs?: Array<{ name?: string; type?: string }>;
        output?: { type?: string; present?: boolean };
      };
      const inputs = (payload.inputs ?? [])
        .map((item) => `${item.name || "未命名输入"}（${item.type || "未知类型"}）`)
        .join("、") || "未声明";
      const output = payload.output?.present === false
        ? "无"
        : payload.output?.type || "文本";
      return `输入：${inputs}；输出：${output}`;
    } catch {
      // Fall through to the bounded plain-text presentation below.
    }
  }
  return candidate.summary
    .replace(/\*\*/g, "")
    .replace(/\s*[（(]workflow_agent[）)]/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function splitLines(value: string) {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function joinLines(value: string[]) {
  return value.join("\n");
}

interface GenerationReadinessItem {
  id: string;
  label: string;
  ready: boolean;
  missing: string;
}

function GenerationReadiness({ items }: { items: GenerationReadinessItem[] }) {
  const missing = items.filter((item) => !item.ready);
  return (
    <section className="mt-5 border-y border-white/10 py-4" aria-labelledby="creator-generation-readiness-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-white" id="creator-generation-readiness-heading">AI 生成准备度</h3>
        <span className={`text-xs font-semibold ${missing.length ? "text-amber-100" : "text-emerald-100"}`}>
          {items.length - missing.length}/{items.length} 项已完成
        </span>
      </div>
      <ul className="mt-3 grid gap-2 sm:grid-cols-2" aria-label="AI 生成准备项">
        {items.map((item) => (
          <li className="flex items-center gap-2 text-xs" key={item.id}>
            <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${item.ready ? "bg-emerald-300/15 text-emerald-100" : "bg-white/[0.055] text-slate-500"}`}>
              {item.ready ? <Check aria-hidden="true" size={12} /> : <span aria-hidden="true">·</span>}
            </span>
            <span className={item.ready ? "text-slate-200" : "text-slate-500"}>{item.label}</span>
          </li>
        ))}
      </ul>
      {missing.length ? (
        <p className="mt-3 text-xs leading-5 text-amber-100" role="status">
          AI 生成暂不可用，仍缺：{missing.map((item) => item.missing).join("、")}。
        </p>
      ) : (
        <p className="mt-3 text-xs leading-5 text-emerald-100" role="status">六项信息齐备，可以生成可评测初稿。</p>
      )}
    </section>
  );
}

function DisabledStudio() {
  return (
    <section className="mx-auto max-w-2xl rounded-lg border border-amber-300/25 bg-amber-300/[0.07] p-6 sm:p-8">
      <LockKeyhole aria-hidden="true" className="text-amber-100" size={28} />
      <h1 className="mt-5 text-2xl font-semibold text-white">Skill Creator 尚未启用</h1>
      <p className="mt-3 text-sm leading-6 text-slate-300">直接访问不会绕过实例开关。启用后可恢复服务端已保存的 Creator 会话。</p>
      <Link className="mt-6 inline-flex items-center gap-2 rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950" to="/skills">
        <ArrowLeft aria-hidden="true" size={16} />
        返回 Skill 货架
      </Link>
    </section>
  );
}

function StepRail({
  activeStep,
  availableSteps,
  onSelect,
  steps,
}: {
  activeStep: number;
  availableSteps: boolean[];
  onSelect: (index: number) => void;
  steps: readonly CreatorStep[];
}) {
  const previous = [...availableSteps.keys()].filter((index) => index < activeStep && availableSteps[index]).at(-1);
  const next = [...availableSteps.keys()].find((index) => index > activeStep && availableSteps[index]);
  return (
    <nav aria-label="Skill Creator 阶段">
      <div className="rounded-lg border border-white/10 bg-surface-900/70 p-3 lg:hidden">
        <div className="flex items-center justify-between gap-3">
          <button aria-label="上一步" className="rounded-md border border-white/10 p-2 text-slate-200 disabled:opacity-30" disabled={previous == null} onClick={() => previous != null && onSelect(previous)} type="button"><ArrowLeft aria-hidden="true" size={15} /></button>
          <div className="min-w-0 text-center">
            <p className="text-xs text-slate-500">当前步骤 {activeStep + 1}/6</p>
            <p className="mt-1 truncate text-sm font-semibold text-white">{steps[activeStep].title}</p>
          </div>
          <button aria-label="下一步" className="rounded-md border border-white/10 p-2 text-slate-200 disabled:opacity-30" disabled={next == null} onClick={() => next != null && onSelect(next)} type="button"><ArrowRight aria-hidden="true" size={15} /></button>
        </div>
        <details className="mt-3 border-t border-white/10 pt-3">
          <summary className="cursor-pointer text-center text-xs font-semibold text-slate-300">展开全部步骤</summary>
          <ol className="mt-3 grid gap-2">
            {steps.map((step, index) => (
              <li key={step.title}><button aria-current={activeStep === index ? "step" : undefined} className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-xs ${activeStep === index ? "bg-hire-300/10 text-hire-100" : "text-slate-300"}`} disabled={!availableSteps[index]} onClick={() => onSelect(index)} type="button"><span>{index + 1}. {step.title}</span>{!availableSteps[index] ? <LockKeyhole aria-hidden="true" size={12} /> : null}</button></li>
            ))}
          </ol>
        </details>
      </div>
      <ol className="hidden grid-cols-6 overflow-hidden rounded-lg border border-white/10 bg-surface-900/70 lg:grid">
        {steps.map((step, index) => {
          const Icon = step.icon;
          const inaccessible = !availableSteps[index];
          const current = activeStep === index;
          return (
            <li key={step.title}>
              <button
                aria-label={`第 ${index + 1} 步：${step.title}`}
                aria-current={current ? "step" : undefined}
                className={`flex min-h-16 w-full items-center gap-2 border-r border-white/10 px-3 py-3 text-left transition last:border-r-0 ${
                  current
                    ? "bg-hire-300/10 text-white"
                    : inaccessible
                      ? "bg-white/[0.015] text-slate-600"
                      : "text-slate-300 hover:bg-white/[0.055]"
                }`}
                disabled={inaccessible}
                onClick={() => onSelect(index)}
                type="button"
              >
                <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${current ? "bg-hire-300 text-ink-950" : "bg-white/[0.055]"}`}>
                  {inaccessible ? <LockKeyhole aria-hidden="true" size={13} /> : <Icon aria-hidden="true" size={14} />}
                </span>
                <span className="min-w-0 text-xs font-semibold">{step.title}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export default function SkillCreatorStudioPage() {
  const { sessionId = "" } = useParams();
  const navigate = useNavigate();
  const { status, loading: statusLoading, error: statusError, reload: reloadStatus } = useSkillCreatorStatus();
  const [session, setSession] = useState<SkillCreatorSession | null>(null);
  const [draft, setDraft] = useState<SkillCreatorDraft | null>(null);
  const [proposal, setProposal] = useState<SkillCreatorProposal | null>(null);
  const [evaluationRun, setEvaluationRun] = useState<SkillEvaluationRun | null>(null);
  const [sourcePreview, setSourcePreview] = useState<SkillCreatorSourcePreview | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<Set<string>>(new Set());
  const [activeStep, setActiveStep] = useState(0);
  const previousActiveStepRef = useRef(activeStep);
  const [intent, setIntent] = useState("");
  const [positiveExamples, setPositiveExamples] = useState("");
  const [nearMissExamples, setNearMissExamples] = useState("");
  const [expectedOutput, setExpectedOutput] = useState("");
  const [successCriteria, setSuccessCriteria] = useState("");
  const [definitionMode, setDefinitionMode] = useState<"simple" | "advanced">("simple");
  const [rootName, setRootName] = useState("");
  const [manualDescription, setManualDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflictMessage, setConflictMessage] = useState("");
  const [errorIssues, setErrorIssues] = useState<SkillPackageIssue[]>([]);
  const [draftDirty, setDraftDirty] = useState(false);
  const restoringHistory = useRef(false);
  const backgroundRefreshInFlight = useRef(false);

  const syncSessionForm = useCallback((value: SkillCreatorSession) => {
    setIntent(value.intent ?? "");
    setPositiveExamples(joinLines(value.positive_examples ?? []));
    setNearMissExamples(joinLines(value.near_miss_examples ?? []));
    setExpectedOutput(value.expected_output ?? "");
    setSuccessCriteria(joinLines(value.success_criteria ?? []));
  }, []);

  const hydrate = useCallback(async (value: SkillCreatorSession, options: HydrateOptions = {}) => {
    let hydratedDraft = value.draft ?? null;
    let hydratedProposal = value.proposal ?? null;
    if (!hydratedDraft && value.draft_id) {
      hydratedDraft = await readSkillCreatorDraft(value.draft_id);
    }
    if (!hydratedProposal && value.proposal_id) {
      hydratedProposal = await readSkillCreatorProposal(value.proposal_id);
    }
    if (
      value.resource_build?.proposal_id
      && hydratedProposal?.proposal_id !== value.resource_build.proposal_id
    ) {
      try {
        hydratedProposal = await readSkillCreatorProposal(value.resource_build.proposal_id);
      } catch {
        // Preserve the last readable proposal when a newer projection cannot be hydrated.
      }
    }
    let hydratedRun = value.evaluation_run ?? null;
    const runId = value.active_evaluation_run_id ?? value.latest_evaluation_run_id;
    if (!hydratedRun && runId) {
      try {
        hydratedRun = await readSkillCreatorEvaluation(runId);
      } catch {
        hydratedRun = null;
      }
    }
    const hydratedSession = { ...value, draft: hydratedDraft, proposal: hydratedProposal, evaluation_run: hydratedRun };
    setSession(hydratedSession);
    setDraft(hydratedDraft);
    setProposal(hydratedProposal);
    setEvaluationRun(hydratedRun);
    syncSessionForm(value);
    let restoredStep = 0;
    const resourceFlow = value.authoring_flow === "resource";
    if (!hydratedDraft && value.evidence_confirmed) restoredStep = 1;
    if (resourceFlow && (value.resource_build || value.resource_plan?.state === "confirmed")) restoredStep = 2;
    if (hydratedDraft) restoredStep = resourceFlow ? 3 : 2;
    if (
      resourceFlow
      && value.experience_candidate_id
      && value.experience_decision === "update"
      && !value.resource_plan
      && !value.resource_build
    ) restoredStep = 1;
    if (value.state === "designing_tests") restoredStep = 3;
    if (hydratedRun || value.state === "reviewing_results") restoredStep = 4;
    if (
      value.state === "iterating" || value.state === "completed" ||
      value.review_state === "revise" || value.review_state === "accepted" || value.review_state === "waived" ||
      value.quality_status === "accepted" || value.quality_status === "eval_waived"
    ) restoredStep = 5;
    if (hydratedProposal?.status === "pending" && value.review_state !== "revise") restoredStep = resourceFlow ? 2 : 1;
    const activeResourceBuild = resourceFlow
      && value.resource_build
      && value.resource_build.state !== "stale"
      && value.resource_build.stale !== true;
    if (activeResourceBuild) restoredStep = 2;
    if (!options.preserveActiveStep) {
      setActiveStep((current) => Math.max(current, restoredStep));
    }
  }, [syncSessionForm]);

  const loadSession = useCallback(async (options: LoadSessionOptions = {}) => {
    if (!sessionId || !status?.enabled) return;
    if (!options.background) setLoading(true);
    setError("");
    try {
      await hydrate(await readSkillCreatorSession(sessionId), options);
    } catch (caught) {
      setError(caught instanceof SkillCreatorApiError ? caught.message : "Creator 会话加载失败。");
    } finally {
      if (!options.background) setLoading(false);
    }
  }, [hydrate, sessionId, status?.enabled]);

  useEffect(() => {
    if (!status?.enabled) {
      if (status) setLoading(false);
      return;
    }
    const refreshVisibleSession = () => {
      if (document.visibilityState !== "visible" || backgroundRefreshInFlight.current) return;
      backgroundRefreshInFlight.current = true;
      void loadSession({ background: true, preserveActiveStep: true }).finally(() => {
        backgroundRefreshInFlight.current = false;
      });
    };
    void loadSession();
    window.addEventListener("focus", refreshVisibleSession);
    document.addEventListener("visibilitychange", refreshVisibleSession);
    return () => {
      window.removeEventListener("focus", refreshVisibleSession);
      document.removeEventListener("visibilitychange", refreshVisibleSession);
    };
  }, [loadSession, status]);

  useLayoutEffect(() => {
    const stepChanged = previousActiveStepRef.current !== activeStep;
    if (window.scrollX !== 0 || window.scrollY !== 0) {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
    if (stepChanged && activeStep === 2) {
      document.getElementById("resource-build-start-heading")?.focus();
      document.getElementById("resource-build-heading")?.focus();
    }
    previousActiveStepRef.current = activeStep;
  }, [activeStep, sessionId]);

  const intentComplete = definitionMode === "simple"
    ? Boolean(intent.trim())
    : Boolean(intent.trim() && positiveExamples.trim() && nearMissExamples.trim() && expectedOutput.trim() && successCriteria.trim());
  const generationReadiness = useMemo<GenerationReadinessItem[]>(() => [
    { id: "intent", label: "用途与触发条件", ready: Boolean(intent.trim()), missing: "用途" },
    { id: "positive", label: "正向示例", ready: Boolean(positiveExamples.trim()), missing: "正向示例" },
    { id: "near-miss", label: "近似反例", ready: Boolean(nearMissExamples.trim()), missing: "近似反例" },
    { id: "output", label: "预期输出", ready: Boolean(expectedOutput.trim()), missing: "预期输出" },
    { id: "criteria", label: "成功标准", ready: Boolean(successCriteria.trim()), missing: "成功标准" },
    { id: "evidence", label: "素材确认", ready: Boolean(session?.evidence_confirmed), missing: "素材确认" },
  ], [expectedOutput, intent, nearMissExamples, positiveExamples, session?.evidence_confirmed, successCriteria]);
  const generationReady = generationReadiness.every((item) => item.ready);
  const validRootName = /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(rootName) && rootName.length <= 64;
  const validManualDescription = manualDescription.trim().length > 0 && manualDescription.trim().length <= 1_024;
  const selectedEvidenceCount = selectedEvidence.size;

  function applySourcePreview(
    value: SkillCreatorSession,
    preview: SkillCreatorSourcePreview,
  ) {
    setSourcePreview(preview);
    const availableIds = new Set(preview.candidates.map((item) => item.candidate_id));
    const persistedIds = value.selected_evidence
      .map((item) => item.candidate_id)
      .filter((candidateId) => availableIds.has(candidateId));
    setSelectedEvidence(new Set(
      value.evidence_confirmed || value.selected_evidence.length > 0
        ? persistedIds
        : preview.candidates
            .filter((item) => item.default_selected)
            .map((item) => item.candidate_id),
    ));
  }

  async function saveIntent() {
    if (!session) return;
    setBusy("intent");
    setError("");
    setNotice("");
    try {
      const quickPositiveExamples = splitLines(positiveExamples).length
        ? splitLines(positiveExamples)
        : [intent.trim()];
      const quickNearMissExamples = splitLines(nearMissExamples).length
        ? splitLines(nearMissExamples)
        : ["与上述目标无关的闲聊、通用改写或其他任务。"];
      const quickExpectedOutput = expectedOutput.trim()
        || "直接完成上述任务；缺少必要信息时明确列出待确认项，不编造事实。";
      const quickSuccessCriteria = splitLines(successCriteria).length
        ? splitLines(successCriteria)
        : ["结果直接解决用户提出的任务。", "只使用已有信息，缺失内容明确标记为待确认。"];
      let updated = await updateSkillCreatorSession(session.session_id, {
        expected_session_revision: session.session_revision,
        intent: intent.trim(),
        positive_examples: definitionMode === "simple" ? quickPositiveExamples : splitLines(positiveExamples),
        near_miss_examples: definitionMode === "simple" ? quickNearMissExamples : splitLines(nearMissExamples),
        expected_output: definitionMode === "simple" ? quickExpectedOutput : expectedOutput.trim(),
        success_criteria: definitionMode === "simple" ? quickSuccessCriteria : splitLines(successCriteria),
      });
      if (updated.mode === "blank" && !updated.evidence_confirmed) {
        const preview = await previewSkillCreatorSource(updated);
        updated = await selectSkillCreatorEvidence(updated, preview, []);
        setSourcePreview(preview);
        setSelectedEvidence(new Set());
      }
      await hydrate(updated, { preserveActiveStep: true });
      setActiveStep(1);
      setNotice(status?.resource_authoring_enabled
        ? updated.mode === "run"
          ? "需求已保存。请检查工作流素材，确认哪些内容可以用于方案。"
          : "需求已保存。接下来让 AI 给出方案；需要补充信息时，它会明确提问。"
        : "需求已保存。接下来确认 AI 的理解并生成草稿。");
      if (updated.mode === "run" && updated.source_kind) {
        try {
          applySourcePreview(updated, await previewSkillCreatorSource(updated));
        } catch (previewError) {
          handleError(previewError, "需求已保存，但脱敏素材暂时无法加载。请在下一步重试。");
        }
      }
    } catch (caught) {
      handleError(caught, "用途保存失败。");
    } finally {
      setBusy("");
    }
  }

  function handleError(caught: unknown, fallback: string) {
    if (caught instanceof SkillCreatorApiError) {
      setError(caught.message);
      setErrorIssues(caught.issues);
      if (caught.status === 409) setConflictMessage(caught.message);
    } else {
      setError(fallback);
    }
  }

  async function loadSourcePreview() {
    if (!session) return;
    setBusy("preview");
    setError("");
    try {
      const preview = await previewSkillCreatorSource(session);
      applySourcePreview(session, preview);
    } catch (caught) {
      handleError(caught, "脱敏素材加载失败。");
    } finally {
      setBusy("");
    }
  }

  async function saveEvidence() {
    if (!session || !sourcePreview) return;
    setBusy("evidence");
    setError("");
    try {
      const updated = await selectSkillCreatorEvidence(session, sourcePreview, [...selectedEvidence]);
      await hydrate(updated);
      setNotice(`已保存 ${selectedEvidenceCount} 项脱敏素材。`);
    } catch (caught) {
      handleError(caught, "素材选择保存失败。");
    } finally {
      setBusy("");
    }
  }

  async function confirmBlankEvidence() {
    if (!session || session.mode !== "blank") return;
    setBusy("evidence");
    setError("");
    setNotice("");
    try {
      const preview = await previewSkillCreatorSource(session);
      const updated = await selectSkillCreatorEvidence(session, preview, []);
      setSourcePreview(preview);
      setSelectedEvidence(new Set());
      await hydrate(updated);
      setNotice("已确认不导入运行素材。草稿将只使用用途、示例与成功标准。");
    } catch (caught) {
      handleError(caught, "空白素材确认失败。");
    } finally {
      setBusy("");
    }
  }

  async function generateProposal() {
    if (!session) return;
    setBusy("generate");
    setError("");
    setNotice("");
    try {
      const result = await generateSkillCreatorProposal(session);
      setProposal(result.proposal);
      await hydrate({ ...result.session, proposal: result.proposal });
      setNotice("生成助手已提交类型化提案，请检查文件差异后再批准。");
    } catch (caught) {
      handleError(caught, "Skill 提案生成失败。");
    } finally {
      setBusy("");
    }
  }

  async function createBlankDraft() {
    if (!session || !validRootName || !validManualDescription) return;
    setBusy("blank");
    setError("");
    setNotice("");
    try {
      const updated = await createBlankSkillCreatorDraft(session, rootName, manualDescription.trim());
      await hydrate(updated);
      setActiveStep(2);
      setNotice("结构化手工模板已创建。它尚未通过初稿完整度或行为评测，请继续补全正文和必要资源。");
    } catch (caught) {
      handleError(caught, "结构化手工模板创建失败。");
    } finally {
      setBusy("");
    }
  }

  async function approveProposal() {
    if (!proposal) return;
    setBusy("approve");
    setError("");
    try {
      await approveSkillCreatorProposal(proposal);
      await loadSession();
      setActiveStep(session?.authoring_flow === "resource" ? 3 : 2);
      setNotice("提案已写入不可变草稿版本。该草稿仍需完成当前摘要的三例对照评测后才能安装。");
    } catch (caught) {
      handleError(caught, "提案批准失败。");
    } finally {
      setBusy("");
    }
  }

  async function saveDraft(payload: SkillPackagePayload) {
    if (!session || !draft) return;
    setBusy("save-draft");
    setError("");
    setNotice("");
    setConflictMessage("");
    setErrorIssues([]);
    try {
      const updated = await saveSkillCreatorDraft(session, draft, payload);
      await hydrate(updated);
      setNotice("草稿已保存为新的不可变内容版本，质量状态已标记为待评测。");
    } catch (caught) {
      handleError(caught, "草稿保存失败。");
    } finally {
      setBusy("");
    }
  }

  async function copyAsNew(payload: SkillPackagePayload) {
    if (!session) return;
    setError("");
    try {
      const copied = await copySkillCreatorSession(session, payload);
      navigate(`/skills/create/${encodeURIComponent(copied.session_id)}`, { replace: true });
    } catch (caught) {
      handleError(caught, "复制为新草稿失败。");
    }
  }

  function toggleEvidence(candidateId: string) {
    setSelectedEvidence((current) => {
      const next = new Set(current);
      if (next.has(candidateId)) next.delete(candidateId);
      else next.add(candidateId);
      return next;
    });
  }

  async function rejectProposal(reason: string) {
    if (!proposal) return;
    setBusy("reject");
    setError("");
    setNotice("");
    try {
      await rejectSkillCreatorProposal(proposal, reason);
      await loadSession();
      setNotice("提案已丢弃，草稿未改变。你可以重新生成提案。");
    } catch (caught) {
      handleError(caught, "提案丢弃失败。");
    } finally {
      setBusy("");
    }
  }

  function confirmDraftNavigation() {
    return !draftDirty || window.confirm("草稿有未保存修改。离开编辑步骤会丢失这些修改，是否继续？");
  }

  function selectStep(index: number) {
    if (!availableSteps[index]) return;
    if (index !== 2 && !confirmDraftNavigation()) return;
    setActiveStep(index);
  }

  useEffect(() => {
    if (!draftDirty) return;
    function confirmLinkNavigation(event: MouseEvent) {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) return;
      const element = event.target instanceof Element
        ? event.target.closest<HTMLAnchorElement>("a[href]")
        : null;
      if (!element || element.target === "_blank" || element.hasAttribute("download")) return;
      const href = element.getAttribute("href");
      if (!href || href.startsWith("#")) return;
      if (!window.confirm("草稿有未保存修改。离开 Creator 会丢失这些修改，是否继续？")) {
        event.preventDefault();
        event.stopPropagation();
      }
    }
    document.addEventListener("click", confirmLinkNavigation, true);
    return () => document.removeEventListener("click", confirmLinkNavigation, true);
  }, [draftDirty]);

  useEffect(() => {
    if (!draftDirty) return;
    function confirmHistoryNavigation() {
      if (restoringHistory.current) {
        restoringHistory.current = false;
        return;
      }
      if (!window.confirm("草稿有未保存修改。返回上一页会丢失这些修改，是否继续？")) {
        restoringHistory.current = true;
        window.history.go(1);
      }
    }
    window.addEventListener("popstate", confirmHistoryNavigation);
    return () => {
      restoringHistory.current = false;
      window.removeEventListener("popstate", confirmHistoryNavigation);
    };
  }, [draftDirty]);

  const resourceFlow = Boolean(status?.resource_authoring_enabled && session?.authoring_flow === "resource");
  const creatorPackageNeedsRepair = draft?.validation?.creator_quality?.ready === false;
  const steps = resourceFlow ? RESOURCE_STEPS : LEGACY_STEPS;
  const currentStep = steps[activeStep];
  const qualityStatus = session?.quality_status ?? draft?.quality_status ?? "not_evaluated";
  const installState = session?.install_state ?? draft?.install_state ?? "not_installed";
  const evaluationTerminal = Boolean(evaluationRun && ["completed", "failed", "cancelled", "stale"].includes(evaluationRun.status));
  const availableSteps = useMemo(() => resourceFlow ? [
    true,
    true,
    Boolean(session?.resource_build || session?.resource_plan?.state === "confirmed"),
    Boolean(draft),
    Boolean(evaluationRun),
    Boolean(draft && (evaluationTerminal || session?.review_state === "revise" || qualityStatus === "accepted" || qualityStatus === "eval_waived")),
  ] : [
    true,
    true,
    Boolean(draft),
    Boolean(draft),
    Boolean(evaluationRun),
    Boolean(draft && (evaluationTerminal || session?.review_state === "revise" || qualityStatus === "accepted" || qualityStatus === "eval_waived")),
  ], [draft, evaluationRun, evaluationTerminal, qualityStatus, resourceFlow, session?.resource_build, session?.resource_plan?.state, session?.review_state]);

  async function acceptHydratedSession(value: SkillCreatorSession) {
    await hydrate(value, { preserveActiveStep: true });
  }

  const refreshSessionInPlace = useCallback(
    () => loadSession({ background: true, preserveActiveStep: true }),
    [loadSession],
  );

  function evaluationError(caught: unknown, fallback: string) {
    setError("");
    setNotice("");
    handleError(caught, fallback);
  }

  function evaluationNotice(message: string) {
    setError("");
    setNotice(message);
  }

  async function acceptIterationProposal(nextProposal: SkillCreatorProposal, nextSession?: SkillCreatorSession) {
    setProposal(nextProposal);
    if (nextSession) await hydrate({ ...nextSession, proposal: nextProposal });
    else await loadSession();
  }

  return (
    <PageContainer activeResource="skills" hideSidebar maxWidthClassName="max-w-[1540px]">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <Link className="inline-flex items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white" to="/skills/create">
          <ArrowLeft aria-hidden="true" size={16} />
          返回我的 Skill
        </Link>
        {session ? <span className="text-xs text-slate-500">自动保存，可刷新恢复</span> : null}
      </div>

      {statusLoading || loading ? (
        <div aria-label="正在加载 Creator 工作台" className="space-y-4">
          <div className="h-24 animate-pulse rounded-lg bg-white/[0.055] motion-reduce:animate-none" />
          <div className="h-[540px] animate-pulse rounded-lg bg-white/[0.04] motion-reduce:animate-none" />
        </div>
      ) : null}

      {!statusLoading && statusError ? (
        <div className="rounded-lg border border-rose-300/25 bg-rose-300/10 p-5" role="alert">
          <p className="font-semibold text-white">无法确认 Creator 状态</p>
          <p className="mt-2 text-sm text-rose-50">{statusError}</p>
          <button className="mt-4 inline-flex items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white" onClick={() => void reloadStatus()} type="button">
            <RefreshCw aria-hidden="true" size={15} />
            重试
          </button>
        </div>
      ) : null}

      {!statusLoading && status && !status.enabled ? <DisabledStudio /> : null}

      {!statusLoading && status?.enabled && !loading && !session ? (
        <div className="rounded-lg border border-rose-300/25 bg-rose-300/10 p-5" role="alert">
          <h1 className="text-xl font-semibold text-white">Creator 会话不可用</h1>
          <p className="mt-2 text-sm text-rose-50">{error || "找不到该会话，或它已被隔离。"}</p>
          <Link className="mt-4 inline-flex rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white" to="/skills/create">返回会话列表</Link>
        </div>
      ) : null}

      {!statusLoading && status?.enabled && !loading && session ? (
        <>
          <header className="mb-5 flex flex-col gap-4 border-y border-white/10 py-5 sm:flex-row sm:items-end sm:justify-between">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-brand-100">Skill Creator 工作台</p>
              <h1 className="mt-2 max-w-4xl text-2xl font-semibold text-white sm:text-3xl">把你的做法变成可复用的 Skill</h1>
              <p className="mt-2 line-clamp-2 max-w-4xl text-sm leading-6 text-slate-400">{session.intent || "先用一句话说出你希望它完成的任务。"}</p>
            </div>
            <div className="flex shrink-0 items-center gap-2 text-xs">
              <span className="rounded-full bg-white/[0.055] px-3 py-1.5 text-slate-300">{session.mode === "run" ? "运行沉淀" : "从零创建"}</span>
              <span className={`rounded-full px-3 py-1.5 font-semibold ${installState === "current" ? "bg-emerald-300/10 text-emerald-100" : qualityStatus === "accepted" ? "bg-emerald-300/10 text-emerald-100" : qualityStatus === "eval_waived" ? "bg-amber-300/10 text-amber-100" : qualityStatus === "running" ? "bg-brand-300/10 text-brand-100" : "bg-white/[0.055] text-slate-400"}`}>
                {installState === "current" ? "已安装" : qualityStatus === "accepted" ? "可以安装" : qualityStatus === "eval_waived" ? "已人工确认" : qualityStatus === "running" ? "正在试用" : "制作中"}
              </span>
            </div>
          </header>

          <StepRail activeStep={activeStep} availableSteps={availableSteps} onSelect={selectStep} steps={steps} />

          {error ? (
            <div className="mt-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-50" role="alert">{error}</div>
          ) : null}
          {notice ? (
            <div className="mt-4 rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-4 py-3 text-sm text-emerald-50" role="status">{notice}</div>
          ) : null}

          {activeStep === 0 ? (
            <section className="mt-5 rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6" aria-labelledby="creator-intent-heading">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-brand-100">第 1 步 · 从一句话开始</p>
                <h2 className="mt-2 text-xl font-semibold text-white sm:text-2xl" id="creator-intent-heading">你希望这个 Skill 帮你做什么？</h2>
                <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">像向同事交代任务一样描述即可。AI 会补全使用场景、边界和验收方式；信息不足时再向你提问。</p>
              </div>
              {session.experience_candidate_id ? (
                <section className="mt-5 rounded-lg border border-emerald-300/20 bg-emerald-300/[0.055] p-4" aria-label="运行经验来源">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <p className="flex items-center gap-2 text-sm font-semibold text-emerald-100">
                        <Check aria-hidden="true" size={15} />
                        已从可信运行预填
                      </p>
                      <p className="mt-1 text-xs leading-5 text-slate-300">
                        {session.experience_decision === "update" ? "本次结论：更新已有 Creator Skill。" : "本次结论：创建新的 Skill。"}
                        需求、正反例和验收条件来自你已确认的脱敏素材。
                      </p>
                    </div>
                    <span className="w-fit rounded-full bg-white/[0.06] px-3 py-1 text-[11px] font-semibold text-slate-300">
                      {session.source_kind === "xpert_chat" ? "Xpert Chat" : "Workflow"}
                    </span>
                  </div>
                  <details className="mt-3 border-t border-white/10 pt-3 text-xs text-slate-400">
                    <summary className="cursor-pointer font-semibold text-slate-300">查看来源与已确认边界</summary>
                    <div className="mt-3 space-y-2 break-words leading-5">
                      <p><span className="text-slate-500">来源运行：</span>{session.source_task_id || "未知任务"} · {session.source_run_id || "未知 run"}</p>
                      <p><span className="text-slate-500">应使用：</span>{session.positive_examples.slice(0, 3).join("；") || "等待补充"}</p>
                      <p><span className="text-slate-500">不应使用：</span>{session.near_miss_examples.slice(0, 3).join("；") || "等待补充"}</p>
                      <p><span className="text-slate-500">预期结果：</span>{session.expected_output || "等待补充"}</p>
                    </div>
                  </details>
                </section>
              ) : null}
              <div className="mt-5 inline-flex rounded-lg border border-white/10 bg-ink-950/55 p-1" aria-label="需求填写方式" role="group">
                <button aria-pressed={definitionMode === "simple"} className={`min-h-11 rounded-md px-4 text-sm font-semibold ${definitionMode === "simple" ? "bg-brand-200 text-ink-950" : "text-slate-300"}`} onClick={() => setDefinitionMode("simple")} type="button">一句话开始</button>
                <button aria-pressed={definitionMode === "advanced"} className={`min-h-11 rounded-md px-4 text-sm font-semibold ${definitionMode === "advanced" ? "bg-brand-200 text-ink-950" : "text-slate-300"}`} onClick={() => setDefinitionMode("advanced")} type="button">我想详细设置</button>
              </div>
              <label className="mt-5 block" htmlFor="creator-studio-intent">
                <span className="sr-only">一句话描述需求</span>
                <textarea className="min-h-36 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-4 text-base leading-7 text-white placeholder:text-slate-400 focus:border-brand-300/50 focus:outline-none" id="creator-studio-intent" maxLength={2000} onChange={(event) => setIntent(event.target.value)} placeholder="例如：把零散的事故记录整理成清楚、可信的中文复盘，缺少的信息要标出来，不要猜。" value={intent} />
              </label>

              {definitionMode === "advanced" ? <div className="mt-6 grid gap-5 rounded-lg border border-white/10 bg-white/[0.02] p-4 lg:grid-cols-2">
                <label className="block" htmlFor="creator-positive-examples">
                  <span className="text-sm font-semibold text-slate-200">哪些请求应该使用它？</span>
                  <span className="mt-1 block text-xs text-slate-500">每行一个例子。</span>
                  <textarea className="mt-2 min-h-36 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-brand-300/50 focus:outline-none" id="creator-positive-examples" onChange={(event) => setPositiveExamples(event.target.value)} placeholder={"分析这份竞品 PDF 并列出证据页码\n把两个版本的定价差异整理成表格"} value={positiveExamples} />
                </label>
                <label className="block" htmlFor="creator-near-miss-examples">
                  <span className="text-sm font-semibold text-slate-200">哪些相似请求不该使用它？</span>
                  <span className="mt-1 block text-xs text-slate-500">帮助 AI 不要在错误场景出现。</span>
                  <textarea className="mt-2 min-h-36 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-brand-300/50 focus:outline-none" id="creator-near-miss-examples" onChange={(event) => setNearMissExamples(event.target.value)} placeholder="只把 PDF 转成纯文本，不需要竞品分析" value={nearMissExamples} />
                </label>
                <label className="block" htmlFor="creator-expected-output">
                  <span className="text-sm font-semibold text-slate-200">你希望拿到什么结果？</span>
                  <textarea className="mt-2 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-brand-300/50 focus:outline-none" id="creator-expected-output" maxLength={2000} onChange={(event) => setExpectedOutput(event.target.value)} placeholder="说明交付格式、语言、必要字段和证据要求。" value={expectedOutput} />
                </label>
                <label className="block" htmlFor="creator-success-criteria">
                  <span className="text-sm font-semibold text-slate-200">怎样算完成得好？</span>
                  <span className="mt-1 block text-xs text-slate-500">每行写一项最重要的要求。</span>
                  <textarea className="mt-2 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-brand-300/50 focus:outline-none" id="creator-success-criteria" onChange={(event) => setSuccessCriteria(event.target.value)} placeholder={"每项结论包含页码\n价格字段保留币种和计费周期"} value={successCriteria} />
                </label>
              </div> : null}
              <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-5">
                <p className="max-w-xl text-xs leading-5 text-slate-500">一句话模式会补上通用边界与“不得编造”要求；你仍会在下一步确认 AI 的具体方案。</p>
                <button className="inline-flex items-center gap-2 rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={!intentComplete || Boolean(busy)} onClick={() => void saveIntent()} type="button">
                  <ArrowRight aria-hidden="true" size={15} />
                  {busy === "intent" ? "正在保存…" : session.mode === "run" ? "保存需求，查看素材" : "保存需求并继续"}
                </button>
              </div>
            </section>
          ) : null}

          {activeStep === 1 ? (
            <div className="mt-5 space-y-5">
              {session.mode !== "blank" && session.source_kind ? <section className="rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6" aria-labelledby="creator-evidence-heading">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <h2 className="text-xl font-semibold text-white" id="creator-evidence-heading">选择方案可以使用的素材</h2>
                    <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">只保存脱敏摘要和内容哈希。完整对话、工具参数、附件与 Sandbox 文件不会进入 Creator。</p>
                  </div>
                  <span className="w-fit rounded-full bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-300">已选 {sourcePreview ? selectedEvidenceCount : session.selected_evidence.length} 项</span>
                </div>

                {sourcePreview ? (
                  <div className="mt-5">
                    {session.source_kind === "workflow_classic" ? (
                      <div className="mb-4 rounded-md border border-brand-300/20 bg-brand-300/[0.06] px-4 py-3">
                        <p className="text-sm font-semibold text-brand-100">工作流分析不会自动写入方案</p>
                        <p className="mt-1 text-xs leading-5 text-slate-300">请阅读“工作流生成的需求分析”。只有你主动勾选并保存后，它才会成为资源规划依据。</p>
                      </div>
                    ) : null}
                    <div className="grid gap-3 lg:grid-cols-2">
                      {sourcePreview.candidates.map((candidate) => {
                        const label = evidenceLabel(candidate, session.source_kind);
                        const summary = evidenceSummary(candidate);
                        return <label className={`flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition ${selectedEvidence.has(candidate.candidate_id) ? "border-brand-300/35 bg-brand-300/[0.08]" : "border-white/10 bg-white/[0.025] hover:bg-white/[0.045]"}`} key={candidate.candidate_id}>
                          <input checked={selectedEvidence.has(candidate.candidate_id)} className="mt-1 h-4 w-4 accent-cyan-300" onChange={() => toggleEvidence(candidate.candidate_id)} type="checkbox" />
                          <span className="min-w-0">
                            <span className="text-xs font-semibold text-brand-100">{label}</span>
                            {candidate.title && candidate.title !== label ? <span className="mt-1 block text-sm font-semibold text-white">{candidate.title}</span> : null}
                            <span className="mt-2 block whitespace-pre-wrap break-words text-xs leading-5 text-slate-300">{summary}</span>
                            {candidate.kind === "final_output_excerpt" ? <span className="mt-2 block text-[11px] text-amber-100">模型输出默认不选中，请确认内容准确后再勾选。</span> : null}
                          </span>
                        </label>;
                      })}
                    </div>
                    <div className="mt-4 flex justify-end">
                      <button className="rounded-full border border-brand-300/30 bg-brand-300/10 px-4 py-2 text-sm font-semibold text-brand-100 transition hover:bg-brand-300/20 disabled:opacity-50" disabled={busy === "evidence"} onClick={() => void saveEvidence()} type="button">{busy === "evidence" ? "正在保存…" : "保存选中素材并继续"}</button>
                    </div>
                  </div>
                ) : (
                  <button className="mt-5 inline-flex items-center gap-2 rounded-full border border-white/15 px-4 py-2.5 text-sm font-semibold text-slate-100 transition hover:bg-white/[0.06]" disabled={busy === "preview"} onClick={() => void loadSourcePreview()} type="button">
                    <ClipboardCheck aria-hidden="true" size={16} />
                    {busy === "preview" ? "正在生成脱敏预览…" : "读取脱敏素材候选"}
                  </button>
                )}
              </section> : (
                <section className="rounded-lg border border-emerald-300/15 bg-emerald-300/[0.04] px-5 py-4" aria-label="素材状态">
                  <p className="text-sm font-semibold text-emerald-100"><Check aria-hidden="true" className="mr-2 inline" size={15} />从零开始，无需导入历史运行记录</p>
                  <p className="mt-1 text-xs leading-5 text-slate-400">AI 只会使用你刚才填写的需求；如果需要权威资料，会在方案阶段明确询问。</p>
                  {!session.evidence_confirmed ? <button className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-full border border-emerald-200/25 px-4 py-2 text-sm font-semibold text-emerald-100 disabled:opacity-40" disabled={busy === "evidence"} onClick={() => void confirmBlankEvidence()} type="button"><ClipboardCheck aria-hidden="true" size={15} />{busy === "evidence" ? "正在确认…" : "确认并继续"}</button> : null}
                </section>
              )}

              {status.resource_authoring_enabled ? (
                <SkillResourcePlanPanel
                  onPlanConfirmed={() => setActiveStep(2)}
                  onSession={acceptHydratedSession}
                  session={session}
                  status={status}
                />
              ) : null}

              {!resourceFlow && proposal?.status !== "pending" ? (
                <section className={`grid gap-5 ${draft ? "lg:grid-cols-1" : "lg:grid-cols-2"}`}>
                  <div className="rounded-lg border border-brand-300/20 bg-surface-900/80 p-5">
                    <div className="flex items-center gap-3">
                      <Sparkles aria-hidden="true" className="text-brand-100" size={20} />
                      <h2 className="text-base font-semibold text-white">{draft ? "AI 生成可评测更新初稿" : "AI 生成可评测初稿"}</h2>
                    </div>
                    <p className="mt-3 text-sm leading-6 text-slate-400">
                      {draft
                        ? "固定 Creator Agent 读取当前不可变草稿，提交具备完整工作流、输出约定和失败处理的更新提案。你仍需检查文件差异并批准。"
                        : "固定 Creator Agent 根据六项已确认信息生成具备完整工作流、输出约定和失败处理的提案。生成结果仍需人工审阅与三例行为评测。"}
                    </p>
                    {!status.model_available ? <p className="mt-3 rounded-md bg-amber-300/[0.08] p-3 text-xs leading-5 text-amber-100">{status.model_unavailable_reason || "当前未配置模型网关 Key，AI 生成已禁用。结构化手工模板仍可使用。"}</p> : null}
                    <GenerationReadiness items={generationReadiness} />
                    <button className="mt-5 inline-flex items-center gap-2 rounded-full bg-brand-200 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={!status.model_available || !generationReady || Boolean(busy)} onClick={() => void generateProposal()} type="button">
                      {busy === "generate" ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={16} /> : <Sparkles aria-hidden="true" size={16} />}
                      {busy === "generate" ? "正在生成…" : draft ? "生成可评测更新初稿" : proposal ? "重新生成可评测初稿" : "生成可评测初稿"}
                    </button>
                  </div>

                  {!draft ? <div className="rounded-lg border border-white/10 bg-white/[0.035] p-5">
                    <div className="flex items-center gap-3">
                      <FileEdit aria-hidden="true" className="text-hire-200" size={20} />
                      <h2 className="text-base font-semibold text-white">结构化手工模板</h2>
                    </div>
                    <p className="mt-3 text-sm leading-6 text-slate-400">不调用模型，只创建带待办段落的编辑模板。模板不代表初稿完整度通过，也不能绕过行为评测质量门。</p>
                    <label className="mt-4 block" htmlFor="creator-root-name">
                      <span className="text-xs font-semibold text-slate-300">Skill ID</span>
                      <input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/75 px-3 py-2.5 font-mono text-sm text-white placeholder:text-slate-500 focus:border-hire-300/50 focus:outline-none" id="creator-root-name" maxLength={64} onChange={(event) => setRootName(event.target.value.toLowerCase())} placeholder="compare-competitor-pdf" value={rootName} />
                    </label>
                    {rootName && !validRootName ? <p className="mt-2 text-xs text-rose-200">仅允许小写字母、数字和单个连字符，长度不超过 64 位。</p> : null}
                    <label className="mt-4 block" htmlFor="creator-manual-description">
                      <span className="text-xs font-semibold text-slate-300">能力、触发场景与不适用边界</span>
                      <span className="mt-1 block text-xs leading-5 text-slate-500">独立描述该 Skill 做什么、用户在何种请求下应使用，以及哪些相似任务不应触发。不会自动复用“用途”文本。</span>
                      <textarea aria-label="能力、触发场景与不适用边界" className="mt-2 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-3 py-2.5 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-hire-300/50 focus:outline-none" id="creator-manual-description" maxLength={1024} onChange={(event) => setManualDescription(event.target.value)} placeholder="比较多份竞品 PDF，提取带页码的可核验证据并生成中文对比表。用于用户要求竞品分析、证据页码或版本差异时；不用于仅做 PDF 转文本或版式转换。" value={manualDescription} />
                    </label>
                    <button className="mt-4 inline-flex items-center gap-2 rounded-full border border-hire-300/30 bg-hire-300/10 px-5 py-2.5 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/20 disabled:cursor-not-allowed disabled:opacity-40" disabled={!validRootName || !validManualDescription || !session.evidence_confirmed || Boolean(busy)} onClick={() => void createBlankDraft()} type="button">
                      <ArrowRight aria-hidden="true" size={16} />
                      {busy === "blank" ? "正在创建…" : "创建结构化手工模板"}
                    </button>
                  </div> : null}
                </section>
              ) : null}

              {proposal ? (
                <SkillProposalReview
                  approving={busy === "approve"}
                  baseDraft={draft}
                  onApprove={approveProposal}
                  onReject={rejectProposal}
                  proposal={proposal}
                  rejecting={busy === "reject"}
                />
              ) : null}
              {draft ? (
                <div className="flex justify-end">
                  <button className="inline-flex items-center gap-2 rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200" onClick={() => setActiveStep(2)} type="button">编辑草稿 <ArrowRight aria-hidden="true" size={16} /></button>
                </div>
              ) : null}
            </div>
          ) : null}

          {activeStep === 2 && resourceFlow ? (
            <div className="mt-5 space-y-5">
              {proposal?.status !== "pending" && creatorPackageNeedsRepair && draft ? (
                <section className="space-y-4 rounded-lg border border-rose-300/25 bg-rose-300/[0.055] p-5 sm:p-6" aria-labelledby="creator-package-repair-heading">
                  <div>
                    <h2 className="text-lg font-semibold text-white" id="creator-package-repair-heading">修复最终说明</h2>
                    <p className="mt-2 text-sm leading-6 text-rose-50/80">只需修改 SKILL.md 中提示的章节；已确认的 references、scripts 和 assets 会保持不变。保存后服务端会重新执行同一套评测与安装前检查。</p>
                  </div>
                  <SkillPackageEditor
                    conflictMessage={conflictMessage}
                    draft={draft}
                    errorIssues={errorIssues.length ? errorIssues : draft.validation?.creator_quality?.issues ?? []}
                    onCopyAsNew={copyAsNew}
                    onDirtyChange={setDraftDirty}
                    onReload={loadSession}
                    onSave={saveDraft}
                    saving={busy === "save-draft"}
                  />
                </section>
              ) : proposal?.status !== "pending" ? (
                <SkillResourceBuildPanel
                  onProposal={async (nextProposal) => {
                    setProposal(nextProposal);
                    setNotice("最终资源包已形成标准提案，请检查全包差异后批准写入草稿。");
                  }}
                  onSessionRefresh={refreshSessionInPlace}
                  session={session}
                  status={status}
                />
              ) : null}
              {proposal?.status === "pending" ? (
                <SkillProposalReview
                  approving={busy === "approve"}
                  baseDraft={draft}
                  onApprove={approveProposal}
                  onReject={rejectProposal}
                  proposal={proposal}
                  rejecting={busy === "reject"}
                />
              ) : null}
            </div>
          ) : null}

          {activeStep === 2 && !resourceFlow && draft ? (
            <div className="mt-5">
              {proposal?.status !== "pending" ? (
                <section className="mb-4 rounded-lg border border-brand-300/20 bg-brand-300/[0.06] p-4" aria-labelledby="creator-update-proposal-heading">
                  <div className="min-w-0">
                    <h2 className="text-sm font-semibold text-white" id="creator-update-proposal-heading">AI 生成可评测更新初稿</h2>
                    <p className="mt-1 text-xs leading-5 text-slate-300">
                      {draftDirty
                        ? "请先保存当前修改。生成助手只读取已保存的不可变草稿版本。"
                        : !status.model_available
                          ? status.model_unavailable_reason || "当前未配置模型网关 Key，无法生成更新提案。"
                          : `将基于 revision ${draft.revision} 生成完整度受检的更新提案，生成后进入文件差异审阅。`}
                    </p>
                  </div>
                  <GenerationReadiness items={generationReadiness} />
                  <div className="mt-4 flex justify-end">
                    <button
                      className="inline-flex shrink-0 items-center justify-center gap-2 rounded-full bg-brand-200 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                      disabled={draftDirty || !status.model_available || !generationReady || Boolean(busy)}
                      onClick={() => void generateProposal()}
                      type="button"
                    >
                      {busy === "generate" ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={16} /> : <Sparkles aria-hidden="true" size={16} />}
                      {busy === "generate" ? "正在生成…" : "生成可评测更新初稿"}
                    </button>
                  </div>
                </section>
              ) : null}
              <SkillPackageEditor
                conflictMessage={conflictMessage}
                draft={draft}
                errorIssues={errorIssues}
                onCopyAsNew={copyAsNew}
                onDirtyChange={setDraftDirty}
                onReload={loadSession}
                onSave={saveDraft}
                saving={busy === "save-draft"}
              />
            </div>
          ) : null}

          {activeStep === 3 && draft ? (
            <SkillEvaluationDesigner
              draft={draft}
              onError={evaluationError}
              onNotice={evaluationNotice}
              onRepairPackage={() => {
                setError("");
                setErrorIssues([]);
                setActiveStep(2);
              }}
              onRunStarted={(run) => {
                setEvaluationRun(run);
                setActiveStep(4);
              }}
              onSessionChange={acceptHydratedSession}
              session={session}
              suiteEnabled={status.evaluation_suite_enabled === true}
            />
          ) : null}

          {activeStep === 4 && draft && evaluationRun ? (
            <SkillEvaluationReview
              draft={draft}
              onError={evaluationError}
              onNotice={evaluationNotice}
              onRunChange={setEvaluationRun}
              onSessionRefresh={refreshSessionInPlace}
              run={evaluationRun}
              session={session}
            />
          ) : null}

          {activeStep === 4 && draft && !evaluationRun ? (
            <section className="mt-5 rounded-lg border border-amber-300/20 bg-amber-300/[0.06] p-6 text-center">
              <ShieldCheck aria-hidden="true" className="mx-auto text-amber-100" size={26} />
              <h2 className="mt-4 text-lg font-semibold text-white">评测记录暂不可用</h2>
              <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-amber-50/80">会话声明了评测阶段，但服务端没有返回完整 run。请刷新恢复；系统不会静默显示不完整结果。</p>
              <button className="mt-4 inline-flex items-center gap-2 rounded-md border border-amber-200/25 px-4 py-2 text-sm font-semibold text-amber-50" onClick={() => void loadSession()} type="button"><RefreshCw aria-hidden="true" size={14} />重新读取会话</button>
            </section>
          ) : null}

          {activeStep === 5 && draft ? (
            <>
              {proposal?.status === "pending" ? (
                <div className="mt-5">
                  <SkillProposalReview
                    approving={busy === "approve"}
                    baseDraft={draft}
                    onApprove={approveProposal}
                    onReject={rejectProposal}
                    proposal={proposal}
                    rejecting={busy === "reject"}
                  />
                </div>
              ) : null}
              <SkillCreatorFinish
                draft={draft}
                onError={evaluationError}
                onNotice={evaluationNotice}
                onProposal={acceptIterationProposal}
                onReload={refreshSessionInPlace}
                onGoToBuild={() => setActiveStep(2)}
                proposal={proposal}
                run={evaluationRun}
                session={session}
              />
            </>
          ) : null}
        </>
      ) : null}
    </PageContainer>
  );
}
