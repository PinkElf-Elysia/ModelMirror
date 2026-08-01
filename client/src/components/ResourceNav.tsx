import { Link } from "react-router-dom";
import { resourceNavItems, type ResourceKey } from "../theme/resources";
import BrandLogo from "./BrandLogo";

interface ResourceNavProps {
  activeResource?: ResourceKey;
}

function navLinkClass(isActive: boolean) {
  return `group inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm font-semibold transition duration-200 ${
    isActive
      ? "border-hire-200/60 bg-hire-300 text-ink-950 shadow-[0_0_24px_rgba(251,146,60,0.22)]"
      : "border-white/10 bg-white/[0.045] text-slate-300 hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
  }`;
}

export default function ResourceNav({ activeResource }: ResourceNavProps) {
  return (
    <>
      <header className="fixed inset-x-0 top-0 z-50 hidden border-b border-white/10 bg-ink-950/82 shadow-dock backdrop-blur-2xl lg:block">
        <div className="mx-auto flex h-20 w-full max-w-[1480px] items-center justify-between gap-5 px-8">
          <BrandLogo />
          <nav aria-label="资源类型" className="flex min-w-0 items-center gap-2">
            {resourceNavItems.map((item) => {
              const isActive = activeResource === item.key;

              return (
                <Link
                  aria-current={isActive ? "page" : undefined}
                  className={navLinkClass(isActive)}
                  key={item.key}
                  title={`${item.title} (${item.english})`}
                  to={item.path}
                >
                  <span
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                      isActive
                        ? "bg-ink-950/12 text-ink-950"
                        : "bg-white/[0.06] text-hire-100"
                    }`}
                  >
                    {item.icon}
                  </span>
                  <span className="hidden xl:inline">{item.title}</span>
                  <span className="xl:hidden">{item.shortTitle}</span>
                </Link>
              );
            })}
          </nav>
        </div>
      </header>

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
    </>
  );
}
