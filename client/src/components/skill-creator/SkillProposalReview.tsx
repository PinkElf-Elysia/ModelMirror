import {
  Check,
  FileDiff,
  FilePlus2,
  FileX2,
  FlaskConical,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { useMemo, useState } from "react";
import {
  proposalPackage,
  type SkillCreatorDraft,
  type SkillCreatorProposal,
} from "../../utils/skillCreatorApi";

interface DiffLine {
  kind: "context" | "added" | "removed";
  text: string;
}

const PROPOSAL_STATUS_COPY = {
  approved: {
    label: "已批准",
    title: "提案已批准并写入草稿",
    detail: "草稿已生成新的不可变版本，后续编辑和质量状态以服务端草稿记录为准。",
    className: "border-emerald-300/20 bg-emerald-300/[0.07] text-emerald-100",
  },
  rejected: {
    label: "已拒绝",
    title: "提案已拒绝",
    detail: "该提案没有写入草稿。你可以保留当前草稿并重新生成提案。",
    className: "border-rose-300/20 bg-rose-300/[0.07] text-rose-100",
  },
  cancelled: {
    label: "已取消",
    title: "提案已取消",
    detail: "该提案没有写入草稿，可能已被新的生成请求替换。你可以重新生成提案。",
    className: "border-white/10 bg-white/[0.035] text-slate-200",
  },
  conflict: {
    label: "有冲突",
    title: "提案与当前草稿冲突",
    detail: "提案没有写入草稿。请重新加载当前版本，再生成更新提案。",
    className: "border-amber-300/20 bg-amber-300/[0.07] text-amber-100",
  },
} as const;

function packageFiles(
  value: SkillCreatorDraft | ReturnType<typeof proposalPackage> | null | undefined,
): Record<string, string> {
  if (!value) return {};
  return { "SKILL.md": value.skill_markdown, ...(value.files ?? {}) };
}

function lineDiff(before: string, after: string): DiffLine[] {
  if (before === after) return before.split("\n").map((text) => ({ kind: "context", text }));
  const oldLines = before.split("\n");
  const nextLines = after.split("\n");
  let prefix = 0;
  while (prefix < oldLines.length && prefix < nextLines.length && oldLines[prefix] === nextLines[prefix]) prefix += 1;
  let suffix = 0;
  while (
    suffix < oldLines.length - prefix &&
    suffix < nextLines.length - prefix &&
    oldLines[oldLines.length - 1 - suffix] === nextLines[nextLines.length - 1 - suffix]
  ) suffix += 1;
  const leading = oldLines.slice(Math.max(0, prefix - 3), prefix).map((text) => ({ kind: "context" as const, text }));
  const removed = oldLines.slice(prefix, oldLines.length - suffix).map((text) => ({ kind: "removed" as const, text }));
  const added = nextLines.slice(prefix, nextLines.length - suffix).map((text) => ({ kind: "added" as const, text }));
  const trailing = oldLines.slice(oldLines.length - suffix, oldLines.length - Math.max(0, suffix - 3)).map((text) => ({ kind: "context" as const, text }));
  return [...leading, ...removed, ...added, ...trailing];
}

export default function SkillProposalReview({
  proposal,
  baseDraft,
  approving,
  rejecting,
  onApprove,
  onReject,
}: {
  proposal: SkillCreatorProposal;
  baseDraft?: SkillCreatorDraft | null;
  approving: boolean;
  rejecting: boolean;
  onApprove: () => Promise<void>;
  onReject: (reason: string) => Promise<void>;
}) {
  const nextPackage = proposalPackage(proposal);
  const beforeFiles = useMemo(() => packageFiles(baseDraft), [baseDraft]);
  const afterFiles = useMemo(() => packageFiles(nextPackage), [nextPackage]);
  const changes = useMemo(() => {
    const paths = [...new Set([...Object.keys(beforeFiles), ...Object.keys(afterFiles)])].sort();
    return paths.flatMap((path) => {
      const before = beforeFiles[path];
      const after = afterFiles[path];
      if (before === after) return [];
      return [{ path, kind: before === undefined ? "added" as const : after === undefined ? "deleted" as const : "modified" as const }];
    });
  }, [afterFiles, beforeFiles]);
  const [selectedPath, setSelectedPath] = useState(changes[0]?.path ?? "SKILL.md");
  const [discardOpen, setDiscardOpen] = useState(false);
  const [discardReason, setDiscardReason] = useState("");
  const selectedChange = changes.find((item) => item.path === selectedPath) ?? changes[0];
  const diff = selectedChange
    ? lineDiff(beforeFiles[selectedChange.path] ?? "", afterFiles[selectedChange.path] ?? "")
    : [];
  const terminalStatus = proposal.status === "pending"
    ? null
    : PROPOSAL_STATUS_COPY[proposal.status];
  const creatorQuality = proposal.creator_quality ?? proposal.validation?.creator_quality ?? null;
  const creatorQualityReady = creatorQuality?.ready === true;
  const creatorQualityReported = creatorQuality !== null;
  const failedQualityChecks = (creatorQuality?.checks ?? []).filter((check) => !check.passed);
  const qualityIssues = creatorQuality?.issues ?? [];
  const packageStructureCheck = creatorQuality?.checks?.find((check) => check.code === "package_structure");
  const structureValid = creatorQualityReported
    ? packageStructureCheck?.passed === true
    : proposal.validation?.valid === true;
  const qualityIssueKeys = new Set(qualityIssues.map((issue) => `${issue.code}:${issue.path ?? "package"}`));
  const structureIssues = (proposal.validation?.issues ?? []).filter(
    (issue) => !qualityIssueKeys.has(`${issue.code}:${issue.path ?? "package"}`),
  );

  return (
    <section className="rounded-lg border border-brand-300/25 bg-surface-900/80 p-4 sm:p-5" aria-labelledby="creator-proposal-title">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-brand-300/10 px-2.5 py-1 text-xs font-semibold text-brand-100">类型化提案</span>
            <span className="text-xs text-slate-500">revision {proposal.revision}</span>
            <span className="rounded-full bg-white/[0.055] px-2.5 py-1 text-xs font-semibold text-slate-300">
              {proposal.status === "pending" ? "待审核" : terminalStatus?.label}
            </span>
          </div>
          <h2 className="mt-3 text-lg font-semibold text-white" id="creator-proposal-title">{proposal.title}</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            模型只能修改下面列出的 Skill 文件。批准会生成不可变草稿版本，不会安装或启用该 Skill。
          </p>
        </div>
      </div>

      <div className="mt-5 grid overflow-hidden rounded-lg border border-white/10 bg-ink-950/45 sm:grid-cols-3" aria-label="提案质量状态">
        <section className="p-4 sm:border-r sm:border-white/10" aria-labelledby="proposal-structure-quality">
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className={structureValid ? "text-emerald-200" : "text-amber-200"} size={16} />
            <h3 className="text-xs font-semibold text-white" id="proposal-structure-quality">结构与安全</h3>
          </div>
          <p className={`mt-2 text-xs font-semibold ${structureValid ? "text-emerald-100" : "text-amber-100"}`}>
            {structureValid ? "校验通过" : "需要处理"}
          </p>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">检查 YAML、路径、引用、脚本与疑似凭据。</p>
        </section>
        <section className="border-y border-white/10 p-4 sm:border-y-0 sm:border-r" aria-labelledby="proposal-draft-quality">
          <div className="flex items-center gap-2">
            {creatorQualityReady ? <Check aria-hidden="true" className="text-emerald-200" size={16} /> : <TriangleAlert aria-hidden="true" className="text-amber-200" size={16} />}
            <h3 className="text-xs font-semibold text-white" id="proposal-draft-quality">初稿完整度</h3>
          </div>
          <p className={`mt-2 text-xs font-semibold ${creatorQualityReady ? "text-emerald-100" : "text-amber-100"}`}>
            {creatorQualityReady
              ? `门槛通过${typeof creatorQuality?.score === "number" ? ` · ${creatorQuality.score} 分` : ""}`
              : creatorQualityReported
                ? "未达到门槛"
                : "后端未报告"}
          </p>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">
            {creatorQualityReported
              ? creatorQuality?.summary || (creatorQualityReady ? "工作流、输出约定与失败处理已覆盖。" : "请根据下方缺项修订后重新生成。")
              : "兼容旧响应；结构通过不等于内容完整。批准仍由服务端规则决定。"}
          </p>
        </section>
        <section className="p-4" aria-labelledby="proposal-behavior-quality">
          <div className="flex items-center gap-2">
            <FlaskConical aria-hidden="true" className="text-slate-500" size={16} />
            <h3 className="text-xs font-semibold text-white" id="proposal-behavior-quality">行为评测</h3>
          </div>
          <p className="mt-2 text-xs font-semibold text-slate-300">草稿批准后进入三例行为评测</p>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">批准提案不会代表真实用例对照评测通过，也不会安装 Skill。</p>
        </section>
      </div>

      {failedQualityChecks.length || qualityIssues.length ? (
        <div className="mt-4 rounded-lg border border-amber-300/20 bg-amber-300/[0.06] p-3" aria-labelledby="proposal-quality-gaps">
          <h3 className="text-xs font-semibold text-amber-100" id="proposal-quality-gaps">初稿完整度缺项</h3>
          <ul className="mt-2 space-y-1.5 text-xs leading-5 text-slate-300">
            {failedQualityChecks.map((check) => (
              <li key={check.code}>{check.label || check.message || check.code}</li>
            ))}
            {qualityIssues.map((issue) => (
              <li key={`${issue.code}-${issue.path ?? "package"}`}>{issue.message}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-5 grid min-w-0 gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
        <div className="min-w-0">
          <p className="px-1 text-xs font-semibold text-slate-400">文件变更（{changes.length}）</p>
          {changes.length ? (
            <div className="mt-2 space-y-1">
              {changes.map((change) => (
                <button
                  className={`flex w-full min-w-0 items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs ${
                    selectedChange?.path === change.path ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/[0.045]"
                  }`}
                  key={change.path}
                  onClick={() => setSelectedPath(change.path)}
                  type="button"
                >
                  {change.kind === "added" ? <FilePlus2 aria-hidden="true" className="shrink-0 text-emerald-200" size={14} /> : change.kind === "deleted" ? <FileX2 aria-hidden="true" className="shrink-0 text-rose-200" size={14} /> : <FileDiff aria-hidden="true" className="shrink-0 text-amber-200" size={14} />}
                  <span className="truncate">{change.path}</span>
                </button>
              ))}
            </div>
          ) : (
            <p className="mt-2 rounded-md border border-dashed border-white/10 p-3 text-xs leading-5 text-slate-500">提案内容与当前草稿一致。</p>
          )}
        </div>

        <div className="min-w-0 overflow-hidden rounded-lg border border-white/10 bg-ink-950/75">
          <div className="flex items-center justify-between gap-3 border-b border-white/10 px-3 py-2 text-xs">
            <span className="truncate font-mono text-slate-300">{selectedChange?.path ?? "没有文件变化"}</span>
            {selectedChange ? <span className="shrink-0 text-slate-500">{selectedChange.kind}</span> : null}
          </div>
          <pre className="max-h-80 overflow-auto p-3 font-mono text-xs leading-5">
            {diff.length ? diff.map((line, index) => (
              <span
                className={`block whitespace-pre-wrap break-words ${
                  line.kind === "added"
                    ? "bg-emerald-300/[0.08] text-emerald-100"
                    : line.kind === "removed"
                      ? "bg-rose-300/[0.08] text-rose-100"
                      : "text-slate-500"
                }`}
                key={`${index}-${line.text}`}
              >
                <span className="mr-2 inline-block w-3 select-none text-center opacity-70">
                  {line.kind === "added" ? "+" : line.kind === "removed" ? "−" : " "}
                </span>
                {line.text || " "}
              </span>
            )) : <span className="text-slate-500">没有可展示的差异。</span>}
          </pre>
        </div>
      </div>

      {structureIssues.length ? (
        <ul className="mt-4 space-y-2">
          {structureIssues.map((issue) => (
            <li className="rounded-md border border-rose-300/20 bg-rose-300/[0.07] px-3 py-2 text-xs leading-5 text-rose-100" key={`${issue.code}-${issue.path ?? "package"}`}>
              {issue.message} <span className="font-mono text-rose-200/70">{issue.path ?? issue.code}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {proposal.status === "pending" ? (
        <div className="mt-5 border-t border-white/10 pt-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs leading-5 text-slate-400">批准后进入草稿编辑，不会绕过行为评测质量门。</p>
            <div className="flex flex-wrap gap-2">
              <button
                className="rounded-full border border-rose-300/25 bg-rose-300/[0.07] px-4 py-2.5 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/15 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={approving || rejecting}
                onClick={() => setDiscardOpen(true)}
                type="button"
              >
                丢弃提案
              </button>
              <button
                className="inline-flex items-center gap-2 rounded-full bg-emerald-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                disabled={approving || rejecting || !structureValid || (creatorQualityReported && !creatorQualityReady) || changes.length === 0}
                onClick={() => void onApprove()}
                type="button"
              >
                <Check aria-hidden="true" size={16} />
                {approving ? "正在写入草稿…" : "批准并写入草稿"}
              </button>
            </div>
          </div>
          {discardOpen ? (
            <div className="mt-4 rounded-lg border border-rose-300/20 bg-rose-300/[0.06] p-4" role="group" aria-labelledby="creator-discard-heading">
              <p className="text-sm font-semibold text-white" id="creator-discard-heading">确认丢弃这份提案</p>
              <p className="mt-1 text-xs leading-5 text-slate-300">草稿不会发生变化。丢弃后可基于当前 Session 重新生成。</p>
              <label className="mt-3 block" htmlFor={`creator-discard-reason-${proposal.proposal_id}`}>
                <span className="text-xs font-semibold text-slate-200">简短原因</span>
                <input
                  className="mt-2 w-full rounded-md border border-white/10 bg-ink-950/75 px-3 py-2.5 text-sm text-white placeholder:text-slate-500 focus:border-rose-300/40 focus:outline-none"
                  id={`creator-discard-reason-${proposal.proposal_id}`}
                  maxLength={200}
                  onChange={(event) => setDiscardReason(event.target.value)}
                  placeholder="例如：内容不符合预期"
                  value={discardReason}
                />
              </label>
              <div className="mt-3 flex flex-wrap justify-end gap-2">
                <button
                  className="rounded-full border border-white/10 px-4 py-2 text-xs font-semibold text-slate-200 hover:bg-white/[0.05]"
                  disabled={rejecting}
                  onClick={() => { setDiscardOpen(false); setDiscardReason(""); }}
                  type="button"
                >
                  继续审阅
                </button>
                <button
                  className="rounded-full bg-rose-200 px-4 py-2 text-xs font-semibold text-ink-950 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                  disabled={rejecting || discardReason.trim().length < 2}
                  onClick={() => void onReject(discardReason.trim())}
                  type="button"
                >
                  {rejecting ? "正在丢弃…" : "确认丢弃提案"}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <div className={`mt-5 rounded-lg border px-4 py-3 ${terminalStatus?.className ?? "border-white/10 bg-white/[0.035] text-slate-200"}`} role="status">
          <p className="text-sm font-semibold">{terminalStatus?.title}</p>
          <p className="mt-1 text-xs leading-5 opacity-85">{terminalStatus?.detail}</p>
        </div>
      )}
    </section>
  );
}
