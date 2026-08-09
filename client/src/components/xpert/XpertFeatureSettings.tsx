import { useEffect, useMemo, useState } from "react";
import {
  extensionsForPurpose,
  fetchFileCapabilities,
  type FileCapabilitiesResponse,
} from "../../data/fileCapabilities";
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
  const [fileCapabilities, setFileCapabilities] =
    useState<FileCapabilitiesResponse | null>(null);
  const [fileCapabilitiesLoaded, setFileCapabilitiesLoaded] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetchFileCapabilities(controller.signal).then((payload) => {
      if (!controller.signal.aborted) {
        setFileCapabilities(payload);
        setFileCapabilitiesLoaded(true);
      }
    });
    return () => controller.abort();
  }, []);

  const allowedAgentExtensions = useMemo(
    () =>
      fileCapabilities
        ? extensionsForPurpose(fileCapabilities, "agent", "document")
        : [],
    [fileCapabilities],
  );
  const selectedAgentExtensions = useMemo(
    () =>
      new Set(
        value.file_upload.allowed_extensions.map((extension) => {
          const normalized = extension.trim().toLowerCase();
          return normalized.startsWith(".") ? normalized : `.${normalized}`;
        }),
      ),
    [value.file_upload.allowed_extensions],
  );
  const unsupportedAgentExtensions = useMemo(
    () =>
      Array.from(selectedAgentExtensions).filter(
        (extension) => !allowedAgentExtensions.includes(extension),
      ),
    [allowedAgentExtensions, selectedAgentExtensions],
  );
  const selectedAllowedAgentExtensions = useMemo(
    () =>
      allowedAgentExtensions.filter((extension) =>
        selectedAgentExtensions.has(extension),
      ),
    [allowedAgentExtensions, selectedAgentExtensions],
  );

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
            <fieldset className="rounded-md border border-white/10 bg-ink-950/55 p-3">
              <legend className="px-1 text-[11px] font-semibold text-slate-300">
                允许格式
              </legend>
              {allowedAgentExtensions.length ? (
                <div className="flex flex-wrap gap-2">
                  {allowedAgentExtensions.map((extension) => {
                    const checked = selectedAgentExtensions.has(extension);
                    const cannotRemoveLast =
                      checked && selectedAllowedAgentExtensions.length === 1;
                    return (
                      <label
                        className="flex cursor-pointer items-center gap-1.5 rounded-md border border-white/10 bg-white/[0.035] px-2 py-1.5 text-xs text-slate-200"
                        key={extension}
                      >
                        <input
                          checked={checked}
                          disabled={cannotRemoveLast}
                          onChange={(event) => {
                            const next = new Set(selectedAgentExtensions);
                            if (event.target.checked) next.add(extension);
                            else next.delete(extension);
                            patch("file_upload", {
                              allowed_extensions: [
                                ...allowedAgentExtensions.filter((item) =>
                                  next.has(item),
                                ),
                                ...unsupportedAgentExtensions,
                              ],
                            });
                          }}
                          type="checkbox"
                        />
                        {extension}
                      </label>
                    );
                  })}
                </div>
              ) : (
                <p className="text-xs leading-5 text-amber-100">
                  {fileCapabilitiesLoaded
                    ? "当前没有已确认可用的智能体文件格式，原配置已保留。"
                    : "正在读取文件能力清单…"}
                </p>
              )}
              <p className="mt-2 text-[11px] leading-5 text-slate-500">
                选项来自后端文件能力清单；发布时仍会再次校验。
              </p>
              {unsupportedAgentExtensions.length ? (
                <div className="mt-2 flex items-center justify-between gap-3 rounded-md border border-amber-300/20 bg-amber-300/[0.06] px-2 py-1.5 text-[11px] text-amber-100">
                  <span>
                    旧配置含未登记格式：{unsupportedAgentExtensions.join("、")}
                  </span>
                  {allowedAgentExtensions.length ? (
                    <button
                      className="shrink-0 rounded border border-amber-200/25 px-2 py-1 font-semibold hover:bg-amber-200/10"
                      onClick={() =>
                        patch("file_upload", {
                          allowed_extensions:
                            selectedAllowedAgentExtensions.length > 0
                              ? selectedAllowedAgentExtensions
                              : allowedAgentExtensions,
                        })
                      }
                      type="button"
                    >
                      移除未登记项
                    </button>
                  ) : null}
                </div>
              ) : null}
            </fieldset>
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
