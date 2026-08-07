import { useEffect, useMemo, useState } from "react";
import type { McpWorkspacePolicy } from "../data/mcpAdaptationPlan";

export interface McpWorkspaceFile {
  file_id: string;
  relative_path: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  content_type: string;
}

export interface McpWorkspaceArtifact {
  artifact_id: string;
  filename: string;
  size_bytes: number;
  content_type: string;
  expires_at: number;
  download_url?: string;
}

export interface McpWorkspace {
  workspace_id: string;
  project_id: string;
  display_name: string;
  persistent: boolean;
  status: "uploading" | "sealed";
  files: McpWorkspaceFile[];
  artifacts: McpWorkspaceArtifact[];
  file_count: number;
  size_bytes: number;
  manifest_sha256: string;
  expires_at: number | null;
}

export interface McpApprovalRequest {
  code: "approval_required";
  message: string;
  approval_id: string;
  summary: string;
  argument_digest: string;
  expires_at: number;
}

interface McpWorkspacePanelProps {
  projectId: string;
  policy: McpWorkspacePolicy;
  configurationSettings?: Record<string, string | number | boolean>;
  credentialBindings?: Record<string, string>;
  boundWorkspaceId?: string | null;
  onBound: (workspace: McpWorkspace | null) => void;
  onApprovalRequired: (
    approval: McpApprovalRequest,
    onConfirmed: () => void,
  ) => void;
  refreshKey?: string;
}

