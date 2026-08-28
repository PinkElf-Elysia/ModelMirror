import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import KnowledgeBenchmarkGenerator from "../components/evaluations/KnowledgeBenchmarkGenerator";

interface KnowledgeBase {
  id: string;
  name: string;
  origin?: string;
  catalog_ref?: Record<string, unknown>;
  corpus_locked?: boolean;
  provisioning_status?: string;
}

interface RagDocument {
  id: string;
  filename: string;
}

interface PipelineVersion {
  version_id: string;
  version: number;
  status: string;
  active: boolean;
  chunk_count: number;
  created_at: number;
}

interface ExpectedReference {
  reference_id?: string;
  document_id: string;
  chunk_id?: string | null;
  source_block_id?: string | null;
  page_number?: number | null;
  relevance: number;
  document_name?: string;
  match_mode?: "document" | "source_block" | "chunk" | null;
  catalog_anchor_key?: string | null;
  source_block_hash?: string | null;
  anchor_start?: number | null;
  anchor_end?: number | null;
  anchor_hash?: string | null;
}

type BenchmarkRole = "unclassified" | "regression_guard" | "strategy_tuning" | "threshold_calibration" | "held_out_qualification" | "promotion_evidence";

interface EvaluationCase {
  case_id: string;
  query: string;
  expected_refs: ExpectedReference[];
  expected_no_result?: boolean;
  review_status?: "not_required" | "pending" | "approved" | "rejected";
  review_evidence?: Record<string, unknown>;
  targeting?: Record<string, unknown>;
  tags: string[];
  notes: string;
}

interface EvaluationSet {
  eval_set_id: string;
  kb_id: string;
  name: string;
  description: string;
  revision: number;
  status: string;
  cases: EvaluationCase[];
  updated_at: number;
  origin?: string;
  catalog_ref?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
  coverage?: Record<string, unknown>;
  calibration?: Record<string, unknown>;
  benchmark_role?: BenchmarkRole;
  latest_version?: number | null;
}

interface EvaluationSetVersion extends EvaluationSet {
  version_id: string;
  version: number;
  source_revision: number;
  checksum: string;
  published_at: number;
  benchmark_contract_version?: "rag-gold-v1" | "rag-gold-v2" | "rag-gold-v3";
  qualification_manifest?: Record<string, unknown>;
}

interface CaseEvidenceBundle {
  evidence: Array<Record<string, unknown>>;
  full_corpus_verification?: {
    completed?: boolean;
    method?: string;
    corpus_snapshot_checksum?: string;
    scanned_document_count?: number;
    scanned_source_block_count?: number;
    top_match_count?: number;
  } | null;
  verification_evidence: Array<Record<string, unknown>>;
}

interface GatePolicy {
  kb_id: string;
  mode: "advisory" | "required";
  min_recall_at_5: number;
  max_mrr_regression: number;
  max_citation_hit_regression: number;
  max_citation_precision_at_5_regression: number;
  max_no_result_increase: number;
  min_no_result_accuracy: number;
  min_citation_coverage: number;
  max_p95_latency_ratio: number;
  max_p95_latency_ms: number;
  max_paired_primary_regression: number;
  paired_confidence_level: number;
  require_comparable_corpus: boolean;
  require_zero_errors: boolean;
}

interface RankingItem {
  rank: number;
  chunk_id: string;
  document_id: string;
  document_name: string;
  relevance: number;
  matched_reference_id?: string | null;
  score?: number | null;
}

interface CaseResult {
  case_id: string;
  query_preview: string;
  status: string;
  metrics: Record<string, number>;
  ranking: RankingItem[];
  latency_ms: number;
  error?: string | null;
}

interface TargetResult {
  target_id: string;
  version_id: string;
  version: number;
  label: string;
  metrics: Record<string, number>;
  case_results: CaseResult[];
  promotion_gate: {
    passed: boolean;
    mode: string;
    checks: Array<{
      id: string;
      passed: boolean;
      actual?: number | boolean | string | null;
      threshold?: number | boolean | string | null;
      required?: boolean;
      status?: string;
      reason_codes?: string[];
      message: string;
    }>;
  };
  execution_integrity?: {
    status?: string;
    qualified?: boolean;
    reason_codes?: string[];
  };
}

interface EvaluationRun {
  run_id: string;
  status: string;
  progress: number;
  eval_set_id: string;
  eval_set_version?: number | null;
  baseline_version_id?: string | null;
  run_mode?: "diagnostic" | "formal";
  metric_contract_version?: string;
  comparability?: { comparable?: boolean; same_corpus?: boolean; reason?: string | null };
  paired_confidence?: Record<string, unknown>;
  reproducibility_status?: "current" | "orphaned" | "unreproducible";
  reproducibility_reasons?: string[];
  target_results: TargetResult[];
  evidence_qualification?: {
    status: "qualified" | "diagnostic_only";
    qualified: boolean;
    counts?: {
      total?: number;
      positive?: number;
      stable_source_block_positive?: number;
      reviewed_hard_negative?: number;
    };
  };
  created_at: number;
  error?: string | null;
}

interface PreviewSource {
  chunk_id: string;
  doc_id: string;
  source_document_id?: string | null;
  document_name: string;
  score: number;
  source_block_id?: string | null;
  page_number?: number | null;
  text?: string;
}

function errorMessage(value: unknown, fallback: string) {
  if (value && typeof value === "object" && "detail" in value) return String(value.detail);
  return fallback;
}

function metric(value: number | undefined, percent = true) {
  if (value == null || Number.isNaN(value)) return "-";
  return percent ? `${(value * 100).toFixed(1)}%` : value.toFixed(3);
}

