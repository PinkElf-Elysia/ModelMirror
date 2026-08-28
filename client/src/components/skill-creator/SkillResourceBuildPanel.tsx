import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Code2,
  FileDiff,
  FileText,
  FlaskConical,
  LoaderCircle,
  Pencil,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
} from "lucide-react";

import {
  advanceSkillCreatorResourceBuild,
  editSkillCreatorResource,
  finalizeSkillCreatorResourceBuild,
  readSkillCreatorResourceBuild,
  reviewSkillCreatorResource,
  startSkillCreatorResourceBuild,
  SkillCreatorApiError,
  type SkillCreatorProposal,
  type SkillCreatorSession,
  type SkillCreatorStatus,
  type SkillPackageIssue,
  type SkillResourceBuild,
  type SkillResourceBuildItem,
} from "../../utils/skillCreatorApi";

const STATE_LABELS: Record<SkillResourceBuildItem["state"], string> = {
  planned: "待生成",
  generating: "生成中",
  awaiting_review: "等待确认",
  accepted: "已确认",
  revision_requested: "等待重做",
  failed: "需要处理",
  stale: "已过期",
};

const KIND_LABELS = {
  script: "脚本",
  reference: "参考资料",
  asset: "输出模板",
} as const;

function byteSize(value?: string | null) {
  return new TextEncoder().encode(value ?? "").length;
}

function shortDigest(value?: string | null) {
  return value ? value.slice(0, 10) : "未生成";
}

function errorMessage(value: unknown, fallback: string) {
  if (
    value instanceof SkillCreatorApiError
    && value.code === "skill_creator_resource_proposal_invalid"
  ) {
    return value.issues.length
      ? "最终检查发现了具体问题。请使用下方的修复入口重新生成 SKILL.md。"
      : "最终检查未通过，但服务端没有返回具体原因。请重新检查最终包后再试。";
  }
  return value instanceof Error && value.message ? value.message : fallback;
}

const SKILL_ISSUE_MESSAGES: Record<string, string> = {
  creator_description_trigger_missing:
    "Skill 描述需要明确说明什么时候使用，以及哪些相似任务不应使用。",
  creator_description_invalid:
    "Skill 描述不符合要求，需要重新生成清晰的用途、使用时机和边界。",
  creator_scope_missing:
    "SKILL.md 需要明确说明用途、适用范围和不适用边界。",
  creator_requirement_uncovered:
    "最终文档没有覆盖已确认的一项需求，需要补充对应步骤或说明。",
  creator_requirement_location_invalid:
    "需求覆盖位置与当前文档章节不一致，需要重新整理章节和覆盖关系。",
};

function issueMessage(issue: SkillPackageIssue) {
  return SKILL_ISSUE_MESSAGES[issue.code] ?? issue.message;
}

const SKILL_ISSUE_REPAIR_GUIDANCE: Record<string, string> = {
  skill_creator_skill_markdown_heading_boundary_invalid:
    "重新生成完整 SKILL.md：确保每个二级章节都从新行开始，删除重复或粘连的章节，并保留已确认资源的准确路径。请使用简体中文，只返回一份完整文档。",
  skill_creator_skill_markdown_duplicate_section:
    "重新生成完整 SKILL.md：合并重复章节，每个二级章节只保留一份，并保留已确认资源的准确路径。请使用简体中文，只返回一份完整文档。",
  skill_creator_reference_search_missing:
    "重新生成完整 SKILL.md：为多份或较长的参考资料补充有界的 rg 或 sandbox_search_files 检索示例，并明确检索路径和范围。请使用简体中文，只返回一份完整文档。",
  creator_description_trigger_missing:
    "重新生成完整 SKILL.md：在 frontmatter description 中明确写出能力、适用场景和不适用边界，同时保留已通过的触发描述。请使用简体中文，只返回一份完整文档。",
};

function skillRepairFeedback(issues: SkillPackageIssue[]) {
  const suggestions = Array.from(new Set(issues
    .map((issue) => SKILL_ISSUE_REPAIR_GUIDANCE[issue.code])
    .filter((value): value is string => Boolean(value))));
  if (suggestions.length) return suggestions.join("\n");
  return `重新生成完整 SKILL.md，并修复以下检查问题：${issues.map(issueMessage).join("；")}。请保留已确认资源，只返回一份完整文档。`;
}

