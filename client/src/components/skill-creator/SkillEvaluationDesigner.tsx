import { AlertTriangle, FilePlus2, FlaskConical, Plus, Save, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  saveSkillCreatorEvaluationCases,
  startSkillCreatorEvaluation,
  updateSkillCreatorSession,
  waiveSkillCreatorEvaluation,
  type SkillCreatorDraft,
  type SkillCreatorQualityMode,
  type SkillCreatorSession,
  type SkillEvaluationAssertion,
  type SkillEvaluationAssertionKind,
  type SkillEvaluationCase,
  type SkillEvaluationRun,
} from "../../utils/skillCreatorApi";

const ASSERTION_LABELS: Record<SkillEvaluationAssertionKind, string> = {
  exact_match: "输出完全等于",
  contains: "输出包含",
  not_contains: "输出不包含",
  json_schema: "输出符合 JSON Schema",
  file_exists: "生成指定文件",
  file_sha256: "文件摘要匹配",
};

function emptyCase(index: number, session: SkillCreatorSession): SkillEvaluationCase {
  return {
    case_id: `draft-case-${index + 1}`,
    name: `用例 ${index + 1}`,
    prompt: session.positive_examples[index] ?? "",
    expected_behavior: session.success_criteria[index] ?? session.expected_output ?? "",
    fixtures: [],
    assertions: [],
  };
}

