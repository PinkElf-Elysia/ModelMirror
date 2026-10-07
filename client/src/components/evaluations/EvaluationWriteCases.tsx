import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Database, Plus, ShieldCheck, Trash2 } from "lucide-react";

export type EvaluationWriteSource = "manual" | "synthetic";
export type EvaluationWriteOperation = "insert" | "update" | "delete";
export type EvaluationWriteStatus =
  | "applied"
  | "noop"
  | "conflict"
  | "not_executed";

export interface EvaluationSeedRecord {
  ref: string;
  data: Record<string, unknown>;
}

export interface EvaluationTableInitialization {
  table_id: string;
  schema_version: number;
  source: EvaluationWriteSource;
  records: EvaluationSeedRecord[];
}

export interface EvaluationWriteEffectExpectation {
  node_ref: string;
  table_id: string;
  operation: EvaluationWriteOperation;
  schema_version?: number | null;
  contract_checksum?: string | null;
  status: EvaluationWriteStatus;
  affected_count: number;
  expected_before: Record<string, unknown>;
  expected_after: Record<string, unknown>;
  error_code?: string | null;
}

export interface EvaluationWriteCase {
  case_id?: string;
  name?: string;
  message: string;
  table_initializations?: EvaluationTableInitialization[];
  effects?: EvaluationWriteEffectExpectation[];
  [key: string]: unknown;
}

export interface EvaluationWriteEditorState {
  has_unapplied_changes: boolean;
  has_invalid_changes: boolean;
}

type EvaluationJsonDraftState = "clean" | "dirty" | "invalid";

interface EvaluationWriteCasesProps {
  cases: EvaluationWriteCase[] | null;
  disabled?: boolean;
  onChange: (cases: EvaluationWriteCase[]) => void;
  onEditorStateChange?: (state: EvaluationWriteEditorState) => void;
}

const SYSTEM_FIELDS = new Set([
  "record_id",
  "created_at",
  "updated_at",
  "revision",
]);
const SEED_FIELD_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const EFFECT_FIELD_PATTERN = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const RECORD_REF_PATTERN = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const NODE_REF_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const ERROR_CODE_PATTERN = /^[A-Z][A-Z0-9_]{0,63}$/;
const OPERATIONS: EvaluationWriteOperation[] = ["insert", "update", "delete"];
const STATUSES: EvaluationWriteStatus[] = [
  "applied",
  "noop",
  "conflict",
  "not_executed",
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertOnlyFields(
  value: Record<string, unknown>,
  fields: string[],
  label: string,
) {
  const unknown = Object.keys(value).filter((field) => !fields.includes(field));
  if (unknown.length) {
    throw new Error(`${label}包含不允许字段：${unknown.join("、")}。`);
  }
}

function assertJsonSafe(value: unknown, label: string): void {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error(`${label}包含非有限数值。`);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => assertJsonSafe(item, label));
    return;
  }
  if (isRecord(value)) {
    Object.values(value).forEach((item) => assertJsonSafe(item, label));
    return;
  }
  throw new Error(`${label}只能包含 JSON 安全值。`);
}

function stringifyJson(value: unknown, label: string) {
  try {
    const serialized = JSON.stringify(value);
    if (serialized !== undefined) return serialized;
  } catch {
    // Fall through to the bounded Chinese validation error.
  }
  throw new Error(`${label}只能包含 JSON 安全值。`);
}

function jsonBytes(value: unknown, label = "JSON") {
  return new TextEncoder().encode(stringifyJson(value, label)).byteLength;
}

function assertBusinessFields(
  value: Record<string, unknown>,
  pattern: RegExp,
  label: string,
) {
  const fields = Object.keys(value);
  if (fields.length > 50) throw new Error(`${label}最多包含 50 个业务字段。`);
  const invalid = fields.find(
    (field) => SYSTEM_FIELDS.has(field) || !pattern.test(field),
  );
  if (invalid) throw new Error(`${label}包含无效业务字段：${invalid}。`);
}