function timestamp(value: number) {
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

function leakageWarningThreshold(item: EvaluationCase) {
  const warning = item.targeting?.leakage_warning;
  if (!warning || typeof warning !== "object") return null;
  const threshold = Number((warning as { threshold?: unknown }).threshold);
  return Number.isFinite(threshold) ? threshold : null;
}

export function isEvaluationPromotionReady(
  run: {
    status: string;
    run_mode?: "diagnostic" | "formal";
    reproducibility_status?: "current" | "orphaned" | "unreproducible";
  } | null,
  target: {
    promotion_gate: { passed: boolean };
    execution_integrity?: { qualified?: boolean };
  },
) {
  return Boolean(
    run?.status === "succeeded"
      && run.run_mode === "formal"
      && run.reproducibility_status === "current"
      && target.execution_integrity?.qualified === true
      && target.promotion_gate.passed,
  );
}

export function isFormalEvaluationVersionEligible(
  version: Pick<EvaluationSetVersion, "benchmark_contract_version" | "benchmark_role" | "qualification_manifest"> | null,
) {
  const manifest = version?.qualification_manifest;
  const tunerUsage = manifest && Array.isArray(manifest.tuner_usage_lineage)
    ? manifest.tuner_usage_lineage
    : null;
  return Boolean(
    version?.benchmark_contract_version === "rag-gold-v3"
      && version.benchmark_role === "held_out_qualification"
      && manifest?.status === "qualified"
      && manifest?.dataset_role === "held_out_qualification"
      && tunerUsage?.length === 0,
  );
}

function benchmarkRoleLabel(role?: BenchmarkRole) {
  return ({
    unclassified: "未分类诊断",
    regression_guard: "回归守卫",
    strategy_tuning: "候选选择",
    threshold_calibration: "阈值校准",
    held_out_qualification: "锁定晋级集",
    promotion_evidence: "旧晋级证据",
  } as Record<BenchmarkRole, string>)[role || "unclassified"];
}

export function isCaseReviewEvidenceReady(
  item: Pick<EvaluationCase, "expected_no_result">,
  bundle: Pick<CaseEvidenceBundle, "evidence" | "full_corpus_verification"> | undefined,
) {
  if (!bundle?.evidence.length) return false;
  return !item.expected_no_result || bundle.full_corpus_verification?.completed === true;
}

function CaseEvidenceReview({ item, bundle }: { item: EvaluationCase; bundle: CaseEvidenceBundle }) {
  const verification = bundle.full_corpus_verification;
  return (
    <div className="mt-2 ml-7 space-y-3 border-l border-cyan-300/20 pl-3">
      {item.expected_no_result ? (
        <div className="rounded-md border border-amber-300/20 bg-amber-300/[0.06] p-2 text-[11px] leading-5 text-amber-100">
          <p>近邻语料只用于确认问题易混淆；请同时确认完整语料复核结果中不存在答案。</p>
          <p className="mt-1 text-amber-100/75">{verification?.completed === true ? `已扫描 ${verification.scanned_document_count ?? 0} 份文档、${verification.scanned_source_block_count ?? 0} 个 canonical source blocks · ${verification.method || "verification"}` : "完整语料复核回执缺失，禁止批准。"}</p>
        </div>
      ) : null}
      {bundle.evidence.map((evidence, evidenceIndex) => (
        <div className="text-xs" key={`${item.case_id}:gold:${evidenceIndex}`}>
          <p className="font-semibold text-slate-200">{String(evidence.document_name || evidence.document_id || "Gold evidence")}{evidence.page_number ? ` · p${evidence.page_number}` : ""}</p>
          {evidence.anchor_hash ? <p className="mt-1 font-mono text-[10px] text-cyan-200/75">anchor {String(evidence.anchor_start)}–{String(evidence.anchor_end)} · {String(evidence.anchor_hash).slice(0, 12)}…</p> : null}
          <p className="mt-1 whitespace-pre-wrap text-slate-500">{String(evidence.text || "")}</p>
        </div>
      ))}
      {item.expected_no_result && bundle.verification_evidence.length ? (
        <div className="space-y-2 border-t border-white/10 pt-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">完整语料高召回近邻</p>
          {bundle.verification_evidence.map((evidence, evidenceIndex) => (
            <div className="text-xs" key={`${item.case_id}:verification:${evidenceIndex}`}>
              <p className="font-semibold text-slate-300">{String(evidence.document_name || evidence.document_id || "Verification evidence")} · coverage {String(evidence.lexical_query_coverage ?? "-")}</p>
              <p className="mt-1 whitespace-pre-wrap text-slate-500">{String(evidence.text || "")}</p>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function KnowledgeEvaluationPage() {
  const { kbId = "" } = useParams();
  const importRef = useRef<HTMLInputElement>(null);
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [documents, setDocuments] = useState<RagDocument[]>([]);
  const [versions, setVersions] = useState<PipelineVersion[]>([]);
  const [evaluationSets, setEvaluationSets] = useState<EvaluationSet[]>([]);
  const [selectedSetId, setSelectedSetId] = useState("");
  const [evaluationSetVersions, setEvaluationSetVersions] = useState<EvaluationSetVersion[]>([]);
  const [selectedEvaluationVersion, setSelectedEvaluationVersion] = useState<string>("draft");
  const [gate, setGate] = useState<GatePolicy | null>(null);
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<EvaluationRun | null>(null);
  const [selectedVersions, setSelectedVersions] = useState<string[]>([]);
  const [baselineVersionId, setBaselineVersionId] = useState("");
  const [runMode, setRunMode] = useState<"diagnostic" | "formal">("diagnostic");
  const [adminCsrfToken, setAdminCsrfToken] = useState("");
  const [newSetName, setNewSetName] = useState("");
  const [newSetRole, setNewSetRole] = useState<Exclude<BenchmarkRole, "held_out_qualification" | "promotion_evidence">>("unclassified");
  const [query, setQuery] = useState("");
  const [tags, setTags] = useState("");
  const [references, setReferences] = useState<ExpectedReference[]>([]);
  const [expectedNoResult, setExpectedNoResult] = useState(false);
  const [previewSources, setPreviewSources] = useState<PreviewSource[]>([]);
  const [previewVersionId, setPreviewVersionId] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [acknowledgeCalibrationWarnings, setAcknowledgeCalibrationWarnings] = useState(false);
  const [caseEvidence, setCaseEvidence] = useState<Record<string, CaseEvidenceBundle>>({});
  const [reviewReasons, setReviewReasons] = useState<Record<string, string>>({});
  const [calibrationJob, setCalibrationJob] = useState<{ job_id: string; status: string; error?: string | null } | null>(null);

  const selectedSet = useMemo(
    () => evaluationSets.find((item) => item.eval_set_id === selectedSetId) ?? null,
    [evaluationSets, selectedSetId],
  );
  const pendingReviewCount = useMemo(
    () => selectedSet?.cases.filter((item) => item.review_status !== "approved" || !item.review_evidence).length ?? 0,
    [selectedSet],
  );
  const selectedPublishedVersion = useMemo(
    () => evaluationSetVersions.find((item) => String(item.version) === selectedEvaluationVersion) ?? null,
    [evaluationSetVersions, selectedEvaluationVersion],
  );
  const formalVersionReady = isFormalEvaluationVersionEligible(selectedPublishedVersion);

  useEffect(() => {
    if (!kbId) return;
    void loadWorkspace();
    void loadAdminSession();
  }, [kbId]);

  async function loadAdminSession() {
    const response = await fetch("/api/router/admin/session");
    if (!response.ok) return setAdminCsrfToken("");
    const session = await response.json().catch(() => null);
    setAdminCsrfToken(session?.authenticated && session?.csrf_token ? String(session.csrf_token) : "");
  }

  useEffect(() => {
    if (!selectedSetId) {
      setEvaluationSetVersions([]);
      setSelectedEvaluationVersion("draft");
      return;
    }
    void loadEvaluationSetVersions(selectedSetId);
  }, [selectedSetId]);

  useEffect(() => {
    const active = versions.find((item) => item.active) ?? versions[0];
    if (!previewVersionId && active) setPreviewVersionId(active.version_id);
    if (selectedVersions.length === 0 && versions.length > 0) {
      const initial = versions.slice(0, 2).map((item) => item.version_id);
      setSelectedVersions(initial);
      setBaselineVersionId((versions.find((item) => item.active) ?? versions[versions.length - 1]).version_id);
    }
  }, [versions, previewVersionId, selectedVersions.length]);

  useEffect(() => {
    if (!selectedRun || !["queued", "running"].includes(selectedRun.status)) return;
    const timer = window.setInterval(() => void refreshRun(selectedRun.run_id), 900);
    return () => window.clearInterval(timer);
  }, [selectedRun?.run_id, selectedRun?.status]);

  useEffect(() => {
    if (!calibrationJob || ["completed", "failed", "cancelled"].includes(calibrationJob.status)) return;
    const timer = window.setInterval(async () => {
      const response = await fetch(`/api/benchmarks/calibrations/${encodeURIComponent(calibrationJob.job_id)}`);
      if (!response.ok) return;
      const data = await response.json();
      setCalibrationJob(data);
      if (["completed", "failed", "cancelled"].includes(data.status)) await reloadSets(selectedSetId);
    }, 900);
    return () => window.clearInterval(timer);
  }, [calibrationJob?.job_id, calibrationJob?.status, selectedSetId]);

  async function loadWorkspace() {
    setBusy("load");
    setError("");
    try {
      const [kbResponse, documentsResponse, versionsResponse, setsResponse, gateResponse, runsResponse] = await Promise.all([
        fetch("/api/rag/knowledge_bases"),
        fetch(`/api/rag/knowledge_bases/${encodeURIComponent(kbId)}/documents`),
        fetch(`/api/rag/pipeline/versions?kb_id=${encodeURIComponent(kbId)}`),
        fetch(`/api/rag/evaluation-sets?kb_id=${encodeURIComponent(kbId)}`),
        fetch(`/api/rag/evaluation-gate/${encodeURIComponent(kbId)}`),
        fetch(`/api/rag/evaluation-runs?kb_id=${encodeURIComponent(kbId)}&limit=20`),
      ]);
      if (![kbResponse, documentsResponse, versionsResponse, setsResponse, gateResponse, runsResponse].every((response) => response.ok)) {
        throw new Error("知识评估工作台加载失败。");
      }
      const kbData = await kbResponse.json();
      const docsData = await documentsResponse.json();
      const versionsData = await versionsResponse.json();
      const setsData = await setsResponse.json();
      const gateData = await gateResponse.json();
      const runsData = await runsResponse.json();
      setKnowledgeBase((kbData.knowledge_bases as KnowledgeBase[]).find((item) => item.id === kbId) ?? null);
      setDocuments(docsData.documents ?? []);
      setVersions(versionsData.versions ?? []);
      setEvaluationSets(setsData.evaluation_sets ?? []);
      setSelectedSetId((current) => current || setsData.evaluation_sets?.[0]?.eval_set_id || "");
      setGate(gateData);
      setRuns(runsData.evaluation_runs ?? []);
      if (!selectedRun && runsData.evaluation_runs?.[0]) await refreshRun(runsData.evaluation_runs[0].run_id, false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "知识评估工作台加载失败。");
    } finally {
      setBusy("");
    }
  }

  async function reloadSets(preferId?: string) {
    const response = await fetch(`/api/rag/evaluation-sets?kb_id=${encodeURIComponent(kbId)}`);
    if (!response.ok) return;
    const data = await response.json();
    setEvaluationSets(data.evaluation_sets ?? []);
    if (preferId) setSelectedSetId(preferId);
  }

  async function loadEvaluationSetVersions(evalSetId: string) {
    const response = await fetch(`/api/rag/evaluation-sets/${encodeURIComponent(evalSetId)}/versions`);
    if (!response.ok) return;
    const data = await response.json();
    const next = (data.versions ?? []) as EvaluationSetVersion[];
    setEvaluationSetVersions(next);
    setSelectedEvaluationVersion((current) => {
      if (current !== "draft" && next.some((item) => String(item.version) === current)) return current;
      return next[0] ? String(next[0].version) : "draft";
    });
  }

  async function createSet() {
    if (!newSetName.trim()) return;
    setBusy("set");
    setError("");
    const response = await fetch("/api/rag/evaluation-sets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kb_id: kbId, name: newSetName.trim(), benchmark_role: newSetRole }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "创建评估集失败。"));
    setNewSetName("");
    await reloadSets(data.eval_set_id);
  }

  async function updateSetRole(role: BenchmarkRole) {
    if (!selectedSet || role === "promotion_evidence") return;
    setBusy("set-role");
    setError("");
    const response = await fetch(`/api/rag/evaluation-sets/${encodeURIComponent(selectedSet.eval_set_id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: selectedSet.revision,
        benchmark_role: role,
      }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "更新评测集用途失败。"));
    await reloadSets(selectedSet.eval_set_id);
    setNotice(`评测集用途已改为“${benchmarkRoleLabel(role)}”；发布前仍需满足该角色的不可变证据门禁。`);
  }

  function addDocumentReference(documentId: string, documentName?: string) {
    if (!documentId || references.some((item) => item.document_id === documentId && !item.chunk_id)) return;
    setReferences((current) => [...current, { document_id: documentId, document_name: documentName, relevance: 2, match_mode: "document" }]);
  }

  function addPreviewReference(source: PreviewSource) {
    const documentId = source.source_document_id || source.doc_id;
    if (references.some((item) => item.chunk_id === source.chunk_id)) return;
    setReferences((current) => [...current, {
      document_id: documentId,
      document_name: source.document_name,
      chunk_id: source.chunk_id,
      source_block_id: source.source_block_id,
      page_number: source.page_number,
      relevance: 3,
      match_mode: source.source_block_id ? "source_block" : "chunk",
    }]);
  }

  async function previewRetrieval() {
    if (!query.trim() || !previewVersionId) return;
    setBusy("preview");
    setError("");
    const response = await fetch(`/api/rag/pipeline/versions/${encodeURIComponent(previewVersionId)}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: query.trim(), top_k: 10 }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "试检索失败。"));
    setPreviewSources(data.sources ?? []);
  }

  async function addCase() {
    if (!selectedSet || !query.trim() || (!expectedNoResult && references.length === 0)) return;
    setBusy("case");
    setError("");
    const response = await fetch(`/api/rag/evaluation-sets/${selectedSet.eval_set_id}/cases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: selectedSet.revision,
        case: {
          query: query.trim(),
          expected_no_result: expectedNoResult,
          expected_refs: expectedNoResult ? [] : references.map(({ document_name: _name, ...item }) => item),
          tags: tags.split(",").map((item) => item.trim()).filter(Boolean),
        },
      }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "保存评估问题失败。"));
    setQuery("");
    setTags("");
    setReferences([]);
    setExpectedNoResult(false);
    setPreviewSources([]);
    await reloadSets(selectedSet.eval_set_id);
    setNotice("评估问题已保存。");
  }

  async function deleteCase(caseId: string) {
    if (!selectedSet) return;
    const response = await fetch(
      `/api/rag/evaluation-sets/${selectedSet.eval_set_id}/cases/${caseId}?expected_revision=${selectedSet.revision}`,
      { method: "DELETE" },
    );
    if (!response.ok) return setError(errorMessage(await response.json().catch(() => null), "删除失败。"));
    await reloadSets(selectedSet.eval_set_id);
  }

  async function importCases(file: File) {
    if (!selectedSet) return;
    const form = new FormData();
    form.append("file", file);
    setBusy("import");
    const response = await fetch(
      `/api/rag/evaluation-sets/${selectedSet.eval_set_id}/import?expected_revision=${selectedSet.revision}`,
      { method: "POST", body: form },
    );
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "导入失败。"));
    await reloadSets(selectedSet.eval_set_id);
    setNotice("评估集已导入。");
  }

  async function createRun() {
    if (!selectedSet || selectedVersions.length === 0) return;
    setBusy("run");
    setError("");
    const response = await fetch("/api/rag/evaluation-runs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(runMode === "formal" && adminCsrfToken ? { "X-ModelMirror-CSRF": adminCsrfToken } : {}),
      },
      body: JSON.stringify({
        eval_set_id: selectedSet.eval_set_id,
        eval_set_version: selectedEvaluationVersion === "draft" ? null : Number(selectedEvaluationVersion),
        targets: selectedVersions.map((versionId) => ({ version_id: versionId })),
        baseline_version_id: selectedVersions.includes(baselineVersionId) ? baselineVersionId : null,
        ks: [1, 3, 5, 10],
        run_mode: runMode,
      }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "启动评估失败。"));
    setSelectedRun(data);
    setRuns((current) => [data, ...current.filter((item) => item.run_id !== data.run_id)]);
  }

  async function publishEvaluationSet() {
    if (!selectedSet) return;
    setBusy("publish-set");
    setError("");
    const response = await fetch(`/api/rag/evaluation-sets/${encodeURIComponent(selectedSet.eval_set_id)}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: selectedSet.revision,
        release_notes: "Published from Knowledge Evaluation workspace",
        acknowledge_calibration_warnings: acknowledgeCalibrationWarnings,
      }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "发布评测版本失败。"));
    await reloadSets(selectedSet.eval_set_id);
    await loadEvaluationSetVersions(selectedSet.eval_set_id);
    setSelectedEvaluationVersion(String(data.version));
    setNotice(`评测集 v${data.version} 已发布；后续草稿编辑不会改变该版本。`);
  }

  async function loadCaseEvidence(caseId: string) {
    if (!selectedSet) return;
    if (caseEvidence[caseId]) {
      setCaseEvidence((current) => {
        const next = { ...current };
        delete next[caseId];
        return next;
      });
      return;
    }
    const response = await fetch(`/api/rag/evaluation-sets/${encodeURIComponent(selectedSet.eval_set_id)}/cases/${encodeURIComponent(caseId)}/evidence`);
    const data = await response.json().catch(() => null);
    if (!response.ok) return setError(errorMessage(data, "Gold 证据加载失败。"));
    setCaseEvidence((current) => ({
      ...current,
      [caseId]: {
        evidence: data.evidence ?? [],
        full_corpus_verification: data.full_corpus_verification ?? null,
        verification_evidence: data.verification_evidence ?? [],
      },
    }));
  }

  async function reviewCase(item: EvaluationCase, decision: "approved" | "rejected") {
    if (!selectedSet) return;
    if (!adminCsrfToken) return setError("请先在设置页完成 Provider 管理员配对，再提交人工审核结论。");
    const leakageWarning = leakageWarningThreshold(item) != null;
    const suppliedReason = String(reviewReasons[item.case_id] || "").trim();
    if (decision === "approved" && leakageWarning && !suppliedReason) {
      return setError("该样例存在原文重合警告，请填写逐条审核理由后再批准。");
    }
    const response = await fetch(`/api/rag/evaluation-sets/${encodeURIComponent(selectedSet.eval_set_id)}/cases/${encodeURIComponent(item.case_id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-ModelMirror-CSRF": adminCsrfToken },
      body: JSON.stringify({
        expected_revision: selectedSet.revision,
        decision,
        reason: suppliedReason || (decision === "approved" ? "已在评测审核工作台核对固定证据。" : "人工审核拒绝。"),
      }),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) return setError(errorMessage(data, "样例审核失败。"));
    await reloadSets(selectedSet.eval_set_id);
    setNotice(decision === "approved" ? "审核结论已由服务器记录。" : "样例已拒绝，不能进入 Formal Gold。" );
  }

  async function recalibrateGeneratedSet() {
    if (!selectedSet || selectedSet.origin !== "generated") return;
    const target = selectedSet.provenance?.target_reference;
    if (!target || typeof target !== "object") return setError("该评测集缺少固定知识版本，无法重新校准。" );
    setBusy("calibration");
    const response = await fetch("/api/benchmarks/calibrations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dataset_id: selectedSet.eval_set_id,
        dataset_revision: selectedSet.revision,
        target,
      }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "重新校准失败。"));
    setCalibrationJob(data);
    setNotice("重新校准已启动，Gold 不会被检索结果改写。" );
  }

  async function refreshRun(runId: string, refreshList = true) {
    const response = await fetch(`/api/rag/evaluation-runs/${encodeURIComponent(runId)}`);
    if (!response.ok) return;
    const data = await response.json();
    setSelectedRun(data);
    if (refreshList) {
      setRuns((current) => [data, ...current.filter((item) => item.run_id !== data.run_id)]);
    }
  }

  async function saveGate() {
    if (!gate) return;
    setBusy("gate");
    const { kb_id: _kb, ...payload } = gate;
    const response = await fetch(`/api/rag/evaluation-gate/${encodeURIComponent(kbId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "保存 Gate 失败。"));
    setGate(data);
    setNotice("Promotion Gate 已保存。");
  }

  async function promote(versionId: string) {
    if (!selectedRun) return;
    setBusy(`promote:${versionId}`);
    const response = await fetch(`/api/rag/pipeline/versions/${encodeURIComponent(versionId)}/promote`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(adminCsrfToken ? { "X-ModelMirror-CSRF": adminCsrfToken } : {}),
      },
      body: JSON.stringify({ evaluation_run_id: selectedRun.run_id }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "推广版本失败。"));
    setVersions((current) => current.map((item) => ({ ...item, active: item.version_id === versionId })));
    setNotice(`知识索引 v${data.version} 已通过评估并激活。`);
  }

  return (
    <PageContainer activeResource="prompts" hideSidebar maxWidthClassName="max-w-[1720px]">
      <div className="space-y-4">
        <header className="flex flex-col gap-4 border-b border-white/10 pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-3 text-xs font-semibold">
              <Link className="text-hire-100 hover:text-hire-50" to="/rag">知识库</Link>
              <span className="text-slate-600">/</span>
              <Link className="text-slate-300 hover:text-white" to={`/rag/${encodeURIComponent(kbId)}/pipeline`}>执行画布</Link>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-semibold text-white">{knowledgeBase?.name || "知识评估"}</h1>
              <span className="rounded-full border border-cyan-300/25 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold text-cyan-100">Evaluation Beta</span>
            </div>
            <p className="mt-1 text-sm text-slate-400">{documents.length} 文档 · {versions.length} 索引版本 · {selectedSet?.cases.length ?? 0} 评估问题</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="rounded-lg border border-white/10 bg-white/[0.05] px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/[0.09]" onClick={() => void loadWorkspace()} type="button">刷新</button>
            <Link className="rounded-lg bg-hire-300 px-4 py-2 text-sm font-bold text-surface-950 hover:bg-hire-200" to={`/rag/${encodeURIComponent(kbId)}/pipeline`}>返回流水线</Link>
          </div>
        </header>

        {error || notice ? (
          <div className={`rounded-lg border px-4 py-3 text-sm ${error ? "border-rose-300/30 bg-rose-400/10 text-rose-100" : "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"}`}>{error || notice}</div>
        ) : null}

        {knowledgeBase?.corpus_locked ? (
          <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-4 py-3 text-sm text-amber-100">
            <strong>RAG 引擎标准基准锁定语料</strong>
            <span className="ml-2 text-amber-100/80">语料由 {String((knowledgeBase.catalog_ref as { pack_id?: string } | undefined)?.pack_id || "Catalog")} 固定；用于检索配置回归，不代表业务知识库质量。可编辑流水线、构建候选、评测、激活和回滚。</span>
          </div>
        ) : null}

        <KnowledgeBenchmarkGenerator
          kbId={kbId}
          versions={versions}
          documents={documents}
          onDatasetReady={async (evalSetId) => {
            await reloadSets(evalSetId);
            setSelectedSetId(evalSetId);
            setNotice("定向评测集已生成；请逐题核对全部 Gold 与无答案证据，全部批准后再发布锁定版本。" );
          }}
        />

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(480px,0.95fr)]">
          <section className="surface-panel rounded-lg border border-white/10 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-3">
              <div>
                <h2 className="text-sm font-semibold text-white">评估数据集</h2>
                <p className="mt-1 text-xs text-slate-500">草稿 revision {selectedSet?.revision ?? "-"} · 已发布 v{selectedSet?.latest_version ?? "-"} · {benchmarkRoleLabel(selectedSet?.benchmark_role)}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <select className="rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" onChange={(event) => setSelectedSetId(event.target.value)} value={selectedSetId}>
                  <option value="">选择评估集</option>
                  {evaluationSets.map((item) => <option key={item.eval_set_id} value={item.eval_set_id}>{item.name} ({item.cases.length})</option>)}
                </select>
                {selectedSet ? <select aria-label="评测集用途" className="rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white disabled:opacity-40" disabled={busy === "set-role"} onChange={(event) => void updateSetRole(event.target.value as BenchmarkRole)} value={selectedSet.benchmark_role || "unclassified"}><option value="unclassified">诊断</option><option value="regression_guard">回归守卫</option><option value="strategy_tuning">候选选择</option><option value="threshold_calibration">阈值校准</option>{selectedSet.origin === "generated" ? <option value="held_out_qualification">锁定晋级集</option> : null}</select> : null}
                <button className="rounded-lg border border-white/10 px-3 py-2 text-sm text-slate-200 disabled:opacity-40" disabled={!selectedSet} onClick={() => importRef.current?.click()} type="button">导入</button>
                {selectedSet?.origin === "generated" && selectedSet.calibration?.status === "warning" ? <label className="flex items-center gap-2 text-xs text-amber-100"><input checked={acknowledgeCalibrationWarnings} onChange={(event) => setAcknowledgeCalibrationWarnings(event.target.checked)} type="checkbox" />确认校准警告</label> : null}
                {selectedSet?.origin === "generated" ? <button className="rounded-lg border border-cyan-300/25 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-40" disabled={pendingReviewCount > 0 || busy === "calibration" || Boolean(calibrationJob && !["completed", "failed", "cancelled"].includes(calibrationJob.status))} onClick={() => void recalibrateGeneratedSet()} type="button">{selectedSet.calibration?.status === "awaiting_review" ? "开始真实校准" : "重新校准"}</button> : null}
                <button className="rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-sm font-semibold text-emerald-100 disabled:opacity-40" disabled={!selectedSet?.cases.length || busy === "publish-set" || (selectedSet?.origin === "generated" && (!["calibrated", "warning"].includes(String(selectedSet.calibration?.status || "")) || pendingReviewCount > 0 || (selectedSet.calibration?.status === "warning" && !acknowledgeCalibrationWarnings)))} onClick={() => void publishEvaluationSet()} type="button">发布版本</button>
                <input accept=".json,.csv,application/json,text/csv" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importCases(file); event.target.value = ""; }} ref={importRef} type="file" />
              </div>
            </div>

            <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_180px_auto]">
              <input className="min-w-0 flex-1 rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/40" onChange={(event) => setNewSetName(event.target.value)} placeholder="新评估集名称" value={newSetName} />
              <select aria-label="新评估集用途" className="rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" onChange={(event) => setNewSetRole(event.target.value as typeof newSetRole)} value={newSetRole}>
                <option value="unclassified">诊断</option>
                <option value="regression_guard">回归守卫</option>
                <option value="strategy_tuning">候选选择</option>
                <option value="threshold_calibration">阈值校准</option>
              </select>
              <button className="rounded-lg border border-hire-300/25 bg-hire-300/10 px-3 py-2 text-sm font-semibold text-hire-100 disabled:opacity-40" disabled={!newSetName.trim() || busy === "set"} onClick={() => void createSet()} type="button">创建</button>
            </div>
            <p className="mt-2 text-[11px] leading-4 text-slate-500">锁定晋级集只能由 Benchmark 生成器建立，并在 42 条逐项审核、anchor 与完整语料无答案复核均合格后发布。</p>

            <div className="mt-5 space-y-3 border-t border-white/10 pt-4">
              <textarea className="min-h-24 w-full resize-y rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm leading-6 text-white outline-none focus:border-hire-300/40" onChange={(event) => setQuery(event.target.value)} placeholder="评估问题" value={query} />
              <div className="grid gap-2 sm:grid-cols-[1fr_180px_auto]">
                <select className="rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" defaultValue="" onChange={(event) => { const document = documents.find((item) => item.id === event.target.value); if (document) addDocumentReference(document.id, document.filename); event.target.value = ""; }}>
                  <option value="">添加期望文档</option>
                  {documents.map((document) => <option key={document.id} value={document.id}>{document.filename}</option>)}
                </select>
                <select className="rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" onChange={(event) => setPreviewVersionId(event.target.value)} value={previewVersionId}>
                  {versions.map((version) => <option key={version.version_id} value={version.version_id}>v{version.version}{version.active ? " · active" : ""}</option>)}
                </select>
                <button className="rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-40" disabled={!query.trim() || !previewVersionId || busy === "preview"} onClick={() => void previewRetrieval()} type="button">试检索</button>
              </div>
              <input className="w-full rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/40" onChange={(event) => setTags(event.target.value)} placeholder="标签，逗号分隔" value={tags} />
              <label className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.025] px-3 py-2 text-xs text-slate-300">
                <input checked={expectedNoResult} className="h-4 w-4 accent-hire-300" onChange={(event) => { setExpectedNoResult(event.target.checked); if (event.target.checked) setReferences([]); }} type="checkbox" />
                无答案样例：正确行为是返回空检索结果
              </label>

              {references.length > 0 ? (
                <div className="divide-y divide-white/10 rounded-lg border border-white/10">
                  {references.map((reference, index) => (
                    <div className="flex items-center gap-3 px-3 py-2 text-xs" key={`${reference.document_id}:${reference.chunk_id || index}`}>
                      <span className="min-w-0 flex-1 truncate text-slate-200">{reference.document_name || reference.document_id}{reference.page_number ? ` · p${reference.page_number}` : ""} · {reference.match_mode || "legacy"}</span>
                      <select className="rounded border border-white/10 bg-surface-950 px-2 py-1 text-slate-200" onChange={(event) => setReferences((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, relevance: Number(event.target.value) } : item))} value={reference.relevance}>
                        <option value={1}>相关</option><option value={2}>重要</option><option value={3}>关键</option>
                      </select>
                      <button className="text-rose-200 hover:text-rose-100" onClick={() => setReferences((current) => current.filter((_, itemIndex) => itemIndex !== index))} type="button">移除</button>
                    </div>
                  ))}
                </div>
              ) : null}

              {previewSources.length > 0 ? (
                <div className="max-h-52 divide-y divide-white/10 overflow-y-auto rounded-lg border border-white/10">
                  {previewSources.map((source, index) => (
                    <button className="flex w-full items-start gap-3 px-3 py-2 text-left hover:bg-white/[0.04]" key={source.chunk_id} onClick={() => addPreviewReference(source)} type="button">
                      <span className="w-6 shrink-0 text-xs font-semibold text-slate-500">{index + 1}</span>
                      <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-white">{source.document_name}</span><span className="mt-1 line-clamp-2 block text-xs text-slate-400">{source.text}</span></span>
                      <span className="text-[11px] text-cyan-100">{source.score.toFixed(3)}</span>
                    </button>
                  ))}
                </div>
              ) : null}

              <button className="w-full rounded-lg bg-hire-300 px-4 py-2.5 text-sm font-bold text-surface-950 disabled:opacity-40" disabled={!selectedSet || !query.trim() || (!expectedNoResult && references.length === 0) || busy === "case"} onClick={() => void addCase()} type="button">保存评估问题</button>
            </div>

            <div className="mt-5 max-h-[360px] divide-y divide-white/10 overflow-y-auto border-t border-white/10">
              {selectedSet?.cases.length ? selectedSet.cases.map((item, index) => (
                <div className="py-3" key={item.case_id}>
                  <div className="flex gap-3"><span className="text-xs font-semibold text-slate-500">{index + 1}</span><p className="min-w-0 flex-1 text-sm text-slate-100">{item.query}</p>{selectedSet.origin === "generated" ? <button className="text-xs text-cyan-100" onClick={() => void loadCaseEvidence(item.case_id)} type="button">{caseEvidence[item.case_id] ? "收起证据" : item.expected_no_result ? "查看近邻语料" : "查看 Gold"}</button> : null}{selectedSet.origin === "generated" && item.review_status !== "approved" ? <><button className="text-xs text-emerald-100 disabled:cursor-not-allowed disabled:opacity-35" disabled={!isCaseReviewEvidenceReady(item, caseEvidence[item.case_id]) || !adminCsrfToken} onClick={() => void reviewCase(item, "approved")} type="button">批准</button><button className="text-xs text-rose-200 disabled:cursor-not-allowed disabled:opacity-35" disabled={!isCaseReviewEvidenceReady(item, caseEvidence[item.case_id]) || !adminCsrfToken} onClick={() => void reviewCase(item, "rejected")} type="button">拒绝</button></> : null}<button className="text-xs text-rose-200" onClick={() => void deleteCase(item.case_id)} type="button">删除</button></div>
                  <p className="mt-2 pl-7 text-xs text-slate-500">{item.expected_no_result ? "期望无结果" : `${item.expected_refs.length} 个期望引用 · ${[...new Set(item.expected_refs.map((ref) => ref.match_mode || "legacy"))].join(" / ")}`} · {String(item.targeting?.query_type || "未分类")} · {String(item.targeting?.locale || "未知语言")} · {item.tags.join(" · ") || "未标记"}</p>
                  {leakageWarningThreshold(item) != null ? <div className="mt-2 ml-7 rounded-md border border-amber-300/25 bg-amber-300/10 p-2"><p className="text-[11px] text-amber-100">检测到至少 {leakageWarningThreshold(item)} 个归一化连续字符与证据重合；批准前必须填写独立审核理由。</p><input className="mt-2 w-full rounded border border-amber-300/20 bg-surface-950 px-2 py-1.5 text-xs text-white" onChange={(event) => setReviewReasons((current) => ({ ...current, [item.case_id]: event.target.value }))} placeholder="说明为何该重合不构成答案泄漏" value={reviewReasons[item.case_id] || ""} /></div> : null}
                  {caseEvidence[item.case_id] ? <CaseEvidenceReview bundle={caseEvidence[item.case_id]} item={item} /> : null}
                </div>
              )) : <p className="py-10 text-center text-sm text-slate-500">尚无评估问题</p>}
            </div>
          </section>

          <section className="surface-panel rounded-lg border border-white/10 p-4">
            <div className="border-b border-white/10 pb-3">
              <h2 className="text-sm font-semibold text-white">版本对比</h2>
              <p className="mt-1 text-xs text-slate-500">固定评测版本与最多 5 个不可变索引版本</p>
            </div>
            <label className="mt-3 block text-xs text-slate-400">评测数据版本
              <select className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" onChange={(event) => setSelectedEvaluationVersion(event.target.value)} value={selectedEvaluationVersion}>
                <option value="draft">草稿 revision {selectedSet?.revision ?? "-"}（兼容模式）</option>
                {evaluationSetVersions.map((item) => <option key={item.version_id} value={String(item.version)}>不可变 v{item.version} · {item.cases.length} cases · {item.benchmark_contract_version ?? "legacy"} · {benchmarkRoleLabel(item.benchmark_role)}</option>)}
              </select>
            </label>
            <label className="mt-3 block text-xs text-slate-400">运行模式
              <select className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" onChange={(event) => setRunMode(event.target.value as "diagnostic" | "formal")} value={runMode}>
                <option value="diagnostic">Diagnostic（兼容诊断）</option>
                <option value="formal">Formal（固定 42 条与同语料门禁）</option>
              </select>
            </label>
            {runMode === "formal" && !adminCsrfToken ? <p className="mt-2 text-xs text-amber-100">Formal 需要先在设置页完成 Provider 管理员配对，然后刷新本页。</p> : null}
            {runMode === "formal" && !formalVersionReady ? <p className="mt-2 text-xs leading-5 text-amber-100">Formal 只接受 qualified rag-gold-v3 锁定晋级集，且 Tuner 使用谱系必须为空；当前版本仅可 Diagnostic。</p> : null}
            <div className="mt-3 divide-y divide-white/10 rounded-lg border border-white/10">
              {versions.map((version) => {
                const checked = selectedVersions.includes(version.version_id);
                return (
                  <label className="flex cursor-pointer items-center gap-3 px-3 py-3" key={version.version_id}>
                    <input checked={checked} className="h-4 w-4 accent-hire-300" onChange={(event) => setSelectedVersions((current) => event.target.checked ? [...current, version.version_id].slice(0, 5) : current.filter((item) => item !== version.version_id))} type="checkbox" />
                    <span className="min-w-0 flex-1"><span className="block text-sm font-semibold text-white">v{version.version} {version.active ? <span className="text-emerald-200">active</span> : null}</span><span className="text-xs text-slate-500">{version.chunk_count} chunks · {timestamp(version.created_at)}</span></span>
                    <label className="flex items-center gap-1.5 text-xs text-slate-400"><input checked={baselineVersionId === version.version_id} disabled={!checked} name="baseline" onChange={() => setBaselineVersionId(version.version_id)} type="radio" />基线</label>
                  </label>
                );
              })}
            </div>
            <button className="mt-3 w-full rounded-lg bg-cyan-300 px-4 py-2.5 text-sm font-bold text-surface-950 disabled:opacity-40" disabled={!selectedSet?.cases.length || selectedVersions.length === 0 || busy === "run" || (runMode === "formal" && (!formalVersionReady || selectedVersions.length !== 2 || !selectedVersions.includes(baselineVersionId) || !adminCsrfToken))} onClick={() => void createRun()} type="button">{runMode === "formal" ? "运行 Formal 评估" : "运行离线诊断"}</button>

            <div className="mt-5 border-t border-white/10 pt-4">
              <div className="flex items-center justify-between"><h3 className="text-sm font-semibold text-white">Promotion Gate</h3><button className="text-xs font-semibold text-hire-100 disabled:opacity-40" disabled={!gate || busy === "gate"} onClick={() => void saveGate()} type="button">保存</button></div>
              {gate ? (
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <label className="text-xs text-slate-400">模式<select className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" onChange={(event) => setGate({ ...gate, mode: event.target.value as GatePolicy["mode"] })} value={gate.mode}><option value="advisory">提示</option><option value="required">强制</option></select></label>
                  <label className="text-xs text-slate-400">最低 Recall@5<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={1} min={0} onChange={(event) => setGate({ ...gate, min_recall_at_5: Number(event.target.value) })} step={0.05} type="number" value={gate.min_recall_at_5} /></label>
                  <label className="text-xs text-slate-400">最低引用覆盖<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={1} min={0} onChange={(event) => setGate({ ...gate, min_citation_coverage: Number(event.target.value) })} step={0.05} type="number" value={gate.min_citation_coverage} /></label>
                  <label className="text-xs text-slate-400">最低无答案准确率<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={1} min={0} onChange={(event) => setGate({ ...gate, min_no_result_accuracy: Number(event.target.value) })} step={0.05} type="number" value={gate.min_no_result_accuracy} /></label>
                  <label className="text-xs text-slate-400">MRR 最大回退<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={1} min={0} onChange={(event) => setGate({ ...gate, max_mrr_regression: Number(event.target.value) })} step={0.01} type="number" value={gate.max_mrr_regression} /></label>
                  <label className="text-xs text-slate-400">Precision@5 最大回退<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={1} min={0} onChange={(event) => setGate({ ...gate, max_citation_precision_at_5_regression: Number(event.target.value) })} step={0.01} type="number" value={gate.max_citation_precision_at_5_regression} /></label>
                  <label className="text-xs text-slate-400">P95 延迟倍数<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={10} min={1} onChange={(event) => setGate({ ...gate, max_p95_latency_ratio: Number(event.target.value) })} step={0.1} type="number" value={gate.max_p95_latency_ratio} /></label>
                  <label className="text-xs text-slate-400">P95 绝对上限（ms）<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={120000} min={1} onChange={(event) => setGate({ ...gate, max_p95_latency_ms: Number(event.target.value) })} step={50} type="number" value={gate.max_p95_latency_ms} /></label>
                  <label className="text-xs text-slate-400">配对主指标最大回退<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={1} min={0} onChange={(event) => setGate({ ...gate, max_paired_primary_regression: Number(event.target.value) })} step={0.01} type="number" value={gate.max_paired_primary_regression} /></label>
                </div>
              ) : null}
            </div>

            <div className="mt-5 border-t border-white/10 pt-4">
              <h3 className="text-sm font-semibold text-white">最近评估</h3>
              <div className="mt-2 max-h-44 divide-y divide-white/10 overflow-y-auto">
                {runs.map((run) => <button className={`flex w-full items-center justify-between px-1 py-2 text-left text-xs ${selectedRun?.run_id === run.run_id ? "text-cyan-100" : "text-slate-400 hover:text-slate-200"}`} key={run.run_id} onClick={() => void refreshRun(run.run_id)} type="button"><span>{timestamp(run.created_at)}</span><span>{run.status} · {run.progress}%</span></button>)}
              </div>
            </div>
          </section>
        </div>

        <section className="surface-panel overflow-hidden rounded-lg border border-white/10">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
            <div><div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-semibold text-white">评估结果</h2>{selectedRun?.run_mode ? <span className="rounded-full border border-white/15 px-2 py-0.5 text-[10px] font-semibold text-slate-300">{selectedRun.run_mode}</span> : null}{selectedRun?.evidence_qualification ? <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${selectedRun.evidence_qualification.qualified ? "border-emerald-300/30 text-emerald-200" : "border-amber-300/30 text-amber-100"}`}>{selectedRun.evidence_qualification.qualified ? "qualified" : "diagnostic_only"}</span> : null}{selectedRun?.reproducibility_status ? <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${selectedRun.reproducibility_status === "current" ? "border-emerald-300/30 text-emerald-200" : selectedRun.reproducibility_status === "orphaned" ? "border-rose-300/30 text-rose-200" : "border-amber-300/30 text-amber-100"}`}>{selectedRun.reproducibility_status === "current" ? "可重放" : selectedRun.reproducibility_status === "orphaned" ? "引用已失效" : "不可重放"}</span> : null}</div><p className="mt-1 text-xs text-slate-500">{selectedRun ? `${selectedRun.status} · ${selectedRun.progress}% · ${selectedRun.run_id}` : "选择或运行一次评估"}</p>{selectedRun?.evidence_qualification && !selectedRun.evidence_qualification.qualified ? <p className="mt-1 text-xs text-amber-100">仅供诊断：正式门禁要求已发布的 held-out rag-gold-v3、完整 anchor/审核/语料复核证据与同一语料快照。</p> : null}{selectedRun?.reproducibility_status && selectedRun.reproducibility_status !== "current" ? <p className="mt-1 max-w-[75ch] text-xs text-amber-100">不可晋级：评测引用已缺失或执行指纹已漂移，请使用当前 Gold 和索引版本重新运行 Formal。</p> : null}</div>
            {selectedRun?.error ? <span className="text-xs text-rose-200">{selectedRun.error}</span> : null}
          </div>
          {selectedRun?.target_results.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1060px] text-left text-xs">
                <thead className="border-b border-white/10 bg-white/[0.025] text-slate-500"><tr><th className="px-4 py-3">版本</th><th>Recall@1</th><th>Recall@5</th><th>MRR@10</th><th>nDCG@10</th><th>Citation P@5</th><th>引用覆盖</th><th>无答案准确</th><th>误召回</th><th>P95</th><th>Gate</th><th className="pr-4">操作</th></tr></thead>
                <tbody className="divide-y divide-white/10">
                  {selectedRun.target_results.map((target) => {
                    const promotionReady = isEvaluationPromotionReady(selectedRun, target);
                    return <tr key={target.version_id}>
                      <td className="px-4 py-3 font-semibold text-white">v{target.version}</td>
                      <td>{metric(target.metrics.recall_at_1)}</td><td>{metric(target.metrics.recall_at_5)}</td><td>{metric(target.metrics.mrr_at_10, false)}</td><td>{metric(target.metrics.ndcg_at_10, false)}</td><td>{metric(target.metrics.citation_precision_at_5)}</td><td>{metric(target.metrics.citation_coverage ?? target.metrics.citation_hit_rate)}</td><td>{metric(target.metrics.no_result_accuracy)}</td><td>{metric(target.metrics.false_positive_rate)}</td><td>{target.metrics.p95_latency_ms?.toFixed(0) ?? "-"} ms</td>
                      <td><span className={promotionReady ? "text-emerald-200" : "text-rose-200"}>{promotionReady ? "通过" : target.promotion_gate.passed ? "证据阻断" : "未通过"}</span></td>
                      <td className="pr-4"><button className="rounded-md border border-emerald-300/25 px-2.5 py-1.5 font-semibold text-emerald-100 disabled:cursor-not-allowed disabled:opacity-35" disabled={!promotionReady || busy === `promote:${target.version_id}`} onClick={() => void promote(target.version_id)} title={promotionReady ? "推广该索引版本" : "仅可推广可重放、执行证据完整且门禁通过的 Formal 结果"} type="button">推广</button></td>
                    </tr>
                  })}
                </tbody>
              </table>
              <div className="grid gap-4 border-t border-white/10 p-4 xl:grid-cols-2">
                {selectedRun.target_results.map((target) => (
                  <div className="rounded-lg border border-white/10" key={`cases:${target.version_id}`}>
                    <div className="border-b border-white/10 px-3 py-2 text-xs font-semibold text-white">v{target.version} 案例明细</div>
                    <div className="max-h-72 divide-y divide-white/10 overflow-y-auto">
                      {target.case_results.map((caseResult) => (
                        <details className="px-3 py-2" key={caseResult.case_id}>
                          <summary className="cursor-pointer text-xs text-slate-200">{caseResult.query_preview}<span className="ml-2 text-slate-500">{caseResult.metrics.no_result_accuracy != null ? `No-result ${metric(caseResult.metrics.no_result_accuracy)}` : `R@5 ${metric(caseResult.metrics.recall_at_5)}`} · {caseResult.latency_ms.toFixed(0)}ms</span></summary>
                          <div className="mt-2 space-y-1 pl-3">{caseResult.ranking.slice(0, 10).map((item) => <div className="flex gap-2 text-[11px]" key={`${caseResult.case_id}:${item.rank}`}><span className="w-5 text-slate-600">{item.rank}</span><span className={`min-w-0 flex-1 truncate ${item.relevance ? "text-emerald-200" : "text-slate-500"}`}>{item.document_name || item.document_id}</span><span className="text-slate-600">{item.score?.toFixed(3) ?? "-"}</span></div>)}</div>
                        </details>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : <div className="py-16 text-center text-sm text-slate-500">{busy === "load" ? "正在加载..." : "暂无评估结果"}</div>}
        </section>
      </div>
    </PageContainer>
  );
}
