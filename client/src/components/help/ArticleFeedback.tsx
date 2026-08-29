import { useState } from "react";

const FEEDBACK_PREFIX = "help-feedback:";

function readFeedback(slug: string): string | null {
  try {
    return window.localStorage.getItem(`${FEEDBACK_PREFIX}${slug}`);
  } catch {
    return null;
  }
}

function writeFeedback(slug: string, value: string) {
  try {
    window.localStorage.setItem(`${FEEDBACK_PREFIX}${slug}`, value);
  } catch {
    // localStorage 不可用（隐私模式等）时静默失败，不阻塞阅读
  }
}

type FeedbackValue = "helpful" | "not-helpful";

/**
 * 文章底部"这篇对你有帮助吗？"反馈。
 * 选择一次后本地记住，不再重复询问；无后端上报。
 */
export function ArticleFeedback({ slug }: { slug: string }) {
  const [stored, setStored] = useState<string | null>(() => readFeedback(slug));
  const [selected, setSelected] = useState<FeedbackValue | null>(() =>
    stored === "helpful" || stored === "not-helpful" ? (stored as FeedbackValue) : null,
  );

  function choose(value: FeedbackValue) {
    writeFeedback(slug, value);
    setSelected(value);
  }

  return (
    <aside aria-label="文章反馈" className="mt-8 max-w-[72ch] rounded-xl border border-white/10 bg-[#071a2b]/68 p-5">
      {selected ? (
        <p className="text-sm leading-6 text-slate-400" aria-live="polite">
          你的选择仅保存在本机浏览器，不会发送给团队。{selected === "helpful" ? "如果问题没能解决，可以从左侧目录找相近内容。" : "你可以换用左侧相近内容，或从帮助首页搜索其他任务词。"}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-4">
          <span className="text-sm font-semibold text-slate-200">这篇对你有帮助吗？</span>
          <div className="flex gap-2">
            <button className="rounded-full border border-white/10 bg-white/[0.04] px-4 py-1.5 text-sm text-slate-200 transition hover:border-cyan-300/30 hover:text-cyan-100" onClick={() => choose("helpful")} type="button">有帮助</button>
            <button className="rounded-full border border-white/10 bg-white/[0.04] px-4 py-1.5 text-sm text-slate-200 transition hover:border-rose-300/30 hover:text-rose-100" onClick={() => choose("not-helpful")} type="button">没帮助</button>
          </div>
          <span className="text-xs text-slate-500">仅保存在本机浏览器，不会发送给团队</span>
        </div>
      )}
    </aside>
  );
}
