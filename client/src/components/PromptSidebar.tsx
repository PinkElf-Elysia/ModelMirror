import { useMemo, useState } from "react";
import promptLibrary from "../data/promptLibrary.json";
import { recruitmentTheme } from "../theme/recruitmentTheme";

interface PromptItem {
  id: string;
  title: string;
  content: string;
  type?: string;
  forDevelopers?: boolean;
}

interface PromptCategory {
  id: string;
  name: string;
  icon: string;
  description: string;
  prompts: PromptItem[];
}

interface PromptSidebarProps {
  isOpen: boolean;
  superPromptMode: boolean;
  onToggleOpen: () => void;
  onSuperPromptModeChange: (enabled: boolean) => void;
  onFillPrompt: (content: string) => void;
  onSendPrompt: (content: string) => void;
}

const categories = promptLibrary.categories as PromptCategory[];

function previewText(content: string) {
  return content.length > 50 ? `${content.slice(0, 50)}...` : content;
}

function categoryMark(name: string) {
  return name.replace(/[^\u4e00-\u9fa5A-Za-z0-9]/g, "").slice(0, 1) || "提";
}

function interviewCategoryName(name: string) {
  const normalized = name.replace(/[^\u4e00-\u9fa5A-Za-z0-9]/g, "");
  if (normalized.includes("编程") || normalized.includes("开发")) return "编程岗笔试";
  if (normalized.includes("图像") || normalized.includes("视觉")) return "创意岗快问快答";
  if (normalized.includes("写作") || normalized.includes("文案")) return "文案岗面试题";
  if (normalized.includes("营销") || normalized.includes("商业")) return "增长岗案例题";
  if (normalized.includes("学习") || normalized.includes("教育")) return "学习助理面试题";
  if (normalized.includes("办公") || normalized.includes("效率")) return "行政助理面试题";
  if (normalized.includes("生活")) return "生活助理面试题";
  return `${normalized || "通用"}面试题`;
}

function PromptCard({
  prompt,
  variant,
  onFillPrompt,
  onSendPrompt,
}: {
  prompt: PromptItem;
  variant: "prompt" | "question";
  onFillPrompt: (content: string) => void;
  onSendPrompt: (content: string) => void;
}) {
  return (
    <article
      className="group rounded-lg border border-white/10 bg-white/[0.045] p-3 transition duration-200 hover:-translate-y-0.5 hover:border-brand-300/40 hover:bg-white/[0.075]"
      title={prompt.content}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-semibold text-white">
            {prompt.title}
          </h4>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            {previewText(prompt.content)}
          </p>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button
          className="rounded-full border border-white/10 bg-white/[0.05] px-2.5 py-1 text-xs font-medium text-slate-200 transition hover:border-brand-300/30 hover:bg-brand-300/10 hover:text-brand-100"
          onClick={() => onFillPrompt(prompt.content)}
          type="button"
        >
          {variant === "question" ? "备题" : "填入"}
        </button>
        <button
          className="rounded-full bg-brand-300 px-2.5 py-1 text-xs font-semibold text-ink-950 transition hover:bg-brand-200 hover:shadow-neon"
          onClick={() => onSendPrompt(prompt.content)}
          type="button"
        >
          {variant === "question" ? "开问" : "发送"}
        </button>
      </div>
    </article>
  );
}

