import { Bot, Circle, MessageSquarePlus } from "lucide-react";
import type { AgentSession } from "../../types/agentWorkspace";

const statusClass: Record<AgentSession["status"], string> = {
  idle: "text-slate-500",
  running: "text-cyan-300",
  waiting_approval: "text-amber-300",
  failed: "text-rose-300",
};

interface SessionSidebarProps {
  sessions: AgentSession[];
  selectedId: string | null;
  loading: boolean;
  creating: boolean;
  onCreate: () => void;
  onSelect: (sessionId: string) => void;
}

export default function SessionSidebar({
  sessions,
  selectedId,
  loading,
  creating,
  onCreate,
  onSelect,
}: SessionSidebarProps) {
  return (
    <aside className="flex min-h-0 flex-col border-b border-white/10 bg-slate-950/65 lg:border-b-0 lg:border-r">
      <div className="border-b border-white/10 p-3">
        <button
          className="flex min-h-10 w-full items-center justify-center gap-2 rounded-md bg-cyan-300 px-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={creating}
          onClick={onCreate}
          type="button"
        >
          <MessageSquarePlus aria-hidden size={16} />
          {creating ? "创建中…" : "新建会话"}
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2" aria-label="会话列表">
        {loading ? (
          <div className="space-y-2 p-1" aria-label="正在加载会话">
            {[0, 1, 2].map((item) => (
              <div
                className="h-14 animate-pulse rounded-md bg-white/[0.045]"
                key={item}
              />
            ))}
          </div>
        ) : null}
        {!loading && !sessions.length ? (
          <div className="px-3 py-10 text-center">
            <Bot className="mx-auto text-slate-600" size={24} />
            <p className="mt-3 text-sm font-medium text-slate-300">还没有会话</p>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              创建后会获得独立且持久化的工作目录。
            </p>
          </div>
        ) : null}
        {!loading
          ? sessions.map((session) => {
              const selected = selectedId === session.session_id;
              return (
                <button
                  aria-current={selected ? "page" : undefined}
                  className={`mb-1 flex w-full items-start gap-2 rounded-md border px-3 py-2.5 text-left transition ${
                    selected
                      ? "border-cyan-300/25 bg-cyan-300/10 text-white"
                      : "border-transparent text-slate-300 hover:border-white/10 hover:bg-white/[0.045]"
                  }`}
                  key={session.session_id}
                  onClick={() => onSelect(session.session_id)}
                  type="button"
                >
                  <Circle
                    aria-hidden
                    className={`mt-1 shrink-0 fill-current ${statusClass[session.status]}`}
                    size={8}
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">
                      {session.title}
                    </span>
                    <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-500">
                      {session.agent_id} · {session.model_id}
                    </span>
                  </span>
                </button>
              );
            })
          : null}
      </div>
    </aside>
  );
}
