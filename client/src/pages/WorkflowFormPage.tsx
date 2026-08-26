import { useEffect, useId, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

type FormFieldType =
  | "short_text"
  | "long_text"
  | "email"
  | "number"
  | "boolean"
  | "date"
  | "single_select"
  | "multi_select";

interface FormOption {
  id: string;
  value: string;
  label: string;
}

interface FormField {
  id: string;
  label: string;
  helpText: string;
  placeholder: string;
  type: FormFieldType;
  required: boolean;
  options: FormOption[];
}

interface FormManifest {
  formTitle: string;
  formDescription: string;
  submitLabel: string;
  privacyNotice: string;
  successTitle: string;
  successMessage: string;
  theme: "light" | "dark";
  fields: FormField[];
  submissionToken: string;
  expiresInSeconds: number;
}

type FormValue = string | number | boolean | string[] | null;

class ManifestRequestError extends Error {
  readonly status: number;

  constructor(status: number) {
    super("manifest_request_failed");
    this.status = status;
  }
}

function accessStorageKey(formId: string) {
  return `modelmirror-workflow-form:${formId}:access`;
}

function readAccessKey(formId: string): string {
  try {
    return window.sessionStorage.getItem(accessStorageKey(formId)) || "";
  } catch {
    return "";
  }
}

function rememberAccessKey(formId: string, accessKey: string): void {
  try {
    window.sessionStorage.setItem(accessStorageKey(formId), accessKey);
  } catch {
    // The fragment key still remains in component memory for this page session.
  }
}

function forgetAccessKey(formId: string): void {
  try {
    window.sessionStorage.removeItem(accessStorageKey(formId));
  } catch {
    // Storage can be unavailable in hardened browser modes.
  }
}

async function fetchManifest(formId: string, accessKey: string): Promise<FormManifest> {
  const response = await fetch(`/api/workflow-forms/${encodeURIComponent(formId)}/manifest`, {
    cache: "no-store",
    credentials: "omit",
    headers: { "X-ModelMirror-Form-Key": accessKey },
  });
  if (!response.ok) throw new ManifestRequestError(response.status);
  return response.json() as Promise<FormManifest>;
}

function fieldSchemaSignature(fields: FormField[]): string {
  return JSON.stringify(fields);
}

function emptyValues(fields: FormField[]): Record<string, FormValue> {
  return Object.fromEntries(
    fields.map((field) => [
      field.id,
      field.type === "boolean"
        ? false
        : field.type === "multi_select"
          ? []
          : field.type === "number"
            ? null
            : "",
    ]),
  );
}

function fieldError(field: FormField, value: FormValue): string {
  if (field.type === "boolean" && field.required && value !== true) {
    return "请勾选此项后再提交。";
  }
  if (field.type === "multi_select") {
    if (field.required && (!Array.isArray(value) || value.length === 0)) {
      return "请至少选择一项。";
    }
    return "";
  }
  if (field.required && (value === null || String(value).trim() === "")) {
    return "请填写此项。";
  }
  if (field.type === "email" && value && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value))) {
    return "请输入有效的邮箱地址。";
  }
  return "";
}

function LoadingState({ dark }: { dark: boolean }) {
  return (
    <main className={`flex min-h-screen items-center justify-center px-5 py-12 ${dark ? "bg-slate-950 text-slate-100" : "bg-slate-50 text-slate-950"}`}>
      <div aria-label="正在载入表单" className="w-full max-w-2xl animate-pulse" role="status">
        <div className={`h-8 w-2/3 rounded ${dark ? "bg-white/10" : "bg-slate-200"}`} />
        <div className={`mt-4 h-4 w-full rounded ${dark ? "bg-white/[0.07]" : "bg-slate-200"}`} />
        <div className={`mt-10 h-40 rounded-xl ${dark ? "bg-white/[0.05]" : "bg-white"}`} />
      </div>
    </main>
  );
}

