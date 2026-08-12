import {
  ArrowLeft,
  Binary,
  CheckCircle2,
  FileCode2,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import SkillTrustPanel, {
  SkillTrustBadge,
} from "../components/skill-trust/SkillTrustPanel";
import {
  deleteLocalSkillImport,
  formatImportBytes,
  installLocalSkillImport,
  previewLocalSkillImportFile,
  readLocalSkillImport,
  rescanLocalSkillImport,
  SkillLocalImportApiError,
  type LocalSkillImportFile,
  type LocalSkillImportRecord,
  type LocalSkillReplacementChange,
} from "../utils/skillLocalImportApi";

const STEP_LABELS = ["选择来源", "扫描规范化", "核对风险", "安装或替换"];
const STATE_LABELS: Record<LocalSkillImportRecord["state"], string> = {
  scanning: "扫描中",
  ready: "可直接安装",
  confirmation_required: "等待风险确认",
  blocked: "已阻断",
  failed: "扫描失败",
  installed: "已安装",
  superseded: "已被新导入替换",
  archived: "已归档",
  stale: "凭据已过期",
};

const NON_PREVIEW_SUFFIXES = new Set([
  "html",
  "htm",
  "svg",
  "xml",
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "pdf",
  "woff",
  "woff2",
  "mp3",
  "wav",
  "mp4",
]);

function currentStep(record: LocalSkillImportRecord) {
  if (record.state === "scanning") return 1;
  if (record.state === "installed") return 3;
  return 2;
}

function isPreviewable(file: LocalSkillImportFile) {
  const suffix = file.path.split(".").at(-1)?.toLowerCase() || "";
  return !NON_PREVIEW_SUFFIXES.has(suffix);
}

function changeLabel(change: LocalSkillReplacementChange) {
  if (change.status === "added") return "新增";
  if (change.status === "removed") return "删除";
  return "变化";
}

export default function SkillLocalImportDetailPage() {
  const { importId = "" } = useParams();
  const navigate = useNavigate();
  const [record, setRecord] = useState<LocalSkillImportRecord | null>(null);
  const [selectedPath, setSelectedPath] = useState("");
  const [preview, setPreview] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"rescan" | "install" | "delete" | "preview" | "">("");
  const [error, setError] = useState("");
  const [showTrust, setShowTrust] = useState(false);

  const load = useCallback(async () => {
    if (!importId) return;
    setLoading(true);
    setError("");
    try {
      setRecord(await readLocalSkillImport(importId));
    } catch (caught) {
      setError(
        caught instanceof SkillLocalImportApiError
          ? caught.message
          : "本地导入记录加载失败。",
      );
    } finally {
      setLoading(false);
    }
  }, [importId]);

  useEffect(() => {
    void load();
  }, [load]);

  const totalBytes = useMemo(
    () => record?.fileManifest.reduce((sum, file) => sum + file.sizeBytes, 0) || 0,
    [record?.fileManifest],
  );

  async function openFile(file: LocalSkillImportFile) {
    if (!record || !isPreviewable(file)) return;
    setBusy("preview");
    setError("");
    try {
      const response = await previewLocalSkillImportFile(record.importId, file.path);
      setSelectedPath(file.path);
      setPreview(response.content);
    } catch (caught) {
      setError(
        caught instanceof SkillLocalImportApiError
          ? caught.message
          : "文件预览失败。",
      );
    } finally {
      setBusy("");
    }
  }

  async function rescan() {
    if (!record) return;
    setBusy("rescan");
    setError("");
    try {
      const rescanned = await rescanLocalSkillImport(record);
      setRecord(await readLocalSkillImport(rescanned.importId));
      setShowTrust(false);
    } catch (caught) {
      setError(
        caught instanceof SkillLocalImportApiError
          ? caught.message
          : "重新扫描失败。",
      );
    } finally {
      setBusy("");
    }
  }

  async function install(confirmed: boolean) {
    if (!record) return;
    setBusy("install");
    setError("");
    try {
      const response = await installLocalSkillImport(record, {
        confirmed,
        expectedInstalledDigest: record.replacementPreview?.required
          ? record.replacementPreview.installedDigest
          : null,
      });
      setRecord(response.import);
      setShowTrust(false);
    } catch (caught) {
      setError(
        caught instanceof SkillLocalImportApiError
          ? caught.message
          : "本地 Skill 安装失败。",
      );
    } finally {
      setBusy("");
    }
  }

  async function removeImport() {
    if (!record || !window.confirm("删除这条本地导入记录？此操作不会删除其他 Skill。")) return;
    setBusy("delete");
    setError("");
    try {
      await deleteLocalSkillImport(record);
      navigate("/skills/import");
    } catch (caught) {
      setError(
        caught instanceof SkillLocalImportApiError
          ? caught.message
          : "删除导入记录失败。",
      );
    } finally {
      setBusy("");
    }
  }

  if (loading) {
    return (
      <PageContainer activeResource="skills" hideSidebar maxWidthClassName="max-w-[1320px]">
        <div aria-label="正在读取本地导入" className="space-y-4">
          <div className="h-12 w-64 animate-pulse rounded bg-white/[0.055] motion-reduce:animate-none" />
          <div className="h-40 animate-pulse rounded-lg bg-white/[0.045] motion-reduce:animate-none" />
          <div className="h-72 animate-pulse rounded-lg bg-white/[0.04] motion-reduce:animate-none" />
        </div>
      </PageContainer>
    );
  }

  if (!record) {
    return (
      <PageContainer activeResource="skills" hideSidebar maxWidthClassName="max-w-[900px]">
        <section className="rounded-lg border border-rose-300/25 bg-rose-300/10 p-6" role="alert">
          <h1 className="text-xl font-semibold text-white">无法打开本地导入</h1>
          <p className="mt-2 text-sm leading-6 text-rose-50">{error || "记录不存在或导入功能已关闭。"}</p>
          <Link className="mt-5 inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 text-sm font-semibold text-white" to="/skills/import">
            <ArrowLeft aria-hidden="true" size={16} />
            返回导入列表
          </Link>
        </section>
      </PageContainer>
    );
  }

  const receipt = record.trustReceipt || null;
  const blocked = record.state === "blocked" || record.state === "failed" || receipt?.installPolicy === "block";
  const replacement = record.replacementPreview || null;
  const replacementBlocked = Boolean(replacement && !replacement.allowed);
  const needsConfirmation = receipt?.installPolicy === "confirm" || Boolean(replacement?.required);
  const canInstall = Boolean(
    receipt &&
      record.packageDigest &&
      record.trustFingerprint &&
      ["ready", "confirmation_required"].includes(record.state) &&
      !blocked &&
      !replacementBlocked,
  );
  const activeStep = currentStep(record);

  return (
    <PageContainer activeResource="skills" hideSidebar maxWidthClassName="max-w-[1320px]">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <Link className="inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white" to="/skills/import">
          <ArrowLeft aria-hidden="true" size={16} />
          本地导入
        </Link>
        <span className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${blocked ? "border-rose-300/25 bg-rose-300/10 text-rose-100" : record.state === "installed" ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100" : "border-amber-300/25 bg-amber-300/10 text-amber-100"}`}>
          {STATE_LABELS[record.state]}
        </span>
      </div>

      <nav aria-label="本地 Skill 导入阶段" className="border-y border-white/10 py-4">
        <ol className="grid grid-cols-1 gap-2 sm:grid-cols-4">
          {STEP_LABELS.map((label, index) => (
            <li className={`flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm ${index === activeStep ? "bg-hire-300 text-ink-950" : index < activeStep ? "bg-emerald-300/10 text-emerald-100" : "bg-white/[0.04] text-slate-500"}`} key={label}>
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-current/30 text-xs font-semibold">{index < activeStep ? "✓" : index + 1}</span>
              <span className="font-semibold">{label}</span>
            </li>
          ))}
        </ol>
      </nav>

      <header className="py-7 sm:py-9">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-hire-200">{record.transportKind === "zip" ? "ZIP 导入" : "文件夹导入"}</p>
            <h1 className="mt-2 break-words text-3xl font-semibold text-white sm:text-4xl">
              {record.localSkillId || record.declaredName || "待指定 Skill ID"}
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-400">
              {record.fileManifest.length} 个文件，{formatImportBytes(totalBytes)}。扫描结果绑定当前原始字节摘要；重扫、替换或扫描器升级后需要重新确认。
            </p>
          </div>
          {receipt ? <SkillTrustBadge summary={receipt} /> : null}
        </div>
      </header>

      {error ? (
        <div className="mb-6 rounded-lg border border-rose-300/25 bg-rose-300/10 p-4 text-sm leading-6 text-rose-50" role="alert">
          {error}
        </div>
      ) : null}

      {showTrust && receipt ? (
        <SkillTrustPanel
          action={needsConfirmation ? "install" : "inspect"}
          busy={busy === "install"}
          onCancel={() => setShowTrust(false)}
          onConfirm={() => void install(true)}
          receipt={receipt}
          title={record.localSkillId || record.declaredName || "本地 Skill"}
        />
      ) : null}

      <section className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0">
          <section className="rounded-lg border border-white/10 bg-surface-900/72 p-4 sm:p-5" aria-labelledby="import-files-heading">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-4">
              <div>
                <h2 className="text-lg font-semibold text-white" id="import-files-heading">文件清单</h2>
                <p className="mt-1 text-xs text-slate-500">二进制和 HTML/SVG 仅展示摘要，不在浏览器主动渲染。</p>
              </div>
              <span className="text-xs font-semibold text-slate-400">{record.fileManifest.length} / 500</span>
            </div>
            {record.fileManifest.length ? (
              <div className="mt-3 max-h-[32rem] divide-y divide-white/10 overflow-y-auto">
                {record.fileManifest.map((file) => {
                  const previewable = isPreviewable(file);
                  return (
                    <button
                      className={`flex min-h-14 w-full min-w-0 items-center gap-3 px-2 py-3 text-left transition ${previewable ? "hover:bg-white/[0.045]" : "cursor-default"}`}
                      disabled={!previewable || busy === "preview"}
                      key={file.path}
                      onClick={() => void openFile(file)}
                      type="button"
                    >
                      {previewable ? <FileCode2 aria-hidden="true" className="shrink-0 text-brand-100" size={17} /> : <Binary aria-hidden="true" className="shrink-0 text-slate-500" size={17} />}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-xs text-slate-200">{file.path}</span>
                        <span className="mt-1 block text-[11px] text-slate-500">{formatImportBytes(file.sizeBytes)} · {file.sha256.slice(0, 12)}</span>
                      </span>
                      <span className="shrink-0 text-[11px] text-slate-500">{previewable ? "查看源码" : "仅摘要"}</span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className="mt-5 text-sm leading-6 text-slate-400">阻断或失败的导入不会保留上传文件字节。</p>
            )}
          </section>

          {selectedPath ? (
            <section className="mt-5 min-w-0 rounded-lg border border-white/10 bg-ink-950/78" aria-labelledby="import-preview-heading">
              <div className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
                <h2 className="min-w-0 truncate font-mono text-xs font-semibold text-white" id="import-preview-heading">{selectedPath}</h2>
                <button className="shrink-0 text-xs font-semibold text-slate-400 hover:text-white" onClick={() => { setSelectedPath(""); setPreview(""); }} type="button">关闭预览</button>
              </div>
              <pre className="max-h-[36rem] overflow-auto p-4 text-xs leading-6 text-slate-200"><code>{preview}</code></pre>
            </section>
          ) : null}

          {replacement?.required ? (
            <section className="mt-5 rounded-lg border border-amber-300/25 bg-amber-300/[0.055] p-4 sm:p-5" aria-labelledby="replacement-heading">
              <div className="flex items-start gap-3">
                <RefreshCw aria-hidden="true" className="mt-0.5 shrink-0 text-amber-100" size={18} />
                <div>
                  <h2 className="font-semibold text-white" id="replacement-heading">将替换已安装的同名本地 Skill</h2>
                  <p className="mt-1 text-sm leading-6 text-slate-300">稳定 Skill ID 会保留，但旧摘要授权会失效。确认前请核对下面的文件变化。</p>
                </div>
              </div>
              <div className="mt-4 space-y-3">
                {replacement.changes.map((change) => (
                  <details className="rounded-lg bg-ink-950/62 p-3" key={`${change.status}-${change.path}`}>
                    <summary className="cursor-pointer break-all text-sm font-semibold text-slate-200">
                      {changeLabel(change)} · {change.path} · {change.kind === "binary" ? "二进制摘要" : "文本"}
                    </summary>
                    {change.diff ? <pre className="mt-3 max-h-80 overflow-auto border-t border-white/10 pt-3 text-xs leading-5 text-slate-300"><code>{change.diff}</code></pre> : (
                      <p className="mt-3 break-all border-t border-white/10 pt-3 font-mono text-[11px] leading-5 text-slate-500">
                        {change.oldSha256 ? `旧 ${change.oldSha256}` : "无旧文件"}<br />{change.newSha256 ? `新 ${change.newSha256}` : "文件将删除"}
                      </p>
                    )}
                  </details>
                ))}
              </div>
            </section>
          ) : null}
        </div>

        <aside className="space-y-5">
          <section className="rounded-lg border border-white/10 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              {blocked ? <ShieldAlert aria-hidden="true" className="text-rose-100" size={20} /> : <ShieldCheck aria-hidden="true" className="text-emerald-100" size={20} />}
              <h2 className="text-base font-semibold text-white">风险与运行状态</h2>
            </div>
            {receipt ? (
              <>
                <dl className="mt-4 space-y-3 text-sm">
                  <div className="flex items-start justify-between gap-4"><dt className="text-slate-500">安装策略</dt><dd className="text-right font-semibold text-slate-200">{receipt.installPolicy === "allow" ? "允许" : receipt.installPolicy === "confirm" ? "确认后允许" : "阻断"}</dd></div>
                  <div className="flex items-start justify-between gap-4"><dt className="text-slate-500">兼容性</dt><dd className="text-right font-semibold text-slate-200">{receipt.compatibilityStatus === "portable" ? "可移植" : receipt.compatibilityStatus === "conditional" ? "条件兼容" : "不支持"}</dd></div>
                  <div className="flex items-start justify-between gap-4"><dt className="text-slate-500">Router</dt><dd className="text-right font-semibold text-slate-200">{receipt.routerEligible ? "可纳入" : "不会纳入"}</dd></div>
                  <div className="flex items-start justify-between gap-4"><dt className="text-slate-500">脚本</dt><dd className="text-right font-semibold text-slate-200">{receipt.summary.scriptCount} 个</dd></div>
                  <div className="flex items-start justify-between gap-4"><dt className="text-slate-500">被动资源</dt><dd className="text-right font-semibold text-slate-200">{receipt.summary.opaqueResourceCount} 个</dd></div>
                </dl>
                <button className="mt-5 min-h-10 w-full rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06]" onClick={() => setShowTrust(true)} type="button">查看完整信任凭据</button>
              </>
            ) : (
              <p className="mt-3 text-sm leading-6 text-rose-100">没有可用信任凭据，不能安装或激活。</p>
            )}
          </section>

          {receipt?.findings.length ? (
            <section className="rounded-lg border border-white/10 bg-white/[0.035] p-5">
              <h2 className="text-base font-semibold text-white">主要发现</h2>
              <ul className="mt-3 space-y-3">
                {receipt.findings.slice(0, 8).map((finding, index) => (
                  <li className="text-xs leading-5 text-slate-300" key={`${finding.code}-${finding.path || ""}-${index}`}>
                    <code className={finding.severity === "critical" ? "text-rose-100" : "text-amber-100"}>{finding.code}</code>
                    <span className="ml-2">{finding.message}</span>
                    {finding.path ? <span className="mt-1 block break-all text-slate-500">{finding.path}{finding.line ? `:${finding.line}` : ""}</span> : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="rounded-lg border border-white/10 bg-surface-900/80 p-5">
            <h2 className="text-base font-semibold text-white">下一步</h2>
            {record.state === "installed" ? (
              <div className="mt-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-emerald-100"><CheckCircle2 aria-hidden="true" size={18} />当前摘要已安装</div>
                <p className="mt-2 break-all font-mono text-[11px] leading-5 text-slate-500">{record.packageDigest}</p>
                <Link className="mt-4 inline-flex min-h-11 w-full items-center justify-center rounded-full bg-emerald-300 px-4 text-sm font-semibold text-ink-950" to="/skills?tab=installed">管理已安装 Skill</Link>
              </div>
            ) : blocked ? (
              <p className="mt-3 text-sm leading-6 text-rose-100">此导入已确定恶意、扫描不完整或无法形成 Skill 包，只能查看原因并删除。</p>
            ) : replacementBlocked ? (
              <p className="mt-3 text-sm leading-6 text-rose-100">同名 Skill 来自其他来源或现有目录摘要不匹配，禁止覆盖。</p>
            ) : (
              <>
                <p className="mt-3 text-sm leading-6 text-slate-300">
                  {needsConfirmation
                    ? receipt?.routerEligible
                      ? "核对风险和替换差异后确认安装。安装会为当前凭据写入本机激活授权。"
                      : "可以确认风险后安装，但该 Skill 不会进入 Router 自动发现。"
                    : "当前凭据允许直接安装，安装后仍由 Server 在每次激活时复核摘要和运行能力。"}
                </p>
                <button
                  className="mt-5 min-h-11 w-full rounded-full bg-hire-300 px-4 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                  disabled={!canInstall || busy === "install"}
                  onClick={() => needsConfirmation ? setShowTrust(true) : void install(false)}
                  type="button"
                >
                  {busy === "install" ? "安装中…" : replacement?.required ? "核对差异并替换" : needsConfirmation ? "核对风险并安装" : "安装当前版本"}
                </button>
              </>
            )}

            <div className="mt-4 grid gap-2">
              {record.packageDigest && record.trustFingerprint && record.state !== "installed" ? (
                <button className="inline-flex min-h-10 items-center justify-center gap-2 rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:opacity-45" disabled={Boolean(busy)} onClick={() => void rescan()} type="button"><RefreshCw aria-hidden="true" size={15} />{busy === "rescan" ? "重扫中…" : "重新扫描"}</button>
              ) : null}
              {record.state !== "installed" ? (
                <button className="inline-flex min-h-10 items-center justify-center gap-2 rounded-full border border-rose-300/25 px-4 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/10 disabled:opacity-45" disabled={Boolean(busy)} onClick={() => void removeImport()} type="button"><Trash2 aria-hidden="true" size={15} />{busy === "delete" ? "删除中…" : "删除导入记录"}</button>
              ) : null}
            </div>
          </section>

          <section className="rounded-lg bg-black/15 p-4 font-mono text-[11px] leading-5 text-slate-500">
            <p className="break-all">import {record.importId}</p>
            <p className="mt-1 break-all">package {record.packageDigest || "unavailable"}</p>
            <p className="mt-1 break-all">trust {record.trustFingerprint || "unavailable"}</p>
          </section>
        </aside>
      </section>
    </PageContainer>
  );
}