function ValidationIssues({
  issues,
  action,
  title = "需要处理",
}: {
  issues: SkillPackageIssue[];
  title?: string;
  action?: {
    disabled: boolean;
    label: string;
    onClick: () => void;
  };
}) {
  if (!issues.length) return null;
  return (
    <div className="rounded-md border border-rose-300/20 bg-rose-300/[0.07] p-4">
      <p className="text-sm font-semibold text-rose-50">{title}</p>
      <ul className="mt-2 space-y-3 text-xs leading-5 text-rose-100">
        {issues.map((issue, index) => (
          <li key={`${issue.code}-${index}`}>
            <p>{issueMessage(issue)}</p>
            <details className="mt-1 text-slate-400">
              <summary className="cursor-pointer">技术信息</summary>
              <code className="mt-1 block break-all">{issue.code}{issue.path ? ` · ${issue.path}` : ""}</code>
            </details>
          </li>
        ))}
      </ul>
      {action ? (
        <div className="mt-4 border-t border-rose-200/15 pt-4">
          <p className="text-xs leading-5 text-rose-100">
            将使用一次生成额度，只重新生成 SKILL.md；已确认的参考资料、脚本和模板不会改变。
          </p>
          <button
            className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-full bg-rose-100 px-4 py-2 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={action.disabled}
            onClick={action.onClick}
            type="button"
          >
            <RotateCcw aria-hidden="true" size={15} />
            {action.label}
          </button>
        </div>
      ) : null}
    </div>
  );
}

interface Props {
  session: SkillCreatorSession;
  status: SkillCreatorStatus;
  onProposal: (proposal: SkillCreatorProposal) => void | Promise<void>;
  onSessionRefresh: () => void | Promise<void>;
}

function Receipt({ item }: { item: SkillResourceBuildItem }) {
  if (item.kind !== "script") return null;
  const receipt = item.script_receipt;
  const hookContractFailed = item.validation_issues.some((issue) =>
    issue.code.startsWith("skill_creator_hook_"));
  return (
    <section className="border-t border-white/10 pt-4" aria-labelledby={`receipt-${item.resource_id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="flex items-center gap-2 text-sm font-semibold text-white" id={`receipt-${item.resource_id}`}>
          <FlaskConical aria-hidden="true" size={15} />脚本实测
        </h4>
        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${receipt?.passed ? "bg-emerald-300/10 text-emerald-100" : "bg-amber-300/10 text-amber-100"}`}>
          {receipt?.passed ? (hookContractFailed ? "基础 CLI 通过" : "全部通过") : "尚无有效 receipt"}
        </span>
      </div>
      {hookContractFailed ? (
        <p className="mt-2 text-xs leading-5 text-rose-100">
          基础脚本用例已通过，但 Hook 的类型化 context/result 合同未通过，当前资源不能确认。
        </p>
      ) : null}
      {receipt ? (
        <div className="mt-3 space-y-2">
          <p className="font-mono text-[11px] text-slate-500">{receipt.profile} · {shortDigest(receipt.script_digest)}</p>
          {receipt.results.map((result) => (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] py-2 text-xs" key={result.test_id}>
              <span className="font-mono text-slate-300">{result.test_id}</span>
              <span className={result.passed ? "text-emerald-100" : "text-rose-100"}>
                {result.passed ? "通过" : `失败，退出码 ${result.exit_code}`} · {Math.round(result.duration_ms)} ms
              </span>
            </div>
          ))}
        </div>
      ) : <p className="mt-2 text-xs leading-5 text-slate-400">脚本必须在离线 Sidecar 中完成 1–3 个用例，内容摘要变化后旧 receipt 自动失效。</p>}
    </section>
  );
}

