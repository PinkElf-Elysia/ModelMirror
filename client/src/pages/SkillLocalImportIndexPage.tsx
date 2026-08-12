import {
  Archive,
  ArrowLeft,
  ArrowRight,
  FileArchive,
  FolderOpen,
  RefreshCw,
  ShieldCheck,
  Upload,
} from "lucide-react";
import {
  type ChangeEvent,
  type InputHTMLAttributes,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, useNavigate } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import {
  formatImportBytes,
  listLocalSkillImports,
  readLocalSkillImportStatus,
  SkillLocalImportApiError,
  type LocalSkillImportRecord,
  type LocalSkillImportStatus,
  uploadLocalSkillFolder,
  uploadLocalSkillZip,
} from "../utils/skillLocalImportApi";

const STATE_LABELS: Record<LocalSkillImportRecord["state"], string> = {
  scanning: "扫描中",
  ready: "可直接安装",
  confirmation_required: "需要确认",
  blocked: "已阻断",
  failed: "扫描失败",
  installed: "已安装",
  superseded: "已被替换",
  archived: "已归档",
  stale: "需要重扫",
};

type FolderInputProps = InputHTMLAttributes<HTMLInputElement> & {
  directory?: string;
  webkitdirectory?: string;
};

const FOLDER_INPUT_PROPS: FolderInputProps = {
  directory: "",
  webkitdirectory: "",
};

