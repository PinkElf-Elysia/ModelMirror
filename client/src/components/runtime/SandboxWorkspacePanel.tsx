import { useCallback, useEffect, useState } from "react";

interface SandboxOperation {
  operation_id: string;
  tool_name: string;
  status: string;
  command_name?: string | null;
  error?: string | null;
}

interface SandboxArtifact {
  artifact_id: string;
  filename: string;
  size_bytes: number;
}

interface SandboxWorkspace {
  workspace_id: string;
  scope_type: string;
  scope_id: string;
  status: string;
  artifact_count: number;
  operations?: SandboxOperation[];
  artifacts?: SandboxArtifact[];
}

interface SandboxFile {
  path: string;
  size_bytes: number;
  kind: string;
}

interface SandboxWorkspacePanelProps {
  scopeType: "workflow" | "conversation" | "goal" | "handoff";
  scopeId?: string;
  scopeIdPrefix?: string;
  compact?: boolean;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export default function SandboxWorkspacePanel({
  scopeType,
  scopeId,
  scopeIdPrefix,
  compact = false,
}: SandboxWorkspacePanelProps) {
  const [workspaces, setWorkspaces] = useState<SandboxWorkspace[]>([]);
  const [files, setFiles] = useState<Record<string, SandboxFile[]>>({});
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!scopeId && !scopeIdPrefix) return;
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ scope_type: scopeType, limit: "50" });
      if (scopeId) query.set("scope_id", scopeId);
      const response = await fetch(`/api/runtime/sandbox-workspaces?${query}`);
      if (!response.ok) throw new Error("工作区暂不可用");
      const payload = (await response.json()) as { items?: SandboxWorkspace[] };
      const matched = (payload.items ?? []).filter(
        (item) => !scopeIdPrefix || item.scope_id.startsWith(scopeIdPrefix),
      );
      const details = await Promise.all(
        matched.map(async (item) => {
          const detailResponse = await fetch(
            `/api/runtime/sandbox-workspaces/${encodeURIComponent(item.workspace_id)}`,
          );
          return detailResponse.ok
            ? ((await detailResponse.json()) as SandboxWorkspace)
            : item;
        }),
      );
      setWorkspaces(details);
      if (expanded) {
        const filePairs = await Promise.all(
          details.map(async (item) => {
            const fileResponse = await fetch(
              `/api/runtime/sandbox-workspaces/${encodeURIComponent(item.workspace_id)}/files`,
            );
            const filePayload = fileResponse.ok
              ? ((await fileResponse.json()) as { items?: SandboxFile[] })
              : {};
            return [item.workspace_id, filePayload.items ?? []] as const;
          }),
        );
        setFiles(Object.fromEntries(filePairs));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "工作区暂不可用");
    } finally {
      setLoading(false);
    }
  }, [expanded, scopeId, scopeIdPrefix, scopeType]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (!scopeId && !scopeIdPrefix) return null;

  return (
    <section className="border-t border-white/10 bg-slate-950/20">
      <button
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-white/[0.04]"
        onClick={() => setExpanded((value) => !value)}
        type="button"
      >
        <span>
          <span className="block text-xs font-semibold text-slate-200">隔离工作区</span>
          <span className="mt-0.5 block text-[11px] text-slate-500">
            {loading ? "正在同步" : `${workspaces.length} 个工作区`}
          </span>
        </span>
        <span className="text-[11px] text-slate-500">{expanded ? "收起" : "展开"}</span>
      </button>
      {expanded ? (
        <div className={`space-y-2 px-4 pb-4 ${compact ? "max-h-72 overflow-y-auto" : ""}`}>
          {error ? (
            <p className="rounded-lg bg-rose-300/10 px-3 py-2 text-xs text-rose-100">{error}</p>
          ) : workspaces.length === 0 ? (
            <p className="rounded-lg border border-dashed border-white/15 px-3 py-4 text-center text-xs text-slate-500">
              Agent 使用 Sandbox 后，文件与产物会显示在这里。
            </p>
          ) : (
            workspaces.map((workspace) => (
              <div className="rounded-lg bg-white/[0.045] p-3" key={workspace.workspace_id}>
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate font-mono text-[11px] text-slate-300">{workspace.scope_id}</p>
                  <span className="rounded-full bg-emerald-300/10 px-2 py-0.5 text-[10px] text-emerald-100">
                    {workspace.status}
                  </span>
                </div>
                {(files[workspace.workspace_id] ?? []).length > 0 ? (
                  <div className="mt-2 space-y-1">
                    {(files[workspace.workspace_id] ?? []).slice(0, 12).map((file) => (
                      <div className="flex items-center justify-between gap-3 text-[11px]" key={file.path}>
                        <span className="truncate text-slate-400">{file.path}</span>
                        <span className="shrink-0 text-slate-600">{formatBytes(file.size_bytes)}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
                {(workspace.operations ?? []).length > 0 ? (
                  <div className="mt-2 border-t border-white/10 pt-2">
                    {(workspace.operations ?? []).slice(0, 5).map((operation) => (
                      <p className="truncate text-[11px] text-slate-500" key={operation.operation_id}>
                        {operation.tool_name} · {operation.status}
                        {operation.command_name ? ` · ${operation.command_name}` : ""}
                      </p>
                    ))}
                  </div>
                ) : null}
                {(workspace.artifacts ?? []).length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-2 border-t border-white/10 pt-2">
                    {(workspace.artifacts ?? []).map((artifact) => (
                      <a
                        className="rounded-full bg-cyan-300/10 px-2.5 py-1 text-[11px] text-cyan-100 transition hover:bg-cyan-300/20"
                        href={`/api/runtime/sandbox-artifacts/${encodeURIComponent(artifact.artifact_id)}/download`}
                        key={artifact.artifact_id}
                      >
                        下载 {artifact.filename}
                      </a>
                    ))}
                  </div>
                ) : null}
              </div>
            ))
          )}
          <button className="text-[11px] text-cyan-200 hover:text-cyan-100" onClick={() => void refresh()} type="button">
            刷新工作区
          </button>
        </div>
      ) : null}
    </section>
  );
}
