import { ChevronDown, Download, History, LoaderCircle } from "lucide-react";
import { useState } from "react";
import type { CodingCycleHistory } from "../types/coding";

interface CodingHistoryPanelProps {
  disabled: boolean;
  history: CodingCycleHistory | null;
  onDownloadAll: () => Promise<void>;
}

export default function CodingHistoryPanel({
  disabled,
  history,
  onDownloadAll,
}: CodingHistoryPanelProps) {
  const [downloading, setDownloading] = useState(false);
  if (!history?.cycles.length) return null;

  const downloadAll = async () => {
    setDownloading(true);
    try {
      await onDownloadAll();
    } finally {
      setDownloading(false);
    }
  };

  return (
    <details className="mt-5 rounded-lg border border-white/10 bg-black/15">
      <summary className="flex min-h-12 cursor-pointer list-none items-center gap-3 px-4 text-sm font-semibold text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-300/70">
        <History aria-hidden="true" className="text-cyan-200" size={17} />
        <span className="flex-1">此前已完成 {history.completed_count} 轮修改</span>
        <ChevronDown aria-hidden="true" className="transition group-open:rotate-180" size={16} />
      </summary>
      <div className="border-t border-white/10 p-4">
        <ol className="space-y-3">
          {history.cycles.map((cycle) => (
            <li className="rounded-lg bg-white/[0.04] p-3" key={cycle.number}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-semibold text-white">
                  第 {cycle.number} 轮
                </span>
                <span className="text-xs text-slate-400">
                  {cycle.file_count} 个文件 · +{cycle.additions} / -{cycle.deletions}
                </span>
              </div>
              <p className="mt-2 break-words text-xs leading-5 text-slate-300">
                {cycle.message || "已保存本地版本"}
              </p>
              {cycle.short_sha ? (
                <code className="mt-2 inline-block rounded bg-black/25 px-2 py-1 text-xs text-cyan-100">
                  {cycle.short_sha}
                </code>
              ) : null}
            </li>
          ))}
        </ol>
        <button
          className="mt-4 inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border border-white/15 px-3 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
          disabled={disabled || downloading}
          onClick={() => void downloadAll()}
          type="button"
        >
          {downloading ? (
            <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={16} />
          ) : (
            <Download aria-hidden="true" size={16} />
          )}
          下载全部修改
        </button>
      </div>
    </details>
  );
}