export function PromptLibraryContent({
  superPromptMode,
  variant = "question",
  onSuperPromptModeChange,
  onFillPrompt,
  onSendPrompt,
}: Omit<PromptSidebarProps, "isOpen" | "onToggleOpen"> & {
  variant?: "prompt" | "question";
}) {
  const [openCategoryIds, setOpenCategoryIds] = useState<string[]>([
    categories[0]?.id ?? "",
  ]);

  const promptCount = useMemo(
    () =>
      categories.reduce(
        (total, category) => total + category.prompts.length,
        0,
      ),
    [],
  );

  function toggleCategory(id: string) {
    setOpenCategoryIds((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-white/10 bg-[linear-gradient(120deg,rgba(36,217,255,0.10),transparent_56%)] p-4">
        <p className="text-sm font-semibold text-white">
          {variant === "question" ? recruitmentTheme.promptPanelTitle : "提示库"}
        </p>
        <p className="mt-1 text-xs leading-5 text-slate-400">
          {variant === "question"
            ? `${categories.length} 个题库，${promptCount} 道面试题`
            : `${categories.length} 个分类，${promptCount} 条可复用提示`}
        </p>

        <button
          aria-pressed={superPromptMode}
          className={`mt-4 flex w-full items-center justify-between rounded-lg border px-3 py-3 text-left transition ${
            superPromptMode
              ? "border-accent-300/40 bg-accent-300/20 text-accent-100 shadow-neon"
              : "border-white/10 bg-white/[0.05] text-slate-200 hover:border-accent-300/30 hover:bg-accent-300/10"
          }`}
          onClick={() => onSuperPromptModeChange(!superPromptMode)}
          type="button"
        >
          <span>
            <span className="block text-sm font-semibold">
              {variant === "question" ? recruitmentTheme.superPromptTitle : "增强提示模式"}
            </span>
            <span className="mt-0.5 block text-xs opacity-75">
              {variant === "question"
                ? recruitmentTheme.superPromptDescription
                : "使用更严格的提示结构组织下一轮请求"}
            </span>
          </span>
          <span
            className={`relative h-6 w-11 rounded-full transition ${
              superPromptMode ? "bg-accent-400" : "bg-white/20"
            }`}
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition ${
                superPromptMode ? "left-5" : "left-0.5"
              }`}
            />
          </span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        <div className="space-y-2">
          {categories.map((category) => {
            const isOpen = openCategoryIds.includes(category.id);

            return (
              <section
                className="overflow-hidden rounded-lg border border-white/10 bg-white/[0.035]"
                key={category.id}
              >
                <button
                  aria-expanded={isOpen}
                  className="flex w-full items-center justify-between gap-2 px-3 py-3 text-left transition hover:bg-white/[0.04]"
                  onClick={() => toggleCategory(category.id)}
                  type="button"
                >
                  <span className="min-w-0">
                    <span className="flex items-center gap-2 text-sm font-semibold text-white">
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-brand-300/25 bg-brand-300/10 text-xs font-semibold text-brand-100">
                        {categoryMark(category.name)}
                      </span>
                      <span className="truncate">
                        {variant === "question"
                          ? interviewCategoryName(category.name)
                          : category.name}
                      </span>
                    </span>
                    <span className="mt-0.5 block text-xs text-slate-400">
                      {category.prompts.length} 条
                    </span>
                  </span>
                  <span
                    className={`shrink-0 text-slate-500 transition ${
                      isOpen ? "rotate-180 text-brand-200" : ""
                    }`}
                  >
                    ⌄
                  </span>
                </button>

                <div
                  className={`grid transition-all duration-200 ${
                    isOpen
                      ? "grid-rows-[1fr] opacity-100"
                      : "grid-rows-[0fr] opacity-0"
                  }`}
                >
                  <div className="overflow-hidden">
                    <div className="space-y-2 px-2 pb-3">
                      {category.prompts.map((prompt) => (
                        <PromptCard
                          key={prompt.id}
                          onFillPrompt={onFillPrompt}
                          onSendPrompt={onSendPrompt}
                          prompt={prompt}
                          variant={variant}
                        />
                      ))}
                    </div>
                  </div>
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default function PromptSidebar({
  isOpen,
  superPromptMode,
  onToggleOpen,
  onSuperPromptModeChange,
  onFillPrompt,
  onSendPrompt,
}: PromptSidebarProps) {
  return (
    <>
      <aside
        className={`surface-panel relative hidden shrink-0 overflow-hidden rounded-lg transition-all duration-300 lg:block lg:h-[calc(100vh-11rem)] ${
          isOpen ? "w-80" : "w-10"
        }`}
      >
        <button
          aria-label={isOpen ? "收起提示词助手" : "展开提示词助手"}
          className="absolute left-0 top-5 z-10 flex h-10 w-10 items-center justify-center rounded-r-full border border-l-0 border-white/10 bg-surface-850 text-slate-300 transition hover:bg-brand-300/10 hover:text-brand-100"
          onClick={onToggleOpen}
          type="button"
        >
          {isOpen ? "→" : "←"}
        </button>
        <div
          className={`h-full transition-opacity duration-200 ${
            isOpen ? "opacity-100" : "pointer-events-none opacity-0"
          }`}
        >
          <PromptLibraryContent
            onFillPrompt={onFillPrompt}
            onSendPrompt={onSendPrompt}
            onSuperPromptModeChange={onSuperPromptModeChange}
            superPromptMode={superPromptMode}
          />
        </div>
      </aside>

      <button
        className="fixed bottom-20 right-5 z-40 rounded-full bg-brand-300 px-4 py-3 text-sm font-semibold text-ink-950 shadow-neon transition hover:bg-brand-200 lg:hidden"
        onClick={onToggleOpen}
        type="button"
      >
        面试题库
      </button>

      <div
        className={`fixed inset-0 z-50 bg-ink-950/70 backdrop-blur-sm transition lg:hidden ${
          isOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onToggleOpen}
      />
      <aside
        className={`fixed inset-x-0 bottom-0 z-50 max-h-[82vh] overflow-hidden rounded-t-lg border border-white/10 bg-surface-900 shadow-panel transition-transform duration-300 lg:hidden ${
          isOpen ? "translate-y-0" : "translate-y-full"
        }`}
      >
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <span className="h-1.5 w-12 rounded-full bg-white/20" />
          <button
            className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-200 transition hover:border-brand-300/30 hover:text-brand-100"
            onClick={onToggleOpen}
            type="button"
          >
            关闭
          </button>
        </div>
        <div className="h-[72vh]">
          <PromptLibraryContent
            onFillPrompt={onFillPrompt}
            onSendPrompt={onSendPrompt}
            onSuperPromptModeChange={onSuperPromptModeChange}
            superPromptMode={superPromptMode}
          />
        </div>
      </aside>
    </>
  );
}
