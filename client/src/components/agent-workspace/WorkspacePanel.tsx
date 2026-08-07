import {
  ArrowUp,
  Bot,
  Download,
  File,
  Folder,
  FolderOpen,
  X,
} from "lucide-react";
import type {
  AgentSession,
  AgentWorkspaceEntry,
} from "../../types/agentWorkspace";

interface WorkspaceFilePreview {
  path: string;
  content: string;
  size: number;
}

interface WorkspacePanelProps {
  sessionId: string;
  path: string;
  entries: AgentWorkspaceEntry[];
  subagents: AgentSession[];
  preview: WorkspaceFilePreview | null;
  loading: boolean;
  downloadUrl: string;
  onOpenDirectory: (path: string) => void;
  onOpenFile: (path: string) => void;
  onClosePreview: () => void;
  onGoUp: () => void;
}

function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export default function WorkspacePanel({
  path,
  entries,
  subagents,
  preview,
  loading,
  downloadUrl,
  onOpenDirectory,
  onOpenFile,
  onClosePreview,
  onGoUp,
}: WorkspacePanelProps) {
  return (
    <aside className="flex min-h-0 flex-col border-t border-white/10 bg-slate-950/65 lg:border-l lg:border-t-0">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-white">Workspace</h2>
          <p className="mt-0.5 max-w-48 truncate font-mono text-[10px] text-slate-500">
            /{path}
          </p>
        </div>
        {path ? (
          <button
            aria-label="返回上级目录"
            className="rounded-md border border-white/10 p-2 text-slate-400 hover:bg-white/[0.05] hover:text-white"
            onClick={onGoUp}
            type="button"
          >
            <ArrowUp aria-hidden size={14} />
          </button>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {preview ? (
          <section>
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold text-white">{preview.path}</p>
                <p className="mt-0.5 text-[10px] text-slate-500">{formatSize(preview.size)}</p>
              </div>
              <div className="flex gap-1">
                <a
                  aria-label="下载文件"
                  className="rounded-md border border-white/10 p-2 text-slate-400 hover:bg-white/[0.05] hover:text-white"
                  href={downloadUrl}
                >
                  <Download aria-hidden size={14} />
                </a>
                <button
                  aria-label="关闭文件预览"
                  className="rounded-md border border-white/10 p-2 text-slate-400 hover:bg-white/[0.05] hover:text-white"
                  onClick={onClosePreview}
                  type="button"
                >
                  <X aria-hidden size={14} />
                </button>
              </div>
            </div>
            <pre className="max-h-[52vh] overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-[11px] leading-5 text-slate-300">
              {preview.content}
            </pre>
          </section>
        ) : (
          <div className="p-2" aria-label="工作区文件">
            {loading ? (
              <div className="space-y-2 p-1" aria-label="正在加载工作区">
                {[0, 1, 2].map((item) => (
                  <div className="h-10 animate-pulse rounded-md bg-white/[0.045]" key={item} />
                ))}
              </div>
            ) : null}
            {!loading && !entries.length ? (
              <div className="px-3 py-10 text-center">
                <FolderOpen className="mx-auto text-slate-600" size={24} />
                <p className="mt-3 text-xs text-slate-500">目录为空</p>
              </div>
            ) : null}
            {!loading
              ? entries.map((entry) => (
                  <button
                    className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-xs text-slate-300 hover:bg-white/[0.05] hover:text-white"
                    key={entry.path}
                    onClick={() =>
                      entry.kind === "directory"
                        ? onOpenDirectory(entry.path)
                        : onOpenFile(entry.path)
                    }
                    type="button"
                  >
                    {entry.kind === "directory" ? (
                      <Folder aria-hidden className="shrink-0 text-cyan-200" size={15} />
                    ) : (
                      <File aria-hidden className="shrink-0 text-slate-500" size={15} />
                    )}
                    <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                    {entry.kind === "file" ? (
                      <span className="text-[10px] text-slate-600">{formatSize(entry.size)}</span>
                    ) : null}
                  </button>
                ))
              : null}
          </div>
        )}
      </div>

      <section className="border-t border-white/10 p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold text-white">子 Agent</h2>
          <span className="text-[10px] text-slate-500">{subagents.length} / 8</span>
        </div>
        {!subagents.length ? (
          <p className="mt-2 text-[11px] leading-5 text-slate-500">
            Agent 调用 run_subagent 后会在这里显示状态。
          </p>
        ) : (
          <div className="mt-2 space-y-1.5">
            {subagents.map((subagent) => (
              <div
                className="flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-2.5 py-2"
                key={subagent.session_id}
              >
                <Bot aria-hidden className="shrink-0 text-cyan-200" size={13} />
                <span className="min-w-0 flex-1 truncate text-[11px] text-slate-300">
                  {subagent.title}
                </span>
                <span className="text-[10px] text-slate-500">{subagent.status}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </aside>
  );
}
