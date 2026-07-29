import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { type XpertAppManifest } from "../types/xpert";

interface AppMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

function historyKey(slug: string) {
  return `modelmirror-xpert-app:${slug}:history`;
}

function tokenKey(slug: string) {
  return `modelmirror-xpert-app:${slug}:access`;
}

function readError(payload: unknown, fallback: string) {
  if (!payload || typeof payload !== "object") return fallback;
  const error = (payload as { error?: unknown }).error;
  if (typeof error === "string") return error;
  if (error && typeof error === "object" && "message" in error) {
    return String((error as { message: unknown }).message || fallback);
  }
  return fallback;
}

export default function XpertAppPage() {
  const { appSlug = "" } = useParams();
  const [manifest, setManifest] = useState<XpertAppManifest | null>(null);
  const [accessToken, setAccessToken] = useState("");
  const [accessDraft, setAccessDraft] = useState("");
  const [messages, setMessages] = useState<AppMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const fragmentToken = params.get("access") || "";
    const storedToken = sessionStorage.getItem(tokenKey(appSlug)) || "";
    const nextToken = fragmentToken || storedToken;
    if (fragmentToken) {
      sessionStorage.setItem(tokenKey(appSlug), fragmentToken);
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    }
    setAccessToken(nextToken);
    setAccessDraft(nextToken);
    try {
      const saved = JSON.parse(localStorage.getItem(historyKey(appSlug)) || "[]") as AppMessage[];
      setMessages(Array.isArray(saved) ? saved.slice(-40) : []);
    } catch {
      setMessages([]);
    }
  }, [appSlug]);

  useEffect(() => {
    if (!accessToken) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    fetch(`/api/apps/${appSlug}/manifest`, {
      headers: { "X-ModelMirror-App-Token": accessToken },
    })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(readError(payload, "分享链接不可用"));
        return payload as XpertAppManifest;
      })
      .then((payload) => {
        if (!cancelled) {
          setManifest(payload);
          document.title = `模镜 App - ${payload.name}`;
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setManifest(null);
          setError(caught instanceof Error ? caught.message : "分享链接不可用");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken, appSlug]);

  useEffect(() => {
    localStorage.setItem(historyKey(appSlug), JSON.stringify(messages.slice(-40)));
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, appSlug]);

  function unlock() {
    const token = accessDraft.trim();
    if (!token) return;
    sessionStorage.setItem(tokenKey(appSlug), token);
    setAccessToken(token);
  }

  function resetAccess() {
    sessionStorage.removeItem(tokenKey(appSlug));
    setAccessToken("");
    setAccessDraft("");
    setManifest(null);
    setError("");
  }

  async function sendMessage(value = input) {
    const content = value.trim();
    if (!content || !manifest || running) return;
    const userMessage: AppMessage = { id: crypto.randomUUID(), role: "user", content };
    const assistantId = crypto.randomUUID();
    const nextMessages = [...messages, userMessage].slice(-20);
    setMessages([...nextMessages, { id: assistantId, role: "assistant", content: "" }]);
    setInput("");
    setRunning(true);
    setError("");
    try {
      const response = await fetch(`/api/v1/xpert-apps/${appSlug}/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-ModelMirror-App-Token": accessToken,
        },
        body: JSON.stringify({
          stream: true,
          messages: nextMessages.map(({ role, content: messageContent }) => ({
            role,
            content: messageContent,
          })),
        }),
      });
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => null);
        throw new Error(readError(payload, `请求失败：${response.status}`));
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let answer = "";
      while (true) {
        const { value: chunk, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(chunk, { stream: true }).replace(/\r\n/g, "\n");
        while (buffer.includes("\n\n")) {
          const boundary = buffer.indexOf("\n\n");
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const raw = line.slice(5).trim();
            if (!raw || raw === "[DONE]") continue;
            const payload = JSON.parse(raw) as {
              error?: { message?: string };
              choices?: Array<{ delta?: { content?: string } }>;
            };
            if (payload.error) throw new Error(payload.error.message || "App 运行失败");
            const delta = payload.choices?.[0]?.delta?.content || "";
            if (!delta) continue;
            answer += delta;
            setMessages((current) => current.map((message) => (
              message.id === assistantId ? { ...message, content: answer } : message
            )));
          }
        }
      }
      if (!answer) throw new Error("App 未返回回答");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "App 运行失败";
      setError(message);
      setMessages((current) => current.map((item) => (
        item.id === assistantId && !item.content ? { ...item, content: `运行失败：${message}` } : item
      )));
    } finally {
      setRunning(false);
    }
  }

  if (loading) {
    return (
      <main className="museum-grid flex min-h-screen items-center justify-center bg-ink-950 px-4 text-slate-100">
        <div className="w-full max-w-3xl animate-pulse rounded-lg border border-white/10 bg-white/[0.04] p-8">
          <div className="h-5 w-48 rounded bg-white/10" />
          <div className="mt-4 h-3 w-72 rounded bg-white/[0.06]" />
        </div>
      </main>
    );
  }

  if (!manifest) {
    return (
      <main className="museum-grid flex min-h-screen items-center justify-center bg-ink-950 px-4 text-slate-100">
        <section className="w-full max-w-md rounded-lg border border-white/10 bg-surface-900/95 p-6">
          <Link className="text-sm font-semibold text-hire-100" to="/models">模镜 ModelMirror</Link>
          <h1 className="mt-6 text-xl font-semibold text-white">打开未列出的 Agent App</h1>
          <p className="mt-2 text-sm leading-6 text-slate-400">请使用完整分享链接，或粘贴 App 分享 token。</p>
          <input
            autoComplete="off"
            className="mt-5 h-11 w-full rounded-md border border-white/10 bg-ink-950/70 px-3 text-sm text-white outline-none focus:border-hire-300/60"
            onChange={(event) => setAccessDraft(event.target.value)}
            placeholder="mmshare_..."
            type="password"
            value={accessDraft}
          />
          {error ? <p className="mt-3 text-xs text-rose-200">{error}</p> : null}
          <button className="mt-4 w-full rounded-md bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 hover:bg-hire-200 disabled:opacity-50" disabled={!accessDraft.trim()} onClick={unlock} type="button">验证访问</button>
        </section>
      </main>
    );
  }

  return (
    <main className="museum-grid min-h-screen bg-ink-950 px-4 py-5 text-slate-100 sm:px-6">
      <div className="mx-auto flex min-h-[calc(100vh-2.5rem)] max-w-5xl flex-col overflow-hidden rounded-lg border border-white/10 bg-surface-900/95">
        <header className="flex flex-col gap-3 border-b border-white/10 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-lg font-semibold text-white">{manifest.name}</h1>
              <span className="rounded-full border border-emerald-300/20 bg-emerald-300/[0.07] px-2 py-0.5 text-[10px] font-semibold text-emerald-100">v{manifest.version}</span>
            </div>
            <p className="mt-1 line-clamp-2 max-w-3xl text-xs leading-5 text-slate-400">{manifest.description || "已发布的模镜 Agent App"}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button className="rounded-md border border-white/10 px-3 py-2 text-xs text-slate-300 hover:bg-white/[0.05]" onClick={() => { setMessages([]); localStorage.removeItem(historyKey(appSlug)); }} type="button">清空对话</button>
            <button className="rounded-md px-3 py-2 text-xs text-slate-500 hover:text-slate-200" onClick={resetAccess} type="button">退出分享</button>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6" ref={scrollRef}>
          {messages.length === 0 ? (
            <div className="mx-auto flex min-h-[420px] max-w-3xl flex-col justify-center">
              <h2 className="text-2xl font-semibold text-white">从一个问题开始</h2>
              <p className="mt-2 text-sm text-slate-400">此 App 固定运行部署版本，不接受客户端模型或工作流替换。</p>
              <div className="mt-6 flex flex-wrap gap-2">
                {manifest.starters.length ? manifest.starters.map((starter) => (
                  <button className="rounded-md border border-white/10 bg-white/[0.035] px-3 py-2 text-left text-xs leading-5 text-slate-200 transition hover:border-hire-300/30 hover:bg-white/[0.06]" key={starter} onClick={() => void sendMessage(starter)} type="button">{starter}</button>
                )) : (
                  <button className="rounded-md border border-white/10 bg-white/[0.035] px-3 py-2 text-xs text-slate-200" onClick={() => setInput("请介绍你的能力和适用任务")} type="button">介绍你的能力</button>
                )}
              </div>
              {manifest.commands?.length ? (
                <div className="mt-4 flex flex-wrap gap-2">
                  {manifest.commands.map((command) => (
                    <button
                      className="rounded-md border border-violet-300/25 bg-violet-300/[0.08] px-3 py-2 text-xs text-violet-100"
                      key={command.alias}
                      onClick={() => setInput(`/${command.alias} `)}
                      title={command.description}
                      type="button"
                    >
                      /{command.alias} {command.argument_hint ? `· ${command.argument_hint}` : ""}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-4">
              {messages.map((message) => (
                <article className={message.role === "user" ? "ml-auto max-w-[84%] rounded-lg bg-hire-300 px-4 py-3 text-ink-950" : "mr-auto max-w-[92%] border-b border-white/10 px-1 py-3 text-slate-100"} key={message.id}>
                  <p className="whitespace-pre-wrap text-sm leading-6">{message.content || "正在生成..."}</p>
                </article>
              ))}
            </div>
          )}
        </div>

        <footer className="border-t border-white/10 bg-ink-950/55 p-4 sm:px-6">
          <div className="mx-auto max-w-3xl">
            {error ? <p className="mb-2 text-xs text-rose-200">{error}</p> : null}
            <div className="flex items-end gap-2">
              <textarea
                className="min-h-12 flex-1 resize-none rounded-md border border-white/10 bg-white/[0.05] px-3 py-3 text-sm leading-6 text-white outline-none focus:border-hire-300/60"
                disabled={running}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
                placeholder="输入消息，Enter 发送"
                rows={2}
                value={input}
              />
              <button className="h-12 rounded-md bg-hire-300 px-5 text-sm font-semibold text-ink-950 hover:bg-hire-200 disabled:opacity-50" disabled={running || !input.trim()} onClick={() => void sendMessage()} type="button">{running ? "运行中..." : "发送"}</button>
            </div>
            <p className="mt-2 text-center text-[10px] text-slate-600">由模镜 Agent App 提供，聊天历史仅保存在当前浏览器。</p>
          </div>
        </footer>
      </div>
    </main>
  );
}