export default function WorkflowFormPage() {
  const { formId = "" } = useParams();
  const instanceId = useId().replace(/:/g, "");
  const [accessKey, setAccessKey] = useState("");
  const [manifest, setManifest] = useState<FormManifest | null>(null);
  const [values, setValues] = useState<Record<string, FormValue>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [pageError, setPageError] = useState("");
  const [closeHint, setCloseHint] = useState("");
  const [manifestAttempt, setManifestAttempt] = useState(0);

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const fragmentKey = params.get("access") || "";
    const storedKey = readAccessKey(formId);
    if (fragmentKey) {
      rememberAccessKey(formId, fragmentKey);
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${window.location.search}`,
      );
    }
    setAccessKey(fragmentKey || storedKey);
  }, [formId]);

  useEffect(() => {
    if (!accessKey) {
      setLoading(false);
      setPageError("此表单链接无效或已失效。请向发布者索取新链接。");
      return;
    }
    let cancelled = false;
    setLoading(true);
    setPageError("");
    fetchManifest(formId, accessKey)
      .then((payload) => {
        if (cancelled) return;
        setManifest(payload);
        setValues(emptyValues(payload.fields));
        document.title = payload.formTitle;
      })
      .catch((caught) => {
        if (!cancelled) {
          if (!(caught instanceof ManifestRequestError && caught.status === 404)) {
            setManifest(null);
            setPageError("网络异常，暂时无法加载表单。请检查连接后重新加载。");
            return;
          }
          forgetAccessKey(formId);
          setAccessKey("");
          setManifest(null);
          setPageError("此表单链接无效或已失效。请向发布者索取新链接。");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accessKey, formId, manifestAttempt]);

  const isDark = manifest?.theme === "dark";
  const describedBy = useMemo(
    () => [manifest?.formDescription ? `${instanceId}-description` : "", manifest?.privacyNotice ? `${instanceId}-privacy` : ""].filter(Boolean).join(" "),
    [instanceId, manifest?.formDescription, manifest?.privacyNotice],
  );

  if (loading) return <LoadingState dark={isDark} />;

  if (!manifest) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 px-5 py-12 text-slate-950">
        <section className="w-full max-w-xl rounded-xl border border-slate-200 bg-white p-6 sm:p-8">
          <div aria-hidden="true" className="flex h-10 w-10 items-center justify-center rounded-full bg-rose-50 text-xl text-rose-700">×</div>
          <h1 className="mt-5 text-2xl font-semibold tracking-tight">表单暂时不可用</h1>
          <p className="mt-3 max-w-[65ch] text-sm leading-6 text-slate-700">{pageError || "请稍后重试。"}</p>
          {accessKey ? (
            <button
              className="mt-6 inline-flex min-h-11 items-center justify-center rounded-lg bg-cyan-700 px-5 text-sm font-semibold text-white transition hover:bg-cyan-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-600"
              onClick={() => setManifestAttempt((current) => current + 1)}
              type="button"
            >
              重新加载表单
            </button>
          ) : null}
        </section>
      </main>
    );
  }

  if (accepted) {
    return (
      <main className={`flex min-h-screen items-center justify-center px-5 py-12 ${isDark ? "bg-slate-950 text-slate-100" : "bg-slate-50 text-slate-950"}`}>
        <section className={`w-full max-w-xl rounded-xl p-7 sm:p-9 ${isDark ? "border border-white/10 bg-slate-900" : "border border-slate-200 bg-white"}`}>
          <div aria-hidden="true" className="flex h-11 w-11 items-center justify-center rounded-full bg-emerald-100 text-xl font-semibold text-emerald-800">✓</div>
          <h1 className="mt-6 text-2xl font-semibold tracking-tight">{manifest.successTitle}</h1>
          <p className={`mt-3 max-w-[65ch] text-sm leading-6 ${isDark ? "text-slate-300" : "text-slate-700"}`}>{manifest.successMessage}</p>
          <button
            className="mt-7 inline-flex min-h-11 items-center justify-center rounded-lg bg-cyan-700 px-5 text-sm font-semibold text-white transition hover:bg-cyan-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-600"
            onClick={() => {
              window.close();
              setCloseHint("如果浏览器没有自动关闭，请直接关闭此标签页。");
            }}
            type="button"
          >
            关闭页面
          </button>
          {closeHint ? <p className={`mt-3 text-xs ${isDark ? "text-slate-400" : "text-slate-600"}`}>{closeHint}</p> : null}
        </section>
      </main>
    );
  }

  const fieldSurface = isDark
    ? "border-white/15 bg-slate-950 text-slate-100 placeholder:text-slate-400"
    : "border-slate-300 bg-white text-slate-950 placeholder:text-slate-600";
  const activeManifest = manifest;

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors = Object.fromEntries(
      activeManifest.fields
        .map((field) => [field.id, fieldError(field, values[field.id])])
        .filter(([, message]) => Boolean(message)),
    );
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      document.getElementById(`${instanceId}-${Object.keys(nextErrors)[0]}`)?.focus();
      return;
    }
    setSubmitting(true);
    setPageError("");
    try {
      const response = await fetch(`/api/workflow-forms/${encodeURIComponent(formId)}/submissions`, {
        method: "POST",
        cache: "no-store",
        credentials: "omit",
        headers: {
          "Content-Type": "application/json",
          "X-ModelMirror-Form-Key": accessKey,
        },
        body: JSON.stringify({
          submissionToken: activeManifest.submissionToken,
          values,
        }),
      });
      if (!response.ok) {
        if (response.status === 404) {
          try {
            const refreshed = await fetchManifest(formId, accessKey);
            const schemaUnchanged = fieldSchemaSignature(refreshed.fields)
              === fieldSchemaSignature(activeManifest.fields);
            setManifest(refreshed);
            document.title = refreshed.formTitle;
            if (schemaUnchanged) {
              setPageError("提交时效已刷新。请确认内容后再次提交。");
            } else {
              setValues(emptyValues(refreshed.fields));
              setErrors({});
              setPageError("表单内容已更新。请按新字段重新填写后提交。");
            }
          } catch (caught) {
            if (caught instanceof ManifestRequestError && caught.status === 404) {
              forgetAccessKey(formId);
              setAccessKey("");
              setManifest(null);
              setPageError("此表单链接无效或已失效。请向发布者索取新链接。");
            } else {
              setPageError("提交时效暂时无法刷新。请检查网络后再次提交。");
            }
          }
          return;
        }
        if (response.status === 429) throw new Error("rate_limited");
        if (response.status === 422) throw new Error("invalid_values");
        throw new Error("submission_failed");
      }
      setValues(emptyValues(activeManifest.fields));
      setAccepted(true);
    } catch (caught) {
      const code = caught instanceof Error ? caught.message : "submission_failed";
      setPageError(
        code === "rate_limited"
          ? "提交过于频繁，请稍后再试。"
          : code === "invalid_values"
            ? "部分内容不符合要求，请检查后重试。"
            : "网络异常，尚未确认提交结果。你可以安全地再次提交。",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className={`min-h-screen px-4 py-8 sm:px-6 sm:py-12 ${isDark ? "bg-slate-950 text-slate-100" : "bg-slate-50 text-slate-950"}`}>
      <section className={`mx-auto w-full max-w-2xl rounded-xl p-5 sm:p-8 ${isDark ? "border border-white/10 bg-slate-900" : "border border-slate-200 bg-white"}`}>
        <header className={`border-b pb-7 ${isDark ? "border-white/10" : "border-slate-200"}`}>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{manifest.formTitle}</h1>
          {manifest.formDescription ? (
            <p className={`mt-3 max-w-[65ch] text-sm leading-6 ${isDark ? "text-slate-300" : "text-slate-700"}`} id={`${instanceId}-description`}>{manifest.formDescription}</p>
          ) : null}
        </header>

        <form aria-describedby={describedBy || undefined} className="mt-7 space-y-6" noValidate onSubmit={submit}>
          {manifest.fields.map((field) => {
            const inputId = `${instanceId}-${field.id}`;
            const error = errors[field.id];
            const helpId = `${inputId}-help`;
            const errorId = `${inputId}-error`;
            const described = [field.helpText ? helpId : "", error ? errorId : ""].filter(Boolean).join(" ");
            const common = {
              "aria-describedby": described || undefined,
              "aria-invalid": Boolean(error),
              className: `mt-2 min-h-11 w-full rounded-lg border px-3 py-2 text-base outline-none transition focus:border-cyan-600 focus:ring-2 focus:ring-cyan-600/25 ${fieldSurface} ${error ? "border-rose-500" : ""}`,
              id: inputId,
              name: field.id,
              required: field.required,
            };
            return (
              <div key={field.id}>
                <label className="text-sm font-semibold" htmlFor={inputId}>
                  {field.label}{field.required ? <span aria-hidden="true" className="ml-1 text-rose-600">*</span> : null}
                </label>
                {field.type === "long_text" ? (
                  <textarea
                    {...common}
                    maxLength={20_000}
                    onChange={(e) => setValues((current) => ({ ...current, [field.id]: e.target.value }))}
                    placeholder={field.placeholder}
                    rows={6}
                    value={String(values[field.id] ?? "")}
                  />
                ) : field.type === "boolean" ? (
                  <label className={`mt-2 flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border px-3 py-3 ${fieldSurface}`}>
                    <input
                      aria-describedby={described || undefined}
                      aria-invalid={Boolean(error)}
                      checked={values[field.id] === true}
                      className="mt-0.5 h-5 w-5 accent-cyan-700"
                      id={inputId}
                      onChange={(e) => setValues((current) => ({ ...current, [field.id]: e.target.checked }))}
                      required={field.required}
                      type="checkbox"
                    />
                    <span className="text-sm leading-5">{field.placeholder || "我确认此项"}</span>
                  </label>
                ) : field.type === "single_select" ? (
                  <select
                    {...common}
                    onChange={(e) => setValues((current) => ({ ...current, [field.id]: e.target.value }))}
                    value={String(values[field.id] ?? "")}
                  >
                    <option value="">请选择</option>
                    {field.options.map((option) => <option key={option.id} value={option.value}>{option.label}</option>)}
                  </select>
                ) : field.type === "multi_select" ? (
                  <fieldset
                    aria-describedby={described || undefined}
                    aria-invalid={Boolean(error)}
                    className={`mt-2 space-y-2 rounded-lg border p-3 outline-none transition focus:ring-2 focus:ring-cyan-600/25 ${fieldSurface} ${error ? "border-rose-500" : ""}`}
                    id={inputId}
                    tabIndex={-1}
                  >
                    <legend className="sr-only">{field.label}</legend>
                    {field.options.map((option) => {
                      const selected = Array.isArray(values[field.id]) ? values[field.id] as string[] : [];
                      return (
                        <label className="flex min-h-10 cursor-pointer items-center gap-3 text-sm" key={option.id}>
                          <input
                            checked={selected.includes(option.value)}
                            className="h-5 w-5 accent-cyan-700"
                            onChange={(e) => setValues((current) => ({
                              ...current,
                              [field.id]: e.target.checked
                                ? [...selected, option.value]
                                : selected.filter((item) => item !== option.value),
                            }))}
                            type="checkbox"
                          />
                          {option.label}
                        </label>
                      );
                    })}
                  </fieldset>
                ) : (
                  <input
                    {...common}
                    maxLength={field.type === "short_text" ? 500 : field.type === "email" ? 254 : undefined}
                    onChange={(e) => setValues((current) => ({
                      ...current,
                      [field.id]: field.type === "number"
                        ? (e.target.value === "" ? null : e.target.valueAsNumber)
                        : e.target.value,
                    }))}
                    placeholder={field.placeholder}
                    type={field.type === "email" ? "email" : field.type === "number" ? "number" : field.type === "date" ? "date" : "text"}
                    value={String(values[field.id] ?? "")}
                  />
                )}
                {field.helpText ? <p className={`mt-2 text-xs leading-5 ${isDark ? "text-slate-400" : "text-slate-600"}`} id={helpId}>{field.helpText}</p> : null}
                {error ? <p className="mt-2 text-xs font-medium text-rose-600" id={errorId} role="alert">{error}</p> : null}
              </div>
            );
          })}

          {manifest.privacyNotice ? (
            <p className={`rounded-lg px-3 py-2 text-xs leading-5 ${isDark ? "bg-white/[0.05] text-slate-300" : "bg-slate-100 text-slate-700"}`} id={`${instanceId}-privacy`}>{manifest.privacyNotice}</p>
          ) : null}
          {pageError ? <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-800" role="alert">{pageError}</p> : null}
          <button
            className="inline-flex min-h-11 w-full items-center justify-center rounded-lg bg-cyan-700 px-5 text-sm font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-600 sm:w-auto"
            disabled={submitting}
            type="submit"
          >
            {submitting ? "正在提交…" : manifest.submitLabel}
          </button>
        </form>
      </section>
    </main>
  );
}
