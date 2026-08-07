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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import SkillPackageEditor from "../components/skill-creator/SkillPackageEditor";
import SkillProposalReview from "../components/skill-creator/SkillProposalReview";
import { useSkillCreatorStatus } from "../hooks/useSkillCreatorStatus";
import {
  approveSkillCreatorProposal,
  copySkillCreatorSession,
  createBlankSkillCreatorDraft,
  generateSkillCreatorProposal,
  previewSkillCreatorSource,
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
  type SkillPackageIssue,
  type SkillPackagePayload,
} from "../utils/skillCreatorApi";

const STEPS = [
  { title: "定义用途", detail: "触发条件与成功标准", icon: Lightbulb },
  { title: "确认素材", detail: "选择证据并生成草稿", icon: ClipboardCheck },
  { title: "编辑草稿", detail: "文件、规范与安全", icon: FileEdit },
  { title: "设计测试", detail: "PR3 开放", icon: FlaskConical, locked: true },
  { title: "评审结果", detail: "PR3 开放", icon: ShieldCheck, locked: true },
  { title: "迭代与安装", detail: "PR3 开放", icon: Sparkles, locked: true },
] as const;

const EVIDENCE_LABELS: Record<SkillCreatorEvidenceCandidate["kind"], string> = {
  intent_summary: "目标摘要",
  successful_steps: "成功步骤",
  tool_names: "工具名称",
  user_correction: "用户修正",
  io_shape: "输入输出结构",
  final_output_excerpt: "最终输出片段",
};

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
  hasDraft,
  onSelect,
}: {
  activeStep: number;
  hasDraft: boolean;
  onSelect: (index: number) => void;
}) {
  return (
    <nav aria-label="Skill Creator 阶段" className="overflow-x-auto pb-2">
      <ol className="grid min-w-[850px] grid-cols-6 gap-2">
        {STEPS.map((step, index) => {
          const Icon = step.icon;
          const inaccessible =
            ("locked" in step && Boolean(step.locked)) ||
            (index === 2 && !hasDraft);
          const current = activeStep === index;
          return (
            <li key={step.title}>
              <button
                aria-label={`第 ${index + 1} 步：${step.title}`}
                aria-current={current ? "step" : undefined}
                className={`flex min-h-20 w-full items-start gap-3 rounded-lg border px-3 py-3 text-left transition ${
                  current
                    ? "border-hire-300/50 bg-hire-300/10 text-white"
                    : inaccessible
                      ? "border-white/[0.06] bg-white/[0.025] text-slate-600"
                      : "border-white/10 bg-surface-900/70 text-slate-300 hover:bg-white/[0.055]"
                }`}
                disabled={inaccessible}
                onClick={() => onSelect(index)}
                type="button"
              >
                <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${current ? "bg-hire-300 text-ink-950" : "bg-white/[0.055]"}`}>
                  {inaccessible ? <LockKeyhole aria-hidden="true" size={13} /> : <Icon aria-hidden="true" size={14} />}
                </span>
                <span className="min-w-0">
                  <span className="block text-xs font-semibold">{index + 1}. {step.title}</span>
                  <span className="mt-1 block text-[11px] leading-4 opacity-75">{step.detail}</span>
                </span>
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
  const [sourcePreview, setSourcePreview] = useState<SkillCreatorSourcePreview | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<Set<string>>(new Set());
  const [activeStep, setActiveStep] = useState(0);
  const [intent, setIntent] = useState("");
  const [positiveExamples, setPositiveExamples] = useState("");
  const [nearMissExamples, setNearMissExamples] = useState("");
  const [expectedOutput, setExpectedOutput] = useState("");
  const [successCriteria, setSuccessCriteria] = useState("");
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

  const syncSessionForm = useCallback((value: SkillCreatorSession) => {
    setIntent(value.intent ?? "");
    setPositiveExamples(joinLines(value.positive_examples ?? []));
    setNearMissExamples(joinLines(value.near_miss_examples ?? []));
    setExpectedOutput(value.expected_output ?? "");
    setSuccessCriteria(joinLines(value.success_criteria ?? []));
  }, []);

  const hydrate = useCallback(async (value: SkillCreatorSession) => {
    let hydratedDraft = value.draft ?? null;
    let hydratedProposal = value.proposal ?? null;
    if (!hydratedDraft && value.draft_id) {
      hydratedDraft = await readSkillCreatorDraft(value.draft_id);
    }
    if (!hydratedProposal && value.proposal_id) {
      hydratedProposal = await readSkillCreatorProposal(value.proposal_id);
    }
    setSession({ ...value, draft: hydratedDraft, proposal: hydratedProposal });
    setDraft(hydratedDraft);
    setProposal(hydratedProposal);
    syncSessionForm(value);
    if (hydratedProposal?.status === "pending") setActiveStep(1);
    else if (hydratedDraft) setActiveStep((current) => Math.max(current, 2));
  }, [syncSessionForm]);

  const loadSession = useCallback(async () => {
    if (!sessionId || !status?.enabled) return;
    setLoading(true);
    setError("");
    try {
      await hydrate(await readSkillCreatorSession(sessionId));
    } catch (caught) {
      setError(caught instanceof SkillCreatorApiError ? caught.message : "Creator 会话加载失败。");
    } finally {
      setLoading(false);
    }
  }, [hydrate, sessionId, status?.enabled]);

  useEffect(() => {
    if (status?.enabled) void loadSession();
    else if (status && !status.enabled) setLoading(false);
  }, [loadSession, status]);

  const intentComplete = Boolean(intent.trim() && positiveExamples.trim() && expectedOutput.trim() && successCriteria.trim());
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

  async function saveIntent() {
    if (!session) return;
    setBusy("intent");
    setError("");
    setNotice("");
    try {
      const updated = await updateSkillCreatorSession(session.session_id, {
        expected_session_revision: session.session_revision,
        intent: intent.trim(),
        positive_examples: splitLines(positiveExamples),
        near_miss_examples: splitLines(nearMissExamples),
        expected_output: expectedOutput.trim(),
        success_criteria: splitLines(successCriteria),
      });
      await hydrate(updated);
      setActiveStep(1);
      setNotice("用途与示例已保存。下一步确认素材并生成草稿。");
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
      setSourcePreview(preview);
      const availableIds = new Set(preview.candidates.map((item) => item.candidate_id));
      const persistedIds = session.selected_evidence
        .map((item) => item.candidate_id)
        .filter((candidateId) => availableIds.has(candidateId));
      setSelectedEvidence(new Set(
        session.evidence_confirmed || session.selected_evidence.length > 0
          ? persistedIds
          : preview.candidates
              .filter((item) => item.default_selected)
              .map((item) => item.candidate_id),
      ));
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
      setActiveStep(2);
      setNotice("提案已写入不可变草稿版本。该草稿仍需 PR3 评测后才能安装。");
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

  const currentStep = useMemo(() => STEPS[activeStep], [activeStep]);

  return (
    <PageContainer activeResource="skills" hideSidebar maxWidthClassName="max-w-[1540px]">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <Link className="inline-flex items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white" to="/skills/create">
          <ArrowLeft aria-hidden="true" size={16} />
          Creator 会话
        </Link>
        {session ? (
          <span className="max-w-full truncate rounded-full border border-white/10 bg-white/[0.045] px-3 py-1.5 font-mono text-xs text-slate-400">
            {session.session_id} · r{session.session_revision}
          </span>
        ) : null}
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
              <h1 className="mt-2 max-w-4xl text-2xl font-semibold text-white sm:text-3xl">
                {session.intent || "未命名 Skill 会话"}
              </h1>
              <p className="mt-2 text-sm text-slate-400">当前阶段：{currentStep.title}。所有写入均绑定 revision 与内容摘要。</p>
            </div>
            <div className="flex shrink-0 items-center gap-2 text-xs">
              <span className="rounded-full bg-white/[0.055] px-3 py-1.5 text-slate-300">{session.mode === "run" ? "运行沉淀" : "从零创建"}</span>
              <span className="rounded-full bg-amber-300/10 px-3 py-1.5 font-semibold text-amber-100">不可安装</span>
            </div>
          </header>

          <StepRail activeStep={activeStep} hasDraft={Boolean(draft)} onSelect={selectStep} />

          {error ? (
            <div className="mt-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-50" role="alert">{error}</div>
          ) : null}
          {notice ? (
            <div className="mt-4 rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-4 py-3 text-sm text-emerald-50" role="status">{notice}</div>
          ) : null}

          {activeStep === 0 ? (
            <section className="mt-5 rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6" aria-labelledby="creator-intent-heading">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h2 className="text-xl font-semibold text-white" id="creator-intent-heading">定义用途与边界</h2>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">用真实任务描述 Skill 应在何时触发、哪些相似请求不应触发，以及什么结果才算成功。</p>
                </div>
                <span className={`w-fit rounded-full px-3 py-1.5 text-xs font-semibold ${intentComplete ? "bg-emerald-300/10 text-emerald-100" : "bg-amber-300/10 text-amber-100"}`}>
                  {intentComplete ? "定义完整" : "需要补充"}
                </span>
              </div>
              <div className="mt-6 grid gap-5 lg:grid-cols-2">
                <label className="block lg:col-span-2" htmlFor="creator-studio-intent">
                  <span className="text-sm font-semibold text-slate-200">用途与触发条件</span>
                  <textarea className="mt-2 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-brand-300/50 focus:outline-none" id="creator-studio-intent" maxLength={2000} onChange={(event) => setIntent(event.target.value)} placeholder="这个 Skill 解决什么任务？用户通常会怎样提出需求？" value={intent} />
                </label>
                <label className="block" htmlFor="creator-positive-examples">
                  <span className="text-sm font-semibold text-slate-200">正向示例</span>
                  <span className="mt-1 block text-xs text-slate-500">每行一个真实需求。</span>
                  <textarea className="mt-2 min-h-36 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-brand-300/50 focus:outline-none" id="creator-positive-examples" onChange={(event) => setPositiveExamples(event.target.value)} placeholder={"分析这份竞品 PDF 并列出证据页码\n把两个版本的定价差异整理成表格"} value={positiveExamples} />
                </label>
                <label className="block" htmlFor="creator-near-miss-examples">
                  <span className="text-sm font-semibold text-slate-200">近似反例</span>
                  <span className="mt-1 block text-xs text-slate-500">每行一个不应触发的相似请求，AI 生成前必须填写。</span>
                  <textarea className="mt-2 min-h-36 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-brand-300/50 focus:outline-none" id="creator-near-miss-examples" onChange={(event) => setNearMissExamples(event.target.value)} placeholder="只把 PDF 转成纯文本，不需要竞品分析" value={nearMissExamples} />
                </label>
                <label className="block" htmlFor="creator-expected-output">
                  <span className="text-sm font-semibold text-slate-200">预期输出</span>
                  <textarea className="mt-2 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-brand-300/50 focus:outline-none" id="creator-expected-output" maxLength={2000} onChange={(event) => setExpectedOutput(event.target.value)} placeholder="说明交付格式、语言、必要字段和证据要求。" value={expectedOutput} />
                </label>
                <label className="block" htmlFor="creator-success-criteria">
                  <span className="text-sm font-semibold text-slate-200">成功标准</span>
                  <span className="mt-1 block text-xs text-slate-500">每行一项可检查标准。</span>
                  <textarea className="mt-2 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 focus:border-brand-300/50 focus:outline-none" id="creator-success-criteria" onChange={(event) => setSuccessCriteria(event.target.value)} placeholder={"每项结论包含页码\n价格字段保留币种和计费周期"} value={successCriteria} />
                </label>
              </div>
              <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-5">
                <p className="text-xs text-slate-500">可先保存四项基础定义；AI 生成还要求近似反例与素材确认齐备。</p>
                <button className="inline-flex items-center gap-2 rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={!intentComplete || Boolean(busy)} onClick={() => void saveIntent()} type="button">
                  <Save aria-hidden="true" size={15} />
                  {busy === "intent" ? "正在保存…" : "保存并确认素材"}
                </button>
              </div>
            </section>
          ) : null}

          {activeStep === 1 ? (
            <div className="mt-5 space-y-5">
              <section className="rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6" aria-labelledby="creator-evidence-heading">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <h2 className="text-xl font-semibold text-white" id="creator-evidence-heading">确认用于草稿的素材</h2>
                    <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">只保存服务端生成的脱敏摘要和内容哈希。完整对话、工具参数、附件与 Sandbox 文件不会进入 Creator。</p>
                  </div>
                  <span className="w-fit rounded-full bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-300">已选 {session.selected_evidence.length} 项</span>
                </div>

                {session.mode === "blank" || !session.source_kind ? (
                  <div className="mt-5 rounded-lg border border-dashed border-white/15 px-5 py-7 text-center">
                    <p className="text-sm font-semibold text-white">本会话从零创建</p>
                    <p className="mt-2 text-sm text-slate-400">没有运行记录需要导入，生成助手将只使用第一步的用途、示例和成功标准。</p>
                    {session.evidence_confirmed ? (
                      <p className="mt-4 text-sm font-semibold text-emerald-100">已确认无需运行素材</p>
                    ) : (
                      <button className="mt-5 inline-flex items-center gap-2 rounded-full border border-brand-300/30 bg-brand-300/10 px-4 py-2.5 text-sm font-semibold text-brand-100 transition hover:bg-brand-300/20 disabled:cursor-wait disabled:opacity-50" disabled={busy === "evidence"} onClick={() => void confirmBlankEvidence()} type="button">
                        <ClipboardCheck aria-hidden="true" size={16} />
                        {busy === "evidence" ? "正在确认…" : "确认无需运行素材"}
                      </button>
                    )}
                  </div>
                ) : sourcePreview ? (
                  <div className="mt-5">
                    <div className="grid gap-3 lg:grid-cols-2">
                      {sourcePreview.candidates.map((candidate) => (
                        <label className={`flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition ${selectedEvidence.has(candidate.candidate_id) ? "border-brand-300/35 bg-brand-300/[0.08]" : "border-white/10 bg-white/[0.025] hover:bg-white/[0.045]"}`} key={candidate.candidate_id}>
                          <input checked={selectedEvidence.has(candidate.candidate_id)} className="mt-1 h-4 w-4 accent-cyan-300" onChange={() => toggleEvidence(candidate.candidate_id)} type="checkbox" />
                          <span className="min-w-0">
                            <span className="text-xs font-semibold text-brand-100">{EVIDENCE_LABELS[candidate.kind]}</span>
                            <span className="mt-1 block text-sm font-semibold text-white">{candidate.title}</span>
                            <span className="mt-2 block text-xs leading-5 text-slate-400">{candidate.summary}</span>
                            {candidate.kind === "final_output_excerpt" ? <span className="mt-2 block text-[11px] text-amber-100">输出片段默认不选中，请逐项确认。</span> : null}
                          </span>
                        </label>
                      ))}
                    </div>
                    <div className="mt-4 flex justify-end">
                      <button className="rounded-full border border-brand-300/30 bg-brand-300/10 px-4 py-2 text-sm font-semibold text-brand-100 transition hover:bg-brand-300/20 disabled:opacity-50" disabled={busy === "evidence"} onClick={() => void saveEvidence()} type="button">{busy === "evidence" ? "正在保存…" : `保存 ${selectedEvidenceCount} 项素材`}</button>
                    </div>
                  </div>
                ) : (
                  <button className="mt-5 inline-flex items-center gap-2 rounded-full border border-white/15 px-4 py-2.5 text-sm font-semibold text-slate-100 transition hover:bg-white/[0.06]" disabled={busy === "preview"} onClick={() => void loadSourcePreview()} type="button">
                    <ClipboardCheck aria-hidden="true" size={16} />
                    {busy === "preview" ? "正在生成脱敏预览…" : "读取脱敏素材候选"}
                  </button>
                )}
              </section>

              {proposal?.status !== "pending" ? (
                <section className={`grid gap-5 ${draft ? "lg:grid-cols-1" : "lg:grid-cols-2"}`}>
                  <div className="rounded-lg border border-brand-300/20 bg-surface-900/80 p-5">
                    <div className="flex items-center gap-3">
                      <Sparkles aria-hidden="true" className="text-brand-100" size={20} />
                      <h2 className="text-base font-semibold text-white">{draft ? "AI 生成可评测更新初稿" : "AI 生成可评测初稿"}</h2>
                    </div>
                    <p className="mt-3 text-sm leading-6 text-slate-400">
                      {draft
                        ? "固定 Creator Agent 读取当前不可变草稿，提交具备完整工作流、输出约定和失败处理的更新提案。你仍需检查文件差异并批准。"
                        : "固定 Creator Agent 根据六项已确认信息生成具备完整工作流、输出约定和失败处理的提案。生成结果仍需人工审阅与 PR3 行为评测。"}
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
                    <p className="mt-3 text-sm leading-6 text-slate-400">不调用模型，只创建带待办段落的编辑模板。模板不代表初稿完整度通过，也不能绕过 PR3 行为评测。</p>
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

          {activeStep === 2 && draft ? (
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

          {activeStep >= 3 ? (
            <section className="mt-5 rounded-lg border border-dashed border-white/15 bg-white/[0.025] px-6 py-14 text-center">
              <LockKeyhole aria-hidden="true" className="mx-auto text-slate-500" size={28} />
              <h2 className="mt-4 text-xl font-semibold text-white">{currentStep.title}将在 PR3 开放</h2>
              <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-slate-400">隔离对照评测、人工反馈、迭代和正式安装需要完整质量门。本轮草稿不会提供绕过入口。</p>
            </section>
          ) : null}
        </>
      ) : null}
    </PageContainer>
  );
}