export function parseInitializationRecordsJson(value: string): EvaluationSeedRecord[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("初始化记录必须是合法 JSON 数组。");
  }
  if (!Array.isArray(parsed) || parsed.length > 200) {
    throw new Error("每张表的初始化记录必须是数组，且最多 200 行。");
  }
  const records = parsed.map((item, index) => {
    if (!isRecord(item)) throw new Error(`第 ${index + 1} 条初始化记录必须是对象。`);
    assertOnlyFields(item, ["ref", "data"], `第 ${index + 1} 条初始化记录`);
    if (typeof item.ref !== "string" || !RECORD_REF_PATTERN.test(item.ref)) {
      throw new Error(`第 ${index + 1} 条初始化记录 ref 无效。`);
    }
    if (!isRecord(item.data)) {
      throw new Error(`第 ${index + 1} 条初始化记录 data 必须是对象。`);
    }
    assertBusinessFields(item.data, SEED_FIELD_PATTERN, `第 ${index + 1} 条初始化记录`);
    assertJsonSafe(item.data, `第 ${index + 1} 条初始化记录`);
    if (jsonBytes(item.data, `第 ${index + 1} 条初始化记录`) > 256 * 1024) {
      throw new Error(`第 ${index + 1} 条初始化记录不能超过 256 KiB。`);
    }
    return { ref: item.ref, data: item.data };
  });
  const refs = records.map((item) => item.ref);
  if (refs.length !== new Set(refs).size) {
    throw new Error("同一张表的初始化记录 ref 不能重复。");
  }
  return records;
}

export function parseExpectedWriteSubsetJson(value: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("效果字段子集必须是合法 JSON 对象。");
  }
  if (!isRecord(parsed)) throw new Error("效果字段子集必须是 JSON 对象。");
  assertBusinessFields(parsed, EFFECT_FIELD_PATTERN, "效果字段子集");
  assertJsonSafe(parsed, "效果字段子集");
  return parsed;
}

function normalizeInitialization(
  value: unknown,
  index: number,
): EvaluationTableInitialization {
  if (!isRecord(value)) throw new Error(`第 ${index + 1} 个表初始化必须是对象。`);
  assertOnlyFields(
    value,
    ["table_id", "schema_version", "source", "records"],
    `第 ${index + 1} 个表初始化`,
  );
  if (
    typeof value.table_id !== "string"
    || !value.table_id.trim()
    || value.table_id.length > 200
  ) {
    throw new Error(`第 ${index + 1} 个表初始化缺少有效 table_id。`);
  }
  if (!Number.isInteger(value.schema_version) || Number(value.schema_version) < 1) {
    throw new Error(`${value.table_id} 的 schema_version 必须是正整数。`);
  }
  const source = value.source ?? "manual";
  if (source !== "manual" && source !== "synthetic") {
    throw new Error(`${value.table_id} 的 source 只能是 manual 或 synthetic。`);
  }
  const records = parseInitializationRecordsJson(
    stringifyJson(value.records ?? [], `${value.table_id} 的初始化记录`),
  );
  return {
    table_id: value.table_id,
    schema_version: Number(value.schema_version),
    source,
    records,
  };
}

