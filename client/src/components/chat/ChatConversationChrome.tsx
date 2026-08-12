import {
  type ComponentType,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
  useEffect,
  useId,
  useMemo,
  useRef,
} from "react";
import { createPortal } from "react-dom";
import {
  ArrowLeft,
  ChevronRight,
  Plus,
  Settings,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";

export type ChatShellMode = "direct" | "auto" | "expert";

export type ChatActionGroup = "content" | "context" | "tools" | "voice";

export interface ChatActionDescriptor {
  id: string;
  group: ChatActionGroup;
  label: string;
  description: string;
  icon: ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  status?: "available" | "active" | "blocked";
  blockedReason?: string;
  count?: number;
  onSelect?: () => void;
  control?: ReactNode;
}

export interface ChatActiveContext {
  id: string;
  label: string;
  detail?: string;
  onRemove: () => void;
  disabled?: boolean;
}

const ACTION_GROUP_LABELS: Record<ChatActionGroup, string> = {
  content: "添加内容",
  context: "添加上下文",
  tools: "工具",
  voice: "语音",
};

const ACTION_GROUP_ORDER: ChatActionGroup[] = ["content", "context", "tools", "voice"];

function focusableElements(root: HTMLElement) {
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
}

export function ChatCompactHeader({
  mode,
  modelLabel,
  providerLabel,
  expertDepartment,
  backTo,
  settingsTriggerRef,
  onOpenSettings,
  onExitExpert,
  disabled,
}: {
  mode: ChatShellMode;
  modelLabel: string;
  providerLabel?: string;
  expertDepartment?: string;
  backTo: string;
  settingsTriggerRef: RefObject<HTMLButtonElement | null>;
  onOpenSettings: () => void;
  onExitExpert?: () => void;
  disabled?: boolean;
}) {
  const modeLabel = mode === "expert" ? "专家模式" : mode === "auto" ? "智能调度" : "直接对话";

  return (
    <header className="sticky top-0 z-40 border-b border-white/10 bg-ink-950/95 backdrop-blur-xl">
      <div className="mx-auto flex h-16 w-full max-w-[1200px] items-center gap-3 px-3 sm:px-5">
        <Link
          aria-label="返回"
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-slate-300 transition hover:bg-white/[0.07] hover:text-white focus:outline-none focus:ring-4 focus:ring-brand-300/15"
          to={backTo}
        >
          <ArrowLeft aria-hidden className="h-5 w-5" />
        </Link>
        <Link
          className="hidden shrink-0 items-center gap-2 text-sm font-semibold text-white transition hover:text-brand-100 sm:inline-flex"
          to="/models"
        >
          <img alt="" className="h-7 w-7 rounded-md object-cover" src="/logo.png" />
          <span>ModelMirror</span>
        </Link>
        <div className="min-w-0 flex-1 text-center">
          <p className="truncate text-sm font-semibold text-white">{modelLabel}</p>
          <p className="mt-0.5 flex items-center justify-center gap-1.5 truncate text-[11px] text-slate-400">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-300" />
            <span className="truncate">
              {modeLabel}
              {expertDepartment ? ` · ${expertDepartment}` : providerLabel ? ` · ${providerLabel}` : ""}
            </span>
          </p>
        </div>
        {mode === "expert" && onExitExpert ? (
          <button
            aria-label="退出专家模式"
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-slate-300 transition hover:bg-white/[0.07] hover:text-white disabled:opacity-50 md:w-auto md:px-3"
            disabled={disabled}
            onClick={onExitExpert}
            type="button"
          >
            <X aria-hidden className="h-4 w-4 md:hidden" />
            <span className="hidden md:inline">退出专家</span>
          </button>
        ) : null}
        <button
          aria-label="打开对话设置"
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-slate-300 transition hover:bg-white/[0.07] hover:text-white focus:outline-none focus:ring-4 focus:ring-brand-300/15"
          onClick={onOpenSettings}
          ref={settingsTriggerRef}
          type="button"
        >
          <Settings aria-hidden className="h-5 w-5" />
        </button>
      </div>
    </header>
  );
}

export function ChatActionMenu({
  open,
  actions,
  triggerRef,
  onOpenChange,
}: {
  open: boolean;
  actions: ChatActionDescriptor[];
  triggerRef: RefObject<HTMLButtonElement | null>;
  onOpenChange: (open: boolean) => void;
}) {
  const menuId = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const groupedActions = useMemo(
    () =>
      ACTION_GROUP_ORDER.map((group) => ({
        group,
        actions: actions.filter((action) => action.group === group),
      })).filter((entry) => entry.actions.length > 0),
    [actions],
  );

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      onOpenChange(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onOpenChange(false);
        window.requestAnimationFrame(() => triggerRef.current?.focus());
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.requestAnimationFrame(() => focusableElements(menuRef.current ?? document.body)[0]?.focus());
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onOpenChange, open, triggerRef]);

  function onMenuKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = focusableElements(event.currentTarget);
    const currentIndex = items.indexOf(document.activeElement as HTMLElement);
    const delta = event.key === "ArrowDown" ? 1 : -1;
    const nextIndex = currentIndex < 0 ? 0 : (currentIndex + delta + items.length) % items.length;
    event.preventDefault();
    items[nextIndex]?.focus();
  }

  return (
    <div className="relative shrink-0">
      <button
        aria-controls={menuId}
        aria-expanded={open}
        aria-label="添加内容与工具"
        className={`inline-flex h-11 w-11 items-center justify-center rounded-full border transition focus:outline-none focus:ring-4 focus:ring-brand-300/15 ${
          open
            ? "border-brand-300/45 bg-brand-300/15 text-brand-100"
            : "border-white/10 bg-white/[0.055] text-slate-300 hover:border-white/20 hover:bg-white/[0.09] hover:text-white"
        }`}
        onClick={() => onOpenChange(!open)}
        ref={triggerRef}
        type="button"
      >
        <Plus aria-hidden className={`h-5 w-5 transition ${open ? "rotate-45" : ""}`} />
      </button>
      {open ? <div
        aria-label="对话功能"
        className="absolute bottom-[calc(100%+0.75rem)] left-0 z-50 w-[min(22rem,calc(100vw-2rem))] origin-bottom-left overflow-hidden rounded-2xl border border-white/10 bg-surface-900 p-2 shadow-panel"
        id={menuId}
        onKeyDown={onMenuKeyDown}
        ref={menuRef}
        role="menu"
      >
        <div className="max-h-[min(62vh,34rem)] overflow-y-auto [scrollbar-width:thin]">
          {groupedActions.map((entry, groupIndex) => (
            <section className={groupIndex > 0 ? "mt-2 border-t border-white/10 pt-2" : ""} key={entry.group}>
              <p className="px-3 pb-1 pt-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                {ACTION_GROUP_LABELS[entry.group]}
              </p>
              <div className="space-y-1">
                {entry.actions.map((action) => {
                  const Icon = action.icon;
                  const blocked = action.status === "blocked";
                  return action.control ? (
                    <div className="rounded-xl px-3 py-2" key={action.id}>
                      <div className="mb-2 flex items-start gap-3">
                        <Icon aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-slate-100">{action.label}</p>
                          <p className="mt-0.5 text-xs leading-5 text-slate-400">{action.description}</p>
                        </div>
                      </div>
                      {action.control}
                    </div>
                  ) : (
                    <button
                      className="group flex min-h-14 w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition hover:bg-white/[0.06] focus:outline-none focus:ring-2 focus:ring-brand-300/25 disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={blocked}
                      key={action.id}
                      onClick={() => {
                        action.onSelect?.();
                        onOpenChange(false);
                      }}
                      role="menuitem"
                      title={blocked ? action.blockedReason : undefined}
                      type="button"
                    >
                      <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${action.status === "active" ? "bg-brand-300/15 text-brand-100" : "bg-white/[0.055] text-slate-300"}`}>
                        <Icon aria-hidden className="h-4 w-4" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                          {action.label}
                          {action.count ? <span className="rounded-full bg-brand-300/15 px-2 py-0.5 text-[10px] text-brand-100">{action.count}</span> : null}
                        </span>
                        <span className={`mt-0.5 block text-xs leading-5 ${blocked ? "text-amber-200/80" : "text-slate-400"}`}>
                          {blocked ? action.blockedReason ?? action.description : action.description}
                        </span>
                      </span>
                      {!blocked ? <ChevronRight aria-hidden className="mt-2 h-4 w-4 shrink-0 text-slate-600 transition group-hover:text-slate-300" /> : null}
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      </div> : null}
    </div>
  );
}

export function ChatActiveContextBar({ contexts }: { contexts: ChatActiveContext[] }) {
  if (contexts.length === 0) return null;
  return (
    <div aria-label="已启用的对话上下文" className="flex gap-2 overflow-x-auto px-1 pb-2 [scrollbar-width:thin]">
      {contexts.map((context) => (
        <span className="inline-flex min-h-9 shrink-0 items-center gap-2 rounded-full border border-brand-300/25 bg-brand-300/[0.08] pl-3 pr-1.5 text-xs font-semibold text-brand-50" key={context.id} title={context.detail}>
          <span className="max-w-48 truncate">{context.label}</span>
          <button
            aria-label={`移除 ${context.label}`}
            className="inline-flex h-8 w-8 items-center justify-center rounded-full text-brand-100 transition hover:bg-brand-300/15 hover:text-white focus:outline-none focus:ring-2 focus:ring-brand-300/25 disabled:opacity-50"
            disabled={context.disabled}
            onClick={context.onRemove}
            type="button"
          >
            <X aria-hidden className="h-3.5 w-3.5" />
          </button>
        </span>
      ))}
    </div>
  );
}

export function ChatOverlayDrawer({
  open,
  title,
  description,
  triggerRef,
  children,
  onClose,
}: {
  open: boolean;
  title: string;
  description?: string;
  triggerRef: RefObject<HTMLElement | null>;
  children: ReactNode;
  onClose: () => void;
}) {
  const titleId = useId();
  const drawerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        window.requestAnimationFrame(() => triggerRef.current?.focus());
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const items = focusableElements(drawerRef.current);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    window.requestAnimationFrame(() => focusableElements(drawerRef.current ?? document.body)[0]?.focus());
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, open, triggerRef]);

  if (typeof document === "undefined") return null;

  return createPortal(
    <div aria-hidden={!open} className={`fixed inset-0 z-[80] transition duration-200 motion-reduce:transition-none ${open ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"}`}>
      <button
        aria-label="关闭覆盖层"
        className="absolute inset-0 cursor-default bg-ink-950/68 backdrop-blur-[2px]"
        onClick={() => {
          onClose();
          window.requestAnimationFrame(() => triggerRef.current?.focus());
        }}
        tabIndex={-1}
        type="button"
      />
      <aside
        aria-labelledby={titleId}
        aria-modal="true"
        className={`absolute inset-x-0 bottom-0 flex max-h-[86dvh] flex-col overflow-hidden rounded-t-3xl border border-white/10 bg-surface-900 shadow-panel transition-transform duration-200 motion-reduce:transition-none sm:inset-y-0 sm:left-auto sm:right-0 sm:max-h-none sm:w-[min(30rem,92vw)] sm:rounded-none sm:rounded-l-2xl ${open ? "translate-y-0 sm:translate-x-0" : "translate-y-full sm:translate-x-full sm:translate-y-0"}`}
        ref={drawerRef}
        role="dialog"
      >
        <div className="flex min-h-16 items-center justify-between gap-4 border-b border-white/10 px-5 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-white" id={titleId}>{title}</h2>
            {description ? <p className="mt-0.5 text-xs text-slate-400">{description}</p> : null}
          </div>
          <button
            aria-label={`关闭${title}`}
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-slate-300 transition hover:bg-white/[0.07] hover:text-white focus:outline-none focus:ring-4 focus:ring-brand-300/15"
            onClick={() => {
              onClose();
              window.requestAnimationFrame(() => triggerRef.current?.focus());
            }}
            type="button"
          >
            <X aria-hidden className="h-5 w-5" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto [scrollbar-width:thin]">{children}</div>
      </aside>
    </div>,
    document.body,
  );
}
