import { Link } from "react-router-dom";

interface BrandLogoProps {
  className?: string;
  compact?: boolean;
}

export default function BrandLogo({
  className = "",
  compact = false,
}: BrandLogoProps) {
  return (
    <Link
      aria-label="返回模镜 ModelMirror 工作空间"
      className={`group inline-flex items-center gap-3 ${className}`}
      to="/studio"
    >
      <span className="relative flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-white/10 bg-white shadow-neon transition duration-200 group-hover:scale-[1.03]">
        <img
          alt="模镜 ModelMirror"
          className="h-full w-full object-cover"
          src="/logo.png"
        />
      </span>
      {!compact ? (
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 leading-tight">
          <span>
            <span className="block text-sm font-semibold text-white">模镜</span>
            <span className="block text-xs text-slate-400">ModelMirror</span>
          </span>
          <span className="rounded-full border border-hire-300/30 bg-hire-300/10 px-2.5 py-1 text-[11px] font-medium text-hire-100 shadow-[0_0_18px_rgba(251,146,60,0.08)]">
            AI 时代的模型浏览器
          </span>
        </span>
      ) : null}
    </Link>
  );
}
