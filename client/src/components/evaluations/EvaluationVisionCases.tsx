import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Download,
  FileImage,
  FileUp,
  LoaderCircle,
  Plus,
  Trash2,
  Unlink,
} from "lucide-react";

import {
  extensionsForPurpose,
  fetchFileCapabilities,
  uploadEvaluationFile,
  type FileAssetResponse,
  type FileCapabilitiesResponse,
} from "../../data/fileCapabilities";

export type VisionEvidenceBlockKind = "ocr" | "description" | "table" | "chart";

export interface EvaluationVisionContentAnchor {
  kind: VisionEvidenceBlockKind;
  text: string;
  page_number?: number | null;
}

export interface EvaluationVisionExpectation {
  node_ref: string;
  asset_sha256?: string | null;
  model_id?: string | null;
  page_count?: number | null;
  status: "success" | "partial";
  required_blocks: VisionEvidenceBlockKind[];
  content_anchors: EvaluationVisionContentAnchor[];
}

export interface EvaluationAttachmentManifest {
  asset_id: string;
  sha256?: string | null;
  format_id?: string | null;
  media_type?: string | null;
  byte_size?: number | null;
  page_count?: number | null;
  display_name?: string | null;
  dataset_id?: string | null;
  dataset_scope_id?: string | null;
  scope_id?: string | null;
  dataset_version?: number | null;
  version_scope_id?: string | null;
}

export interface EvaluationVisionCase {
  case_id?: string;
  name?: string;
  message: string;
  attachment?: EvaluationAttachmentManifest | null;
  vision?: EvaluationVisionExpectation[];
  [key: string]: unknown;
}

interface EvaluationVisionCasesProps {
  datasetId: string;
  cases: EvaluationVisionCase[] | null;
  disabled?: boolean;
  onChange: (cases: EvaluationVisionCase[]) => void;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}

const blockOptions: Array<{ value: VisionEvidenceBlockKind; label: string }> = [
  { value: "ocr", label: "文字识别" },
  { value: "description", label: "画面描述" },
  { value: "table", label: "表格" },
  { value: "chart", label: "图表" },
];

const nodeRefPattern = /^[a-z][a-z0-9_-]{0,63}$/;
const sha256Pattern = /^[a-f0-9]{64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseVisionAnchorsJson(value: string): EvaluationVisionContentAnchor[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("内容锚点必须是合法 JSON 数组。");
  }
  if (!Array.isArray(parsed) || parsed.length > 10) {
    throw new Error("内容锚点必须是数组，且最多 10 项。");
  }
  return parsed.map((item, index) => {
    if (!isRecord(item)) throw new Error(`第 ${index + 1} 个锚点必须是对象。`);
    const unknownFields = Object.keys(item).filter(
      (field) => !["kind", "text", "page_number"].includes(field),
    );
    if (unknownFields.length) {
      throw new Error(`第 ${index + 1} 个锚点包含不允许字段：${unknownFields.join("、")}。`);
    }
    if (!blockOptions.some((option) => option.value === item.kind)) {
      throw new Error(`第 ${index + 1} 个锚点 kind 必须是 ocr、description、table 或 chart。`);
    }
    if (typeof item.text !== "string" || !item.text.trim() || item.text.length > 200) {
      throw new Error(`第 ${index + 1} 个锚点 text 必须为 1 至 200 个字符。`);
    }
    if (
      item.page_number !== undefined
      && item.page_number !== null
      && (!Number.isInteger(item.page_number)
        || Number(item.page_number) < 1
        || Number(item.page_number) > 20)
    ) {
      throw new Error(`第 ${index + 1} 个锚点页码必须为 1 至 20 的整数。`);
    }
    return {
      kind: item.kind as VisionEvidenceBlockKind,
      text: item.text,
      ...(item.page_number === undefined
        ? {}
        : { page_number: item.page_number === null ? null : Number(item.page_number) }),
    };
  });
}

