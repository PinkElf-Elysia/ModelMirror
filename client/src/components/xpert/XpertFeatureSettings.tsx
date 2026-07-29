import { models } from "../../data/models";
import { type XpertFeatureConfig } from "../../types/xpert";

interface Props {
  value: XpertFeatureConfig;
  onChange: (next: XpertFeatureConfig) => void;
}

function Toggle({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 text-xs font-semibold text-slate-200">
      <span>{label}</span>
      <input
        checked={checked}
        className="h-4 w-4 accent-hire-300"
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
    </label>
  );
}

function ModelSelect({
  category,
  onChange,
  value,
}: {
  category?: "speech" | "transcription";
  onChange: (modelId: string) => void;
  value: string;
}) {
  const options = models
    .filter((model) => !category || model.categories.includes(category))
    .sort((left, right) => left.name.localeCompare(right.name));
  return (
    <select
      className="h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-slate-200 outline-none focus:border-hire-300/60"
      onChange={(event) => onChange(event.target.value)}
      value={value}
    >
      <option value="">跟随主 Agent 模型</option>
      {options.map((model) => (
        <option key={model.id} value={model.id}>
          {model.name}
        </option>
      ))}
    </select>
  );
}

export default function XpertFeatureSettings({ onChange, value }: Props) {
  const patch = <K extends keyof XpertFeatureConfig>(
    key: K,
    next: Partial<XpertFeatureConfig[K]>,
  ) => {
    onChange({
      ...value,
      [key]: { ...value[key], ...next },
    });
  };

  return (
    <section className="mb-5 rounded-lg border border-white/10 bg-ink-950/72 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-white">会话功能</h2>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            配置会随智能体版本发布并固定，旧版本不会被草稿修改影响。
          </p>
        </div>
        <span className="rounded-full border border-hire-300/25 bg-hire-300/10 px-2 py-1 text-[11px] font-semibold text-hire-100">
          版本化
        </span>
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-2">
        <details className="rounded-md border border-white/10 bg-white/[0.025] p-3" open>
          <summary className="cursor-pointer text-xs font-semibold text-white">开场与问题建议</summary>
          <div className="mt-3 space-y-3">
            <Toggle
              checked={value.opening.enabled}
              label="启用开场白"
              onChange={(enabled) => patch("opening", { enabled })}
            />
            <textarea
              className="min-h-20 w-full resize-y rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-hire-300/60"
              maxLength={4000}
              onChange={(event) => patch("opening", { message: event.target.value })}
              placeholder="开场白"
              value={value.opening.message}
            />
            <textarea
              className="min-h-20 w-full resize-y rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-hire-300/60"
              onChange={(event) => patch("opening", {
                questions: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean).slice(0, 8),
              })}
              placeholder="开场问题，每行一个"
              value={value.opening.questions.join("\n")}
            />
            <Toggle
              checked={value.generated_questions.enabled}
              label="回答后生成后续问题"
              onChange={(enabled) => patch("generated_questions", { enabled })}
            />
            <div className="grid grid-cols-[1fr_96px] gap-2">
              <ModelSelect
                onChange={(model_id) => patch("generated_questions", { model_id })}
                value={value.generated_questions.model_id}
              />
              <input
                className="h-9 rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white outline-none"
                max={6}
                min={1}
                onChange={(event) => patch("generated_questions", { count: Number(event.target.value) })}
                type="number"
                value={value.generated_questions.count}
              />
            </div>
          </div>
        </details>

        <details className="rounded-md border border-white/10 bg-white/[0.025] p-3" open>
          <summary className="cursor-pointer text-xs font-semibold text-white">标题、摘要与记忆回复</summary>
          <div className="mt-3 space-y-3">
            <Toggle
              checked={value.conversation_title.enabled}
              label="自动生成会话标题"
              onChange={(enabled) => patch("conversation_title", { enabled })}
            />
            <ModelSelect
              onChange={(model_id) => patch("conversation_title", { model_id })}
              value={value.conversation_title.model_id}
            />
            <Toggle
              checked={value.conversation_summary.enabled}
              label="长对话自动摘要"
              onChange={(enabled) => patch("conversation_summary", { enabled })}
            />
            <ModelSelect
              onChange={(model_id) => patch("conversation_summary", { model_id })}
              value={value.conversation_summary.model_id}
            />
            <div className="grid grid-cols-2 gap-2">
              <label className="text-[11px] text-slate-400">
                保留近期消息
                <input
                  className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white"
                  max={30}
                  min={2}
                  onChange={(event) => patch("conversation_summary", { keep_recent_messages: Number(event.target.value) })}
                  type="number"
                  value={value.conversation_summary.keep_recent_messages}
                />
              </label>
              <label className="text-[11px] text-slate-400">
                触发比例
                <input
                  className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white"
                  max={0.95}
                  min={0.5}
                  onChange={(event) => patch("conversation_summary", { trigger_ratio: Number(event.target.value) })}
                  step={0.05}
                  type="number"
                  value={value.conversation_summary.trigger_ratio}
                />
              </label>
            </div>
            <Toggle
              checked={value.memory_reply.enabled}
              label="高置信记忆直接回复"
              onChange={(enabled) => patch("memory_reply", { enabled })}
            />
            <label className="text-[11px] text-slate-400">
              最低置信度
              <input
                className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white"
                max={1}
                min={0.8}
                onChange={(event) => patch("memory_reply", { min_confidence: Number(event.target.value) })}
                step={0.01}
                type="number"
                value={value.memory_reply.min_confidence}
              />
            </label>
          </div>
        </details>

        <details className="rounded-md border border-white/10 bg-white/[0.025] p-3">
          <summary className="cursor-pointer text-xs font-semibold text-white">文件能力</summary>
          <div className="mt-3 space-y-3">
            <Toggle
              checked={value.file_upload.enabled}
              label="允许会话文件"
              onChange={(enabled) => patch("file_upload", { enabled })}
            />
            <label className="text-[11px] text-slate-400">
              单次最多文件
              <input
                className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white"
                max={5}
                min={1}
                onChange={(event) => patch("file_upload", { max_files_per_run: Number(event.target.value) })}
                type="number"
                value={value.file_upload.max_files_per_run}
              />
            </label>
            <label className="text-[11px] text-slate-400">
              允许扩展名
              <input
                className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white"
                onChange={(event) => patch("file_upload", {
                  allowed_extensions: event.target.value.split(/[,\s]+/).map((item) => item.trim()).filter(Boolean),
                })}
                value={value.file_upload.allowed_extensions.join(", ")}
              />
            </label>
          </div>
        </details>

        <details className="rounded-md border border-white/10 bg-white/[0.025] p-3">
          <summary className="cursor-pointer text-xs font-semibold text-white">语音输入与播报</summary>
          <div className="mt-3 space-y-3">
            <Toggle
              checked={value.speech_to_text.enabled}
              label="启用语音转文字"
              onChange={(enabled) => patch("speech_to_text", { enabled })}
            />
            <ModelSelect
              category="transcription"
              onChange={(model_id) => patch("speech_to_text", { model_id })}
              value={value.speech_to_text.model_id}
            />
            <Toggle
              checked={value.text_to_speech.enabled}
              label="启用回答播报"
              onChange={(enabled) => patch("text_to_speech", { enabled })}
            />
            <ModelSelect
              category="speech"
              onChange={(model_id) => patch("text_to_speech", { model_id })}
              value={value.text_to_speech.model_id}
            />
            <div className="grid grid-cols-2 gap-2">
              <input
                className="h-9 rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white"
                onChange={(event) => patch("text_to_speech", { voice: event.target.value })}
                placeholder="voice"
                value={value.text_to_speech.voice}
              />
              <input
                className="h-9 rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white"
                max={10000}
                min={100}
                onChange={(event) => patch("text_to_speech", { max_text_chars: Number(event.target.value) })}
                type="number"
                value={value.text_to_speech.max_text_chars}
              />
            </div>
          </div>
        </details>
      </div>
    </section>
  );
}