interface PendingUpload {
  files: File[];
  rejectedPaths: string[];
  workspaceId: string;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

function enableDirectoryPicker(input: HTMLInputElement | null) {
  if (!input) return;
  input.setAttribute("webkitdirectory", "");
  input.setAttribute("directory", "");
}

function selectedFilePath(file: File) {
  return (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
}

function fileExtension(path: string) {
  const filename = path.split(/[\\/]/).pop() ?? "";
  const dotIndex = filename.lastIndexOf(".");
  return dotIndex > 0 ? filename.slice(dotIndex).toLowerCase() : "";
}

function preflightFiles(files: File[], acceptedExtensions: string[]) {
  if (!acceptedExtensions.length) {
    return { accepted: files, rejectedPaths: [] as string[] };
  }
  const allowed = new Set(acceptedExtensions.map((value) => value.toLowerCase()));
  const accepted: File[] = [];
  const rejectedPaths: string[] = [];
  files.forEach((file) => {
    const path = selectedFilePath(file);
    const extension = fileExtension(path);
    if (extension === ".zip" || allowed.has(extension)) accepted.push(file);
    else rejectedPaths.push(path);
  });
  return { accepted, rejectedPaths };
}

async function responseDetail(response: Response) {
  try {
    const body = (await response.json()) as {
      detail?: string | McpApprovalRequest;
      error?: string;
    };
    return body.detail ?? body.error ?? response.statusText;
  } catch {
    return response.statusText;
  }
}

export default function McpWorkspacePanel({
  projectId,
  policy,
  configurationSettings = {},
  credentialBindings = {},
  boundWorkspaceId = null,
  onBound,
  onApprovalRequired,
  refreshKey = "",
}: McpWorkspacePanelProps) {
  const [items, setItems] = useState<McpWorkspace[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pendingUpload, setPendingUpload] = useState<PendingUpload | null>(null);

  const selected = useMemo(
    () => items.find((item) => item.workspace_id === selectedId) ?? null,
    [items, selectedId],
  );
  const accept = policy.accepted_extensions.length
    ? policy.accepted_extensions.join(",") + ",.zip"
    : ".zip,*";
  const acceptedTypeLabel = policy.accepted_extensions.length
    ? policy.accepted_extensions.join("、")
    : "任意文件类型";

  useEffect(() => {
    setPendingUpload(null);
    void load();
  }, [boundWorkspaceId, projectId, refreshKey]);

  async function load(preferredId = "") {
    setError("");
    const response = await fetch(`/api/mcp/catalog/${projectId}/workspaces`);
    if (!response.ok) {
      const detail = await responseDetail(response);
      setError(typeof detail === "string" ? detail : detail.message);
      return;
    }
    const payload = (await response.json()) as { items: McpWorkspace[] };
    setItems(payload.items);
    const restoredBoundId = payload.items.some(
      (item) => item.workspace_id === boundWorkspaceId && item.status === "sealed",
    )
      ? boundWorkspaceId ?? ""
      : "";
    const nextId =
      preferredId ||
      restoredBoundId ||
      selectedId ||
      payload.items.find((item) => item.status === "sealed")?.workspace_id ||
      payload.items[0]?.workspace_id ||
      "";
    setSelectedId(nextId);
    const next = payload.items.find((item) => item.workspace_id === nextId) ?? null;
    if (next?.status === "sealed" && next.workspace_id === restoredBoundId) {
      onBound(next);
    } else {
      onBound(null);
    }
  }

  async function createWorkspace() {
    setBusy("create");
    setError("");
    setMessage("");
    setPendingUpload(null);
    try {
      const response = await fetch(`/api/mcp/catalog/${projectId}/workspaces`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: displayName.trim() || "我的工作区" }),
      });
      if (!response.ok) throw new Error(String(await responseDetail(response)));
      const workspace = (await response.json()) as McpWorkspace;
      setDisplayName("");
      await load(workspace.workspace_id);
      setMessage(
        workspace.persistent
          ? "持久记忆库已创建。可以直接封存，或先导入 Markdown。"
          : "工作区已创建，请上传文件或仓库快照。",
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "创建工作区失败");
    } finally {
      setBusy("");
    }
  }

  async function uploadFiles(files: File[], skippedCount = 0) {
    if (!selected || !files.length) return;
    setBusy("upload");
    setError("");
    setMessage("");
    setPendingUpload(null);
    try {
      const form = new FormData();
      files.forEach((file) => {
        form.append("files", file);
        form.append("relative_paths", selectedFilePath(file));
      });
      const response = await fetch(
        `/api/mcp/catalog/${projectId}/workspaces/${selected.workspace_id}/files`,
        { method: "POST", body: form },
      );
      if (!response.ok) throw new Error(String(await responseDetail(response)));
      const result = (await response.json()) as { uploaded: number };
      await load(selected.workspace_id);
      setMessage(
        skippedCount
          ? `已安全导入 ${result.uploaded} 个文件，跳过 ${skippedCount} 个不支持的文件。`
          : `已安全导入 ${result.uploaded} 个文件。`,
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "文件上传失败");
    } finally {
      setBusy("");
    }
  }

  function handleFileSelection(fileList: FileList | null) {
    if (!selected || !fileList?.length) return;
    setError("");
    setMessage("");
    const { accepted, rejectedPaths } = preflightFiles(
      Array.from(fileList),
      policy.accepted_extensions,
    );
    if (!rejectedPaths.length) {
      void uploadFiles(accepted);
      return;
    }
    setPendingUpload({
      files: accepted,
      rejectedPaths,
      workspaceId: selected.workspace_id,
    });
  }

  async function sealAndBind() {
    if (!selected) return;
    setBusy("seal");
    setError("");
    try {
      let workspace = selected;
      if (workspace.status !== "sealed") {
        const sealResponse = await fetch(
          `/api/mcp/catalog/${projectId}/workspaces/${workspace.workspace_id}/seal`,
          { method: "POST" },
        );
        if (!sealResponse.ok) throw new Error(String(await responseDetail(sealResponse)));
        workspace = (await sealResponse.json()) as McpWorkspace;
      }
      const configureResponse = await fetch(
        `/api/mcp/catalog/${projectId}/configuration`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workspace_id: workspace.workspace_id,
            settings: configurationSettings,
            credential_bindings: credentialBindings,
          }),
        },
      );
      if (!configureResponse.ok) {
        throw new Error(String(await responseDetail(configureResponse)));
      }
      await load(workspace.workspace_id);
      onBound(workspace);
      setMessage("工作区已封存并绑定，可以连接适配器。已保存的连接字段和加密凭据保持不变。");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "工作区封存失败");
    } finally {
      setBusy("");
    }
  }

  async function removeWorkspace() {
    if (!selected) return;
    setBusy("delete");
    setError("");
    try {
      const response = await fetch(
        `/api/mcp/catalog/${projectId}/workspaces/${selected.workspace_id}`,
        { method: "DELETE" },
      );
      if (response.status === 409) {
        const detail = await responseDetail(response);
        if (typeof detail !== "string" && detail.code === "approval_required") {
          onApprovalRequired(detail, () => void load());
          return;
        }
      }
      if (!response.ok) throw new Error(String(await responseDetail(response)));
      onBound(null);
      await load();
      setMessage("工作区及临时产物已清理。");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "清理工作区失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <section
      aria-busy={Boolean(busy)}
      aria-labelledby={`${projectId}-workspace-heading`}
      className="relative mt-4 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.055] p-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold text-cyan-100" id={`${projectId}-workspace-heading`}>
            受控文件工作区
          </h3>
          <p className="mt-1 text-[11px] leading-5 text-slate-400">
            不上传宿主路径；输入封存后只读，产物独立保存并可清理。
          </p>
        </div>
        <span className="rounded-full border border-cyan-300/20 px-2 py-1 text-[10px] text-cyan-100">
          {policy.persistent ? "持久记忆" : "闲置 24 小时清理"}
        </span>
      </div>

      <label
        className="mt-3 block text-xs font-semibold text-slate-200"
        htmlFor={`${projectId}-workspace-name`}
      >
        工作区名称
      </label>
      <div className="mt-2 flex gap-2">
        <input
          className="min-h-11 min-w-0 flex-1 rounded-md border border-white/10 bg-ink-950 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan-300/50"
          id={`${projectId}-workspace-name`}
          onChange={(event) => setDisplayName(event.target.value)}
          placeholder="工作区名称"
          value={displayName}
        />
        <button
          className="min-h-11 rounded-md bg-cyan-300 px-3 py-2 text-xs font-semibold text-ink-950 disabled:opacity-50"
          disabled={Boolean(busy)}
          onClick={() => void createWorkspace()}
          type="button"
        >
          {busy === "create" ? "创建中…" : "新建"}
        </button>
      </div>

      {items.length ? (
        <label className="mt-3 block text-xs font-semibold text-slate-200">
          选择工作区
          <select
            className="mt-2 min-h-11 w-full rounded-md border border-white/10 bg-ink-950 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan-300/50"
            onChange={(event) => {
              setSelectedId(event.target.value);
              setPendingUpload(null);
              onBound(null);
            }}
            value={selectedId}
          >
            {items.map((item) => (
              <option key={item.workspace_id} value={item.workspace_id}>
                {item.display_name} · {item.status === "sealed" ? "已封存" : "上传中"}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {selected ? (
        <div className="mt-3 rounded-md border border-white/10 bg-black/15 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-400">
            <span>{selected.file_count} 个文件 · {formatBytes(selected.size_bytes)}</span>
            <span>{selected.status === "sealed" ? "输入只读" : `上限 ${policy.max_files} 个 / ${formatBytes(policy.max_workspace_bytes)}`}</span>
          </div>

          {selected.status === "uploading" ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <label className="relative inline-flex min-h-11 cursor-pointer items-center overflow-hidden rounded-md border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-semibold text-slate-200 hover:border-cyan-300/40 focus-within:ring-2 focus-within:ring-cyan-300/50">
                {busy === "upload" ? "上传中…" : "选择文件或 ZIP"}
                <input
                  accept={accept}
                  aria-label="选择文件或 ZIP"
                  className="absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
                  disabled={Boolean(busy)}
                  multiple
                  onChange={(event) => {
                    handleFileSelection(event.currentTarget.files);
                    event.currentTarget.value = "";
                  }}
                  type="file"
                />
              </label>
              <label className="relative inline-flex min-h-11 cursor-pointer items-center overflow-hidden rounded-md border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-semibold text-slate-200 hover:border-cyan-300/40 focus-within:ring-2 focus-within:ring-cyan-300/50">
                选择文件夹
                <input
                  accept={accept}
                  aria-label="选择文件夹"
                  className="absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
                  disabled={Boolean(busy)}
                  multiple
                  onChange={(event) => {
                    handleFileSelection(event.currentTarget.files);
                    event.currentTarget.value = "";
                  }}
                  ref={enableDirectoryPicker}
                  type="file"
                />
              </label>
              <p className="basis-full text-[11px] leading-5 text-slate-400">
                文件夹会递归上传其中的文件并保留相对目录；空文件夹不会上传。
                当前支持：{acceptedTypeLabel}。ZIP 内容仍由服务端校验。
              </p>
            </div>
          ) : null}

          {selected.status === "uploading" &&
          pendingUpload?.workspaceId === selected.workspace_id ? (
            <section
              aria-labelledby={`upload-preflight-${selected.workspace_id}`}
              aria-live="polite"
              className="mt-3 rounded-md border border-amber-300/25 bg-amber-300/[0.07] p-3"
            >
              <p
                className="text-xs font-semibold text-amber-100"
                id={`upload-preflight-${selected.workspace_id}`}
              >
                发现 {pendingUpload.rejectedPaths.length} 个不支持的文件
              </p>
              <p className="mt-1 text-[11px] leading-5 text-slate-300">
                {pendingUpload.files.length
                  ? `可以继续上传 ${pendingUpload.files.length} 个支持的文件，其余文件会被跳过。`
                  : "所选内容中没有可上传的文件，请取消后重新选择。"}
              </p>
              <ul className="mt-2 max-h-28 space-y-1 overflow-auto text-[11px] text-amber-100/90">
                {pendingUpload.rejectedPaths.slice(0, 6).map((path) => (
                  <li className="truncate" key={path} title={path}>
                    {path}
                  </li>
                ))}
              </ul>
              {pendingUpload.rejectedPaths.length > 6 ? (
                <p className="mt-1 text-[11px] text-slate-400">
                  另有 {pendingUpload.rejectedPaths.length - 6} 个不支持的文件
                </p>
              ) : null}
              <div className="mt-3 flex flex-wrap gap-2">
                {pendingUpload.files.length ? (
                  <button
                    className="min-h-11 rounded-md bg-amber-200 px-3 py-2 text-xs font-semibold text-ink-950 hover:bg-amber-100 focus-visible:outline-amber-200 disabled:opacity-50"
                    disabled={Boolean(busy)}
                    onClick={() =>
                      void uploadFiles(
                        pendingUpload.files,
                        pendingUpload.rejectedPaths.length,
                      )
                    }
                    type="button"
                  >
                    跳过并上传 {pendingUpload.files.length} 个
                  </button>
                ) : null}
                <button
                  className="min-h-11 rounded-md border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200 hover:border-white/30 disabled:opacity-50"
                  disabled={Boolean(busy)}
                  onClick={() => setPendingUpload(null)}
                  type="button"
                >
                  取消上传
                </button>
              </div>
            </section>
          ) : null}

          {selected.files.length ? (
            <ul className="mt-3 max-h-24 space-y-1 overflow-auto text-[11px] text-slate-400">
              {selected.files.slice(0, 50).map((file) => (
                <li className="flex justify-between gap-2" key={file.file_id}>
                  <span className="truncate">{file.relative_path}</span>
                  <span className="shrink-0">{formatBytes(file.size_bytes)}</span>
                </li>
              ))}
            </ul>
          ) : null}

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              className="min-h-11 rounded-md bg-emerald-300 px-3 py-2 text-xs font-semibold text-ink-950 disabled:opacity-50"
              disabled={Boolean(busy)}
              onClick={() => void sealAndBind()}
              type="button"
            >
              {busy === "seal" ? "绑定中…" : selected.status === "sealed" ? "绑定此工作区" : "封存并绑定"}
            </button>
            <button
              className="min-h-11 rounded-md border border-rose-300/25 px-3 py-2 text-xs font-semibold text-rose-100 disabled:opacity-50"
              disabled={Boolean(busy)}
              onClick={() => void removeWorkspace()}
              type="button"
            >
              {selected.persistent ? "删除记忆库（需确认）" : "清理工作区"}
            </button>
          </div>

          {selected.artifacts.length ? (
            <div className="mt-3 border-t border-white/10 pt-3">
              <p className="text-[11px] font-semibold text-slate-200">可下载产物</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {selected.artifacts.map((artifact) => (
                  <a
                    className="inline-flex min-h-11 items-center rounded-md border border-brand-300/20 bg-brand-300/10 px-2.5 py-1.5 text-[11px] text-brand-100 hover:bg-brand-300/15"
                    href={`/api/mcp/catalog/${projectId}/workspaces/${selected.workspace_id}/artifacts/${artifact.artifact_id}/download`}
                    key={artifact.artifact_id}
                  >
                    {artifact.filename} · {formatBytes(artifact.size_bytes)}
                  </a>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <div aria-live="polite" aria-relevant="text" role="status">
        {message ? <p className="mt-2 text-[11px] leading-5 text-emerald-100">{message}</p> : null}
      </div>
      {error ? (
        <p className="mt-2 text-[11px] leading-5 text-rose-100" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
