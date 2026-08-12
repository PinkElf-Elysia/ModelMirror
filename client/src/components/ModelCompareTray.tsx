import { type Model } from "../data/models";

interface ModelCompareTrayProps {
  models: Model[];
  onClear: () => void;
  onCompare: () => void;
  onRemove: (modelId: string) => void;
}

export default function ModelCompareTray({
  models,
  onClear,
  onCompare,
  onRemove,
}: ModelCompareTrayProps) {
  if (models.length === 0) return null;

  return (
    <aside
      aria-label="模型对比栏"
      className="fixed inset-x-3 bottom-3 z-40 mx-auto max-w-4xl rounded-xl border border-white/15 bg-[#0b1626] p-3 shadow-[0_8px_24px_rgba(0,0,0,0.45)] sm:bottom-5 sm:p-4"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-[#ffc57d]">已选择 {models.length} / 4</p>
          <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
            {models.map((model) => (
              <button
                className="shrink-0 rounded-md bg-white/[0.06] px-2.5 py-1.5 text-xs text-slate-200"
                key={model.id}
                onClick={() => onRemove(model.id)}
                title="移出对比"
                type="button"
              >
                {model.name} ×
              </button>
            ))}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            className="min-h-11 rounded-lg px-3 text-sm font-semibold text-slate-300 hover:bg-white/[0.06]"
            onClick={onClear}
            type="button"
          >
            清空
          </button>
          <button
            className="min-h-11 rounded-lg bg-[#ffb45e] px-4 text-sm font-semibold text-[#08111f] disabled:cursor-not-allowed disabled:opacity-45"
            disabled={models.length < 2}
            onClick={onCompare}
            type="button"
          >
            开始对比
          </button>
        </div>
      </div>
    </aside>
  );
}
