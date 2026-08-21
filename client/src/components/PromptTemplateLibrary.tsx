import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import promptLibrary from "../data/promptLibrary.json";
import { chatModelOptions } from "../data/modelOptions";
import { createPromptDraftHandoff } from "../data/promptDraftHandoff";

interface PromptItem {
  id: string;
  title: string;
  content: string;
}

interface PromptCategory {
  id: string;
  name: string;
  description: string;
  prompts: PromptItem[];
}

const categories = promptLibrary.categories as PromptCategory[];

interface TargetModelPickerProps {
  models: typeof chatModelOptions;
  available: boolean;
  value: string;
  onChange: (modelId: string) => void;
}

function TargetModelPicker({ models, available, value, onChange }: TargetModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const selectedModel = models.find((model) => model.id === value);
  const filteredModels = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return models
      .filter((model) =>
        !normalized || `${model.name} ${model.id}`.toLowerCase().includes(normalized),
      )
      .slice(0, 80);
  }, [models, query]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  useEffect(() => {
    if (open) window.requestAnimationFrame(() => searchRef.current?.focus());
  }, [open]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        aria-controls="prompt-target-model-list"
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex min-h-12 w-full items-center gap-3 rounded-xl border border-white/10 bg-[#0a1423] px-3 text-left text-sm transition hover:border-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#ffb45e]/60 disabled:cursor-not-allowed disabled:opacity-55"
        disabled={!available}
        onClick={() => setOpen((current) => !current)}
        ref={triggerRef}
        type="button"
      >
        <span className="shrink-0 text-slate-400">目标模型</span>
        <span className={`min-w-0 flex-1 truncate ${selectedModel ? "text-white" : "text-slate-300"}`}>
          {selectedModel?.name ?? (available ? "使用前选择" : "实时目录不可用")}
        </span>
        <span aria-hidden="true" className="text-slate-400">⌄</span>
      </button>
      {open ? (
        <div className="absolute inset-x-0 top-[calc(100%+0.5rem)] z-50 rounded-xl border border-white/15 bg-[#081321] p-2 shadow-[0_8px_24px_rgba(0,0,0,0.45)]">
          <label className="block">
            <span className="sr-only">搜索目标模型</span>
            <input
              className="h-10 w-full rounded-lg border border-white/10 bg-white/[0.045] px-3 text-sm text-white outline-none placeholder:text-slate-500 focus:border-[#ffb45e]/60"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索模型名称或 ID"
              ref={searchRef}
              type="search"
              value={query}
            />
          </label>
          <div className="mt-2 max-h-72 overflow-y-auto overscroll-contain" id="prompt-target-model-list" role="listbox">
            {filteredModels.map((model) => (
              <button
                aria-selected={model.id === value}
                className="flex min-h-11 w-full flex-col justify-center rounded-lg px-3 text-left text-sm text-slate-200 hover:bg-white/[0.07] focus-visible:bg-white/[0.07] focus-visible:outline-none aria-selected:bg-[#ffb45e]/15 aria-selected:text-[#ffd2a0]"
                key={model.id}
                onClick={() => {
                  onChange(model.id);
                  setOpen(false);
                  setQuery("");
                  triggerRef.current?.focus();
                }}
                role="option"
                type="button"
              >
                <span className="font-medium">{model.name}</span>
                <span className="truncate text-xs text-slate-500">{model.id}</span>
              </button>
            ))}
            {filteredModels.length === 0 ? (
              <p className="px-3 py-5 text-center text-sm text-slate-500">没有匹配的目标模型</p>
            ) : null}
          </div>
          {models.length > filteredModels.length ? (
            <p className="border-t border-white/10 px-3 pt-2 text-xs text-slate-500">继续输入以缩小范围</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default function PromptTemplateLibrary() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [categoryId, setCategoryId] = useState("all");
  const [targetModelId, setTargetModelId] = useState("");
  const [notice, setNotice] = useState("");

  const selectableModels = useMemo(
    () => chatModelOptions.filter(
      (model) => model.interaction_status === "ready",
    ),
    [],
  );
  const visibleCategories = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return categories
      .filter((category) => categoryId === "all" || category.id === categoryId)
      .map((category) => ({
        ...category,
        prompts: category.prompts.filter((prompt) =>
          !normalized || `${prompt.title} ${prompt.content}`.toLowerCase().includes(normalized),
        ),
      }))
      .filter((category) => category.prompts.length > 0);
  }, [categoryId, query]);

  function useTemplate(prompt: PromptItem) {
    if (!targetModelId) {
      setNotice("请先选择一个可调用的目标模型。");
      return;
    }
    const draft = createPromptDraftHandoff(window.sessionStorage, {
      templateId: prompt.id,
      targetModelId,
      content: prompt.content,
    });
    navigate(`/chat/${encodeURIComponent(targetModelId)}?prompt_draft=${encodeURIComponent(draft.nonce)}`);
  }

  return (
    <section aria-labelledby="prompt-library-title">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,26rem)]">
        <label>
          <span className="sr-only">搜索模板</span>
          <input className="h-12 w-full rounded-xl border border-white/10 bg-[#0a1423] px-4 text-sm text-white outline-none placeholder:text-slate-500 focus:border-[#ffb45e]/60" onChange={(event) => setQuery(event.target.value)} placeholder="搜索模板或使用场景" type="search" value={query} />
        </label>
          <TargetModelPicker
            available
            models={selectableModels}
          onChange={(modelId) => {
            setTargetModelId(modelId);
            setNotice("");
          }}
          value={targetModelId}
        />
      </div>
      <div aria-label="模板分类" className="mt-3 flex gap-2 overflow-x-auto pb-1">
        <button className="min-h-11 shrink-0 rounded-lg border border-white/10 px-3 text-sm text-slate-300 aria-pressed:border-[#ffb45e]/50 aria-pressed:text-[#ffd2a0]" aria-pressed={categoryId === "all"} onClick={() => setCategoryId("all")} type="button">全部</button>
        {categories.map((category) => <button className="min-h-11 shrink-0 rounded-lg border border-white/10 px-3 text-sm text-slate-300 aria-pressed:border-[#ffb45e]/50 aria-pressed:text-[#ffd2a0]" aria-pressed={categoryId === category.id} key={category.id} onClick={() => setCategoryId(category.id)} type="button">{category.name}</button>)}
      </div>
      {notice ? <p aria-live="polite" className="mt-3 rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-sm text-amber-100">{notice}</p> : null}
      <div className="mt-6 space-y-8">
        {visibleCategories.map((category) => (
          <section key={category.id}>
            <div className="mb-3">
              <h2 className="text-lg font-semibold text-white" id={category.id === visibleCategories[0]?.id ? "prompt-library-title" : undefined}>{category.name}</h2>
              <p className="mt-1 text-sm text-slate-400">{category.description}</p>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {category.prompts.map((prompt) => (
                <article className="flex min-h-52 flex-col rounded-xl border border-white/10 bg-[#0e1929] p-4" key={prompt.id}>
                  <h3 className="text-base font-semibold text-white">{prompt.title}</h3>
                  <p className="mt-2 line-clamp-4 text-sm leading-6 text-slate-400">{prompt.content}</p>
                  <button className="mt-auto min-h-11 self-start rounded-lg bg-[#ffb45e] px-4 text-sm font-semibold text-[#08111f]" onClick={() => useTemplate(prompt)} type="button">用于对话</button>
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
    </section>
  );
}
