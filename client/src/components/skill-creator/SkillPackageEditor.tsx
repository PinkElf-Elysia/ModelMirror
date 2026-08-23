import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Copy,
  FileCode2,
  FileText,
  Folder,
  LockKeyhole,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  SkillCreatorDraft,
  SkillPackageIssue,
  SkillPackagePayload,
} from "../../utils/skillCreatorApi";

type EditorView = "structured" | "source" | "preview";
type MobilePane = "files" | "editor" | "checks";

interface SkillPackageEditorProps {
  draft: SkillCreatorDraft;
  errorIssues?: SkillPackageIssue[];
  conflictMessage?: string;
  saving: boolean;
  onCopyAsNew: (payload: SkillPackagePayload) => Promise<void>;
  onReload: () => Promise<void>;
  onSave: (payload: SkillPackagePayload) => Promise<void>;
  onDirtyChange?: (dirty: boolean) => void;
}

const TEXT_ROOTS = ["scripts/", "references/", "assets/"];

function skillMarkdownBody(markdown: string) {
  const lines = markdown.split("\n");
  const lineValue = (line: string) => line.endsWith("\r") ? line.slice(0, -1) : line;
  if (lineValue(lines[0] ?? "") !== "---") return markdown;
  const closing = lines.findIndex((line, index) => index > 0 && lineValue(line) === "---");
  return closing < 0 ? markdown : lines.slice(closing + 1).join("\n");
}

function normalizePath(value: string) {
  return value.trim().replaceAll("\\", "/").replace(/^\/+/, "");
}

function validTextPath(path: string) {
  if (!path || path === "SKILL.md" || path.includes("..") || path.includes("//")) return false;
  return TEXT_ROOTS.some((root) => path.startsWith(root)) || path === "agents/openai.yaml";
}

function pathGroups(paths: string[]) {
  const groups = new Map<string, string[]>();
  for (const path of paths) {
    const group = path === "SKILL.md" ? "Skill 根目录" : path.split("/")[0];
    groups.set(group, [...(groups.get(group) ?? []), path]);
  }
  return [...groups.entries()];
}

function packageFingerprint(rootName: string, files: Record<string, string>) {
  return JSON.stringify([rootName, Object.entries(files).sort(([a], [b]) => a.localeCompare(b))]);
}

