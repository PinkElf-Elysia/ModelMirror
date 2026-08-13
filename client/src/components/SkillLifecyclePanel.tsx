import { useCallback, useEffect, useMemo, useState } from "react";
import {
  auditSkillLifecycleMigration,
  loadSkillLifecycleStates,
  loadSkillLifecycleVersions,
  migrateSkillLifecycle,
  rollbackSkillLifecycleVersion,
  type SkillLifecycleMigrationReport,
  type SkillLifecycleState,
  type SkillLifecycleStatus,
  type SkillLifecycleVersion,
  type SkillLifecycleVersionsResponse,
} from "../utils/skillLifecycleApi";

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

function formatTime(value: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

function sourceLabel(version: SkillLifecycleVersion) {
  if (version.source_kind === "git") return "Git 固定提交";
  if (version.source_kind === "local_import") return "本地导入";
  return "Creator 草稿";
}

function canRestore(version: SkillLifecycleVersion) {
  if (
    version.source_kind === "workspace_draft" &&
    version.quality_required &&
    !(
      version.quality_evidence_status === "matched" &&
      ["accepted", "eval_waived"].includes(version.quality_status || "")
    )
  ) {
    return false;
  }
  if (
    ["git", "local_import"].includes(version.source_kind) &&
    (!version.trust_evidence_frozen ||
      version.trust_status === "blocked" ||
      version.trust_compatibility_status === "unsupported")
  ) {
    return false;
  }
  return true;
}

function stateLabel(item: SkillLifecycleState) {
  if (item.status === "active") return "当前已安装";
  if (item.status === "uninstalled") return "保留恢复点";
  return "迁移受阻";
}

export default function SkillLifecyclePanel({
  focusSkillId,
  onChanged,
}: {
  focusSkillId?: string;
  onChanged?: () => void;
}) {
  const [status, setStatus] = useState<SkillLifecycleStatus | null>(null);
  const [states, setStates] = useState<SkillLifecycleState[]>([]);
  const [migration, setMigration] = useState<SkillLifecycleMigrationReport | null>(null);
  const [selectedSkillId, setSelectedSkillId] = useState(focusSkillId || "");
  const [detail, setDetail] = useState<SkillLifecycleVersionsResponse | null>(null);
  const [rollbackTarget, setRollbackTarget] = useState<SkillLifecycleVersion | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [statePayload, migrationPayload] = await Promise.all([
        loadSkillLifecycleStates(),
        auditSkillLifecycleMigration(),
      ]);
      setStatus(statePayload.status);
      setStates(statePayload.items);
      setMigration(migrationPayload);
      setSelectedSkillId((current) => {
        if (focusSkillId && statePayload.items.some((item) => item.skill_id === focusSkillId)) return focusSkillId;
        if (current && statePayload.items.some((item) => item.skill_id === current)) return current;
        return statePayload.items[0]?.skill_id || "";
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "生命周期状态加载失败");
    } finally {
      setLoading(false);
    }
  }, [focusSkillId]);

  const loadDetail = useCallback(async (skillId: string) => {
    if (!skillId) {
      setDetail(null);
      return;
    }
    setBusy(`detail:${skillId}`);
    setError("");
    try {
      setDetail(await loadSkillLifecycleVersions(skillId));
    } catch (caught) {
      setDetail(null);
      setError(caught instanceof Error ? caught.message : "版本历史加载失败");
    } finally {
      setBusy("");
    }
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    void loadDetail(selectedSkillId);
    setRollbackTarget(null);
  }, [loadDetail, selectedSkillId]);

  const selectedState = useMemo(
    () => states.find((item) => item.skill_id === selectedSkillId) ?? null,
    [selectedSkillId, states],
  );

  async function migrate() {
    if (!window.confirm("将已核验的当前安装复制到不可变版本历史。此操作不会切换或卸载 Skill，是否继续？")) return;
    setBusy("migration");
    setError("");
    setNotice("");
    try {
      const report = await migrateSkillLifecycle();
      setNotice(`迁移完成：${report.counts.migrated} 个已纳入，${report.counts.blocked} 个保留为受阻状态。`);
      await loadOverview();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "生命周期迁移失败");
    } finally {
      setBusy("");
    }
  }

  async function rollback() {
    if (!detail || !rollbackTarget) return;
    setBusy(`rollback:${rollbackTarget.version_id}`);
    setError("");
    setNotice("");
    try {
      await rollbackSkillLifecycleVersion({
        skillId: detail.state.skill_id,
        versionId: rollbackTarget.version_id,
        expectedStateRevision: detail.state.revision,
        expectedCurrentVersionId: detail.state.current_version_id,
        expectedPackageDigest: rollbackTarget.package_digest,
      });
      setNotice(detail.state.status === "uninstalled" ? `已从版本 ${rollbackTarget.ordinal} 恢复安装。` : `已切换到版本 ${rollbackTarget.ordinal}。`);
      setRollbackTarget(null);
      await loadOverview();
      await loadDetail(detail.state.skill_id);
      onChanged?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "版本切换失败");
    } finally {
      setBusy("");
    }
  }

  if (loading && !status) {
    return (
      <section aria-busy="true" className="rounded-lg border border-white/10 bg-white/[0.035] p-5">
        <div className="h-5 w-40 animate-pulse rounded bg-white/10" />
        <div className="mt-4 h-20 animate-pulse rounded-lg bg-white/[0.06]" />
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.035] p-4 sm:p-5" id="skill-lifecycle-panel">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-white">版本与恢复</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">每次替换都会保存不可变版本。卸载保留恢复点，切换版本只影响之后启动的运行。</p>
        </div>
        <button className="min-h-11 rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200 disabled:opacity-45" disabled={Boolean(busy)} onClick={() => void loadOverview()} type="button">刷新版本状态</button>
      </div>

      {error ? <p className="mt-4 rounded-lg border border-rose-300/25 bg-rose-300/10 p-3 text-sm text-rose-100" role="alert">{error}</p> : null}
      {notice ? <p className="mt-4 rounded-lg border border-emerald-300/25 bg-emerald-300/10 p-3 text-sm text-emerald-100" role="status">{notice}</p> : null}
      {status && !status.available ? <p className="mt-4 rounded-lg border border-rose-300/25 bg-rose-300/10 p-3 text-sm text-rose-100">生命周期 Store 不可用，版本切换已失败关闭。原始数据不会被覆盖。</p> : null}
      {status && status.available && !status.enabled ? <p className="mt-4 rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-sm text-amber-100">生命周期功能已由服务端关闭，当前只显示审计信息。</p> : null}

      {status ? (
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 border-y border-white/10 py-3 text-xs text-slate-400">
          <span><strong className="text-slate-100">{status.counts.skills}</strong> 个 Skill</span>
          <span><strong className="text-slate-100">{status.counts.versions}</strong> 个版本</span>
          <span><strong className="text-slate-100">{formatBytes(status.storageBytes)}</strong> / {formatBytes(status.limits.storageBytes)}</span>
          {status.pendingTransactions ? <span className="text-amber-100">{status.pendingTransactions} 个事务待恢复</span> : null}
        </div>
      ) : null}

      {migration && migration.counts.eligible > 0 ? (
        <div className="mt-4 flex flex-col gap-3 rounded-lg border border-sky-300/25 bg-sky-300/[0.08] p-4 sm:flex-row sm:items-center sm:justify-between">
          <div><p className="text-sm font-semibold text-sky-100">{migration.counts.eligible} 个现有安装可纳入版本管理</p><p className="mt-1 text-xs leading-5 text-sky-100/75">只复制已核验字节，不改变当前安装或激活状态。</p></div>
          <button className="min-h-11 rounded-full bg-sky-200 px-4 text-sm font-semibold text-ink-950 transition hover:bg-sky-100 disabled:opacity-45" disabled={busy === "migration" || !status?.enabled} onClick={() => void migrate()} type="button">{busy === "migration" ? "正在纳入..." : "纳入版本管理"}</button>
        </div>
      ) : null}

      <div className="mt-5 grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div aria-label="Skill 生命周期列表" className="space-y-2">
          {states.length === 0 ? <p className="rounded-lg border border-dashed border-white/15 p-5 text-center text-sm text-slate-500">暂无版本历史。安装或迁移首个受支持 Skill 后会显示在这里。</p> : null}
          {states.map((item) => {
            const selected = item.skill_id === selectedSkillId;
            return (
              <button aria-pressed={selected} className={`min-h-16 w-full rounded-lg border p-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200 ${selected ? "border-hire-300/45 bg-hire-300/10" : "border-white/10 bg-ink-950/45 hover:bg-white/[0.06]"}`} key={item.skill_id} onClick={() => setSelectedSkillId(item.skill_id)} type="button">
                <span className="block break-all text-sm font-semibold text-white">{item.skill_id}</span>
                <span className={`mt-1 block text-xs ${item.status === "migration_blocked" ? "text-rose-200" : item.status === "uninstalled" ? "text-amber-100" : "text-emerald-100"}`}>{stateLabel(item)} · {item.version_ids.length} 个版本</span>
              </button>
            );
          })}
        </div>

        <div className="min-w-0">
          {!selectedState ? <div className="grid min-h-48 place-items-center rounded-lg border border-dashed border-white/15 text-sm text-slate-500">选择一个 Skill 查看版本</div> : null}
          {busy.startsWith("detail:") && !detail ? <div className="h-48 animate-pulse rounded-lg bg-white/[0.05]" /> : null}
          {detail ? (
            <div>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><h3 className="break-all text-lg font-semibold text-white">{detail.state.skill_id}</h3><p className="mt-1 text-xs text-slate-500">状态 revision {detail.state.revision} · {stateLabel(detail.state)}</p></div>
                {detail.state.recovery_version_id ? <span className="rounded-full border border-amber-300/25 bg-amber-300/10 px-3 py-1 text-xs font-semibold text-amber-100">已保留恢复点</span> : null}
              </div>
              <div className="mt-4 space-y-3">
                {detail.versions.map((version) => {
                  const current = version.version_id === detail.state.current_version_id;
                  const recovery = version.version_id === detail.state.recovery_version_id;
                  const restorable = canRestore(version);
                  const confirming = rollbackTarget?.version_id === version.version_id;
                  return (
                    <article className="rounded-lg border border-white/10 bg-ink-950/45 p-4" key={version.version_id}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div><div className="flex flex-wrap items-center gap-2"><h4 className="text-sm font-semibold text-white">版本 {version.ordinal}</h4>{current ? <span className="rounded-full bg-emerald-300 px-2 py-0.5 text-[11px] font-semibold text-ink-950">当前</span> : null}{recovery ? <span className="rounded-full border border-amber-300/30 px-2 py-0.5 text-[11px] font-semibold text-amber-100">恢复点</span> : null}</div><p className="mt-1 text-xs text-slate-400">{sourceLabel(version)} · {formatTime(version.created_at)} · {version.file_count} 文件 · {formatBytes(version.total_bytes)}</p></div>
                        {!current ? <button className="min-h-10 rounded-full border border-hire-300/30 px-4 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/10 disabled:cursor-not-allowed disabled:opacity-40" disabled={!restorable || Boolean(busy)} onClick={() => setRollbackTarget(version)} type="button">{detail.state.status === "uninstalled" ? "恢复此版本" : "切换到此版本"}</button> : null}
                      </div>
                      <p className="mt-3 break-all font-mono text-[11px] leading-5 text-slate-500">{version.package_digest}</p>
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
                        {version.source_ref ? <span className="break-all">SHA {version.source_ref}</span> : null}
                        {version.trust_evidence_frozen ? <span className="text-emerald-100">信任凭据已冻结</span> : null}
                        {version.quality_required ? <span className={version.quality_evidence_status === "matched" ? "text-emerald-100" : "text-rose-100"}>质量证据 {version.quality_evidence_status === "matched" ? "有效" : "不可用"}</span> : null}
                        {!version.trust_router_eligible && ["git", "local_import"].includes(version.source_kind) ? <span>Router 不纳入</span> : null}
                      </div>
                      {!restorable ? <p className="mt-2 text-xs leading-5 text-rose-100">该版本缺少可验证的信任或质量证据，不能恢复。</p> : null}
                      {confirming ? (
                        <div className="mt-4 rounded-lg border border-amber-300/25 bg-amber-300/[0.08] p-3">
                          <p className="text-sm leading-6 text-amber-50">这会把全局安装切换到版本 {version.ordinal}。正在运行的任务继续使用原绑定，新运行使用切换后的版本。第三方中高风险授权仍需单独确认。</p>
                          <div className="mt-3 flex flex-wrap justify-end gap-2"><button className="min-h-10 rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200" onClick={() => setRollbackTarget(null)} type="button">取消</button><button className="min-h-10 rounded-full bg-amber-300 px-4 text-sm font-semibold text-ink-950 disabled:opacity-45" disabled={busy.startsWith("rollback:")} onClick={() => void rollback()} type="button">{busy.startsWith("rollback:") ? "正在切换..." : detail.state.status === "uninstalled" ? "确认恢复版本" : "确认切换版本"}</button></div>
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
