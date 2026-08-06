import {
  CheckCircle2,
  CircleAlert,
  Copy,
  FolderPlus,
  Link2,
  Pencil,
  RefreshCw,
  Trash2,
  Unplug,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type {
  CodingProjectHostCapability,
  CodingProjectHostPairing,
  CodingProjectHostStatus,
  CodingProjectSelection,
  CodingProjectSummary,
} from "../types/coding";
import {
  CodingApiError,
  createCodingProjectHostPairing,
  createCodingProjectSelection,
  getCodingProjectHost,
  getCodingProjectSelection,
  removeCodingProject,
  renameCodingProject,
  revokeCodingProjectHost,
} from "../utils/codingApi";

type Action =
  | "idle"
  | "checking"
  | "pairing"
  | "selecting"
  | "renaming"
  | "removing"
  | "revoking";

interface CodingProjectHostPanelProps {
  capability?: CodingProjectHostCapability;
  locked: boolean;
  onProjectsChanged: (preferredProjectId?: string) => Promise<void>;
  selectedProject: CodingProjectSummary | null;
}

const hostError: Record<string, string> = {
  project_active: "当前任务正在使用这个项目，请先处理或结束当前任务。",
  project_host_already_paired: "已有一台助手完成连接，请先移除原有授权。",
  project_host_offline: "本地项目助手尚未连接，请先在 Windows 中打开它。",
  project_host_request_timeout: "等待本地项目助手响应的时间过长，请确认助手仍在运行。",
  project_host_unavailable: "本地项目助手暂时不可用，请打开助手后重新检查。",
  project_name_invalid: "项目名称不能为空，也不能包含控制字符。",
  project_not_found: "这个项目已被移除，请刷新项目列表。",
  project_selection_cancelled: "你已取消选择文件夹，项目列表没有变化。",
  project_selection_expired: "本次选择已过期，请重新添加项目。",
};

function messageFor(error: unknown) {
  if (error instanceof CodingApiError) {
    return hostError[error.code] ?? "操作没有完成，请检查本地项目助手后重试。";
  }
  return "暂时无法连接本地项目助手，请稍后重试。";
}

export default function CodingProjectHostPanel({
  capability,
  locked,
  onProjectsChanged,
  selectedProject,
}: CodingProjectHostPanelProps) {
  const [status, setStatus] = useState<CodingProjectHostStatus | null>(null);
  const [pairing, setPairing] = useState<CodingProjectHostPairing | null>(null);
  const [selection, setSelection] = useState<CodingProjectSelection | null>(null);
  const [action, setAction] = useState<Action>("idle");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [name, setName] = useState("");
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [confirmRevoke, setConfirmRevoke] = useState(false);

  const refreshStatus = useCallback(async () => {
    if (!capability?.enabled) return;
    setAction("checking");
    setError("");
    try {
      setStatus(await getCodingProjectHost());
    } catch (requestError) {
      setError(messageFor(requestError));
    } finally {
      setAction("idle");
    }
  }, [capability?.enabled]);

  useEffect(() => {
    if (!capability?.enabled) return;
    void refreshStatus();
  }, [capability?.available, capability?.enabled, capability?.paired, refreshStatus]);

  useEffect(() => {
    if (!pairing) return;
    let active = true;
    let timer = 0;
    const poll = async () => {
      if (!active) return;
      if (Date.now() / 1000 >= pairing.expires_at) {
        setPairing(null);
        setError("连接码已过期，请重新生成。 ");
        return;
      }
      try {
        const next = await getCodingProjectHost();
        if (!active) return;
        setStatus(next);
        if (next.available) {
          setPairing(null);
          setNotice("本地项目助手已连接，可以添加项目。 ");
          await onProjectsChanged();
          return;
        }
      } catch {
        // The next poll can recover from a short Server restart.
      }
      timer = window.setTimeout(() => void poll(), 1_500);
    };
    timer = window.setTimeout(() => void poll(), 600);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [onProjectsChanged, pairing]);

  useEffect(() => {
    if (!selection || !["pending", "dispatched"].includes(selection.status)) return;
    let active = true;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await getCodingProjectSelection(selection.request_id);
        if (!active) return;
        setSelection(next);
        if (next.status === "completed" && next.project_id) {
          setAction("idle");
          setNotice("项目已添加，你可以从上方列表选择它。 ");
          await onProjectsChanged(next.project_id);
          return;
        }
        if (next.status === "failed" || next.status === "expired") {
          setAction("idle");
          setError(hostError[next.error ?? ""] ?? "没有添加项目，请重新选择。 ");
          return;
        }
        timer = window.setTimeout(() => void poll(), 900);
      } catch (requestError) {
        if (!active) return;
        setAction("idle");
        setError(messageFor(requestError));
      }
    };
    timer = window.setTimeout(() => void poll(), 600);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [onProjectsChanged, selection]);

  if (!capability?.enabled) return null;

  const connected = status?.available === true;
  const selectedHostProject = selectedProject?.kind === "host_git";
  const busy = action !== "idle";

  const createPairing = async () => {
    setAction("pairing");
    setError("");
    setNotice("");
    try {
      setPairing(await createCodingProjectHostPairing());
    } catch (requestError) {
      setError(messageFor(requestError));
    } finally {
      setAction("idle");
    }
  };

  const addProject = async () => {
    setAction("selecting");
    setError("");
    setNotice("请在 Windows 弹出的窗口中选择一个干净的 Git 项目。 ");
    try {
      const next = await createCodingProjectSelection();
      setSelection(next);
      if (next.status === "completed" && next.project_id) {
        setAction("idle");
        setNotice("项目已添加，你可以从上方列表选择它。 ");
        await onProjectsChanged(next.project_id);
      } else if (next.status === "failed" || next.status === "expired") {
        setAction("idle");
        setError(hostError[next.error ?? ""] ?? "没有添加项目，请重新选择。 ");
      }
    } catch (requestError) {
      setAction("idle");
      setError(messageFor(requestError));
    }
  };

  const saveName = async () => {
    if (!selectedHostProject || !name.trim()) return;
    setAction("renaming");
    setError("");
    try {
      await renameCodingProject(selectedProject.id, name.trim());
      setEditingName(false);
      setNotice("项目名称已更新。 ");
      await onProjectsChanged(selectedProject.id);
    } catch (requestError) {
      setError(messageFor(requestError));
    } finally {
      setAction("idle");
    }
  };

  const removeProject = async () => {
    if (!selectedHostProject) return;
    setAction("removing");
    setError("");
    try {
      await removeCodingProject(selectedProject.id);
      setConfirmRemove(false);
      setNotice("项目授权已移除，本地文件没有被删除。 ");
      await onProjectsChanged("modelmirror");
    } catch (requestError) {
      setError(messageFor(requestError));
    } finally {
      setAction("idle");
    }
  };

  const revokeHost = async () => {
    if (!status?.host_id) return;
    setAction("revoking");
    setError("");
    try {
      await revokeCodingProjectHost(status.host_id);
      setConfirmRevoke(false);
      setPairing(null);
      setStatus(await getCodingProjectHost());
      setNotice("助手授权已移除，本地项目和文件没有被改变。 ");
      await onProjectsChanged("modelmirror");
    } catch (requestError) {
      setError(messageFor(requestError));
    } finally {
      setAction("idle");
    }
  };

  return (
    <div className="mt-4 border-t border-white/10 pt-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {connected ? (
              <CheckCircle2 aria-hidden="true" className="text-emerald-200" size={17} />
            ) : (
              <Link2 aria-hidden="true" className="text-slate-300" size={17} />
            )}
            <h3 className="text-sm font-semibold text-white">连接本地项目助手</h3>
          </div>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-400">
            {connected
              ? "助手已连接。选择文件夹时，项目路径只保存在你的电脑上。"
              : status?.paired
                ? "已记住这台电脑，请打开 Windows 本地项目助手，然后检查连接。"
                : "打开 Windows 本地项目助手并输入一次性连接码，之后即可选择你允许访问的项目。"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {connected ? (
            <button
              className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-cyan-300 px-3 text-sm font-semibold text-ink-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={busy || locked}
              onClick={() => void addProject()}
              type="button"
            >
              <FolderPlus aria-hidden="true" size={16} />
              添加本地项目
            </button>
          ) : status?.paired ? (
            <button
              className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-white/15 px-3 text-sm font-semibold text-slate-200 transition hover:border-cyan-300/40 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              disabled={busy}
              onClick={() => void refreshStatus()}
              type="button"
            >
              <RefreshCw aria-hidden="true" size={16} />
              重新连接
            </button>
          ) : (
            <button
              className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-cyan-300/35 bg-cyan-300/10 px-3 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/15 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={busy || pairing !== null}
              onClick={() => void createPairing()}
              type="button"
            >
              <Link2 aria-hidden="true" size={16} />
              生成连接码
            </button>
          )}
        </div>
      </div>

      {pairing ? (
        <div className="mt-3 flex flex-col gap-3 rounded-lg bg-cyan-300/10 p-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs text-cyan-100/80">在助手中输入，5 分钟内有效</p>
            <p className="mt-1 font-mono text-xl font-semibold tracking-[0.16em] text-white">
              {pairing.pairing_code}
            </p>
          </div>
          <button
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-cyan-100/20 px-3 text-sm font-semibold text-cyan-50 transition hover:bg-cyan-100/10"
            onClick={() => {
              if (!navigator.clipboard) {
                setError("无法自动复制，请手动输入上方连接码。 ");
                return;
              }
              void navigator.clipboard.writeText(pairing.pairing_code).then(
                () => setNotice("连接码已复制。 "),
                () => setError("无法自动复制，请手动输入上方连接码。 "),
              );
            }}
            type="button"
          >
            <Copy aria-hidden="true" size={16} />
            复制连接码
          </button>
        </div>
      ) : null}

      {action === "selecting" ? (
        <p className="mt-3 text-xs leading-5 text-cyan-100" role="status">
          等待你在 Windows 中选择文件夹。页面会在选择完成后自动更新。
        </p>
      ) : null}

      {selectedHostProject && connected ? (
        <div className="mt-4 flex flex-col gap-3 border-t border-white/10 pt-4">
          <div className="flex flex-wrap gap-2">
            <button
              className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/15 px-3 text-xs font-semibold text-slate-200 transition hover:border-white/25 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              disabled={busy || locked}
              onClick={() => {
                setName(selectedProject.name);
                setEditingName(true);
                setConfirmRemove(false);
              }}
              type="button"
            >
              <Pencil aria-hidden="true" size={14} />
              重命名项目
            </button>
            <button
              className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/15 px-3 text-xs font-semibold text-slate-300 transition hover:border-rose-300/35 hover:text-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={busy || locked}
              onClick={() => {
                setConfirmRemove(true);
                setEditingName(false);
              }}
              type="button"
            >
              <Trash2 aria-hidden="true" size={14} />
              移除项目授权
            </button>
          </div>
          {editingName ? (
            <div className="flex flex-col gap-2 sm:flex-row">
              <label className="sr-only" htmlFor="coding-project-name">项目名称</label>
              <input
                className="min-h-10 min-w-0 flex-1 rounded-lg border border-white/15 bg-ink-950/70 px-3 text-sm text-white outline-none focus:border-cyan-300/70 focus:ring-4 focus:ring-cyan-300/10"
                id="coding-project-name"
                maxLength={80}
                onChange={(event) => setName(event.target.value)}
                value={name}
              />
              <div className="flex gap-2">
                <button className="min-h-10 rounded-lg bg-cyan-300 px-3 text-sm font-semibold text-ink-950 disabled:opacity-50" disabled={busy || !name.trim()} onClick={() => void saveName()} type="button">保存名称</button>
                <button className="min-h-10 rounded-lg px-3 text-sm font-semibold text-slate-300 hover:text-white" onClick={() => setEditingName(false)} type="button">取消</button>
              </div>
            </div>
          ) : null}
          {confirmRemove ? (
            <div className="rounded-lg bg-rose-300/10 p-3 text-xs leading-5 text-rose-50">
              <p>只会移除 ModelMirror 的访问授权，不会删除项目文件。</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button className="min-h-9 rounded-lg bg-rose-200 px-3 font-semibold text-rose-950 disabled:opacity-50" disabled={busy} onClick={() => void removeProject()} type="button">确认移除授权</button>
                <button className="min-h-9 rounded-lg px-3 font-semibold text-rose-100" onClick={() => setConfirmRemove(false)} type="button">继续保留</button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {status?.paired ? (
        <div className="mt-4 border-t border-white/10 pt-3">
          {confirmRevoke ? (
            <div className="flex flex-col gap-2 text-xs leading-5 text-amber-50">
              <p>这会断开整台助手并移除所有项目访问授权，本地文件不会改变。</p>
              <div className="flex flex-wrap gap-2">
                <button className="min-h-9 rounded-lg bg-amber-200 px-3 font-semibold text-amber-950 disabled:opacity-50" disabled={busy || locked} onClick={() => void revokeHost()} type="button">确认移除助手</button>
                <button className="min-h-9 rounded-lg px-3 font-semibold text-amber-100" onClick={() => setConfirmRevoke(false)} type="button">继续保留</button>
              </div>
            </div>
          ) : (
            <button
              className="inline-flex min-h-9 items-center gap-2 text-xs font-semibold text-slate-400 transition hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              disabled={busy || locked}
              onClick={() => setConfirmRevoke(true)}
              type="button"
            >
              <Unplug aria-hidden="true" size={14} />
              移除助手授权
            </button>
          )}
        </div>
      ) : null}

      {notice ? <p className="mt-3 text-xs leading-5 text-emerald-100" role="status">{notice}</p> : null}
      {error ? (
        <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-rose-100" role="alert">
          <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0" size={15} />
          {error}
        </p>
      ) : null}
    </div>
  );
}