function ValidationReport({
  issues,
  valid,
  onOpenIssue,
}: {
  issues: SkillPackageIssue[];
  valid: boolean;
  onOpenIssue: (issue: SkillPackageIssue) => void;
}) {
  return (
    <section aria-labelledby="creator-validation-heading" className="min-w-0">
      <div className="flex items-center gap-3">
        {valid ? (
          <CheckCircle2 aria-hidden="true" className="text-emerald-200" size={19} />
        ) : (
          <TriangleAlert aria-hidden="true" className="text-amber-200" size={19} />
        )}
        <div>
          <h3 className="text-sm font-semibold text-white" id="creator-validation-heading">规范与安全检查</h3>
          <p className="mt-0.5 text-xs text-slate-400">
            {valid ? "当前服务端校验有效" : `${issues.length} 项需要处理`}
          </p>
        </div>
      </div>

      {issues.length === 0 ? (
        <div className="mt-4 rounded-lg bg-emerald-300/[0.07] p-3 text-xs leading-5 text-emerald-100">
          没有结构或凭据问题。内容修改后请保存，以重新运行服务端校验。
        </div>
      ) : (
        <ul className="mt-4 space-y-2">
          {issues.map((issue, index) => (
            <li key={`${issue.code}-${issue.path ?? "package"}-${issue.line ?? index}`}>
              <button
                className={`w-full rounded-lg border p-3 text-left transition hover:bg-white/[0.055] ${
                  issue.severity === "warning"
                    ? "border-amber-300/20 bg-amber-300/[0.06]"
                    : "border-rose-300/20 bg-rose-300/[0.07]"
                }`}
                onClick={() => onOpenIssue(issue)}
                type="button"
              >
                <span className="flex items-start gap-2">
                  <AlertCircle aria-hidden="true" className={issue.severity === "warning" ? "mt-0.5 shrink-0 text-amber-200" : "mt-0.5 shrink-0 text-rose-200"} size={14} />
                  <span className="min-w-0">
                    <span className="block break-words text-xs font-semibold text-slate-100">{issue.message}</span>
                    <span className="mt-1 block break-all font-mono text-[11px] text-slate-400">
                      {issue.path ?? "Skill 包"}{issue.line ? `:${issue.line}` : ""} · {issue.code}
                    </span>
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function SkillPackageEditor({
  draft,
  errorIssues = [],
  conflictMessage,
  saving,
  onCopyAsNew,
  onDirtyChange,
  onReload,
  onSave,
}: SkillPackageEditorProps) {
  const initialFiles = useMemo(
    () => ({ "SKILL.md": draft.skill_markdown, ...(draft.files ?? {}) }),
    [draft.content_digest, draft.files, draft.skill_markdown],
  );
  const rootName = draft.root_name || draft.slug;
  const [files, setFiles] = useState<Record<string, string>>(initialFiles);
  const [selectedPath, setSelectedPath] = useState("SKILL.md");
  const [view, setView] = useState<EditorView>(draft.frontmatter ? "structured" : "source");
  const [mobilePane, setMobilePane] = useState<MobilePane>("editor");
  const [newPath, setNewPath] = useState("");
  const [pathError, setPathError] = useState("");
  const [copying, setCopying] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    setFiles(initialFiles);
    setSelectedPath("SKILL.md");
    setView(draft.frontmatter ? "structured" : "source");
  }, [draft.content_digest, draft.frontmatter, initialFiles]);

  const initialFingerprint = useMemo(
    () => packageFingerprint(draft.root_name || draft.slug, initialFiles),
    [draft.root_name, draft.slug, initialFiles],
  );
  const dirty = packageFingerprint(rootName, files) !== initialFingerprint;
  const issues = [...errorIssues, ...(draft.validation?.issues ?? [])];
  const validationCurrent = Boolean(draft.validation?.valid) && !dirty && errorIssues.length === 0;
  const currentContent = files[selectedPath] ?? "";
  const selectedReadOnly = selectedPath === "hooks/manifest.json";
  const previewBody = useMemo(
    () => skillMarkdownBody(files["SKILL.md"] ?? ""),
    [files],
  );

  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const payload = useCallback((): SkillPackagePayload => {
    const extras = Object.fromEntries(Object.entries(files).filter(([path]) => path !== "SKILL.md"));
    const projection = draft.frontmatter;
    return {
      root_name: rootName.trim(),
      name: projection?.name ?? draft.name,
      description: projection?.description ?? draft.description,
      skill_markdown: files["SKILL.md"] ?? "",
      files: extras,
      license: projection?.license ?? draft.license ?? null,
      compatibility: projection?.compatibility ?? draft.compatibility ?? null,
      metadata: projection?.metadata ?? draft.metadata ?? {},
      allowed_tools: [...(projection?.allowed_tools ?? draft.allowed_tools ?? [])],
    };
  }, [draft, files, rootName]);

  const save = useCallback(async () => {
    if (!dirty || saving) return;
    await onSave(payload());
  }, [dirty, onSave, payload, saving]);

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void save();
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [save]);

  useEffect(() => {
    if (!dirty) return;
    function warnBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [dirty]);

  function updateFile(content: string) {
    if (selectedReadOnly) return;
    setFiles((current) => ({ ...current, [selectedPath]: content }));
  }

  function addFile() {
    const path = normalizePath(newPath);
    if (!validTextPath(path)) {
      setPathError("路径必须位于 scripts/、references/、assets/，或为 agents/openai.yaml。");
      return;
    }
    if (path in files || Object.keys(files).some((item) => item.toLowerCase() === path.toLowerCase())) {
      setPathError("该文件路径已存在，路径大小写也必须唯一。");
      return;
    }
    setFiles((current) => ({ ...current, [path]: "" }));
    setSelectedPath(path);
    setNewPath("");
    setPathError("");
    setMobilePane("editor");
  }

  function deleteFile(path: string) {
    if (path === "SKILL.md" || path === "hooks/manifest.json") return;
    setFiles((current) => Object.fromEntries(Object.entries(current).filter(([item]) => item !== path)));
    setSelectedPath("SKILL.md");
  }

  function openIssue(issue: SkillPackageIssue) {
    const path = issue.path && issue.path in files ? issue.path : "SKILL.md";
    setSelectedPath(path);
    setView("source");
    setMobilePane("editor");
    window.setTimeout(() => {
      const textarea = textareaRef.current;
      if (!textarea || !issue.line) return;
      const lines = textarea.value.split("\n");
      const offset = lines.slice(0, Math.max(issue.line - 1, 0)).reduce((total, line) => total + line.length + 1, 0);
      textarea.focus();
      textarea.setSelectionRange(offset, Math.min(offset + (lines[issue.line - 1]?.length ?? 0), textarea.value.length));
    }, 0);
  }

  async function copyAsNew() {
    setCopying(true);
    try {
      await onCopyAsNew(payload());
    } finally {
      setCopying(false);
    }
  }

  return (
    <div className="min-w-0">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-white/10 bg-surface-900/80 px-4 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-base font-semibold text-white">{draft.frontmatter?.name || draft.name}</h2>
            <span className="rounded-full bg-amber-300/10 px-2.5 py-1 text-xs font-semibold text-amber-100">
              {draft.quality_status === "outdated" ? "评测已过期" : "待评测"}
            </span>
          </div>
          <p className="mt-1 truncate font-mono text-xs text-slate-500">
            revision {draft.revision} · {draft.content_digest.slice(0, 12)}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span aria-live="polite" className={`text-xs ${dirty ? "text-amber-100" : "text-emerald-200"}`}>
            {dirty ? "有未保存修改" : "已保存"}
          </span>
          <button
            className="inline-flex items-center gap-2 rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
            disabled={!dirty || saving}
            onClick={() => void save()}
            type="button"
          >
            <Save aria-hidden="true" size={15} />
            {saving ? "正在保存…" : "保存草稿"}
          </button>
        </div>
      </div>

      {conflictMessage ? (
        <div className="mb-4 rounded-lg border border-amber-300/25 bg-amber-300/[0.08] p-4" role="alert">
          <div className="flex items-start gap-3">
            <TriangleAlert aria-hidden="true" className="mt-0.5 shrink-0 text-amber-100" size={18} />
            <div>
              <p className="text-sm font-semibold text-white">草稿已在其他位置更新</p>
              <p className="mt-1 text-sm leading-6 text-slate-300">{conflictMessage}</p>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              className="inline-flex items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-100 transition hover:bg-white/[0.06]"
              onClick={() => void onReload()}
              type="button"
            >
              <RefreshCw aria-hidden="true" size={15} />
              重新加载服务端版本
            </button>
            <button
              className="inline-flex items-center gap-2 rounded-full border border-brand-300/30 bg-brand-300/10 px-4 py-2 text-sm font-semibold text-brand-100 transition hover:bg-brand-300/20 disabled:opacity-50"
              disabled={copying}
              onClick={() => void copyAsNew()}
              type="button"
            >
              <Copy aria-hidden="true" size={15} />
              {copying ? "正在复制…" : "复制为新草稿"}
            </button>
          </div>
        </div>
      ) : null}

      <div className="mb-3 grid grid-cols-3 rounded-lg border border-white/10 bg-ink-950/70 p-1 lg:hidden" role="tablist" aria-label="草稿编辑区域">
        {([
          ["files", "文件"],
          ["editor", "编辑"],
          ["checks", `检查${issues.length ? ` ${issues.length}` : ""}`],
        ] as Array<[MobilePane, string]>).map(([id, label]) => (
          <button
            aria-selected={mobilePane === id}
            className={`rounded-md px-3 py-2 text-sm font-semibold ${mobilePane === id ? "bg-white/10 text-white" : "text-slate-400"}`}
            key={id}
            onClick={() => setMobilePane(id)}
            role="tab"
            type="button"
          >
            {label}
          </button>
        ))}
      </div>

      <div className="grid min-h-[640px] min-w-0 overflow-hidden rounded-lg border border-white/10 bg-surface-900/75 lg:grid-cols-[210px_minmax(0,1fr)_270px]">
        <aside className={`${mobilePane === "files" ? "block" : "hidden"} min-w-0 border-white/10 p-3 lg:block lg:border-r`}>
          <div className="flex items-center justify-between gap-2 px-1">
            <h3 className="text-xs font-semibold text-slate-300">UTF-8 文件</h3>
            <span className="text-[11px] text-slate-500">{Object.keys(files).length}</span>
          </div>
          <div className="mt-3 max-h-[430px] overflow-y-auto">
            {pathGroups(Object.keys(files).sort()).map(([group, paths]) => (
              <div className="mb-3" key={group}>
                <p className="flex items-center gap-1.5 px-2 py-1 text-xs font-semibold text-slate-500">
                  <Folder aria-hidden="true" size={13} />
                  {group}
                </p>
                <div className="mt-1 space-y-0.5">
                  {paths.map((path) => (
                    <div className={`group flex items-center rounded-md ${selectedPath === path ? "bg-brand-300/10" : "hover:bg-white/[0.045]"}`} key={path}>
                      <button
                        className={`flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left text-xs ${selectedPath === path ? "text-brand-100" : "text-slate-300"}`}
                        onClick={() => { setSelectedPath(path); setMobilePane("editor"); }}
                        type="button"
                      >
                        {path.endsWith(".md") ? <FileText aria-hidden="true" size={13} /> : <FileCode2 aria-hidden="true" size={13} />}
                        <span className="truncate">{path.split("/").at(-1)}</span>
                      </button>
                      {path !== "SKILL.md" && path !== "hooks/manifest.json" ? (
                        <button
                          aria-label={`删除 ${path}`}
                          className="mr-1 rounded p-1 text-slate-600 opacity-0 transition hover:bg-rose-300/10 hover:text-rose-200 group-hover:opacity-100 group-focus-within:opacity-100"
                          onClick={() => deleteFile(path)}
                          type="button"
                        >
                          <Trash2 aria-hidden="true" size={13} />
                        </button>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-4 border-t border-white/10 pt-3">
            <label className="text-xs font-semibold text-slate-400" htmlFor="creator-new-file">添加文本文件</label>
            <input
              className="mt-2 w-full rounded-md border border-white/10 bg-ink-950/80 px-2.5 py-2 text-xs text-white placeholder:text-slate-500 focus:border-brand-300/50 focus:outline-none"
              id="creator-new-file"
              onChange={(event) => { setNewPath(event.target.value); setPathError(""); }}
              onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addFile(); } }}
              placeholder="references/guide.md"
              value={newPath}
            />
            {pathError ? <p className="mt-2 text-xs leading-5 text-rose-200">{pathError}</p> : null}
            <button
              className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.06]"
              onClick={addFile}
              type="button"
            >
              <Plus aria-hidden="true" size={14} />
              添加文件
            </button>
          </div>
        </aside>

        <section className={`${mobilePane === "editor" ? "flex" : "hidden"} min-h-[640px] min-w-0 flex-col lg:flex`}>
          <div className="flex min-h-12 flex-wrap items-center justify-between gap-2 border-b border-white/10 px-3 py-2">
            <div className="flex min-w-0 items-center gap-1 text-xs text-slate-400">
              <span className="truncate">{rootName || "skill"}</span>
              <ChevronRight aria-hidden="true" className="shrink-0" size={13} />
              <span className="truncate text-slate-200">{selectedPath}</span>
            </div>
            {selectedPath === "SKILL.md" ? (
              <div className="inline-flex rounded-md bg-white/[0.045] p-1" role="tablist" aria-label="SKILL.md 视图">
                {([
                  ["structured", "结构化（只读）"],
                  ["source", "源码编辑"],
                  ["preview", "预览"],
                ] as Array<[EditorView, string]>).map(([id, label]) => (
                  <button
                    aria-selected={view === id}
                    className={`rounded px-2.5 py-1.5 text-xs font-semibold ${view === id ? "bg-white/10 text-white" : "text-slate-400 hover:text-white"}`}
                    disabled={id === "structured" && !draft.frontmatter}
                    key={id}
                    onClick={() => setView(id)}
                    role="tab"
                    title={id === "structured" && !draft.frontmatter ? "服务端尚未提供有效 frontmatter 投影，请在源码中修复" : undefined}
                    type="button"
                  >
                    {label}
                  </button>
                ))}
              </div>
            ) : selectedReadOnly ? (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-300/20 bg-amber-300/[0.07] px-2.5 py-1 text-[11px] font-semibold text-amber-100">
                <LockKeyhole aria-hidden="true" size={12} />由已确认 Hook 计划生成，只读
              </span>
            ) : null}
          </div>

          {selectedPath === "SKILL.md" && view === "structured" && draft.frontmatter ? (
            <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
              <div className="mx-auto max-w-3xl space-y-5">
                <div className="rounded-lg border border-brand-300/20 bg-brand-300/[0.07] p-4">
                  <p className="text-sm font-semibold text-brand-100">服务端已验证投影</p>
                  <p className="mt-1 text-xs leading-5 text-slate-300">
                    此处只读，不会解析或重写 YAML。要修改 frontmatter、注释或正文，请切换到“源码编辑”，保存后由服务端重新生成投影。
                  </p>
                  {dirty ? (
                    <p className="mt-2 text-xs font-semibold text-amber-100">当前投影仍对应已保存版本；未保存源码尚未参与校验。</p>
                  ) : null}
                </div>
                <dl className="grid gap-4 sm:grid-cols-2">
                  <div className="rounded-lg border border-white/10 bg-ink-950/55 p-4 sm:col-span-2">
                    <dt className="text-xs font-semibold text-slate-500">Skill ID 与根目录</dt>
                    <dd className="mt-2 break-all font-mono text-sm text-white">{draft.frontmatter.name}</dd>
                  </div>
                  <div className="rounded-lg border border-white/10 bg-ink-950/55 p-4 sm:col-span-2">
                    <dt className="text-xs font-semibold text-slate-500">能力与触发场景</dt>
                    <dd className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-100">{draft.frontmatter.description}</dd>
                  </div>
                  <div className="rounded-lg border border-white/10 bg-ink-950/55 p-4">
                    <dt className="text-xs font-semibold text-slate-500">License</dt>
                    <dd className="mt-2 text-sm text-slate-100">{draft.frontmatter.license || "未声明"}</dd>
                  </div>
                  <div className="rounded-lg border border-white/10 bg-ink-950/55 p-4">
                    <dt className="text-xs font-semibold text-slate-500">兼容要求</dt>
                    <dd className="mt-2 whitespace-pre-wrap text-sm text-slate-100">{draft.frontmatter.compatibility || "未声明"}</dd>
                  </div>
                  <div className="rounded-lg border border-white/10 bg-ink-950/55 p-4 sm:col-span-2">
                    <dt className="text-xs font-semibold text-slate-500">allowed-tools</dt>
                    <dd className="mt-2 font-mono text-sm text-slate-100">
                      {draft.frontmatter.allowed_tools.length ? draft.frontmatter.allowed_tools.join(", ") : "未声明"}
                    </dd>
                    <p className="mt-2 text-xs leading-5 text-slate-500">仅为包元数据，不会授予 ModelMirror 运行权限。</p>
                  </div>
                  <div className="rounded-lg border border-white/10 bg-ink-950/55 p-4 sm:col-span-2">
                    <dt className="text-xs font-semibold text-slate-500">metadata</dt>
                    <dd className="mt-2 overflow-x-auto whitespace-pre font-mono text-xs leading-5 text-slate-200">
                      {JSON.stringify(draft.frontmatter.metadata, null, 2)}
                    </dd>
                  </div>
                </dl>
              </div>
            </div>
          ) : selectedPath === "SKILL.md" && view === "preview" ? (
            <article className="prose prose-invert min-h-0 max-w-none flex-1 overflow-y-auto p-5 text-sm leading-7 text-slate-300 prose-headings:text-white prose-a:text-brand-100 prose-code:text-hire-100">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{previewBody || "_SKILL.md 暂无正文。_"}</ReactMarkdown>
            </article>
          ) : (
            <textarea
              aria-label={`${selectedReadOnly ? "查看" : "编辑"} ${selectedPath}`}
              className={`min-h-0 flex-1 resize-none bg-ink-950/55 p-4 font-mono text-[13px] leading-6 text-slate-200 outline-none ${selectedReadOnly ? "cursor-default" : "focus:bg-ink-950/70"}`}
              onChange={(event) => updateFile(event.target.value)}
              readOnly={selectedReadOnly}
              ref={textareaRef}
              spellCheck={selectedPath.endsWith(".md")}
              value={currentContent}
            />
          )}

          {selectedPath === "SKILL.md" && !draft.frontmatter ? (
            <div className="border-t border-amber-300/20 bg-amber-300/[0.07] px-4 py-3 text-xs leading-5 text-amber-100">
              服务端未提供有效 frontmatter 投影，结构化视图不可用。请在“源码编辑”中修复 YAML，并保存以重新运行服务端校验。
            </div>
          ) : null}
        </section>

        <aside className={`${mobilePane === "checks" ? "block" : "hidden"} min-w-0 border-white/10 p-4 lg:block lg:border-l`}>
          <ValidationReport issues={issues} onOpenIssue={openIssue} valid={validationCurrent} />
          <dl className="mt-6 space-y-3 border-t border-white/10 pt-4 text-xs">
            <div>
              <dt className="text-slate-500">固定内容版本</dt>
              <dd className="mt-1 break-all font-mono text-slate-300">{draft.content_digest}</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-slate-500">文件数量</dt>
              <dd className="font-semibold text-slate-200">{Object.keys(files).length}</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-slate-500">安装状态</dt>
              <dd className="font-semibold text-amber-100">质量门锁定</dd>
            </div>
          </dl>
        </aside>
      </div>
    </div>
  );
}

export { validTextPath };
