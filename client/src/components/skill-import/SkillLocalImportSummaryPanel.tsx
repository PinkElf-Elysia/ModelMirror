import { ArrowRight, FileArchive, RefreshCw, Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  formatImportBytes,
  listLocalSkillImports,
  readLocalSkillImportStatus,
  SkillLocalImportApiError,
  type LocalSkillImportRecord,
  type LocalSkillImportStatus,
} from "../../utils/skillLocalImportApi";

const STATE_LABELS: Record<LocalSkillImportRecord["state"], string> = {
  scanning: "扫描中",
  ready: "可直接安装",
  confirmation_required: "等待确认",
  blocked: "已阻断",
  failed: "扫描失败",
  installed: "已安装",
  superseded: "已被替换",
  archived: "已归档",
  stale: "需要重扫",
};

function recordBytes(record: LocalSkillImportRecord) {
  return record.fileManifest.reduce((sum, file) => sum + file.sizeBytes, 0);
}

export default function SkillLocalImportSummaryPanel() {
  const [status, setStatus] = useState<LocalSkillImportStatus | null>(null);
  const [imports, setImports] = useState<LocalSkillImportRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const nextStatus = await readLocalSkillImportStatus();
      setStatus(nextStatus);
      if (nextStatus.enabled && nextStatus.available) {
        setImports((await listLocalSkillImports()).imports);
      } else {
        setImports([]);
      }
    } catch (caught) {
      setError(
        caught instanceof SkillLocalImportApiError
          ? caught.message
          : "无法读取本地导入记录。",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section aria-labelledby="local-import-summary-heading">
      <div className="flex flex-col gap-4 border-b border-white/10 pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-hire-200">私有本机来源</p>
          <h2 className="mt-1 text-2xl font-semibold text-white" id="local-import-summary-heading">
            本地 Skill 导入
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            ZIP 或文件夹会先经过确定性扫描，不调用模型、不联网，也不会执行包内脚本。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:opacity-45"
            disabled={loading}
            onClick={() => void load()}
            type="button"
          >
            <RefreshCw aria-hidden="true" className={loading ? "animate-spin motion-reduce:animate-none" : ""} size={15} />
            刷新
          </button>
          {status?.enabled && status.available ? (
            <Link
              className="inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-4 text-sm font-semibold text-ink-950 transition hover:bg-hire-200"
              to="/skills/import"
            >
              <Upload aria-hidden="true" size={16} />
              导入 Skill
            </Link>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="mt-5 rounded-lg border border-rose-300/25 bg-rose-300/10 p-4 text-sm text-rose-50" role="alert">
          {error}
        </div>
      ) : null}

      {!loading && status && (!status.enabled || !status.available) ? (
        <div className="mt-5 rounded-lg border border-amber-300/25 bg-amber-300/[0.07] p-5">
          <p className="font-semibold text-white">
            {status.enabled ? "导入存储暂不可用" : "本地导入已关闭"}
          </p>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            {status.enabled
              ? `Server 返回 ${status.errorCode || "skill_import_storage_unavailable"}，不会接受新文件。`
              : "当前实例不接受新上传、重扫或替换；已安装且凭据有效的本地 Skill 不受影响。"}
          </p>
        </div>
      ) : null}

      {loading ? (
        <div aria-label="正在读取本地导入" className="mt-5 space-y-3">
          {[0, 1].map((item) => (
            <div className="h-24 animate-pulse rounded-lg bg-white/[0.05] motion-reduce:animate-none" key={item} />
          ))}
        </div>
      ) : imports.length ? (
        <div className="mt-5 divide-y divide-white/10 border-y border-white/10">
          {imports.map((record) => (
            <Link
              className="flex min-h-20 min-w-0 items-center gap-4 py-4 transition hover:bg-white/[0.035] sm:px-3"
              key={record.importId}
              to={`/skills/import/${encodeURIComponent(record.importId)}`}
            >
              <FileArchive aria-hidden="true" className="shrink-0 text-brand-100" size={20} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="truncate font-semibold text-white">
                    {record.localSkillId || record.declaredName || "待指定 Skill ID"}
                  </h3>
                  <span className="rounded-full border border-white/10 px-2.5 py-1 text-xs font-semibold text-slate-300">
                    {STATE_LABELS[record.state]}
                  </span>
                </div>
                <p className="mt-2 truncate text-xs text-slate-500">
                  {record.fileManifest.length} 个文件 · {formatImportBytes(recordBytes(record))} · {record.transportKind === "zip" ? "ZIP" : "文件夹"}
                </p>
              </div>
              <ArrowRight aria-hidden="true" className="shrink-0 text-slate-500" size={17} />
            </Link>
          ))}
        </div>
      ) : status?.enabled && status.available ? (
        <div className="mt-5 rounded-lg border border-dashed border-white/15 px-6 py-10 text-center">
          <p className="font-semibold text-white">还没有本地导入记录</p>
          <p className="mt-2 text-sm text-slate-400">从 ZIP 或文件夹开始，扫描结果会在刷新后恢复。</p>
          <Link
            className="mt-5 inline-flex min-h-11 items-center gap-2 rounded-full bg-hire-300 px-5 text-sm font-semibold text-ink-950"
            to="/skills/import"
          >
            <Upload aria-hidden="true" size={16} />
            导入第一个 Skill
          </Link>
        </div>
      ) : null}
    </section>
  );
}