function normalizeEffect(
  value: unknown,
  index: number,
): EvaluationWriteEffectExpectation {
  if (!isRecord(value)) throw new Error(`第 ${index + 1} 个写效果断言必须是对象。`);
  assertOnlyFields(
    value,
    [
      "node_ref",
      "table_id",
      "operation",
      "schema_version",
      "contract_checksum",
      "status",
      "affected_count",
      "expected_before",
      "expected_after",
      "error_code",
    ],
    `第 ${index + 1} 个写效果断言`,
  );
  if (typeof value.node_ref !== "string" || !NODE_REF_PATTERN.test(value.node_ref)) {
    throw new Error(`第 ${index + 1} 个写效果断言 node_ref 无效。`);
  }
  if (
    typeof value.table_id !== "string"
    || !value.table_id.trim()
    || value.table_id.length > 200
  ) {
    throw new Error(`${value.node_ref} 缺少有效 table_id。`);
  }
  if (!OPERATIONS.includes(value.operation as EvaluationWriteOperation)) {
    throw new Error(`${value.node_ref} 的 operation 无效。`);
  }
  if (
    value.schema_version != null
    && (!Number.isInteger(value.schema_version) || Number(value.schema_version) < 1)
  ) {
    throw new Error(`${value.node_ref} 的 schema_version 必须是正整数。`);
  }
  if (
    value.contract_checksum != null
    && (typeof value.contract_checksum !== "string"
      || !SHA256_PATTERN.test(value.contract_checksum))
  ) {
    throw new Error(`${value.node_ref} 的 contract_checksum 必须是 64 位小写十六进制。`);
  }
  const status = value.status ?? "applied";
  if (!STATUSES.includes(status as EvaluationWriteStatus)) {
    throw new Error(`${value.node_ref} 的 status 无效。`);
  }
  const affectedCount = value.affected_count ?? 1;
  if (
    !Number.isInteger(affectedCount)
    || Number(affectedCount) < 0
    || Number(affectedCount) > 100
  ) {
    throw new Error(`${value.node_ref} 的 affected_count 必须是 0 至 100 的整数。`);
  }
  if (status === "applied" && affectedCount === 0) {
    throw new Error(`${value.node_ref} 的 applied 效果必须影响至少一行。`);
  }
  if (status !== "applied" && affectedCount !== 0) {
    throw new Error(`${value.node_ref} 的 ${status} 效果必须将 affected_count 设为 0。`);
  }
  const expectedBefore = parseExpectedWriteSubsetJson(
    stringifyJson(value.expected_before ?? {}, `${value.node_ref} 的 expected_before`),
  );
  const expectedAfter = parseExpectedWriteSubsetJson(
    stringifyJson(value.expected_after ?? {}, `${value.node_ref} 的 expected_after`),
  );
  if (jsonBytes(
    { expected_before: expectedBefore, expected_after: expectedAfter },
    `${value.node_ref} 的效果字段子集`,
  ) > 256 * 1024) {
    throw new Error(`${value.node_ref} 的效果字段子集总量不能超过 256 KiB。`);
  }
  if (value.operation === "insert" && Object.keys(expectedBefore).length) {
    throw new Error(`${value.node_ref} 的 insert 效果不能配置 expected_before。`);
  }
  if (value.operation === "delete" && Object.keys(expectedAfter).length) {
    throw new Error(`${value.node_ref} 的 delete 效果不能配置 expected_after。`);
  }
  if (
    value.error_code != null
    && (typeof value.error_code !== "string" || !ERROR_CODE_PATTERN.test(value.error_code))
  ) {
    throw new Error(`${value.node_ref} 的 error_code 必须是安全大写错误码。`);
  }
  if (status === "conflict" && value.error_code == null) {
    throw new Error(`${value.node_ref} 的 conflict 效果必须配置 error_code。`);
  }
  return {
    node_ref: value.node_ref,
    table_id: value.table_id,
    operation: value.operation as EvaluationWriteOperation,
    ...(value.schema_version == null
      ? {}
      : { schema_version: Number(value.schema_version) }),
    ...(value.contract_checksum == null
      ? {}
      : { contract_checksum: value.contract_checksum as string }),
    status: status as EvaluationWriteStatus,
    affected_count: Number(affectedCount),
    expected_before: expectedBefore,
    expected_after: expectedAfter,
    ...(value.error_code == null ? {} : { error_code: value.error_code as string }),
  };
}

export function normalizeEvaluationWriteCaseForSave<T extends EvaluationWriteCase>(
  item: T,
): T {
  const initializations = item.table_initializations;
  const effects = item.effects;
  if (initializations !== undefined && !Array.isArray(initializations)) {
    throw new Error("table_initializations 必须是数组。");
  }
  if (effects !== undefined && !Array.isArray(effects)) {
    throw new Error("effects 必须是数组。");
  }
  if ((initializations?.length ?? 0) > 20) {
    throw new Error("每条用例最多初始化 20 张表。");
  }
  if ((effects?.length ?? 0) > 64) {
    throw new Error("每条用例最多配置 64 个写效果断言。");
  }
  const normalizedInitializations = initializations?.map(normalizeInitialization);
  const tableIds = normalizedInitializations?.map((entry) => entry.table_id) ?? [];
  if (tableIds.length !== new Set(tableIds).size) {
    throw new Error("同一用例不能重复初始化同一张表。");
  }
  if (
    normalizedInitializations
    && jsonBytes(normalizedInitializations, "表初始化 JSON") > 16 * 1024 * 1024
  ) {
    throw new Error("单条用例的表初始化 JSON 不能超过 16 MiB。");
  }
  const normalizedEffects = effects?.map(normalizeEffect);
  const nodeRefs = normalizedEffects?.map((entry) => entry.node_ref) ?? [];
  if (nodeRefs.length !== new Set(nodeRefs).size) {
    throw new Error("同一用例不能重复配置同一写节点效果。");
  }
  return {
    ...item,
    ...(normalizedInitializations === undefined
      ? {}
      : { table_initializations: normalizedInitializations }),
    ...(normalizedEffects === undefined ? {} : { effects: normalizedEffects }),
  };
}

