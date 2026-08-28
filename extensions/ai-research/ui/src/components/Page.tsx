import { AlertCircle, Beaker } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1 className="page-title">{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function FixtureNotice() {
  return (
    <div className="notice" role="note">
      <Beaker aria-hidden="true" className="mt-1 shrink-0 text-[var(--cyan)]" size={16} />
      <span>当前仅运行工程夹具，用于验证执行、取消与证据链；不调用模型，不产生科研结论。</span>
    </div>
  );
}

export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="mt-4 flex items-start justify-between gap-4 rounded-md border border-[#704044] bg-[#291719] px-4 py-3 text-sm text-[#ffc2c2]" role="alert">
      <span className="flex items-start gap-2">
        <AlertCircle aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
        {message}
      </span>
      {onRetry ? (
        <button type="button" className="shrink-0 font-semibold underline" onClick={onRetry}>重试</button>
      ) : null}
    </div>
  );
}

export function LoadingRows({ count = 3 }: { count?: number }) {
  return (
    <div className="mt-4 grid gap-2" aria-label="正在加载" aria-busy="true">
      {Array.from({ length: count }, (_, index) => <div className="skeleton" key={index} />)}
    </div>
  );
}