function formatTime(value: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

export default function SkillLocalImportIndexPage() {
  const navigate = useNavigate();
  const zipInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<LocalSkillImportStatus | null>(null);
  const [imports, setImports] = useState<LocalSkillImportRecord[]>([]);
  const [localSkillId, setLocalSkillId] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState<"zip" | "folder" | "">("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const nextStatus = await readLocalSkillImportStatus();
      setStatus(nextStatus);
      if (nextStatus.enabled && nextStatus.available) {
        const response = await listLocalSkillImports();
        setImports(response.imports);
      } else {
        setImports([]);
      }
    } catch (caught) {
      setError(
        caught instanceof SkillLocalImportApiError
          ? caught.message
          : "无法读取本地 Skill 导入状态。",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function uploadZip(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading("zip");
    setError("");
    try {
      const record = await uploadLocalSkillZip(file, localSkillId);
      navigate(`/skills/import/${encodeURIComponent(record.importId)}`);
    } catch (caught) {
      setError(
        caught instanceof SkillLocalImportApiError
          ? caught.message
          : "ZIP 导入失败。",
      );
    } finally {
      setUploading("");
    }
  }

  async function uploadFolder(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (!files.length) return;
    setUploading("folder");
    setError("");
    try {
      const record = await uploadLocalSkillFolder(files, localSkillId);
      navigate(`/skills/import/${encodeURIComponent(record.importId)}`);
    } catch (caught) {
      setError(
        caught instanceof SkillLocalImportApiError
          ? caught.message
          : "文件夹导入失败。",
      );
    } finally {
      setUploading("");
    }
  }

  const disabled = !status?.enabled || !status.available || Boolean(uploading);

  return (
    <PageContainer activeResource="skills" hideSidebar maxWidthClassName="max-w-[1260px]">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <Link
          className="inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white"
          to="/skills"
        >
          <ArrowLeft aria-hidden="true" size={16} />
          Skill 货架
        </Link>
        <span className="inline-flex items-center gap-2 rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100">
          <ShieldCheck aria-hidden="true" size={14} />
          本机扫描，不执行脚本
        </span>
      </div>

      <header className="border-y border-hire-300/20 py-8 sm:py-10">
        <div className="flex max-w-4xl items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-hire-300/10 text-hire-100">
            <Upload aria-hidden="true" size={23} />
          </div>
          <div>
            <p className="text-sm font-semibold text-hire-200">私有控制台</p>
            <h1 className="mt-2 text-3xl font-semibold text-white sm:text-4xl">
              导入本地 Skill
            </h1>
            <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-300 sm:text-base">
              选择一个 ZIP 或文件夹。服务端会规范化路径、扫描凭据和脚本、生成不可变信任凭据，再由你决定是否安装。导入不会调用模型或网络。
            </p>
          </div>
        </div>
      </header>

      {error ? (
        <div className="mt-6 rounded-lg border border-rose-300/25 bg-rose-300/10 p-4 text-sm leading-6 text-rose-50" role="alert">
          {error}
        </div>
      ) : null}

      {!loading && status && (!status.enabled || !status.available) ? (
        <section className="mt-8 rounded-lg border border-amber-300/25 bg-amber-300/[0.07] p-6">
          <h2 className="text-xl font-semibold text-white">
            {status.enabled ? "导入存储暂不可用" : "本地导入已关闭"}
          </h2>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            {status.enabled
              ? `服务端返回 ${status.errorCode || "skill_import_storage_unavailable"}，不会接受新文件。已安装 Skill 的运行不受影响。`
              : "当前实例不接受新上传、重扫或替换。已安装且凭据仍有效的本地 Skill 可以继续运行。"}
          </p>
        </section>
      ) : null}

      <section className="mt-8 grid gap-7 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6">
          <div className="flex items-start gap-3">
            <Archive aria-hidden="true" className="mt-0.5 text-brand-100" size={20} />
            <div>
              <h2 className="text-xl font-semibold text-white">选择传输方式</h2>
              <p className="mt-1 text-sm leading-6 text-slate-400">
                每次只导入一个 Skill 根目录。唯一一层包装目录会自动剥离。
              </p>
            </div>
          </div>

          <label className="mt-6 block" htmlFor="local-skill-id">
            <span className="text-sm font-semibold text-slate-200">本地 Skill ID（可选）</span>
            <span className="mt-1 block text-xs leading-5 text-slate-500">
              默认使用 SKILL.md 中合法的 kebab-case name。名称冲突或无效时再填写独立 ID，不会修改包内文件。
            </span>
            <input
              className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/75 px-4 text-sm text-white placeholder:text-slate-500 focus:border-hire-300/55 focus:outline-none"
              id="local-skill-id"
              maxLength={64}
              onChange={(event) => setLocalSkillId(event.target.value)}
              pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
              placeholder="例如 incident-review-local"
              value={localSkillId}
            />
          </label>

          <div className="mt-6 flex flex-col gap-3 sm:flex-row">
            <button
              className="inline-flex min-h-12 flex-1 items-center justify-center gap-2 rounded-lg bg-hire-300 px-4 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
              disabled={disabled}
              onClick={() => zipInput.current?.click()}
              type="button"
            >
              <FileArchive aria-hidden="true" size={18} />
              {uploading === "zip" ? "正在扫描 ZIP…" : "选择 ZIP"}
            </button>
            <button
              className="inline-flex min-h-12 flex-1 items-center justify-center gap-2 rounded-lg border border-white/15 px-4 text-sm font-semibold text-slate-100 transition hover:border-hire-300/35 hover:bg-hire-300/10 disabled:cursor-not-allowed disabled:opacity-45"
              disabled={disabled}
              onClick={() => folderInput.current?.click()}
              type="button"
            >
              <FolderOpen aria-hidden="true" size={18} />
              {uploading === "folder" ? "正在扫描文件夹…" : "选择文件夹"}
            </button>
          </div>
          <input
            accept=".zip,application/zip"
            className="sr-only"
            onChange={(event) => void uploadZip(event)}
            ref={zipInput}
            type="file"
          />
          <input
            {...FOLDER_INPUT_PROPS}
            className="sr-only"
            multiple
            onChange={(event) => void uploadFolder(event)}
            ref={folderInput}
            type="file"
          />
          <p aria-live="polite" className="mt-4 text-xs leading-5 text-slate-500">
            {uploading
              ? "文件正在发送到本机 Server 并进行确定性扫描。页面不会保存文件副本。"
              : "ZIP 最大 64 MiB；展开后最多 500 个文件、50 MiB，单文件不超过 10 MiB。"}
          </p>
        </div>

        <aside className="rounded-lg border border-white/10 bg-white/[0.035] p-5">
          <h2 className="text-base font-semibold text-white">扫描边界</h2>
          <ul className="mt-4 space-y-3 text-sm leading-6 text-slate-300">
            <li>拒绝路径穿越、链接、设备项、嵌套归档和未知二进制。</li>
            <li>Python 与 JavaScript 只做静态语法检查，不执行脚本。</li>
            <li>PDF、图片、字体、音视频按 magic 与扩展名核对后原样保留。</li>
            <li>秘密或扫描不完整会阻断；其他风险可在提示后确认安装。</li>
          </ul>
          {status ? (
            <p className="mt-5 border-t border-white/10 pt-4 font-mono text-[11px] leading-5 text-slate-500">
              {status.scannerVersion}
            </p>
          ) : null}
        </aside>
      </section>

      <section className="mt-10" aria-labelledby="local-import-history-heading">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-4">
          <div>
            <h2 className="text-xl font-semibold text-white" id="local-import-history-heading">
              最近导入
            </h2>
            <p className="mt-1 text-sm text-slate-400">刷新后从本机 Import Store 恢复。</p>
          </div>
          <button
            className="inline-flex min-h-10 items-center gap-2 rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:opacity-45"
            disabled={loading}
            onClick={() => void load()}
            type="button"
          >
            <RefreshCw aria-hidden="true" className={loading ? "animate-spin motion-reduce:animate-none" : ""} size={15} />
            刷新列表
          </button>
        </div>

        {loading ? (
          <div className="mt-5 space-y-3" aria-label="正在读取本地导入">
            {[0, 1].map((item) => <div className="h-24 animate-pulse rounded-lg bg-white/[0.05] motion-reduce:animate-none" key={item} />)}
          </div>
        ) : imports.length ? (
          <div className="mt-5 divide-y divide-white/10 border-y border-white/10">
            {imports.map((item) => (
              <Link
                className="flex min-w-0 items-center gap-4 py-4 transition hover:bg-white/[0.035] sm:px-3"
                key={item.importId}
                to={`/skills/import/${encodeURIComponent(item.importId)}`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate font-semibold text-white">{item.localSkillId || item.declaredName || "待指定 Skill ID"}</h3>
                    <span className="rounded-full border border-white/10 px-2.5 py-1 text-xs font-semibold text-slate-300">{STATE_LABELS[item.state]}</span>
                  </div>
                  <p className="mt-2 truncate text-xs text-slate-500">
                    {item.fileManifest.length} 个文件 · {formatImportBytes(item.fileManifest.reduce((sum, file) => sum + file.sizeBytes, 0))} · {formatTime(item.updatedAt)}
                  </p>
                </div>
                <ArrowRight aria-hidden="true" className="shrink-0 text-slate-500" size={17} />
              </Link>
            ))}
          </div>
        ) : (
          <div className="mt-5 rounded-lg border border-dashed border-white/15 px-6 py-10 text-center">
            <p className="font-semibold text-white">还没有本地导入记录</p>
            <p className="mt-2 text-sm text-slate-400">选择一个 ZIP 或文件夹后，扫描结果会出现在这里。</p>
          </div>
        )}
      </section>
    </PageContainer>
  );
}
