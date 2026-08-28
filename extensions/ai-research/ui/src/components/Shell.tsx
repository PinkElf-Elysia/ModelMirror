import { Activity, Beaker, FolderKanban, ListTree, Menu, ServerCog, X } from "lucide-react";
import { useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

const links = [
  { to: "/", label: "总览", icon: Activity, end: true },
  { to: "/projects", label: "研究项目", icon: FolderKanban, end: false },
  { to: "/runs", label: "工程夹具", icon: ListTree, end: false },
  { to: "/system", label: "系统", icon: ServerCog, end: false },
];

export function Shell() {
  const [open, setOpen] = useState(false);
  const navigationToggle = useRef<HTMLButtonElement>(null);

  const closeWithFocusReturn = () => {
    setOpen(false);
    requestAnimationFrame(() => navigationToggle.current?.focus());
  };

  return (
    <div className="app-shell">
      <aside className="sidebar" onKeyDown={(event) => { if (event.key === "Escape" && open) closeWithFocusReturn(); }}>
        <div className="flex min-h-[66px] items-center justify-between border-b border-[var(--border)] px-4">
          <NavLink to="/" className="flex min-w-0 items-center gap-3" onClick={() => setOpen(false)}>
            <span className="grid h-8 w-8 shrink-0 place-items-center border border-[#3e7777] bg-[#102829] text-[var(--cyan)]">
              <Beaker aria-hidden="true" size={17} />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold text-white">模镜科研</span>
              <span className="block text-[10px] tracking-[0.04em] text-[var(--muted)]">
                Research Console
              </span>
            </span>
          </NavLink>
          <button
            ref={navigationToggle}
            type="button"
            className="button !min-h-9 !border-0 !p-2 md:hidden"
            aria-label={open ? "关闭导航" : "打开导航"}
            aria-expanded={open}
            aria-controls="research-console-navigation"
            onClick={() => setOpen((value) => !value)}
          >
            {open ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>

        <nav id="research-console-navigation" className={`${open ? "flex" : "hidden"} flex-col gap-1 p-3 md:flex`} aria-label="科研控制台">
          {links.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `flex min-h-10 items-center gap-3 rounded-[5px] px-3 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-[var(--cyan-soft)] text-[#b8efec]"
                    : "text-[#aab3ba] hover:bg-white/[0.035] hover:text-white"
                }`
              }
            >
              <Icon aria-hidden="true" size={16} />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className={`${open ? "block" : "hidden"} mt-auto border-t border-[var(--border)] p-4 text-xs leading-5 text-[var(--muted)] md:block`}>
          <span className="block font-semibold text-[#bdc7cd]">0.3.0-v0.1</span>
          Literature · scientificClaim none
        </div>
      </aside>
      <main className="main-column" id="main-content">
        <Outlet />
      </main>
    </div>
  );
}
