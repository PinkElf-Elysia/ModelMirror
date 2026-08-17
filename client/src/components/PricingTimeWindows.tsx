import type { TimeWindowPricingOverride } from "../data/models";
import { formatUtcPricingWindow } from "../utils/tokenPricing";

interface PricingTimeWindowsProps {
  windows: TimeWindowPricingOverride[];
  className?: string;
  compact?: boolean;
}

function WindowRows({ windows }: Pick<PricingTimeWindowsProps, "windows">) {
  return (
    <div className="mt-2 space-y-2">
      <p className="text-[11px] leading-5 text-slate-400">
        以下为每百万 token 的人民币估算；开始时间包含，结束时间不包含。
      </p>
      <dl className="space-y-1.5">
        {windows.map((window) => (
          <div
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md bg-black/10 px-2.5 py-2"
            key={`${window.utc_start}-${window.utc_end}`}
          >
            <dt className="font-medium text-slate-200">
              {formatUtcPricingWindow(window)}
            </dt>
            <dd className="text-right text-slate-300">
              输入 ¥{window.price_cny.input.toFixed(2)} / 输出 ¥
              {window.price_cny.output.toFixed(2)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export default function PricingTimeWindows({
  windows,
  className = "",
  compact = false,
}: PricingTimeWindowsProps) {
  if (windows.length === 0) return null;

  if (compact) {
    return (
      <details
        className={`${className} rounded-lg border border-violet-300/20 bg-violet-300/[0.07] px-3 py-2 text-xs text-violet-100`}
      >
        <summary className="cursor-pointer font-semibold outline-none focus-visible:ring-2 focus-visible:ring-violet-300/70">
          UTC 分时价格 · {windows.length} 个时段
        </summary>
        <WindowRows windows={windows} />
      </details>
    );
  }

  return (
    <section
      aria-label="UTC 分时价格"
      className={`${className} rounded-xl border border-violet-300/20 bg-violet-300/[0.07] px-3 py-3 text-xs text-violet-100`}
    >
      <h4 className="font-semibold">UTC 分时价格</h4>
      <WindowRows windows={windows} />
    </section>
  );
}