function formatBytes(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "大小待保存确认";
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KiB`;
  return `${(value / 1024 / 1024).toFixed(2)} MiB`;
}

function normalizedExtensions(capabilities: FileCapabilitiesResponse | null) {
  if (!capabilities) return [];
  return extensionsForPurpose(capabilities, "evaluation", "visual_analysis")
    .map((extension) => extension.startsWith(".") ? extension : `.${extension}`);
}

function caseLabel(item: EvaluationVisionCase, index: number) {
  return item.name?.trim() || item.case_id?.trim() || `用例 ${index + 1}`;
}

export function evaluationCaseUploadIdentity(item: EvaluationVisionCase) {
  const caseId = item.case_id?.trim();
  if (caseId) return `case:${caseId}`;
  return `draft:${JSON.stringify([
    item.name ?? null,
    item.message,
    item.messages ?? null,
  ])}`;
}

export function resolveEvaluationUploadCaseIndex(
  cases: EvaluationVisionCase[],
  identity: string,
) {
  const matches = cases.flatMap((item, index) =>
    evaluationCaseUploadIdentity(item) === identity ? [index] : [],
  );
  return matches.length === 1 ? matches[0] : null;
}

function AnchorEditor({
  anchors,
  label,
  disabled,
  onApply,
}: {
  anchors: EvaluationVisionContentAnchor[];
  label: string;
  disabled: boolean;
  onApply: (anchors: EvaluationVisionContentAnchor[]) => void;
}) {
  const serialized = JSON.stringify(anchors, null, 2);
  const [text, setText] = useState(serialized);
  const [error, setError] = useState("");

  useEffect(() => setText(serialized), [serialized]);

  return (
    <div className="sm:col-span-2">
      <div className="flex items-center justify-between gap-3">
        <label className="text-[11px] font-semibold text-slate-400" htmlFor={label}>
          内容锚点 JSON
        </label>
        <span className="text-[10px] text-slate-500">仅允许 kind、text、page_number</span>
      </div>
      <textarea
        className="mt-1 min-h-28 w-full resize-y rounded-md border border-white/10 bg-ink-950 px-3 py-2 font-mono text-[11px] leading-5 text-slate-200 outline-none focus:border-cyan-300/40"
        disabled={disabled}
        id={label}
        onChange={(event) => {
          setText(event.target.value);
          setError("");
        }}
        spellCheck={false}
        value={text}
      />
      <div className="mt-2 flex items-start justify-between gap-3">
        {error ? <p className="text-[11px] leading-5 text-rose-200" role="alert">{error}</p> : <span />}
        <button
          className="shrink-0 rounded-md border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[11px] font-semibold text-slate-200 disabled:opacity-50"
          disabled={disabled}
          onClick={() => {
            try {
              onApply(parseVisionAnchorsJson(text));
              setError("");
            } catch (caught) {
              setError(caught instanceof Error ? caught.message : "内容锚点无效。");
            }
          }}
          type="button"
        >
          应用锚点
        </button>
      </div>
    </div>
  );
}

export default function EvaluationVisionCases({
  datasetId,
  cases,
  disabled = false,
  onChange,
  onError,
  onNotice,
}: EvaluationVisionCasesProps) {
  const [capabilities, setCapabilities] = useState<FileCapabilitiesResponse | null>(null);
  const [capabilitiesLoaded, setCapabilitiesLoaded] = useState(false);
  const [uploadingCase, setUploadingCase] = useState<string | null>(null);
  const [recentUploads, setRecentUploads] = useState<Record<string, FileAssetResponse>>({});
  const casesRef = useRef(cases);
  const datasetIdRef = useRef(datasetId);
  const uploadAbortRef = useRef<AbortController | null>(null);

  useLayoutEffect(() => {
    casesRef.current = cases;
  }, [cases]);

  useLayoutEffect(() => {
    datasetIdRef.current = datasetId;
    uploadAbortRef.current?.abort();
    uploadAbortRef.current = null;
    setUploadingCase(null);
  }, [datasetId]);

  useEffect(() => () => {
    uploadAbortRef.current?.abort();
    uploadAbortRef.current = null;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setCapabilitiesLoaded(false);
    setCapabilities(null);
    setRecentUploads({});
    void fetchFileCapabilities(controller.signal, { purpose: "evaluation" })
      .then((payload) => {
        if (!controller.signal.aborted) {
          setCapabilities(payload);
          setCapabilitiesLoaded(true);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setCapabilitiesLoaded(true);
      });
    return () => controller.abort();
  }, [datasetId]);

  const extensions = useMemo(() => normalizedExtensions(capabilities), [capabilities]);
  const capability = capabilities?.capabilities.find(
    (item) => item.purpose === "evaluation"
      && item.input_kind === "visual_analysis"
      && item.interaction_status === "ready",
  );
  const uploadAvailable = Boolean(capability && extensions.length);
  const editorDisabled = disabled || uploadingCase !== null;
  const uploadStatus = !capabilitiesLoaded
    ? "正在读取附件能力..."
    : uploadAvailable
      ? `${extensions.map((item) => item.slice(1).toUpperCase()).join("、")}，单文件最多 10 MiB，PDF 最多 20 页`
      : "评测附件能力当前不可用，已禁止上传。";

  function replaceCase(index: number, nextCase: EvaluationVisionCase) {
    const currentCases = casesRef.current;
    if (!currentCases) return;
    onChange(currentCases.map((item, itemIndex) => itemIndex === index ? nextCase : item));
  }

  function updateVision(
    caseIndex: number,
    visionIndex: number,
    update: (current: EvaluationVisionExpectation) => EvaluationVisionExpectation,
  ) {
    const currentCases = casesRef.current;
    if (!currentCases) return;
    const item = currentCases[caseIndex];
    const vision = [...(item.vision ?? [])];
    vision[visionIndex] = update(vision[visionIndex]);
    replaceCase(caseIndex, { ...item, vision });
  }

  async function uploadAttachment(caseIndex: number, file: File) {
    const startingCases = casesRef.current;
    if (!startingCases || !capability) return;
    const originalDatasetId = datasetIdRef.current;
    const startingCase = startingCases[caseIndex];
    if (!startingCase) return;
    const identity = evaluationCaseUploadIdentity(startingCase);
    if (resolveEvaluationUploadCaseIndex(startingCases, identity) !== caseIndex) {
      onError("当前用例缺少唯一身份。请先保存草稿生成 case_id，再上传附件。");
      return;
    }
    const extension = file.name.includes(".")
      ? `.${file.name.split(".").pop()?.toLowerCase()}`
      : "";
    if (!file.size || file.size > capability.max_bytes_per_file) {
      onError("附件必须非空且不超过 10 MiB。");
      return;
    }
    if (!extensions.includes(extension)) {
      onError("评测附件仅支持 PNG、JPEG、WebP 或 PDF。");
      return;
    }
    const controller = new AbortController();
    uploadAbortRef.current?.abort();
    uploadAbortRef.current = controller;
    setUploadingCase(identity);
    onError("");
    try {
      const asset = await uploadEvaluationFile(file, originalDatasetId, controller.signal);
      if (controller.signal.aborted || datasetIdRef.current !== originalDatasetId) return;
      const currentCases = casesRef.current;
      const currentIndex = currentCases
        ? resolveEvaluationUploadCaseIndex(currentCases, identity)
        : null;
      if (currentIndex === null || !currentCases) {
        throw new Error("评测用例身份已变化，旧上传结果未绑定。请重新选择附件。");
      }
      const current = currentCases[currentIndex];
      replaceCase(currentIndex, {
        ...current,
        attachment: { asset_id: asset.asset_id },
      });
      setRecentUploads((items) => ({ ...items, [asset.asset_id]: asset }));
      onNotice(`已为“${caseLabel(current, currentIndex)}”上传附件。请按需显式添加视觉断言，再保存草稿。`);
    } catch (caught) {
      if (controller.signal.aborted) return;
      onError(caught instanceof Error ? caught.message : "评测附件上传失败。");
    } finally {
      if (uploadAbortRef.current === controller) {
        uploadAbortRef.current = null;
        setUploadingCase(null);
      }
    }
  }

  return (
    <section aria-labelledby="evaluation-vision-title" className="border-y border-white/10 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <FileImage className="h-4 w-4 text-cyan-200" />
            <h3 className="text-sm font-semibold text-white" id="evaluation-vision-title">逐例视觉附件与断言</h3>
          </div>
          <p className="mt-1 max-w-3xl text-[11px] leading-5 text-slate-400">
            每个用例最多一个附件。上传只建立当前数据集草稿引用，不会自动添加视觉断言；解除引用也不会删除已发布版本中的固定附件。
          </p>
        </div>
        <span className="text-[11px] text-slate-500">{uploadStatus}</span>
      </div>

      {cases === null ? (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-300/20 bg-amber-300/10 p-3 text-xs leading-5 text-amber-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          完整用例 JSON 当前无法解析。修正后才能使用逐例视觉编辑器。
        </div>
      ) : cases.length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-white/10 p-4 text-xs text-slate-500">先添加评测用例，再配置视觉附件。</p>
      ) : (
        <div className="mt-4 space-y-2">
          {cases.map((item, caseIndex) => {
            const caseIdentity = evaluationCaseUploadIdentity(item);
            const attachment = item.attachment;
            const uploaded = attachment ? recentUploads[attachment.asset_id] : undefined;
            const displayName = attachment?.display_name || uploaded?.display_name;
            const format = attachment?.format_id || uploaded?.format;
            const byteSize = attachment?.byte_size ?? uploaded?.byte_size;
            const vision = item.vision ?? [];
            const inputId = `evaluation-vision-file-${caseIndex}`;
            return (
              <details className="rounded-md border border-white/10 bg-white/[0.02]" key={`${caseIdentity}:${caseIndex}`}>
                <summary className="cursor-pointer list-none px-3 py-3 marker:content-none">
                  <div className="flex min-w-0 items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-xs font-semibold text-slate-200">{caseLabel(item, caseIndex)}</p>
                      <p className="mt-1 truncate text-[11px] text-slate-500">{item.message}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 text-[10px]">
                      <span className={attachment ? "text-emerald-200" : "text-slate-500"}>{attachment ? "已关联附件" : "无附件"}</span>
                      <span className={vision.length ? "text-cyan-200" : "text-slate-500"}>{vision.length} 条断言</span>
                    </div>
                  </div>
                </summary>

                <div className="border-t border-white/10 px-3 py-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0 text-[11px] leading-5 text-slate-400">
                      {attachment ? (
                        <>
                          <p className="truncate font-semibold text-slate-200">{displayName || "已上传附件"}</p>
                          <p>
                            {format ? format.toUpperCase() : "格式待保存确认"} · {formatBytes(byteSize)}
                            {attachment.page_count ? ` · ${attachment.page_count} 页` : ""}
                          </p>
                          {attachment.sha256 ? <p className="break-all font-mono text-[10px] text-slate-500">SHA-256 {attachment.sha256}</p> : null}
                        </>
                      ) : (
                        <p>尚未上传附件。CSV/JSON 不会抓取 URL 或物理路径，会话导入也只保留文本。</p>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {attachment ? (
                        <a
                          aria-label={`下载 ${caseLabel(item, caseIndex)} 的附件原件`}
                          className="grid h-8 w-8 place-items-center rounded-md border border-white/10 text-slate-400 hover:border-cyan-300/30 hover:text-slate-200"
                          href={`/api/files/${encodeURIComponent(attachment.asset_id)}/download?${new URLSearchParams({ purpose: "evaluation", scope_id: `evaluation:${datasetId}` })}`}
                          title="下载附件原件"
                        >
                          <Download className="h-3.5 w-3.5" />
                        </a>
                      ) : null}
                      <input
                        accept={extensions.join(",")}
                        className="sr-only"
                        disabled={editorDisabled || !uploadAvailable}
                        id={inputId}
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          if (file) void uploadAttachment(caseIndex, file);
                          event.target.value = "";
                        }}
                        type="file"
                      />
                      <label
                        aria-disabled={editorDisabled || !uploadAvailable}
                        className={`inline-flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-[11px] font-semibold text-slate-200 ${editorDisabled || !uploadAvailable ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:border-cyan-300/30"}`}
                        htmlFor={inputId}
                      >
                        {uploadingCase === caseIdentity ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <FileUp className="h-3.5 w-3.5" />}
                        {attachment ? "替换附件" : "上传附件"}
                      </label>
                      {attachment ? (
                        <button
                          aria-label={`解除 ${caseLabel(item, caseIndex)} 的附件引用`}
                          className="grid h-8 w-8 place-items-center rounded-md border border-white/10 text-slate-400 hover:border-rose-300/25 hover:text-rose-200 disabled:opacity-50"
                          disabled={editorDisabled}
                          onClick={() => {
                            const next = { ...item };
                            delete next.attachment;
                            replaceCase(caseIndex, next);
                            onNotice(`已解除“${caseLabel(item, caseIndex)}”的草稿附件引用。保存草稿后生效，已发布版本不受影响。`);
                          }}
                          title="解除草稿引用"
                          type="button"
                        >
                          <Unlink className="h-3.5 w-3.5" />
                        </button>
                      ) : null}
                    </div>
                  </div>

                  {attachment && vision.length === 0 ? (
                    <p className="mt-3 flex items-center gap-2 text-[11px] leading-5 text-amber-200">
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                      尚未配置视觉断言，最终答案通过不能证明视觉节点已被验证。
                    </p>
                  ) : null}
                  {!attachment && vision.length > 0 ? (
                    <p className="mt-3 flex items-center gap-2 text-[11px] leading-5 text-rose-200">
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                      视觉断言需要先关联当前用例附件。
                    </p>
                  ) : null}

                  <div className="mt-4 flex items-center justify-between gap-3 border-t border-white/10 pt-3">
                    <p className="text-xs font-semibold text-slate-300">视觉节点断言</p>
                    <button
                      className="inline-flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[11px] font-semibold text-slate-200 disabled:opacity-50"
                      disabled={editorDisabled || vision.length >= 20}
                      onClick={() => replaceCase(caseIndex, {
                        ...item,
                        vision: [
                          ...vision,
                          {
                            node_ref: "",
                            status: "success",
                            required_blocks: [],
                            content_anchors: [],
                          },
                        ],
                      })}
                      type="button"
                    >
                      <Plus className="h-3.5 w-3.5" />添加断言
                    </button>
                  </div>

                  {vision.length ? (
                    <div className="mt-3 divide-y divide-white/10">
                      {vision.map((expectation, visionIndex) => {
                        const anchorId = `vision-anchors-${caseIndex}-${visionIndex}`;
                        return (
                          <div className="py-4 first:pt-0 last:pb-0" key={`${caseIdentity}:vision:${visionIndex}`}>
                            <div className="flex items-center justify-between gap-3">
                              <p className="text-[11px] font-semibold text-cyan-100">断言 {visionIndex + 1}</p>
                              <button
                                aria-label={`删除断言 ${visionIndex + 1}`}
                                className="grid h-7 w-7 place-items-center rounded-md text-slate-500 hover:bg-rose-300/10 hover:text-rose-200"
                                disabled={editorDisabled}
                                onClick={() => replaceCase(caseIndex, {
                                  ...item,
                                  vision: vision.filter((_, index) => index !== visionIndex),
                                })}
                                title="删除视觉断言"
                                type="button"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                            <div className="mt-3 grid gap-3 sm:grid-cols-2">
                              <label className="text-[11px] font-semibold text-slate-400">
                                视觉节点 ref
                                <input
                                  aria-invalid={!nodeRefPattern.test(expectation.node_ref)}
                                  className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 font-mono text-xs text-white outline-none focus:border-cyan-300/40"
                                  disabled={editorDisabled}
                                  maxLength={64}
                                  onChange={(event) => updateVision(caseIndex, visionIndex, (current) => ({ ...current, node_ref: event.target.value }))}
                                  placeholder="vision_reader"
                                  value={expectation.node_ref}
                                />
                                {!nodeRefPattern.test(expectation.node_ref) ? <span className="mt-1 block font-normal text-amber-200">小写字母开头，仅可用小写字母、数字、下划线或连字符。</span> : null}
                              </label>
                              <label className="text-[11px] font-semibold text-slate-400">
                                预期状态
                                <select
                                  className="modelmirror-form-control mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 text-xs text-white"
                                  disabled={editorDisabled}
                                  onChange={(event) => updateVision(caseIndex, visionIndex, (current) => ({ ...current, status: event.target.value as "success" | "partial" }))}
                                  value={expectation.status}
                                >
                                  <option value="success">成功</option>
                                  <option value="partial">部分成功</option>
                                </select>
                              </label>
                              <label className="text-[11px] font-semibold text-slate-400">
                                预期模型 ID（可选）
                                <input
                                  className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 text-xs text-white outline-none focus:border-cyan-300/40"
                                  disabled={editorDisabled}
                                  maxLength={512}
                                  onChange={(event) => updateVision(caseIndex, visionIndex, (current) => {
                                    const next = { ...current };
                                    if (event.target.value) next.model_id = event.target.value;
                                    else delete next.model_id;
                                    return next;
                                  })}
                                  placeholder="不限制"
                                  value={expectation.model_id ?? ""}
                                />
                              </label>
                              <label className="text-[11px] font-semibold text-slate-400">
                                预期页数（可选）
                                <input
                                  className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 text-xs text-white outline-none focus:border-cyan-300/40"
                                  disabled={editorDisabled}
                                  max={20}
                                  min={1}
                                  onChange={(event) => updateVision(caseIndex, visionIndex, (current) => {
                                    const next = { ...current };
                                    if (event.target.value) next.page_count = Number(event.target.value);
                                    else delete next.page_count;
                                    return next;
                                  })}
                                  placeholder="不限制"
                                  type="number"
                                  value={expectation.page_count ?? ""}
                                />
                              </label>
                              <label className="text-[11px] font-semibold text-slate-400 sm:col-span-2">
                                附件 SHA-256（可选）
                                <div className="mt-1 flex gap-2">
                                  <input
                                    aria-invalid={Boolean(expectation.asset_sha256 && !sha256Pattern.test(expectation.asset_sha256))}
                                    className="h-9 min-w-0 flex-1 rounded-md border border-white/10 bg-ink-950 px-2.5 font-mono text-[11px] text-white outline-none focus:border-cyan-300/40"
                                    disabled={editorDisabled}
                                    maxLength={64}
                                    onChange={(event) => updateVision(caseIndex, visionIndex, (current) => {
                                      const next = { ...current };
                                      if (event.target.value) next.asset_sha256 = event.target.value;
                                      else delete next.asset_sha256;
                                      return next;
                                    })}
                                    placeholder="不限制"
                                    value={expectation.asset_sha256 ?? ""}
                                  />
                                  {attachment?.sha256 ? (
                                    <button
                                      className="shrink-0 rounded-md border border-white/10 px-2.5 text-[11px] text-slate-300 disabled:opacity-50"
                                      disabled={editorDisabled}
                                      onClick={() => updateVision(caseIndex, visionIndex, (current) => ({ ...current, asset_sha256: attachment.sha256! }))}
                                      type="button"
                                    >
                                      使用附件 hash
                                    </button>
                                  ) : null}
                                </div>
                                {expectation.asset_sha256 && !sha256Pattern.test(expectation.asset_sha256) ? <span className="mt-1 block font-normal text-amber-200">必须是 64 位小写十六进制摘要。</span> : null}
                              </label>
                              <fieldset className="sm:col-span-2">
                                <legend className="text-[11px] font-semibold text-slate-400">必需证据块（可选）</legend>
                                <div className="mt-2 flex flex-wrap gap-2">
                                  {blockOptions.map((option) => (
                                    <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-slate-300" key={option.value}>
                                      <input
                                        checked={expectation.required_blocks.includes(option.value)}
                                        className="h-3.5 w-3.5 accent-cyan-300"
                                        disabled={editorDisabled}
                                        onChange={(event) => updateVision(caseIndex, visionIndex, (current) => ({
                                          ...current,
                                          required_blocks: event.target.checked
                                            ? [...current.required_blocks, option.value]
                                            : current.required_blocks.filter((item) => item !== option.value),
                                        }))}
                                        type="checkbox"
                                      />
                                      {option.label}
                                    </label>
                                  ))}
                                </div>
                              </fieldset>
                              <AnchorEditor
                                anchors={expectation.content_anchors}
                                disabled={editorDisabled}
                                label={anchorId}
                                onApply={(anchors) => updateVision(caseIndex, visionIndex, (current) => ({ ...current, content_anchors: anchors }))}
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="mt-3 text-[11px] leading-5 text-slate-500">只有点击“添加断言”后才会写入 EvaluationVisionExpectation。</p>
                  )}
                </div>
              </details>
            );
          })}
        </div>
      )}
    </section>
  );
}