export default function SkillResourceBuildPanel({
  session,
  status,
  onProposal,
  onSessionRefresh,
}: Props) {
  const [build, setBuild] = useState<SkillResourceBuild | null>(session.resource_build ?? null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [finalizeIssues, setFinalizeIssues] = useState<SkillPackageIssue[]>([]);
  const [validationRefreshNeeded, setValidationRefreshNeeded] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [editing, setEditing] = useState(false);
  const [editedContent, setEditedContent] = useState("");

  useEffect(() => {
    if (session.resource_build) {
      setBuild(session.resource_build);
      setFinalizeIssues([]);
      setValidationRefreshNeeded(false);
    }
  }, [session.resource_build?.digest]);

  useEffect(() => {
    if (!build) return;
    if (build.phase !== "resources") {
      setSelectedId(null);
      return;
    }
    if (build.current_resource_id) {
      setSelectedId(build.current_resource_id);
      return;
    }
    if (
      build.hooks?.some((item) => item.action !== "delete")
      && build.resources.every((item) => item.state === "accepted")
      && !build.hook_manifest_digest
    ) {
      setSelectedId(null);
      return;
    }
    const preferred = build.resources.find((item) => item.state !== "accepted")?.resource_id
      ?? build.resources[0]?.resource_id
      ?? null;
    setSelectedId((current) => (
      current && build.resources.some((item) => item.resource_id === current)
        ? current
        : preferred
    ));
  }, [build?.digest]);

  useEffect(() => {
    if (!build || busy !== "next") return;
    const timer = window.setInterval(() => {
      void readSkillCreatorResourceBuild(build.build_id).then(setBuild).catch(() => undefined);
    }, 1_500);
    return () => window.clearInterval(timer);
  }, [build?.build_id, busy]);

  const selected = useMemo(
    () => build?.resources.find((item) => item.resource_id === selectedId) ?? null,
    [build, selectedId],
  );
  const acceptedCount = build?.resources.filter((item) => item.state === "accepted").length ?? 0;
  const totalCount = build?.resources.length ?? 0;
  const generatedBytes = build
    ? build.resources.reduce((sum, item) => sum + byteSize(item.content), 0) + byteSize(build.skill_markdown)
    : 0;
  const builderReady = Boolean(status.resource_builder_available);
  const activeHooks = build?.hooks?.filter((item) => item.action !== "delete") ?? [];
  const planHasActiveHooks = Boolean(session.resource_plan?.hooks?.some((item) => item.action !== "delete"));
  const hookBuildUnavailable = status.hook_authoring_enabled === false
    && (planHasActiveHooks || activeHooks.length > 0);
  const hookSandboxUnavailable = status.script_sandbox_configured === false
    && (planHasActiveHooks || activeHooks.length > 0);
  const canRunBuilder = builderReady && !hookBuildUnavailable && !hookSandboxUnavailable;
  const hookValidationPending = Boolean(
    build
    && build.phase === "resources"
    && activeHooks.length
    && build.resources.every((item) => item.state === "accepted")
  );
  const buildIssuesAreActionable = Boolean(
    build && ["awaiting_review", "failed"].includes(build.state),
  );
  const selectedValidationIssues = buildIssuesAreActionable
    ? selected?.validation_issues ?? []
    : [];
  const skillValidationIssues = buildIssuesAreActionable
    ? Array.from(new Map([
      ...(build?.skill_validation_issues ?? []),
      ...finalizeIssues,
    ].map((issue) => [`${issue.code}:${issue.path ?? ""}:${issue.field ?? ""}`, issue])).values())
    : [];

  function update(next: SkillResourceBuild, message = "") {
    setBuild(next);
    setFinalizeIssues([]);
    setValidationRefreshNeeded(false);
    if (message) setNotice(message);
    setError("");
  }

  async function run(label: string, operation: () => Promise<SkillResourceBuild>, success: string) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      update(await operation(), success);
    } catch (caught) {
      setError(errorMessage(caught, "资源构建操作失败。"));
    } finally {
      setBusy("");
    }
  }

  async function start() {
    setBusy("start");
    setError("");
    setNotice("");
    try {
      const started = await startSkillCreatorResourceBuild(session);
      setBuild(started);
      const generated = await advanceSkillCreatorResourceBuild(session, started);
      update(generated, generated.phase === "skill_markdown"
        ? "辅助内容已准备好，正在整理最终说明。"
        : "第一项内容已经生成并通过基础检查，请确认是否符合需要。");
      await onSessionRefresh();
    } catch (caught) {
      setError(errorMessage(caught, "资源生成启动失败。"));
    } finally {
      setBusy("");
    }
  }

  async function next() {
    if (!build) return;
    await run(
      "next",
      () => advanceSkillCreatorResourceBuild(session, build),
      build.phase === "skill_markdown" ? "最终 SKILL.md 已组装并完成校验。" : "当前资源已完整组装并完成校验。",
    );
  }

  async function reviewResource(decision: "accept" | "revise") {
    if (!build || !selected) return;
    if (decision === "revise" && !feedback.trim()) {
      setError("请说明需要重做的具体内容。");
      return;
    }
    if (decision === "revise") {
      setBusy("revise");
      setError("");
      setNotice("");
      try {
        const rejected = await reviewSkillCreatorResource(
          session,
          build,
          selected.resource_id,
          decision,
          feedback,
        );
        setBuild(rejected);
        setFeedback("");
        setEditing(false);
        const regenerated = await advanceSkillCreatorResourceBuild(session, rejected);
        update(regenerated, `${selected.path} 已按反馈重新生成并完成基础检查。`);
      } catch (caught) {
        setError(errorMessage(caught, "资源重做失败。已保存的反馈可在重新读取后继续处理。"));
      } finally {
        setBusy("");
      }
      return;
    }
    await run(
      decision,
      () => reviewSkillCreatorResource(session, build, selected.resource_id, decision, feedback),
      `已确认 ${selected.path}。`,
    );
    setFeedback("");
    setEditing(false);
  }

  async function saveEdit() {
    if (!build || !selected) return;
    await run(
      "edit",
      () => editSkillCreatorResource(session, build, selected.resource_id, editedContent),
      `已保存 ${selected.path} 的新构建 revision，并重新执行校验。`,
    );
    setEditing(false);
  }

  async function finalize(decision: "accept" | "revise", feedbackOverride?: string) {
    if (!build) return;
    const requestedFeedback = feedbackOverride?.trim() || feedback.trim();
    if (decision === "revise" && !requestedFeedback) {
      setError("请说明最终 SKILL.md 需要修改的内容。");
      return;
    }
    setBusy("finalize");
    setError("");
    setNotice("");
    try {
      const result = await finalizeSkillCreatorResourceBuild(session, build, decision, requestedFeedback);
      if (decision === "revise") {
        setBuild(result.build);
        const regenerated = await advanceSkillCreatorResourceBuild(session, result.build);
        update(regenerated, "最终 SKILL.md 已按反馈重新生成并完成校验。");
      } else {
        update(result.build, "最终包已形成标准草稿提案。");
      }
      if (result.proposal) await onProposal(result.proposal);
      await onSessionRefresh();
      setFeedback("");
    } catch (caught) {
      if (
        decision === "accept"
        && caught instanceof SkillCreatorApiError
        && caught.code === "skill_creator_resource_proposal_invalid"
      ) {
        setFinalizeIssues(caught.issues);
        setValidationRefreshNeeded(caught.issues.length === 0);
      }
      setError(errorMessage(caught, decision === "revise"
        ? "最终文档重做失败。已保存的反馈可在重新读取后继续处理。"
        : "最终包确认失败。"));
    } finally {
      setBusy("");
    }
  }

  if (!session.resource_plan || session.resource_plan.state !== "confirmed") {
    return (
      <section className="rounded-lg border border-amber-300/20 bg-amber-300/[0.06] p-5 text-sm text-amber-50">
        请先在“素材与资源计划”中确认并冻结资源计划。
      </section>
    );
  }

  const buildMatchesCurrentPlan = Boolean(build
    && !build.stale
    && build.state !== "stale"
    && build.plan_id === session.resource_plan.plan_id
    && build.plan_revision === session.resource_plan.revision
    && build.plan_digest === session.resource_plan.digest);

  if (!build || !buildMatchesCurrentPlan) {
    return (
      <section className="rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6" aria-labelledby="resource-build-start-heading">
        <h2 className="text-xl font-semibold text-white focus:outline-none" id="resource-build-start-heading" tabIndex={-1}>
          {build ? "按新方案重新生成" : "准备生成内容"}
        </h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">点击后会立即生成第一项内容。每项完成基础检查后只请你确认一次，不会用内部生成片段反复打扰。</p>
        {build ? (
          <p className="mt-3 rounded-md border border-amber-300/25 bg-amber-300/10 px-4 py-3 text-xs leading-5 text-amber-50">
            旧版本仍会只读保留；这次会严格按你刚确认的新方案重新生成。
          </p>
        ) : null}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-40" disabled={!canRunBuilder || Boolean(busy)} onClick={() => void start()} type="button">
            {busy === "start" ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={16} /> : <Play aria-hidden="true" size={16} />}
            {busy === "start" ? "正在生成第一项…" : build ? "按新方案开始生成" : "开始生成内容"}
          </button>
          <span className="text-xs text-slate-500">共 {session.resource_plan.resources.length} 项辅助内容{session.resource_plan.hooks?.length ? `、${session.resource_plan.hooks.length} 个 Hook` : ""}，最后自动整理完整使用说明</span>
        </div>
        {!builderReady ? <p className="mt-3 text-xs text-amber-200">模型网关不可用，当前只能查看已保存计划，不能开始生成。</p> : null}
        {hookBuildUnavailable ? <p className="mt-3 text-xs text-amber-200">该计划包含 Hook，但 Hook V2 当前已关闭。计划仍可查看；重新开启后才能开始构建。</p> : null}
        {hookSandboxUnavailable ? <p className="mt-3 text-xs text-amber-200">该计划包含 Hook，但离线 authoring Sidecar 不可用。修复隔离脚本实测环境后才能开始构建。</p> : null}
      </section>
    );
  }

  const activeTarget = hookValidationPending
    ? "Hook manifest 与离线 receipt"
    : build.phase === "skill_markdown"
      ? "SKILL.md"
      : selected?.path;
  const canEdit = Boolean(selected && ["create", "update"].includes(selected.action) && !build.proposal_id && build.state !== "generating");

  return (
    <section className="space-y-5" aria-labelledby="resource-build-heading">
      <div className="border-y border-white/10 py-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-white focus:outline-none" id="resource-build-heading" tabIndex={-1}>逐项生成内容</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">{activeTarget ? `正在处理：${activeTarget}` : "辅助内容已完成，准备整理最终使用说明。"}</p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-brand-300/10 px-3 py-1.5 text-brand-100">已确认 {acceptedCount}/{totalCount}</span>
            <details className="relative"><summary className="cursor-pointer list-none rounded-full bg-white/[0.055] px-3 py-1.5 text-slate-400">技术信息</summary><div className="absolute right-0 z-10 mt-2 w-48 rounded-lg border border-white/10 bg-ink-950 p-3 text-right shadow-xl"><p>构建版本 {build.revision}</p><p className="mt-1">{Math.ceil(generatedBytes / 1024)} KiB / 160 KiB</p></div></details>
          </div>
        </div>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/[0.06]" aria-label={`资源确认进度 ${acceptedCount}/${totalCount}`}>
          <div className="h-full bg-hire-300 transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${totalCount ? (acceptedCount / totalCount) * 100 : build.skill_markdown ? 100 : 0}%` }} />
        </div>
      </div>

      {error ? <p className="rounded-md border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-50" role="alert">{error}</p> : null}
      {validationRefreshNeeded && build ? (
        <button
          className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
          disabled={Boolean(busy)}
          onClick={() => void run(
            "refresh-validation",
            () => readSkillCreatorResourceBuild(build.build_id),
            "已重新检查最终包。",
          )}
          type="button"
        >
          <RefreshCw aria-hidden="true" size={15} />
          {busy === "refresh-validation" ? "正在重新检查…" : "重新检查最终包"}
        </button>
      ) : null}
      {notice ? <p className="rounded-md border border-emerald-300/20 bg-emerald-300/10 px-4 py-3 text-sm text-emerald-50" role="status">{notice}</p> : null}
      {build.stale || build.state === "stale" ? (
        <div className="rounded-md border border-amber-300/25 bg-amber-300/10 p-4 text-sm text-amber-50"><CircleAlert className="mr-2 inline" size={16} />用途、计划或草稿已变化。此构建只读保留，请回到上一步重新规划。</div>
      ) : null}

      <div className="grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)]">
        <nav className="min-w-0 border-b border-white/10 pb-4 xl:border-b-0 xl:border-r xl:pb-0 xl:pr-4" aria-label="资源文件进度">
          <div className="space-y-1">
            {build.resources.map((item) => (
              <button
                className={`flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2 text-left transition ${selectedId === item.resource_id ? "bg-brand-300/10 text-white" : "text-slate-300 hover:bg-white/[0.045]"}`}
                key={item.resource_id}
                onClick={() => { setSelectedId(item.resource_id); setEditing(false); }}
                type="button"
              >
                {item.kind === "script" ? <Code2 aria-hidden="true" size={15} /> : <FileText aria-hidden="true" size={15} />}
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-xs">{item.path}</span>
                  <span className={`mt-1 block text-[11px] ${item.state === "accepted" ? "text-emerald-100" : item.state === "failed" ? "text-rose-100" : "text-slate-500"}`}>{STATE_LABELS[item.state]}</span>
                </span>
              </button>
            ))}
            <button className={`flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2 text-left ${build.phase === "skill_markdown" || build.phase === "proposal" ? "bg-brand-300/10 text-white" : "text-slate-500"}`} onClick={() => setSelectedId(null)} type="button">
              <FileDiff aria-hidden="true" size={15} /><span><span className="block font-mono text-xs">SKILL.md</span><span className="mt-1 block text-[11px]">{build.skill_markdown ? (build.phase === "proposal" ? "已确认" : "等待确认") : "最后生成"}</span></span>
            </button>
            {activeHooks.length ? (
              <div className="mt-3 border-t border-white/10 pt-3">
                <p className="px-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-amber-100">Typed Hooks</p>
                {activeHooks.map((hook) => (
                  <div className="mt-1 flex items-start gap-3 px-3 py-2 text-xs" key={hook.hook_id}>
                    <ShieldCheck aria-hidden="true" className={hook.test_receipt?.passed ? "text-emerald-200" : "text-amber-200"} size={14} />
                    <span className="min-w-0"><span className="block truncate font-mono text-slate-300">{hook.hook_id}</span><span className="mt-1 block text-[11px] text-slate-500">{hook.event} · {hook.mode} · {hook.test_receipt?.passed ? "receipt 通过" : "等待实测"}</span></span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </nav>

        <div className="min-w-0">
          {selected ? (
            <div className="space-y-5">
              <header className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-brand-100">{KIND_LABELS[selected.kind]} · {selected.action}</p>
                  <h3 className="mt-1 break-all font-mono text-base font-semibold text-white">{selected.path}</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-400">{selected.purpose}</p>
                </div>
                <span className="rounded-full bg-white/[0.055] px-3 py-1.5 text-xs text-slate-300">{STATE_LABELS[selected.state]}</span>
              </header>

              <div className="flex flex-wrap gap-x-5 gap-y-2 border-y border-white/10 py-3 text-xs text-slate-400">
                <span>内部片段：{selected.chunks.length}</span><span>内容：{byteSize(selected.content)} bytes</span><span>摘要：{shortDigest(selected.content_digest)}</span><span>尝试：{selected.attempt}</span>
              </div>

              <ValidationIssues issues={selectedValidationIssues} />

              {editing ? (
                <div>
                  <label className="text-sm font-semibold text-white" htmlFor={`resource-editor-${selected.resource_id}`}>编辑完整资源</label>
                  <textarea className="mt-2 min-h-[360px] w-full resize-y rounded-md border border-white/10 bg-ink-950 px-4 py-3 font-mono text-xs leading-6 text-slate-100 focus:border-brand-300/50 focus:outline-none" id={`resource-editor-${selected.resource_id}`} onChange={(event) => setEditedContent(event.target.value)} value={editedContent} />
                  <div className="mt-3 flex flex-wrap justify-end gap-2">
                    <button className="min-h-11 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white" onClick={() => setEditing(false)} type="button">取消编辑</button>
                    <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={!editedContent.trim() || busy === "edit"} onClick={() => void saveEdit()} type="button"><Save aria-hidden="true" size={15} />{busy === "edit" ? "保存并校验…" : "保存资源 revision"}</button>
                  </div>
                </div>
              ) : selected.content ? (
                <pre className="max-h-[560px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-ink-950 p-4 font-mono text-xs leading-6 text-slate-200">{selected.content}</pre>
              ) : <p className="rounded-md border border-dashed border-white/15 p-6 text-center text-sm text-slate-400">该资源尚未生成完整内容。</p>}

              <Receipt item={selected} />

              {canEdit && selected.content && !editing ? <button className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white" onClick={() => { setEditedContent(selected.content ?? ""); setEditing(true); }} type="button"><Pencil aria-hidden="true" size={15} />直接编辑完整资源</button> : null}

              {build.current_resource_id === selected.resource_id && ["awaiting_review", "failed"].includes(build.state) ? (
                <div className="border-t border-white/10 pt-4">
                  <label className="text-sm font-semibold text-white" htmlFor="resource-review-feedback">重做反馈</label>
                  <textarea className="mt-2 min-h-24 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm leading-6 text-white" id="resource-review-feedback" maxLength={4000} onChange={(event) => setFeedback(event.target.value)} placeholder="仅在需要重做时填写具体修改要求。" value={feedback} />
                  <div className="mt-3 flex flex-wrap justify-end gap-2">
                    <button className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={!feedback.trim() || !canRunBuilder || Boolean(busy)} onClick={() => void reviewResource("revise")} type="button"><RotateCcw aria-hidden="true" size={15} />{busy === "revise" ? "正在按反馈重做…" : "按反馈重做"}</button>
                    <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={selectedValidationIssues.length > 0 || (selected.kind === "script" && !selected.script_receipt?.passed) || Boolean(busy)} onClick={() => void reviewResource("accept")} type="button"><CheckCircle2 aria-hidden="true" size={15} />确认完整资源</button>
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="space-y-5">
              <header>
                <p className="text-xs font-semibold text-brand-100">最终 Skill 文档</p>
                <h3 className="mt-1 text-lg font-semibold text-white">SKILL.md 与全包差异</h3>
                <p className="mt-2 text-sm leading-6 text-slate-400">附加资源全部确认后才生成。文档应精确导航资源，并保留输出合同与失败降级。</p>
              </header>
              {activeHooks.length ? (
                <section className="border-y border-white/10 py-4" aria-labelledby="hook-build-heading">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="flex items-center gap-2 text-sm font-semibold text-white" id="hook-build-heading"><ShieldCheck aria-hidden="true" size={16} />Hook 合同与实测</h4>
                    <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${activeHooks.every((item) => item.test_receipt?.passed) ? "bg-emerald-300/10 text-emerald-100" : "bg-amber-300/10 text-amber-100"}`}>{activeHooks.every((item) => item.test_receipt?.passed) ? "全部通过" : "等待离线实测"}</span>
                  </div>
                  <div className="mt-3 space-y-3">
                    {activeHooks.map((hook) => (
                      <div className="border-l-2 border-amber-300/30 pl-3 text-xs" key={hook.hook_id}>
                        <p className="font-mono text-slate-200">{hook.hook_id} · {hook.event} · {hook.mode}</p>
                        <p className="mt-1 leading-5 text-slate-400">{hook.purpose}</p>
                        {hook.test_receipt ? <p className={hook.test_receipt.passed ? "mt-1 text-emerald-100" : "mt-1 text-rose-100"}>{hook.test_receipt.results.length} 个类型化 fixture · {hook.test_receipt.passed ? "通过" : "失败"} · {shortDigest(hook.test_receipt.manifest_digest)}</p> : null}
                      </div>
                    ))}
                  </div>
                  {build.hook_manifest ? (
                    <details className="mt-4">
                      <summary className="cursor-pointer text-xs font-semibold text-slate-300">查看只读 hooks/manifest.json</summary>
                      <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-ink-950 p-3 font-mono text-[11px] leading-5 text-slate-300">{build.hook_manifest}</pre>
                    </details>
                  ) : null}
                </section>
              ) : null}
              <ValidationIssues
                action={skillValidationIssues.length ? {
                  disabled: !canRunBuilder || Boolean(busy),
                  label: busy === "finalize" ? "正在修复并重新生成…" : "修复并重新生成 SKILL.md",
                  onClick: () => void finalize("revise", skillRepairFeedback(skillValidationIssues)),
                } : undefined}
                issues={skillValidationIssues}
                title="最终文档需要修复"
              />
              {build.skill_markdown ? <pre className="max-h-[640px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-ink-950 p-4 font-mono text-xs leading-6 text-slate-200">{build.skill_markdown}</pre> : <p className="rounded-md border border-dashed border-white/15 p-6 text-center text-sm text-slate-400">等待生成最终 SKILL.md。</p>}
              {build.skill_markdown ? (
                <details className="rounded-md border border-white/10 p-4">
                  <summary className="cursor-pointer text-sm font-semibold text-white">查看完整包文件差异</summary>
                  <div className="mt-4 space-y-4">
                    {build.resources.map((item) => <article className="border-t border-white/10 pt-3" key={item.resource_id}><p className="font-mono text-xs text-brand-100">{item.action} {item.path}</p>{item.base_content != null ? <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-black/20 p-3 font-mono text-[11px] text-slate-500">原版本：\n{item.base_content}</pre> : null}{item.action !== "delete" && item.content != null ? <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap rounded-md bg-ink-950 p-3 font-mono text-[11px] text-slate-300">新版本：\n{item.content}</pre> : null}</article>)}
                  </div>
                </details>
              ) : null}
              {build.phase === "skill_markdown" && ["awaiting_review", "failed"].includes(build.state) ? (
                <div className="border-t border-white/10 pt-4">
                  <label className="text-sm font-semibold text-white" htmlFor="skill-review-feedback">最终文档反馈</label>
                  <textarea className="mt-2 min-h-24 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm leading-6 text-white" id="skill-review-feedback" maxLength={4000} onChange={(event) => setFeedback(event.target.value)} value={feedback} />
                  <div className="mt-3 flex flex-wrap justify-end gap-2">
                    <button className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={!feedback.trim() || !canRunBuilder || Boolean(busy)} onClick={() => void finalize("revise")} type="button"><RotateCcw aria-hidden="true" size={15} />{busy === "finalize" ? "正在按反馈重做…" : "按反馈重做 SKILL.md"}</button>
                    <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={skillValidationIssues.length > 0 || Boolean(busy)} onClick={() => void finalize("accept")} type="button"><CheckCircle2 aria-hidden="true" size={15} />确认最终包并形成提案</button>
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>

      {!build.stale && ["planned", "revision_requested"].includes(build.state) ? (
        <div className="sticky bottom-20 z-10 flex flex-wrap items-center justify-between gap-3 border border-white/10 bg-ink-950 px-4 py-3 sm:bottom-4">
          <p className="text-xs text-slate-400">{hookValidationPending ? "确定性生成 manifest，并在离线 Sidecar 实测每个 Hook" : build.phase === "resources" ? "生成下一个依赖已满足的完整资源" : "生成最终 SKILL.md 并执行全包校验"}</p>
          <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-brand-200 px-5 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={!canRunBuilder || Boolean(busy)} onClick={() => void next()} type="button">{busy === "next" ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={16} /> : <Play aria-hidden="true" size={16} />}{busy === "next" ? "正在生成并校验…" : hookValidationPending ? "实测 Hook 并生成 SKILL.md" : build.phase === "resources" ? "生成下一个资源" : "生成最终 SKILL.md"}</button>
        </div>
      ) : null}

      {hookBuildUnavailable && build ? <p className="text-xs text-amber-200">Hook V2 当前已关闭；已保存的资源和 receipt 不会丢失，重新开启后可继续。</p> : null}
      {hookSandboxUnavailable && build ? <p className="text-xs text-amber-200">离线 authoring Sidecar 当前不可用；已保存内容不会丢失，恢复后可继续实测。</p> : null}

      {build.state === "generating" ? <p className="flex items-center gap-2 text-sm text-brand-100" aria-live="polite"><LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={16} />模型正在生成 {activeTarget}，刷新不会丢失已保存片段。</p> : null}
      {build.phase === "proposal" && build.proposal_id ? <p className="flex items-center gap-2 rounded-md border border-emerald-300/20 bg-emerald-300/[0.07] p-4 text-sm text-emerald-50"><CheckCircle2 aria-hidden="true" size={16} />最终包已确认，等待在下方审阅标准草稿提案。</p> : null}
      {build.state === "failed" ? <button className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white" onClick={() => void readSkillCreatorResourceBuild(build.build_id).then((value) => update(value, "已重新读取构建状态。"))} type="button"><RefreshCw aria-hidden="true" size={15} />重新读取构建</button> : null}
    </section>
  );
}