function caseLabel(item: EvaluationWriteCase, index: number) {
  return item.name?.trim() || item.case_id?.trim() || `用例 ${index + 1}`;
}

function caseWriteError(item: EvaluationWriteCase) {
  try {
    normalizeEvaluationWriteCaseForSave(item);
    return "";
  } catch (caught) {
    return caught instanceof Error ? caught.message : "写入评测配置无效。";
  }
}

function JsonEditor({
  ariaLabel,
  disabled,
  editorKey,
  label,
  onApply,
  onDraftStateChange,
  value,
}: {
  ariaLabel: string;
  disabled: boolean;
  editorKey: string;
  label: string;
  onApply: (value: Record<string, unknown>) => void;
  onDraftStateChange: (key: string, state: EvaluationJsonDraftState) => void;
  value: Record<string, unknown>;
}) {
  const serialized = JSON.stringify(value, null, 2);
  const [text, setText] = useState(serialized);
  const [error, setError] = useState("");

  useEffect(() => {
    setText(serialized);
    setError("");
    onDraftStateChange(editorKey, "clean");
  }, [editorKey, onDraftStateChange, serialized]);

  useEffect(() => (
    () => onDraftStateChange(editorKey, "clean")
  ), [editorKey, onDraftStateChange]);

  return (
    <div>
      <label className="text-[11px] font-semibold text-slate-400">
        {label}
        <textarea
          aria-label={ariaLabel}
          className="mt-1 min-h-24 w-full resize-y rounded-md border border-white/10 bg-ink-950 px-3 py-2 font-mono text-[11px] leading-5 text-slate-200 outline-none focus:border-cyan-300/40 disabled:opacity-50"
          disabled={disabled}
          onChange={(event) => {
            setText(event.target.value);
            setError("");
            onDraftStateChange(editorKey, "dirty");
          }}
          spellCheck={false}
          value={text}
        />
      </label>
      <div className="mt-2 flex items-start justify-between gap-3">
        {error ? <p className="text-[11px] leading-5 text-rose-200" role="alert">{error}</p> : <span />}
        <button
          className="shrink-0 rounded-md border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[11px] font-semibold text-slate-200 disabled:opacity-50"
          disabled={disabled}
          onClick={() => {
            try {
              onApply(parseExpectedWriteSubsetJson(text));
              setError("");
              onDraftStateChange(editorKey, "clean");
            } catch (caught) {
              setError(caught instanceof Error ? caught.message : "效果字段子集无效。");
              onDraftStateChange(editorKey, "invalid");
            }
          }}
          type="button"
        >
          应用字段子集
        </button>
      </div>
    </div>
  );
}

function RecordsEditor({
  disabled,
  editorKey,
  label,
  onApply,
  onDraftStateChange,
  records,
}: {
  disabled: boolean;
  editorKey: string;
  label: string;
  onApply: (records: EvaluationSeedRecord[]) => void;
  onDraftStateChange: (key: string, state: EvaluationJsonDraftState) => void;
  records: EvaluationSeedRecord[];
}) {
  const serialized = JSON.stringify(records, null, 2);
  const [text, setText] = useState(serialized);
  const [error, setError] = useState("");

  useEffect(() => {
    setText(serialized);
    setError("");
    onDraftStateChange(editorKey, "clean");
  }, [editorKey, onDraftStateChange, serialized]);

  useEffect(() => (
    () => onDraftStateChange(editorKey, "clean")
  ), [editorKey, onDraftStateChange]);

  return (
    <div className="mt-3">
      <div className="flex items-center justify-between gap-3">
        <label className="text-[11px] font-semibold text-slate-400">
          初始化记录 JSON
        </label>
        <span className="text-[10px] text-slate-400">{records.length}/200 行</span>
      </div>
      <textarea
        aria-label={`${label} 初始化记录 JSON`}
        className="mt-1 min-h-32 w-full resize-y rounded-md border border-white/10 bg-ink-950 px-3 py-2 font-mono text-[11px] leading-5 text-slate-200 outline-none focus:border-cyan-300/40 disabled:opacity-50"
        disabled={disabled}
        onChange={(event) => {
          setText(event.target.value);
          setError("");
          onDraftStateChange(editorKey, "dirty");
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
              onApply(parseInitializationRecordsJson(text));
              setError("");
              onDraftStateChange(editorKey, "clean");
            } catch (caught) {
              setError(caught instanceof Error ? caught.message : "初始化记录无效。");
              onDraftStateChange(editorKey, "invalid");
            }
          }}
          type="button"
        >
          应用记录
        </button>
      </div>
    </div>
  );
}

