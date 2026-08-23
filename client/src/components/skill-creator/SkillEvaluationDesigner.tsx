import { AlertTriangle, Check, FilePlus2, FlaskConical, Plus, Save, ShieldAlert, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  confirmSkillCreatorEvaluationSuite,
  generateSkillCreatorEvaluationSuite,
  saveSkillCreatorEvaluationCases,
  startSkillCreatorEvaluation,
  updateSkillCreatorEvaluationSuite,
  updateSkillCreatorSession,
  waiveSkillCreatorEvaluation,
  type SkillCreatorDraft,
  type SkillCreatorQualityMode,
  type SkillCreatorSession,
  type SkillEvaluationAssertion,
  type SkillEvaluationAssertionKind,
  type SkillEvaluationRun,
  type SkillEvaluationSuiteCase,
} from "../../utils/skillCreatorApi";

const CORE_ROLES = ["normal", "ambiguous", "boundary"] as const;
const ROLE_LABELS = {
  normal: "正常任务",
  ambiguous: "歧义 / 信息不足",
  boundary: "边界 / 失败",
  regression: "用户确认回归",
} as const;

const ASSERTION_LABELS: Record<SkillEvaluationAssertionKind, string> = {
  exact_match: "输出完全等于",
  contains: "输出包含",
  not_contains: "输出不包含",
  json_schema: "输出符合 JSON Schema",
  file_exists: "生成指定文件",
  file_sha256: "文件摘要匹配",
};

function emptyCase(index: number, session: SkillCreatorSession): SkillEvaluationSuiteCase {
  return {
    case_id: `draft-case-${index + 1}`,
    name: `用例 ${index + 1}`,
    prompt: session.positive_examples[index] ?? "",
    expected_behavior: session.success_criteria[index] ?? session.expected_output ?? "",
    fixtures: [],
    assertions: [],
    role: CORE_ROLES[index] ?? "regression",
    source: "user",
    requirement_ids: [],
    required_resource_paths: [],
    workflow_step_ids: [],
  };
}

function initialCases(session: SkillCreatorSession) {
  if (session.evaluation_suite?.cases.length) return session.evaluation_suite.cases;
  if (session.evaluation_cases?.length === 3) {
    return session.evaluation_cases.map((item, index) => ({
      ...item,
      role: CORE_ROLES[index] ?? "normal",
      source: "migrated" as const,
      requirement_ids: [],
      required_resource_paths: [],
      workflow_step_ids: [],
    }));
  }
  return [0, 1, 2].map((index) => emptyCase(index, session));
}

function assertionComplete(assertion: SkillEvaluationAssertion) {
  if (assertion.kind === "json_schema") return Boolean(assertion.schema);
  if (assertion.kind === "file_exists") return Boolean(assertion.path?.trim());
  if (assertion.kind === "file_sha256") {
    return Boolean(assertion.path?.trim() && /^[a-f0-9]{64}$/i.test(assertion.sha256 ?? ""));
  }
  return Boolean(assertion.value?.trim());
}

function casesComplete(cases: SkillEvaluationSuiteCase[], useSuite: boolean) {
  const coreComplete = !useSuite || CORE_ROLES.every((role) => cases.filter((item) => item.role === role).length === 1);
  const regressionCount = cases.filter((item) => item.role === "regression").length;
  return coreComplete && regressionCount <= 9 && cases.length >= 3 && cases.length <= (useSuite ? 12 : 3) && cases.every((item) =>
    item.name.trim() &&
    item.prompt.trim() &&
    item.expected_behavior.trim() &&
    item.fixtures.every((fixture) => fixture.path.trim() && !fixture.path.startsWith("/") && !fixture.path.includes("..")) &&
    item.assertions.every(assertionComplete),
  );
}

