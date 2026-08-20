import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { CircleAlert, KeyRound, LoaderCircle, LogOut } from "lucide-react";

interface AdminSession {
  configured: boolean;
  authenticated: boolean;
  expires_at: number | null;
  csrf_token: string | null;
}

interface ProviderAdminGateProps {
  children: (session: { csrfToken: string }) => ReactNode;
}

async function readError(response: Response) {
  if (response.status === 429) {
    const retryAfter = response.headers.get("Retry-After") || "300";
    return `配对尝试过多，请在 ${retryAfter} 秒后重试。`;
  }
  try {
    const payload = await response.json();
    if (payload?.detail?.code === "admin_session_required") {
      return "管理会话已失效，请重新配对。";
    }
    if (payload?.detail?.code === "admin_pairing_not_configured") {
      return "Provider 管理面尚未配置。";
    }
    if (typeof payload?.detail?.message === "string") {
      return payload.detail.message as string;
    }
  } catch {
    // Use the stable fallback below.
  }
  return "Provider 管理会话操作未完成，请稍后重试。";
}

export default function ProviderAdminGate({ children }: ProviderAdminGateProps) {
  const [session, setSession] = useState<AdminSession | null>(null);
  const [pairingSecret, setPairingSecret] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/router/admin/session");
      if (!response.ok) throw new Error(await readError(response));
      setSession((await response.json()) as AdminSession);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取管理会话。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const pair = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      if (!pairingSecret) return;
      setSubmitting(true);
      setError("");
      try {
        const response = await fetch("/api/router/admin/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pairing_secret: pairingSecret }),
        });
        if (!response.ok) throw new Error(await readError(response));
        setSession((await response.json()) as AdminSession);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "管理员配对失败。");
      } finally {
        setPairingSecret("");
        setSubmitting(false);
      }
    },
    [pairingSecret],
  );

  const logout = useCallback(async () => {
    if (!session?.csrf_token) return;
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch("/api/router/admin/session", {
        method: "DELETE",
        headers: { "X-ModelMirror-CSRF": session.csrf_token },
      });
      if (!response.ok) throw new Error(await readError(response));
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法注销管理会话。");
    } finally {
      setSubmitting(false);
    }
  }, [refresh, session]);

  if (loading) {
    return (
      <section className="mb-6 rounded-lg border border-white/10 bg-ink-950/82 p-5 text-sm text-slate-300">
        <LoaderCircle className="mr-2 inline h-4 w-4 animate-spin" />
        正在读取 Provider 管理会话
      </section>
    );
  }

  if (!session) {
    return (
      <section className="mb-6 rounded-lg border border-rose-300/20 bg-rose-300/10 p-5">
        <div className="flex items-start gap-3">
          <CircleAlert className="mt-0.5 h-5 w-5 text-rose-200" />
          <div>
            <h2 className="font-semibold text-white">无法读取 Provider 管理会话</h2>
            <p className="mt-1 text-sm leading-6 text-rose-100/80" role="alert">
              {error || "请检查后端服务后重试。"}
            </p>
          </div>
        </div>
      </section>
    );
  }

  if (!session?.configured) {
    return (
      <section className="mb-6 rounded-lg border border-amber-300/20 bg-amber-300/10 p-5">
        <div className="flex items-start gap-3">
          <CircleAlert className="mt-0.5 h-5 w-5 text-amber-200" />
          <div>
            <h2 className="font-semibold text-white">Provider 管理面尚未配置</h2>
            <p className="mt-1 text-sm leading-6 text-amber-100/80">
              请由运维人员注入 MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET。
              模型调用与 Marble 设置不受影响。
            </p>
          </div>
        </div>
      </section>
    );
  }

  if (!session.authenticated || !session.csrf_token) {
    return (
      <section className="mb-6 rounded-lg border border-white/10 bg-ink-950/82 p-5 shadow-prism">
        <div className="flex items-center gap-2 text-hire-100">
          <KeyRound className="h-4 w-4" />
          <h2 className="font-semibold">Provider 管理员配对</h2>
        </div>
        <p className="mt-2 text-sm leading-6 text-slate-300">
          配对密钥仅用于本次提交，不会写入浏览器存储、URL 或日志。
        </p>
        <form className="mt-4 flex max-w-2xl flex-col gap-3 sm:flex-row" onSubmit={pair}>
          <input
            autoComplete="new-password"
            className="min-w-0 flex-1 rounded-lg border border-white/15 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none focus:border-cyan-300/60"
            onChange={(event) => setPairingSecret(event.target.value)}
            placeholder="输入管理员配对密钥"
            type="password"
            value={pairingSecret}
          />
          <button
            className="rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-slate-950 disabled:opacity-45"
            disabled={!pairingSecret || submitting}
            type="submit"
          >
            {submitting ? "正在配对" : "开始管理"}
          </button>
        </form>
        {error ? (
          <p className="mt-3 text-sm text-rose-200" role="alert">
            {error}
          </p>
        ) : null}
      </section>
    );
  }

  return (
    <>
      <div className="mb-4 flex items-center justify-between rounded-lg border border-emerald-300/20 bg-emerald-300/10 px-4 py-3 text-sm">
        <span className="text-emerald-100">Provider 管理会话已解锁</span>
        <button
          className="inline-flex items-center gap-2 text-slate-200 hover:text-white disabled:opacity-45"
          disabled={submitting}
          onClick={() => void logout()}
          type="button"
        >
          <LogOut className="h-4 w-4" />
          注销
        </button>
      </div>
      {error ? (
        <p className="mb-4 text-sm text-rose-200" role="alert">
          {error}
        </p>
      ) : null}
      {children({ csrfToken: session.csrf_token })}
    </>
  );
}