export default function EvaluationWriteCases({
  cases,
  disabled = false,
  onChange,
  onEditorStateChange,
}: EvaluationWriteCasesProps) {
  const [draftStates, setDraftStates] = useState<Record<string, EvaluationJsonDraftState>>({});
  const updateDraftState = useCallback((key: string, state: EvaluationJsonDraftState) => {
    setDraftStates((current) => {
      if (state === "clean") {
        if (!(key in current)) return current;
        const next = { ...current };
        delete next[key];
        return next;
      }
      if (current[key] === state) return current;
      return { ...current, [key]: state };
    });
  }, []);
  const hasUnappliedChanges = Object.keys(draftStates).length > 0;
  const hasInvalidChanges = Object.values(draftStates).includes("invalid");

  useEffect(() => {
    onEditorStateChange?.({
      has_unapplied_changes: hasUnappliedChanges,
      has_invalid_changes: hasInvalidChanges,
    });
  }, [hasInvalidChanges, hasUnappliedChanges, onEditorStateChange]);

  useEffect(() => (
    () => onEditorStateChange?.({
      has_unapplied_changes: false,
      has_invalid_changes: false,
    })
  ), [onEditorStateChange]);

  function replaceCase(index: number, next: EvaluationWriteCase) {
    if (!cases) return;
    onChange(cases.map((item, itemIndex) => itemIndex === index ? next : item));
  }

  return (
    <section
      aria-labelledby="evaluation-write-cases-title"
      className="rounded-md border border-white/10 bg-white/[0.025] p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-amber-200" />
            <h3 className="text-sm font-semibold text-white" id="evaluation-write-cases-title">
              逐例表初始化与写入效果
            </h3>
          </div>
          <p className="mt-1 max-w-3xl text-[11px] leading-5 text-slate-400">
            初始化仅接受手工 JSON 或合成输入，不读取、导入或回填活表业务记录。效果字段正文只保存在当前可信 Dataset 编辑区。
          </p>
        </div>
        <div className="text-right text-[11px] text-slate-400">
          <span>每例最多 20 表、64 个效果</span>
          {hasUnappliedChanges ? (
            <p className={hasInvalidChanges ? "mt-1 text-rose-200" : "mt-1 text-amber-100"} role="status">
              {hasInvalidChanges ? "存在无效 JSON，请修正并应用。" : "存在尚未应用的 JSON 修改。"}
            </p>
          ) : null}
        </div>
      </div>

      {cases === null ? (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-300/20 bg-amber-300/10 p-3 text-xs leading-5 text-amber-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          完整用例 JSON 当前无法解析。修正后才能使用逐例写入编辑器。
        </div>
      ) : cases.length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-white/10 p-4 text-xs text-slate-400">
          先添加评测用例，再配置隔离表初始化与写入效果。
        </p>
      ) : (
        <div className="mt-4 space-y-2">
          {cases.map((item, caseIndex) => {
            const initializations = Array.isArray(item.table_initializations)
              ? item.table_initializations
              : [];
            const effects = Array.isArray(item.effects) ? item.effects : [];
            const writeError = caseWriteError(item);
            return (
              <details
                className="rounded-md border border-white/10 bg-white/[0.02]"
                key={`${item.case_id ?? item.name ?? item.message}:${caseIndex}`}
              >
                <summary className="cursor-pointer list-none px-3 py-3 marker:content-none">
                  <div className="flex min-w-0 items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-xs font-semibold text-slate-200">
                        {caseLabel(item, caseIndex)}
                      </p>
                      <p className="mt-1 truncate text-[11px] text-slate-400">{item.message}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 text-[10px]">
                      <span className={initializations.length ? "text-amber-100" : "text-slate-400"}>
                        {initializations.length} 张表
                      </span>
                      <span className={effects.length ? "text-cyan-100" : "text-slate-400"}>
                        {effects.length} 个效果
                      </span>
                    </div>
                  </div>
                </summary>

                <div className="border-t border-white/10 px-3 py-4">
                  {writeError ? (
                    <p className="mb-4 flex items-start gap-2 text-[11px] leading-5 text-rose-200" role="alert">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      {writeError}
                    </p>
                  ) : null}

                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-xs font-semibold text-slate-300">隔离表初始化</p>
                      <p className="mt-1 text-[10px] text-slate-400">每张表最多 200 行，局部 ref 不会成为真实 record_id。</p>
                    </div>
                    <button
                      className="inline-flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[11px] font-semibold text-slate-200 disabled:opacity-50"
                      disabled={disabled || initializations.length >= 20}
                      onClick={() => replaceCase(caseIndex, {
                        ...item,
                        table_initializations: [
                          ...initializations,
                          { table_id: "", schema_version: 1, source: "manual", records: [] },
                        ],
                      })}
                      type="button"
                    >
                      <Plus className="h-3.5 w-3.5" />添加初始化表
                    </button>
                  </div>

                  {initializations.length ? (
                    <div className="mt-3 divide-y divide-white/10">
                      {initializations.map((initialization, initializationIndex) => {
                        const label = initialization.table_id || `初始化表 ${initializationIndex + 1}`;
                        return (
                          <div className="py-4 first:pt-0" key={`table:${initializationIndex}`}>
                            <div className="flex items-center justify-between gap-3">
                              <div className="flex min-w-0 items-center gap-2">
                                <Database className="h-3.5 w-3.5 shrink-0 text-amber-200" />
                                <span className="truncate font-mono text-[11px] text-amber-100">{label}</span>
                              </div>
                              <button
                                aria-label={`删除初始化表 ${initializationIndex + 1}`}
                                className="grid h-7 w-7 place-items-center rounded-md text-slate-400 hover:bg-rose-300/10 hover:text-rose-200 disabled:opacity-50"
                                disabled={disabled}
                                onClick={() => replaceCase(caseIndex, {
                                  ...item,
                                  table_initializations: initializations.filter((_, index) => index !== initializationIndex),
                                })}
                                title="删除初始化表"
                                type="button"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                            <div className="mt-3 grid gap-3 sm:grid-cols-3">
                              <label className="text-[11px] font-semibold text-slate-400">
                                数据表 ID
                                <input
                                  className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 font-mono text-xs text-white outline-none focus:border-cyan-300/40"
                                  disabled={disabled}
                                  maxLength={200}
                                  onChange={(event) => {
                                    const next = [...initializations];
                                    next[initializationIndex] = { ...initialization, table_id: event.target.value };
                                    replaceCase(caseIndex, { ...item, table_initializations: next });
                                  }}
                                  value={initialization.table_id}
                                />
                              </label>
                              <label className="text-[11px] font-semibold text-slate-400">
                                Schema 版本
                                <input
                                  className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 text-xs text-white outline-none focus:border-cyan-300/40"
                                  disabled={disabled}
                                  min={1}
                                  onChange={(event) => {
                                    const next = [...initializations];
                                    next[initializationIndex] = { ...initialization, schema_version: Number(event.target.value) };
                                    replaceCase(caseIndex, { ...item, table_initializations: next });
                                  }}
                                  type="number"
                                  value={initialization.schema_version}
                                />
                              </label>
                              <label className="text-[11px] font-semibold text-slate-400">
                                输入来源
                                <select
                                  className="modelmirror-form-control mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 text-xs text-white"
                                  disabled={disabled}
                                  onChange={(event) => {
                                    const next = [...initializations];
                                    next[initializationIndex] = {
                                      ...initialization,
                                      source: event.target.value as EvaluationWriteSource,
                                    };
                                    replaceCase(caseIndex, { ...item, table_initializations: next });
                                  }}
                                  value={initialization.source}
                                >
                                  <option value="manual">手工 JSON</option>
                                  <option value="synthetic">合成输入</option>
                                </select>
                              </label>
                            </div>
                            <RecordsEditor
                              disabled={disabled}
                              editorKey={`case:${caseIndex}:table:${initializationIndex}:records`}
                              label={label}
                              onApply={(records) => {
                                const next = [...initializations];
                                next[initializationIndex] = { ...initialization, records };
                                replaceCase(caseIndex, { ...item, table_initializations: next });
                              }}
                              onDraftStateChange={updateDraftState}
                              records={initialization.records}
                            />
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="mt-3 text-[11px] leading-5 text-slate-400">尚未配置初始化表。</p>
                  )}

                  <div className="mt-5 flex items-center justify-between gap-3 border-t border-white/10 pt-4">
                    <div>
                      <p className="text-xs font-semibold text-slate-300">写节点效果断言</p>
                      <p className="mt-1 text-[10px] text-slate-400">按 Planner node_ref 断言真实 Backend 效果，不以最终回答文本代替。</p>
                    </div>
                    <button
                      className="inline-flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[11px] font-semibold text-slate-200 disabled:opacity-50"
                      disabled={disabled || effects.length >= 64}
                      onClick={() => replaceCase(caseIndex, {
                        ...item,
                        effects: [
                          ...effects,
                          {
                            node_ref: "",
                            table_id: "",
                            operation: "insert",
                            status: "applied",
                            affected_count: 1,
                            expected_before: {},
                            expected_after: {},
                          },
                        ],
                      })}
                      type="button"
                    >
                      <Plus className="h-3.5 w-3.5" />添加效果断言
                    </button>
                  </div>

                  {effects.length ? (
                    <div className="mt-3 divide-y divide-white/10">
                      {effects.map((effect, effectIndex) => (
                        <div className="py-4 first:pt-0" key={`effect:${effectIndex}`}>
                          <div className="flex items-center justify-between gap-3">
                            <span className="font-mono text-[11px] font-semibold text-cyan-100">
                              {effect.node_ref || `效果 ${effectIndex + 1}`}
                            </span>
                            <button
                              aria-label={`删除效果断言 ${effectIndex + 1}`}
                              className="grid h-7 w-7 place-items-center rounded-md text-slate-400 hover:bg-rose-300/10 hover:text-rose-200 disabled:opacity-50"
                              disabled={disabled}
                              onClick={() => replaceCase(caseIndex, {
                                ...item,
                                effects: effects.filter((_, index) => index !== effectIndex),
                              })}
                              title="删除效果断言"
                              type="button"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                            <label className="text-[11px] font-semibold text-slate-400">
                              写节点 ref
                              <input
                                aria-invalid={!NODE_REF_PATTERN.test(effect.node_ref)}
                                className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 font-mono text-xs text-white outline-none focus:border-cyan-300/40"
                                disabled={disabled}
                                maxLength={64}
                                onChange={(event) => {
                                  const next = [...effects];
                                  next[effectIndex] = { ...effect, node_ref: event.target.value };
                                  replaceCase(caseIndex, { ...item, effects: next });
                                }}
                                placeholder="write_order"
                                value={effect.node_ref}
                              />
                            </label>
                            <label className="text-[11px] font-semibold text-slate-400">
                              数据表 ID
                              <input
                                className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 font-mono text-xs text-white outline-none focus:border-cyan-300/40"
                                disabled={disabled}
                                maxLength={200}
                                onChange={(event) => {
                                  const next = [...effects];
                                  next[effectIndex] = { ...effect, table_id: event.target.value };
                                  replaceCase(caseIndex, { ...item, effects: next });
                                }}
                                value={effect.table_id}
                              />
                            </label>
                            <label className="text-[11px] font-semibold text-slate-400">
                              操作
                              <select
                                className="modelmirror-form-control mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 text-xs text-white"
                                disabled={disabled}
                                onChange={(event) => {
                                  const operation = event.target.value as EvaluationWriteOperation;
                                  const next = [...effects];
                                  next[effectIndex] = {
                                    ...effect,
                                    operation,
                                    expected_before: operation === "insert" ? {} : effect.expected_before,
                                    expected_after: operation === "delete" ? {} : effect.expected_after,
                                  };
                                  replaceCase(caseIndex, { ...item, effects: next });
                                }}
                                value={effect.operation}
                              >
                                <option value="insert">新增 insert</option>
                                <option value="update">更新 update</option>
                                <option value="delete">删除 delete</option>
                              </select>
                            </label>
                            <label className="text-[11px] font-semibold text-slate-400">
                              预期状态
                              <select
                                className="modelmirror-form-control mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 text-xs text-white"
                                disabled={disabled}
                                onChange={(event) => {
                                  const status = event.target.value as EvaluationWriteStatus;
                                  const next = [...effects];
                                  next[effectIndex] = {
                                    ...effect,
                                    status,
                                    affected_count: status === "applied"
                                      ? Math.max(1, effect.affected_count)
                                      : 0,
                                  };
                                  replaceCase(caseIndex, { ...item, effects: next });
                                }}
                                value={effect.status}
                              >
                                <option value="applied">已应用 applied</option>
                                <option value="noop">无变化 noop</option>
                                <option value="conflict">冲突 conflict</option>
                                <option value="not_executed">未执行 not_executed</option>
                              </select>
                            </label>
                            <label className="text-[11px] font-semibold text-slate-400">
                              影响行数
                              <input
                                className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 text-xs text-white outline-none focus:border-cyan-300/40"
                                disabled={disabled || effect.status !== "applied"}
                                max={100}
                                min={effect.status === "applied" ? 1 : 0}
                                onChange={(event) => {
                                  const next = [...effects];
                                  next[effectIndex] = { ...effect, affected_count: Number(event.target.value) };
                                  replaceCase(caseIndex, { ...item, effects: next });
                                }}
                                type="number"
                                value={effect.affected_count}
                              />
                            </label>
                            <label className="text-[11px] font-semibold text-slate-400">
                              Schema 版本（可选）
                              <input
                                className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 text-xs text-white outline-none focus:border-cyan-300/40"
                                disabled={disabled}
                                min={1}
                                onChange={(event) => {
                                  const nextEffect = { ...effect };
                                  if (event.target.value) nextEffect.schema_version = Number(event.target.value);
                                  else delete nextEffect.schema_version;
                                  const next = [...effects];
                                  next[effectIndex] = nextEffect;
                                  replaceCase(caseIndex, { ...item, effects: next });
                                }}
                                placeholder="不限制"
                                type="number"
                                value={effect.schema_version ?? ""}
                              />
                            </label>
                            <label className="text-[11px] font-semibold text-slate-400 lg:col-span-2">
                              契约校验和（可选）
                              <input
                                aria-invalid={Boolean(effect.contract_checksum && !SHA256_PATTERN.test(effect.contract_checksum))}
                                className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 font-mono text-[11px] text-white outline-none focus:border-cyan-300/40"
                                disabled={disabled}
                                maxLength={64}
                                onChange={(event) => {
                                  const nextEffect = { ...effect };
                                  if (event.target.value) nextEffect.contract_checksum = event.target.value;
                                  else delete nextEffect.contract_checksum;
                                  const next = [...effects];
                                  next[effectIndex] = nextEffect;
                                  replaceCase(caseIndex, { ...item, effects: next });
                                }}
                                placeholder="不限制"
                                value={effect.contract_checksum ?? ""}
                              />
                            </label>
                            <label className="text-[11px] font-semibold text-slate-400">
                              错误码（可选）
                              <input
                                aria-invalid={Boolean(effect.error_code && !ERROR_CODE_PATTERN.test(effect.error_code))}
                                className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 font-mono text-xs text-white outline-none focus:border-cyan-300/40"
                                disabled={disabled}
                                maxLength={64}
                                onChange={(event) => {
                                  const nextEffect = { ...effect };
                                  if (event.target.value) nextEffect.error_code = event.target.value;
                                  else delete nextEffect.error_code;
                                  const next = [...effects];
                                  next[effectIndex] = nextEffect;
                                  replaceCase(caseIndex, { ...item, effects: next });
                                }}
                                placeholder={effect.status === "conflict" ? "WRITE_CONFLICT" : "不限制"}
                                value={effect.error_code ?? ""}
                              />
                            </label>
                          </div>
                          <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            <JsonEditor
                              ariaLabel={`${effect.node_ref || `效果 ${effectIndex + 1}`} expected_before`}
                              disabled={disabled || effect.operation === "insert"}
                              editorKey={`case:${caseIndex}:effect:${effectIndex}:before`}
                              label="变更前字段子集"
                              onApply={(expectedBefore) => {
                                const next = [...effects];
                                next[effectIndex] = { ...effect, expected_before: expectedBefore };
                                replaceCase(caseIndex, { ...item, effects: next });
                              }}
                              onDraftStateChange={updateDraftState}
                              value={effect.expected_before}
                            />
                            <JsonEditor
                              ariaLabel={`${effect.node_ref || `效果 ${effectIndex + 1}`} expected_after`}
                              disabled={disabled || effect.operation === "delete"}
                              editorKey={`case:${caseIndex}:effect:${effectIndex}:after`}
                              label="变更后字段子集"
                              onApply={(expectedAfter) => {
                                const next = [...effects];
                                next[effectIndex] = { ...effect, expected_after: expectedAfter };
                                replaceCase(caseIndex, { ...item, effects: next });
                              }}
                              onDraftStateChange={updateDraftState}
                              value={effect.expected_after}
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-3 text-[11px] leading-5 text-slate-400">尚未配置写节点效果断言。</p>
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