function CaseEditor({
  index,
  value,
  onChange,
}: {
  index: number;
  value: SkillEvaluationSuiteCase;
  onChange: (value: SkillEvaluationSuiteCase) => void;
}) {
  function updateAssertion(assertionIndex: number, patch: Partial<SkillEvaluationAssertion>) {
    onChange({
      ...value,
      assertions: value.assertions.map((item, current) => current === assertionIndex ? { ...item, ...patch } : item),
    });
  }

  return (
    <article className="border-t border-white/10 py-6 first:border-t-0 first:pt-0" aria-labelledby={`evaluation-case-${index}`}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand-300/10 text-sm font-semibold text-brand-100">{index + 1}</span>
          <h3 className="text-base font-semibold text-white" id={`evaluation-case-${index}`}>{value.name || `用例 ${index + 1}`}</h3>
        </div>
        <span className="rounded-full bg-white/[0.055] px-2.5 py-1 text-xs font-semibold text-slate-300">{ROLE_LABELS[value.role]}</span>
      </div>
      {value.requirement_ids.length || value.required_resource_paths.length || value.workflow_step_ids.length ? (
        <details className="mt-3 rounded-md bg-white/[0.025] p-3 text-xs text-slate-400">
          <summary className="cursor-pointer font-semibold text-slate-300">查看 coverage 与资源要求</summary>
          <div className="mt-2 space-y-1">
            {value.requirement_ids.length ? <p>需求：{value.requirement_ids.join("、")}</p> : null}
            {value.workflow_step_ids.length ? <p>步骤：{value.workflow_step_ids.join("、")}</p> : null}
            {value.required_resource_paths.length ? <p className="break-all">必须暂存：{value.required_resource_paths.join("、")}</p> : null}
          </div>
        </details>
      ) : null}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <label className="block">
          <span className="text-xs font-semibold text-slate-300">用例名称</span>
          <input
            className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2.5 text-sm text-white focus:border-brand-300/50 focus:outline-none"
            maxLength={120}
            onChange={(event) => onChange({ ...value, name: event.target.value })}
            value={value.name}
          />
        </label>
        <label className="block lg:row-span-2">
          <span className="text-xs font-semibold text-slate-300">期望行为</span>
          <textarea
            className="mt-2 min-h-32 w-full resize-y rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2.5 text-sm leading-6 text-white focus:border-brand-300/50 focus:outline-none"
            maxLength={2_000}
            onChange={(event) => onChange({ ...value, expected_behavior: event.target.value })}
            placeholder="描述可人工判断的成功结果，不要把答案写进提示。"
            value={value.expected_behavior}
          />
        </label>
        <label className="block">
          <span className="text-xs font-semibold text-slate-300">真实任务提示</span>
          <textarea
            className="mt-2 min-h-32 w-full resize-y rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2.5 text-sm leading-6 text-white focus:border-brand-300/50 focus:outline-none"
            maxLength={4_000}
            onChange={(event) => onChange({ ...value, prompt: event.target.value })}
            placeholder="写成用户在真实工作中会提交的请求。"
            value={value.prompt}
          />
        </label>
      </div>

      <details className="mt-5 rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <summary className="cursor-pointer text-sm font-semibold text-slate-300">添加文件或自动检查（可选）</summary>
      <section className="mt-5" aria-label={`${value.name || `用例 ${index + 1}`} 的文本夹具`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 className="text-sm font-semibold text-white">测试时要附带的文本文件</h4>
            <p className="mt-1 text-xs text-slate-500">只有任务确实依赖文件时才需要添加。</p>
          </div>
          <button
            className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.055] disabled:opacity-40"
            disabled={value.fixtures.length >= 10}
            onClick={() => onChange({ ...value, fixtures: [...value.fixtures, { path: `fixture-${value.fixtures.length + 1}.txt`, content: "" }] })}
            type="button"
          >
            <FilePlus2 aria-hidden="true" size={14} /> 添加测试文件
          </button>
        </div>
        <div className="mt-3 space-y-3">
          {value.fixtures.map((fixture, fixtureIndex) => (
            <div className="grid gap-2 rounded-lg bg-white/[0.025] p-3 sm:grid-cols-[minmax(140px,0.35fr)_minmax(0,1fr)_auto]" key={`${fixture.path}-${fixtureIndex}`}>
              <input
                aria-label={`用例 ${index + 1} 夹具 ${fixtureIndex + 1} 路径`}
                className="rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 font-mono text-xs text-white focus:border-brand-300/50 focus:outline-none"
                onChange={(event) => onChange({
                  ...value,
                  fixtures: value.fixtures.map((item, current) => current === fixtureIndex ? { ...item, path: event.target.value } : item),
                })}
                placeholder="sample/input.txt"
                value={fixture.path}
              />
              <textarea
                aria-label={`用例 ${index + 1} 夹具 ${fixtureIndex + 1} 内容`}
                className="min-h-20 resize-y rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 font-mono text-xs leading-5 text-white focus:border-brand-300/50 focus:outline-none"
                onChange={(event) => onChange({
                  ...value,
                  fixtures: value.fixtures.map((item, current) => current === fixtureIndex ? { ...item, content: event.target.value } : item),
                })}
                value={fixture.content}
              />
              <button
                aria-label={`移除用例 ${index + 1} 的夹具 ${fixtureIndex + 1}`}
                className="self-start rounded-md p-2 text-slate-500 transition hover:bg-rose-300/10 hover:text-rose-100"
                onClick={() => onChange({ ...value, fixtures: value.fixtures.filter((_, current) => current !== fixtureIndex) })}
                type="button"
              ><Trash2 aria-hidden="true" size={15} /></button>
            </div>
          ))}
        </div>
      </section>

      <section className="mt-5" aria-label={`${value.name || `用例 ${index + 1}`} 的确定性断言`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 className="text-sm font-semibold text-white">自动检查规则</h4>
            <p className="mt-1 text-xs text-slate-500">可自动检查是否包含关键内容；最终结论仍由你确认。</p>
          </div>
          <button
            className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.055]"
            onClick={() => onChange({ ...value, assertions: [...value.assertions, { kind: "contains", value: "" }] })}
            type="button"
          ><Plus aria-hidden="true" size={14} /> 添加检查</button>
        </div>
        <div className="mt-3 space-y-2">
          {value.assertions.map((assertion, assertionIndex) => (
            <div className="grid gap-2 rounded-lg bg-white/[0.025] p-3 sm:grid-cols-[180px_minmax(0,1fr)_auto]" key={`${assertion.kind}-${assertionIndex}`}>
              <select
                aria-label={`用例 ${index + 1} 断言 ${assertionIndex + 1} 类型`}
                className="rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-xs text-white focus:border-brand-300/50 focus:outline-none"
                onChange={(event) => updateAssertion(assertionIndex, {
                  kind: event.target.value as SkillEvaluationAssertionKind,
                  value: "",
                  path: "",
                  schema: null,
                  sha256: "",
                })}
                value={assertion.kind}
              >
                {Object.entries(ASSERTION_LABELS).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}
              </select>
              {assertion.kind === "json_schema" ? (
                <textarea
                  aria-label={`用例 ${index + 1} JSON Schema`}
                  className="min-h-20 rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 font-mono text-xs text-white focus:border-brand-300/50 focus:outline-none"
                  onChange={(event) => {
                    try {
                      updateAssertion(assertionIndex, { schema: JSON.parse(event.target.value) as Record<string, unknown>, value: event.target.value });
                    } catch {
                      updateAssertion(assertionIndex, { schema: null, value: event.target.value });
                    }
                  }}
                  placeholder={'{"type":"object","required":["summary"]}'}
                  value={assertion.value ?? (assertion.schema ? JSON.stringify(assertion.schema, null, 2) : "")}
                />
              ) : assertion.kind === "file_exists" ? (
                <input aria-label={`用例 ${index + 1} 文件路径`} className="rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 font-mono text-xs text-white focus:border-brand-300/50 focus:outline-none" onChange={(event) => updateAssertion(assertionIndex, { path: event.target.value })} placeholder="report.md" value={assertion.path ?? ""} />
              ) : assertion.kind === "file_sha256" ? (
                <div className="grid gap-2 lg:grid-cols-[0.4fr_0.6fr]">
                  <input aria-label={`用例 ${index + 1} 文件路径`} className="rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 font-mono text-xs text-white focus:border-brand-300/50 focus:outline-none" onChange={(event) => updateAssertion(assertionIndex, { path: event.target.value })} placeholder="report.md" value={assertion.path ?? ""} />
                  <input aria-label={`用例 ${index + 1} 文件 SHA-256`} className="rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 font-mono text-xs text-white focus:border-brand-300/50 focus:outline-none" onChange={(event) => updateAssertion(assertionIndex, { sha256: event.target.value.toLowerCase() })} placeholder="64 位 SHA-256" value={assertion.sha256 ?? ""} />
                </div>
              ) : (
                <input aria-label={`用例 ${index + 1} 断言值`} className="rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 text-xs text-white focus:border-brand-300/50 focus:outline-none" onChange={(event) => updateAssertion(assertionIndex, { value: event.target.value })} placeholder="期望文本" value={assertion.value ?? ""} />
              )}
              <button aria-label={`移除用例 ${index + 1} 的断言 ${assertionIndex + 1}`} className="self-start rounded-md p-2 text-slate-500 transition hover:bg-rose-300/10 hover:text-rose-100" onClick={() => onChange({ ...value, assertions: value.assertions.filter((_, current) => current !== assertionIndex) })} type="button"><Trash2 aria-hidden="true" size={15} /></button>
            </div>
          ))}
        </div>
      </section>
      </details>
    </article>
  );
}