function initialCases(session: SkillCreatorSession) {
  if (session.evaluation_cases?.length === 3) return session.evaluation_cases;
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

function casesComplete(cases: SkillEvaluationCase[]) {
  return cases.length === 3 && cases.every((item) =>
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
  value: SkillEvaluationCase;
  onChange: (value: SkillEvaluationCase) => void;
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
        <span className="text-xs text-slate-500">baseline 与 with-skill 使用完全相同的输入</span>
      </div>

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

      <section className="mt-5" aria-label={`${value.name || `用例 ${index + 1}`} 的文本夹具`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 className="text-sm font-semibold text-white">UTF-8 文本夹具</h4>
            <p className="mt-1 text-xs text-slate-500">最多 10 个，运行时以只读方式放入 inputs/。</p>
          </div>
          <button
            className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.055] disabled:opacity-40"
            disabled={value.fixtures.length >= 10}
            onClick={() => onChange({ ...value, fixtures: [...value.fixtures, { path: `fixture-${value.fixtures.length + 1}.txt`, content: "" }] })}
            type="button"
          >
            <FilePlus2 aria-hidden="true" size={14} /> 添加夹具
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
            <h4 className="text-sm font-semibold text-white">确定性断言（可选）</h4>
            <p className="mt-1 text-xs text-slate-500">断言用于辅助评审，最终结论仍由你确认。</p>
          </div>
          <button
            className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.055]"
            onClick={() => onChange({ ...value, assertions: [...value.assertions, { kind: "contains", value: "" }] })}
            type="button"
          ><Plus aria-hidden="true" size={14} /> 添加断言</button>
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
}: {
  session: SkillCreatorSession;
  draft: SkillCreatorDraft;
  onSessionChange: (session: SkillCreatorSession) => Promise<void> | void;
  onRunStarted: (run: SkillEvaluationRun) => void;
  onError: (error: unknown, fallback: string) => void;
  onNotice: (message: string) => void;
}) {
  const [mode, setMode] = useState<SkillCreatorQualityMode>(session.quality_mode ?? "objective");
  const [cases, setCases] = useState<SkillEvaluationCase[]>(() => initialCases(session));
  const [repetitions, setRepetitions] = useState(session.evaluation_repetitions ?? 1);
  const [savedSignature, setSavedSignature] = useState(() => JSON.stringify(session.evaluation_cases ?? []));
  const [busy, setBusy] = useState("");
  const [waiverReason, setWaiverReason] = useState("");
  const [waiverConfirmed, setWaiverConfirmed] = useState(false);

  useEffect(() => {
    if (!session.evaluation_cases?.length) return;
    setCases(session.evaluation_cases);
    setSavedSignature(JSON.stringify(session.evaluation_cases));
  }, [session.cases_revision, session.evaluation_cases]);

  const complete = useMemo(() => casesComplete(cases), [cases]);
  const dirty = JSON.stringify(cases) !== savedSignature || mode !== (session.quality_mode ?? "objective");
  const canEvaluate = complete && !dirty && Boolean(session.cases_revision);

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
      const updated = await saveSkillCreatorEvaluationCases(current, draft, cases);
      await onSessionChange(updated);
      setSavedSignature(JSON.stringify(updated.evaluation_cases ?? cases));
      onNotice("三个真实用例已保存，并绑定当前草稿摘要。");
    } catch (error) {
      onError(error, "测试用例保存失败。");
    } finally {
      setBusy("");
    }
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
              <h2 className="text-xl font-semibold text-white" id="creator-test-design-heading">设计三个真实用例</h2>
            </div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">每个用例都会在相同模型、提示、预算和文本夹具下分别运行 baseline 与 with-skill。只有 Skill Overlay 不同。</p>
          </div>
          <label className="flex items-center gap-3 text-sm text-slate-300">
            <span className="font-semibold">每侧重复</span>
            <select className="rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-white" onChange={(event) => setRepetitions(Number(event.target.value))} value={repetitions}>
              {[1, 2, 3].map((value) => <option key={value} value={value}>{value} 次</option>)}
            </select>
          </label>
        </div>

        <fieldset className="mt-5 border-y border-white/10 py-4">
          <legend className="sr-only">质量评测模式</legend>
          <div className="grid gap-3 md:grid-cols-2">
            <label className={`cursor-pointer rounded-lg p-4 ${mode === "objective" ? "bg-brand-300/10 ring-1 ring-brand-300/40" : "bg-white/[0.025] ring-1 ring-white/10"}`}>
              <input checked={mode === "objective"} className="accent-cyan-300" name="quality-mode" onChange={() => setMode("objective")} type="radio" />
              <span className="ml-3 text-sm font-semibold text-white">客观任务（默认）</span>
              <span className="mt-2 block text-xs leading-5 text-slate-400">必须运行当前摘要对应的三个对照用例，完成后由你评审。</span>
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
            <CaseEditor key={item.case_id || index} index={index} onChange={(next) => setCases((current) => current.map((entry, currentIndex) => currentIndex === index ? next : entry))} value={item} />
          ))}
        </div>

        {!complete ? (
          <div className="flex items-start gap-3 rounded-lg bg-amber-300/[0.08] p-4 text-sm leading-6 text-amber-100" role="status">
            <AlertTriangle aria-hidden="true" className="mt-0.5 shrink-0" size={17} />
            三个用例都必须填写名称、真实提示和期望行为。夹具路径不得使用绝对路径或 ..，已添加的断言也必须完整。
          </div>
        ) : null}

        <div className="mt-5 flex flex-col gap-3 border-t border-white/10 pt-5 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs leading-5 text-slate-500">保存后，测试集与草稿 revision {draft.revision}、{draft.content_digest.slice(0, 12)}… 绑定。</p>
          <div className="flex flex-wrap gap-2">
            <button className="inline-flex items-center gap-2 rounded-md border border-white/15 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-white/[0.055] disabled:cursor-not-allowed disabled:opacity-40" disabled={!complete || Boolean(busy)} onClick={() => void saveCases()} type="button"><Save aria-hidden="true" size={15} />{busy === "save" ? "正在保存…" : "保存三个用例"}</button>
            <button className="inline-flex items-center gap-2 rounded-md bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={!canEvaluate || Boolean(busy)} onClick={() => void startEvaluation()} type="button"><FlaskConical aria-hidden="true" size={15} />{busy === "start" ? "正在启动…" : "开始对照评测"}</button>
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
