import { useEffect, useState } from "react";
import { getOrCreateAnonymousId } from "../../utils/anonymousId";

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

type FeedbackStats = { slug: string | null; total: number; helpful: number };

/**
 * 文章底部"这篇对你有帮助吗？"反馈。
 *
 * 真闭环（已上报后端数据库）：
 * - 用户选择写入后端（POST /api/help/feedback），维护者可见、可统计。
 * - 防重复：anonymous_id（浏览器随机 UUID）+ slug 在后端唯一，重复提交返回 409。
 * - 防刷：后端按 IP 限流（60 秒最多 5 条）。
 * - 数据最小化：只上报 { slug, article_version, value, anonymous_id }，不含身份/IP/正文。
 * - 本机 localStorage 仅用于"记住选择、避免重复询问"，不是唯一存储。
 */
export function ArticleFeedback({
  slug,
  articleVersion,
}: {
  slug: string;
  articleVersion: string;
}) {
  const [stored, setStored] = useState<string | null>(() => readFeedback(slug));
  const [selected, setSelected] = useState<FeedbackValue | null>(() =>
    stored === "helpful" || stored === "not-helpful" ? (stored as FeedbackValue) : null,
  );
  const [submitted, setSubmitted] = useState<"ok" | "duplicate" | "offline" | null>(null);
  const [stats, setStats] = useState<FeedbackStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/help/feedback/stats?slug=${encodeURIComponent(slug)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled && data) setStats(data);
      })
      .catch(() => {
        if (!cancelled) setStats(null);
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  async function choose(value: FeedbackValue) {
    writeFeedback(slug, value);
    setSelected(value);
    try {
      const response = await fetch("/api/help/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug,
          article_version: articleVersion,
          value,
          anonymous_id: getOrCreateAnonymousId(),
        }),
      });
      if (response.status === 201) {
        setSubmitted("ok");
        // 刷新统计标签
        const statsResponse = await fetch(`/api/help/feedback/stats?slug=${encodeURIComponent(slug)}`);
        if (statsResponse.ok) setStats(await statsResponse.json());
      } else if (response.status === 409) {
        setSubmitted("duplicate");
      } else {
        setSubmitted("offline");
      }
    } catch {
      setSubmitted("offline");
    }
  }

  const confirmationText =
    submitted === "ok"
      ? "感谢反馈，你的意见已发送给团队。"
      : submitted === "offline"
        ? "本次提交未送达（仅保存在本机浏览器），可稍后重试。"
        : `你的选择已记录${selected === "helpful" ? "，如果问题没能解决，可以从左侧目录找相近内容。" : "，你可以换用左侧相近内容，或从帮助首页搜索其他任务词。"}`;

  return (
    <aside aria-label="文章反馈" className="mt-8 max-w-[72ch] rounded-xl border border-white/10 bg-[#071a2b]/68 p-5">
      {stats && stats.total > 0 ? (
        <p className="text-xs text-slate-500" aria-live="polite">
          已收到 {stats.total} 人评价，{stats.helpful} 人认为有帮助
        </p>
      ) : null}
      {selected ? (
        <p className="mt-2 text-sm leading-6 text-slate-400" aria-live="polite">
          {confirmationText}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-4">
          <span className="text-sm font-semibold text-slate-200">这篇对你有帮助吗？</span>
          <div className="flex gap-2">
            <button className="rounded-full border border-white/10 bg-white/[0.04] px-4 py-1.5 text-sm text-slate-200 transition hover:border-cyan-300/30 hover:text-cyan-100" onClick={() => void choose("helpful")} type="button">有帮助</button>
            <button className="rounded-full border border-white/10 bg-white/[0.04] px-4 py-1.5 text-sm text-slate-200 transition hover:border-rose-300/30 hover:text-rose-100" onClick={() => void choose("not-helpful")} type="button">没帮助</button>
          </div>
        </div>
      )}
    </aside>
  );
}