export default function SkillEvaluationDesigner({
  session,
  draft,
  onSessionChange,
  onRunStarted,
  onError,
  onNotice,
  suiteEnabled = false,
}: {
  session: SkillCreatorSession;
  draft: SkillCreatorDraft;
  onSessionChange: (session: SkillCreatorSession) => Promise<void> | void;
  onRunStarted: (run: SkillEvaluationRun) => void;
  onError: (error: unknown, fallback: string) => void;
  onNotice: (message: string) => void;
  suiteEnabled?: boolean;
}) {
  const [mode, setMode] = useState<SkillCreatorQualityMode>(session.quality_mode ?? "objective");
  const [cases, setCases] = useState<SkillEvaluationSuiteCase[]>(() => initialCases(session));
  const [repetitions, setRepetitions] = useState(session.evaluation_repetitions ?? 1);
  const [savedSignature, setSavedSignature] = useState(() => JSON.stringify(initialCases(session)));
  const [changeReason, setChangeReason] = useState("");
  const [busy, setBusy] = useState("");
  const [waiverReason, setWaiverReason] = useState("");
  const [waiverConfirmed, setWaiverConfirmed] = useState(false);

  useEffect(() => {
    if (session.evaluation_suite?.cases.length) {
      setCases(session.evaluation_suite.cases);
      setSavedSignature(JSON.stringify(session.evaluation_suite.cases));
      setChangeReason("");
      return;
    }
    if (!session.evaluation_cases?.length) return;
    const legacyCases = initialCases(session);
    setCases(legacyCases);
    setSavedSignature(JSON.stringify(legacyCases));
  }, [session.cases_revision, session.evaluation_cases, session.evaluation_suite]);

  const useSuite = Boolean(session.evaluation_suite);
  const mustUseSuite = suiteEnabled && !useSuite && !session.cases_revision;
  const suiteConfirmed = session.evaluation_suite?.state === "confirmed" && !session.evaluation_suite.stale;
  const complete = useMemo(() => casesComplete(cases, useSuite), [cases, useSuite]);
  const dirty = JSON.stringify(cases) !== savedSignature || mode !== (session.quality_mode ?? "objective");
  const suiteNeedsRebase = Boolean(session.evaluation_suite?.stale) && !dirty;
  const canEvaluate = complete && !dirty && (useSuite ? suiteConfirmed : Boolean(session.cases_revision));
  const maxRepetitions = session.regression_governance?.max_repetitions ?? 3;
  const targetCount = session.regression_governance?.target_count ?? 2;
  const estimatedCalls = cases.length * targetCount * repetitions;

  async function saveCases() {
    setBusy("save");
    try {
      let current = session;
      if (mode !== (session.quality_mode ?? "objective")) {
        current = await updateSkillCreatorSession(session.session_id, {
          expected_session_revision: session.session_revision,
          quality_mode: mode,
        });
      }
      const updated = current.evaluation_suite
        ? await updateSkillCreatorEvaluationSuite(current, draft, cases, changeReason)
        : await saveSkillCreatorEvaluationCases(current, draft, cases);
      await onSessionChange(updated);
      setSavedSignature(JSON.stringify(updated.evaluation_suite?.cases ?? updated.evaluation_cases ?? cases));
      setChangeReason("");
      onNotice(current.evaluation_suite ? "测试套件新 revision 已保存，确认后才能运行。" : "三个真实用例已保存，并绑定当前草稿摘要。");
    } catch (error) {
      onError(error, "测试用例保存失败。");
    } finally {
      setBusy("");
    }
  }

  async function generateSuite() {
    setBusy("suite-generate");
    try {
      const updated = await generateSkillCreatorEvaluationSuite(session, draft);
      await onSessionChange(updated);
      onNotice(session.cases_revision ? "旧三例已无模型迁移为套件 revision 1。" : "测试设计助手已生成三类核心用例草案，请检查后确认。 ");
    } catch (error) {
      onError(error, "测试套件生成失败。");
    } finally {
      setBusy("");
    }
  }

  async function confirmSuite() {
    setBusy("suite-confirm");
    try {
      const updated = await confirmSkillCreatorEvaluationSuite(session, draft);
      await onSessionChange(updated);
      setSavedSignature(JSON.stringify(updated.evaluation_suite?.cases ?? cases));
      onNotice("测试套件已冻结；后续修改会形成新 revision 并使旧评测过期。");
    } catch (error) {
      onError(error, "测试套件确认失败。");
    } finally {
      setBusy("");
    }
  }

  function addRegressionCase() {
    const nextIndex = cases.length;
    setCases((current) => [...current, {
      ...emptyCase(nextIndex, session),
      case_id: `user-regression-${crypto.randomUUID()}`,
      name: `回归案例 ${current.filter((item) => item.role === "regression").length + 1}`,
      prompt: "",
      expected_behavior: "",
      role: "regression",
      source: "user",
    }]);
  }

  async function startEvaluation() {
    setBusy("start");
    try {
      const result = await startSkillCreatorEvaluation(session, draft, repetitions);
      if (result.session) await onSessionChange(result.session);
      onRunStarted(result.run);
      onNotice("隔离对照评测已开始。你可以离开页面，稍后返回查看进度。");
    } catch (error) {
      onError(error, "对照评测启动失败。");
    } finally {
      setBusy("");
    }
  }

  async function waiveEvaluation() {
    if (!waiverConfirmed || waiverReason.trim().length < 8) return;
    setBusy("waive");
    try {
      let current = session;
      if ((session.quality_mode ?? "objective") !== "subjective") {
        current = await updateSkillCreatorSession(session.session_id, {
          expected_session_revision: session.session_revision,
          quality_mode: "subjective",
        });
      }
      const updated = await waiveSkillCreatorEvaluation(current, draft, waiverReason);
      await onSessionChange(updated);
      onNotice("已记录人工评测豁免。安装前仍会再次核对当前摘要与安全校验。");
    } catch (error) {
      onError(error, "评测豁免保存失败。");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="mt-5 space-y-5">
      <section className="rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6" aria-labelledby="creator-test-design-heading">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex items-center gap-3">
              <FlaskConical aria-hidden="true" className="text-brand-100" size={20} />
              <h2 className="text-xl font-semibold text-white" id="creator-test-design-heading">用三个真实任务试一试</h2>
            </div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">AI 会准备“正常使用、信息不足、明显不适用”三类任务。你只需检查任务和期望结果是否真实；文件与自动检查都是可选的高级设置。</p>
          </div>
          <label className="flex items-center gap-3 text-sm text-slate-300">
            <span className="font-semibold">每侧重复</span>
            <select className="rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-white" onChange={(event) => setRepetitions(Number(event.target.value))} value={repetitions}>
              {[1, 2, 3].filter((value) => value <= maxRepetitions).map((value) => <option key={value} value={value}>{value} 次</option>)}
            </select>
          </label>
        </div>

        <div className="mt-5 rounded-lg border border-brand-300/20 bg-brand-300/[0.055] p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-white">{useSuite ? "三类任务已准备" : "还没有测试任务"}</p>
              <p className="mt-1 text-xs leading-5 text-slate-400">{suiteConfirmed ? "当前测试已确认，可以开始运行。" : suiteNeedsRebase ? "草稿已更新；测试内容未变，沿用到新版本后再确认即可。" : useSuite ? dirty ? "请检查并保存当前修改。" : "任务已保存，请确认后运行。" : "让 AI 先生成三类任务草案，你可以再修改。"}</p>
            </div>
            {!useSuite ? (
              <button className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-brand-200 px-4 py-2.5 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void generateSuite()} type="button"><Sparkles aria-hidden="true" size={15} />{busy === "suite-generate" ? "正在准备…" : session.cases_revision ? "沿用已有测试" : "让 AI 准备三个任务"}</button>
            ) : suiteConfirmed ? (
              <span className="inline-flex items-center gap-2 text-sm font-semibold text-emerald-100"><Check aria-hidden="true" size={15} />已冻结</span>
            ) : (
              <div className="flex flex-wrap gap-2">
                <button className="inline-flex items-center justify-center gap-2 rounded-md border border-white/15 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40" disabled={dirty || Boolean(busy)} onClick={() => void generateSuite()} type="button"><Sparkles aria-hidden="true" size={15} />{busy === "suite-generate" ? "正在重新生成…" : "重新生成套件"}</button>
                <button className="inline-flex items-center justify-center gap-2 rounded-md bg-emerald-300 px-4 py-2.5 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={dirty || !complete || Boolean(busy)} onClick={() => void confirmSuite()} type="button"><Check aria-hidden="true" size={15} />{busy === "suite-confirm" ? "正在确认…" : "确认当前套件"}</button>
              </div>
            )}
          </div>
        </div>

        <fieldset className="mt-5 border-y border-white/10 py-4">
          <legend className="sr-only">质量评测模式</legend>
          <div className="grid gap-3 md:grid-cols-2">
            <label className={`cursor-pointer rounded-lg p-4 ${mode === "objective" ? "bg-brand-300/10 ring-1 ring-brand-300/40" : "bg-white/[0.025] ring-1 ring-white/10"}`}>
              <input checked={mode === "objective"} className="accent-cyan-300" name="quality-mode" onChange={() => setMode("objective")} type="radio" />
              <span className="ml-3 text-sm font-semibold text-white">客观任务（默认）</span>
              <span className="mt-2 block text-xs leading-5 text-slate-400">必须运行当前摘要对应的全部核心与回归案例，完成后由你评审。</span>
            </label>
            <label className={`cursor-pointer rounded-lg p-4 ${mode === "subjective" ? "bg-amber-300/[0.08] ring-1 ring-amber-300/30" : "bg-white/[0.025] ring-1 ring-white/10"}`}>
              <input checked={mode === "subjective"} className="accent-amber-300" name="quality-mode" onChange={() => setMode("subjective")} type="radio" />
              <span className="ml-3 text-sm font-semibold text-white">主观创作任务</span>
              <span className="mt-2 block text-xs leading-5 text-slate-400">仍建议运行三例；确实无法用对照判断时，可由你说明原因并豁免。</span>
            </label>
          </div>
        </fieldset>

        <div className="mt-6">
          {cases.map((item, index) => (
            <div className="relative" key={item.case_id || index}>
              {useSuite && item.role === "regression" ? (
                <button aria-label={`删除 ${item.name}`} className="absolute right-0 top-6 z-10 inline-flex items-center gap-1 rounded-md border border-rose-300/20 px-2 py-1 text-xs text-rose-100" onClick={() => setCases((current) => current.filter((_, currentIndex) => currentIndex !== index))} type="button"><Trash2 aria-hidden="true" size={12} />删除回归</button>
              ) : null}
              <CaseEditor index={index} onChange={(next) => setCases((current) => current.map((entry, currentIndex) => currentIndex === index ? next : entry))} value={item} />
            </div>
          ))}
        </div>

        {useSuite ? (
          <div className="space-y-4 border-t border-white/10 pt-5">
            <button className="inline-flex items-center gap-2 rounded-md border border-white/15 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={cases.filter((item) => item.role === "regression").length >= 9 || Boolean(busy)} onClick={addRegressionCase} type="button"><Plus aria-hidden="true" size={14} />加入用户确认的回归案例</button>
            {dirty && session.evaluation_suite ? (
              <label className="block">
                <span className="text-xs font-semibold text-slate-300">套件修改原因</span>
                <textarea className="mt-2 min-h-20 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2.5 text-sm text-white" maxLength={4_000} onChange={(event) => setChangeReason(event.target.value)} placeholder="说明新增、删除或改写案例的原因；历史 revision 不会被覆盖。" value={changeReason} />
              </label>
            ) : null}
          </div>
        ) : null}

        {!complete ? (
          <div className="flex items-start gap-3 rounded-lg bg-amber-300/[0.08] p-4 text-sm leading-6 text-amber-100" role="status">
            <AlertTriangle aria-hidden="true" className="mt-0.5 shrink-0" size={17} />
            三类核心用例和所有回归案例都必须填写名称、真实提示和期望行为。夹具路径不得使用绝对路径或 ..，已添加的断言也必须完整。
          </div>
        ) : null}

        <div className="mt-5 flex flex-col gap-3 border-t border-white/10 pt-5 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs leading-5 text-slate-500">这次会运行约 {estimatedCalls} 次，用相同设置比较“未使用 Skill”和“使用当前 Skill”的结果。</p>
          <div className="flex flex-wrap gap-2">
            <button className="inline-flex min-h-11 items-center gap-2 rounded-md border border-white/15 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-white/[0.055] disabled:cursor-not-allowed disabled:opacity-40" disabled={mustUseSuite || !complete || (useSuite && dirty && !changeReason.trim()) || Boolean(busy)} onClick={() => void saveCases()} type="button"><Save aria-hidden="true" size={15} />{busy === "save" ? "正在保存…" : mustUseSuite ? "先准备三个任务" : suiteNeedsRebase ? "沿用到新版本" : "保存测试任务"}</button>
            <button className="inline-flex min-h-11 items-center gap-2 rounded-md bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={!canEvaluate || Boolean(busy)} onClick={() => void startEvaluation()} type="button"><FlaskConical aria-hidden="true" size={15} />{busy === "start" ? "正在启动…" : "开始试用对比"}</button>
          </div>
        </div>
      </section>

      {mode === "subjective" ? (
        <section className="rounded-lg border border-amber-300/25 bg-amber-300/[0.055] p-5" aria-labelledby="creator-waiver-heading">
          <div className="flex items-start gap-3">
            <ShieldAlert aria-hidden="true" className="mt-0.5 shrink-0 text-amber-100" size={20} />
            <div>
              <h2 className="text-base font-semibold text-white" id="creator-waiver-heading">人工豁免行为评测</h2>
              <p className="mt-2 text-sm leading-6 text-amber-50/80">仅适用于难以客观比较的创作类 Skill。豁免不会跳过结构、安全、凭据和内容完整度检查。</p>
            </div>
          </div>
          <label className="mt-4 block" htmlFor="creator-waiver-reason">
            <span className="text-xs font-semibold text-amber-50">豁免原因（至少 8 个字符）</span>
            <textarea className="mt-2 min-h-24 w-full resize-y rounded-lg border border-amber-300/20 bg-ink-950/70 px-3 py-2.5 text-sm leading-6 text-white focus:border-amber-200/60 focus:outline-none" id="creator-waiver-reason" maxLength={1_000} onChange={(event) => setWaiverReason(event.target.value)} value={waiverReason} />
          </label>
          <label className="mt-3 flex items-start gap-3 text-sm leading-6 text-amber-50/90">
            <input checked={waiverConfirmed} className="mt-1 h-4 w-4 accent-amber-300" onChange={(event) => setWaiverConfirmed(event.target.checked)} type="checkbox" />
            我确认这是主观创作任务，并愿意由人工判断代替 baseline/with-skill 对照评测。
          </label>
          <button className="mt-4 rounded-md border border-amber-200/30 bg-amber-200/10 px-4 py-2.5 text-sm font-semibold text-amber-50 transition hover:bg-amber-200/15 disabled:cursor-not-allowed disabled:opacity-40" disabled={!waiverConfirmed || waiverReason.trim().length < 8 || Boolean(busy)} onClick={() => void waiveEvaluation()} type="button">{busy === "waive" ? "正在记录…" : "确认人工豁免"}</button>
        </section>
      ) : null}
    </div>
  );
}
