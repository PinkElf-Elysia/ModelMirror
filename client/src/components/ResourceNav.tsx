import { ArrowLeft, HelpCircle } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { resourceNavItems, type ResourceKey } from "../theme/resources";
import BrandLogo from "./BrandLogo";

interface ResourceNavProps {
  activeResource?: ResourceKey;
}

function navLinkClass(isActive: boolean, calm: boolean) {
  return `group inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm font-semibold transition duration-200 ${
    isActive
      ? "border-hire-200/60 bg-hire-300 text-ink-950 shadow-[0_0_24px_rgba(251,146,60,0.22)]"
      : calm
        ? "border-white/10 bg-white/[0.035] text-slate-300 hover:border-cyan-300/30 hover:bg-cyan-300/[0.07] hover:text-cyan-100"
        : "border-white/10 bg-white/[0.045] text-slate-300 hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
  }`;
}

export default function ResourceNav({ activeResource }: ResourceNavProps) {
  const location = useLocation();
  const helpActive = location.pathname.startsWith("/help");

  return (
    <>
      <header className={`fixed inset-x-0 top-0 z-50 hidden border-b border-white/10 shadow-dock backdrop-blur-2xl lg:block ${helpActive ? "bg-[#04111f]/96" : "bg-ink-950/82"}`}>
        <div className="mx-auto flex h-20 w-full max-w-[1480px] items-center justify-between gap-5 px-8">
          <BrandLogo />
          <div className="flex min-w-0 items-center gap-3">
            <nav aria-label="资源类型" className="flex min-w-0 items-center gap-2">
              {resourceNavItems.map((item) => {
                const isActive = !helpActive && activeResource === item.key;

                return (
                  <Link
                    aria-current={isActive ? "page" : undefined}
                    className={navLinkClass(isActive, helpActive)}
                    key={item.key}
                    title={`${item.title} (${item.english})`}
                    to={item.path}
                  >
                    <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${isActive ? "bg-ink-950/12 text-ink-950" : helpActive ? "bg-cyan-300/[0.08] text-cyan-200" : "bg-white/[0.06] text-hire-100"}`}>
                      {item.icon}
                    </span>
                    <span className="hidden xl:inline">{item.title}</span>
                    <span className="xl:hidden">{item.shortTitle}</span>
                  </Link>
                );
              })}
            </nav>
            <span aria-hidden="true" className="h-8 w-px bg-white/10" />
            <Link
              aria-current={helpActive ? "page" : undefined}
              aria-label="帮助"
              className={`inline-flex min-h-11 items-center gap-2 rounded-full border px-4 text-sm font-semibold transition ${helpActive ? "border-cyan-300/45 bg-cyan-300/12 text-cyan-100" : "border-white/10 bg-white/[0.035] text-slate-300 hover:border-cyan-300/30 hover:text-cyan-100"}`}
              to="/help"
            >
              <HelpCircle aria-hidden="true" className="h-4 w-4" />
              <span>帮助</span>
            </Link>
          </div>
        </div>
      </header>

      <div className="mb-1 flex justify-end px-4 lg:hidden">
        {helpActive ? (
          <Link
            aria-label="返回模型市场"
            className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/10 bg-ink-950/82 px-4 text-sm font-semibold text-slate-200 hover:border-cyan-300/30 hover:text-cyan-100"
            to="/models"
          >
            <ArrowLeft aria-hidden="true" className="h-4 w-4" />
            <span>返回模型市场</span>
          </Link>
        ) : (
          <Link
            aria-label="帮助"
            className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/10 bg-ink-950/82 px-4 text-sm font-semibold text-slate-200 hover:border-cyan-300/30 hover:text-cyan-100"
            to="/help"
          >
            <HelpCircle aria-hidden="true" className="h-4 w-4" />
            <span>帮助</span>
          </Link>
        )}
      </div>

      {!helpActive ? (
        <nav
          aria-label="资源类型"
          className="fixed bottom-4 left-1/2 z-50 grid w-[min(21rem,calc(100vw-1rem))] -translate-x-1/2 grid-cols-6 items-center overflow-hidden rounded-full border border-white/10 bg-ink-950/86 px-1.5 py-2 shadow-dock backdrop-blur-2xl lg:hidden"
        >
          {resourceNavItems.map((item) => {
            const isActive = activeResource === item.key;

            return (
              <Link
                aria-current={isActive ? "page" : undefined}
                className={`flex min-w-0 flex-col items-center justify-center gap-0.5 rounded-full px-0.5 py-1.5 text-[10px] font-semibold transition duration-200 ${
                  isActive
                    ? "bg-hire-300 text-ink-950 shadow-[0_0_20px_rgba(251,146,60,0.18)]"
                    : "text-slate-300 hover:bg-white/10 hover:text-white"
                }`}
                key={item.key}
                title={`${item.title} (${item.english})`}
                to={item.path}
              >
                <span className="text-xs font-bold">{item.icon}</span>
                <span className="truncate">{item.shortTitle}</span>
              </Link>
            );
          })}
        </nav>
      ) : null}
    </>
  );
}
