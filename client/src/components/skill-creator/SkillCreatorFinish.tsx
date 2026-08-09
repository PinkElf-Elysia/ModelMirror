import { Check, Download, LoaderCircle, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { useState } from "react";
import {
  installSkillCreatorDraft,
  iterateSkillCreatorDraft,
  type SkillCreatorDraft,
  type SkillCreatorProposal,
  type SkillCreatorSession,
  type SkillEvaluationRun,
} from "../../utils/skillCreatorApi";

export default function SkillCreatorFinish({
  session,
  draft,
  run,
  proposal,
  onProposal,
  onReload,
  onError,
  onNotice,
}: {
  session: SkillCreatorSession;
  draft: SkillCreatorDraft;
  run: SkillEvaluationRun | null;
  proposal: SkillCreatorProposal | null;
  onProposal: (proposal: SkillCreatorProposal, session?: SkillCreatorSession) => Promise<void> | void;
  onReload: () => Promise<void>;
  onError: (error: unknown, fallback: string) => void;
  onNotice: (message: string) => void;
}) {
  const [busy, setBusy] = useState("");
  const qualityStatus = session.quality_status ?? draft.quality_status ?? "not_evaluated";
  const installState = session.install_state ?? draft.install_state ?? "not_installed";
  const accepted = qualityStatus === "accepted";
  const waived = qualityStatus === "eval_waived";
  const canInstall = (accepted || waived) && session.current_digest === draft.content_digest && installState !== "current";

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

  async function install() {
    if (!canInstall || !window.confirm("确认把当前 Skill 全局安装到工作区？安装后仍只会按现有权限由 Agent 使用。")) return;
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
            <h2 className="text-lg font-semibold text-white" id="creator-iteration-heading">根据真实结果迭代</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">改进助手只读取已保存、已绑定本次 run 与 digest 的人工反馈。生成结果仍是类型化提案，需要你检查 diff 并批准。</p>
          </div>
        </div>
        {session.review_state === "revise" ? (
          <div className="mt-5 rounded-lg bg-amber-300/[0.07] p-4">
            <p className="text-xs font-semibold text-amber-100">当前评审：需要修改</p>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-amber-50/85">{session.review_feedback || run?.reviews?.at(-1)?.feedback || "反馈已保存在评测记录中。"}</p>
            <button className="mt-4 inline-flex items-center gap-2 rounded-md bg-brand-200 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={!run || Boolean(busy) || proposal?.status === "pending"} onClick={() => void iterate()} type="button">
              {busy === "iterate" ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={15} /> : <Sparkles aria-hidden="true" size={15} />}
              {proposal?.status === "pending" ? "更新提案待评审" : busy === "iterate" ? "正在生成…" : "根据反馈生成改进提案"}
            </button>
          </div>
        ) : (
          <p className="mt-5 rounded-lg bg-white/[0.025] p-4 text-sm leading-6 text-slate-400">评审选择“需要修改”后，可在这里生成下一版提案。任何内容修改都会让旧评测变为过期。</p>
        )}
      </section>

      <section className={`rounded-lg p-5 sm:p-6 ${waived ? "border border-amber-300/25 bg-amber-300/[0.055]" : accepted || installState === "current" ? "border border-emerald-300/20 bg-emerald-300/[0.045]" : "border border-white/10 bg-surface-900/80"}`} aria-labelledby="creator-install-heading">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex items-start gap-3">
            <ShieldCheck aria-hidden="true" className={`mt-0.5 shrink-0 ${waived ? "text-amber-100" : accepted || installState === "current" ? "text-emerald-100" : "text-slate-500"}`} size={22} />
            <div>
              <h2 className="text-lg font-semibold text-white" id="creator-install-heading">质量门与正式安装</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
                {installState === "current"
                  ? "当前摘要已安装。后续编辑只会生成新 revision，不会静默替换已安装版本。"
                  : accepted
                    ? "当前摘要的三个对照用例已经人工接受。安装仍是一次独立的全局写入。"
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
          <p className="break-all font-mono text-xs text-slate-500">revision {draft.revision} · {draft.content_digest}</p>
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
