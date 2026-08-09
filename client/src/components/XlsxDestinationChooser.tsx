import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

export type XlsxDestination = "chat" | "rag" | "datax";

const DESTINATIONS: Array<{
  id: XlsxDestination;
  label: string;
  href: string;
}> = [
  { id: "chat", label: "与模型讨论", href: "/models" },
  { id: "rag", label: "加入资料库", href: "/rag" },
  { id: "datax", label: "用 Data X 分析", href: "/datax" },
];

export function isXlsxFile(file: Pick<File, "name">) {
  return file.name.trim().toLowerCase().endsWith(".xlsx");
}

interface XlsxDestinationChooserProps {
  currentDestination: XlsxDestination;
  fileName: string;
  disabled?: boolean;
  className?: string;
  onCancel: () => void;
  onNavigate?: (destination: XlsxDestination) => void;
  onUseCurrent: () => void;
}

export default function XlsxDestinationChooser({
  currentDestination,
  fileName,
  disabled = false,
  className = "",
  onCancel,
  onNavigate,
  onUseCurrent,
}: XlsxDestinationChooserProps) {
  const currentActionRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    currentActionRef.current?.focus();
  }, [currentDestination, fileName]);

  return (
    <section
      aria-label={`${fileName} 的使用方式`}
      className={`border-y border-white/10 bg-white/[0.035] px-3 py-2.5 ${className}`}
    >
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold text-slate-100">
            {fileName}
          </p>
          <p className="mt-0.5 text-[11px] leading-5 text-slate-400">
            请选择用途后再处理，不会自动转交到其他模块。
          </p>
        </div>
        <button
          aria-label={`取消选择 ${fileName}`}
          className="min-h-11 shrink-0 px-2 text-xs font-semibold text-slate-400 transition hover:text-white focus:outline-none focus:ring-2 focus:ring-brand-300/40"
          disabled={disabled}
          onClick={onCancel}
          type="button"
        >
          取消选择
        </button>
      </div>

      <div className="mt-2 flex flex-wrap gap-2">
        {DESTINATIONS.map((destination) => {
          const actionClass =
            "inline-flex min-h-11 items-center justify-center border px-3 text-xs font-semibold transition focus:outline-none focus:ring-2 focus:ring-brand-300/40";
          if (destination.id === currentDestination) {
            return (
              <button
                className={`${actionClass} border-brand-300/40 bg-brand-300/12 text-brand-100 hover:bg-brand-300/20 disabled:cursor-not-allowed disabled:opacity-50`}
                disabled={disabled}
                key={destination.id}
                onClick={onUseCurrent}
                ref={currentActionRef}
                type="button"
              >
                {destination.label}
              </button>
            );
          }
          return (
            <Link
              aria-disabled={disabled}
              className={`${actionClass} border-white/10 text-slate-200 hover:border-brand-300/35 hover:text-brand-100 ${disabled ? "pointer-events-none opacity-50" : ""}`}
              key={destination.id}
              onClick={(event) => {
                if (disabled) {
                  event.preventDefault();
                  return;
                }
                onNavigate?.(destination.id);
              }}
              tabIndex={disabled ? -1 : undefined}
              to={destination.href}
            >
              {destination.label}
            </Link>
          );
        })}
      </div>

      <p className="mt-2 text-[11px] leading-5 text-slate-500">
        切换模块不会携带本地文件，请在目标页面重新选择。
      </p>
    </section>
  );
}
