import { useMemo, useState } from "react";

export interface InsertableVariable {
  /** 插入到模板中的变量名（不含 {{ }}）。 */
  name: string;
  /** 展示标签，如节点标题。 */
  label?: string;
}

/**
 * 全站可复用的"插入变量"按钮 + 下拉。对标 Dify/Coze 的变量选择器：
 * 从上游节点变量树中点选，把 `{{变量}}` 交给调用方插入到字段（文本光标处）。
 *
 * 用法：
 * ```tsx
 * <VariableInsertMenu
 *   variables={[{ name: "user_input", label: "输入工位" }]}
 *   onPick={(name) => insertAtCursor(`{{${name}}}`)}
 * />
 * ```
 */
export default function VariableInsertMenu({
  variables,
  onPick,
  disabled = false,
}: {
  variables: InsertableVariable[];
  onPick: (name: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return variables;
    return variables.filter(
      (item) =>
        item.name.toLowerCase().includes(normalized) ||
        (item.label ?? "").toLowerCase().includes(normalized),
    );
  }, [query, variables]);

  return (
    <div className="relative">
      <button
        className="rounded-md border border-brand-300/30 bg-brand-300/10 px-2 py-0.5 text-[11px] font-semibold text-brand-100 transition hover:bg-brand-300/20 disabled:cursor-not-allowed disabled:opacity-50"
        disabled={disabled || variables.length === 0}
        onClick={() => setOpen((current) => !current)}
        title="插入变量"
        type="button"
      >
        ＋ 变量
      </button>
      {open ? (
        <>
          <div
            aria-hidden="true"
            className="fixed inset-0 z-30"
            onClick={() => setOpen(false)}
          />
          <div className="absolute left-0 top-full z-40 mt-1 w-52 overflow-hidden rounded-lg border border-white/10 bg-[#101828] shadow-xl shadow-ink-950/60">
            <input
              autoFocus
              className="w-full border-b border-white/10 bg-[#0f1728] px-2.5 py-2 text-xs text-white outline-none placeholder:text-slate-500"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索变量..."
              value={query}
            />
            <div className="max-h-48 overflow-y-auto p-1">
              {filtered.length === 0 ? (
                <p className="px-2.5 py-2 text-xs text-slate-500">没有匹配的变量。</p>
              ) : (
                filtered.map((item) => (
                  <button
                    className="flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-xs text-slate-200 transition hover:bg-white/10"
                    key={item.name}
                    onClick={() => {
                      onPick(item.name);
                      setOpen(false);
                      setQuery("");
                    }}
                    type="button"
                  >
                    <span className="truncate font-mono text-[11px] text-brand-200">
                      {`{{${item.name}}}`}
                    </span>
                    {item.label ? (
                      <span className="shrink-0 truncate text-slate-500">
                        {item.label}
                      </span>
                    ) : null}
                  </button>
                ))
              )}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
