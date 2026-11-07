import { useEffect, useRef, useState, type ReactNode } from "react";
const url = "/rpg-app/custom-config/entry.mjs";
const plugin = () => import(/* @vite-ignore */ url);
export async function configRequest(
  scope: string,
  url: string,
  options: RequestInit,
  modIds: string[] = [],
) {
  const request = JSON.parse(String(options.body));
  const envelope = localStorage.getItem("rpg-custom-config-used")
    ? await (await plugin()).prepare(scope, request)
    : null;
  if (!envelope && !modIds.length) return fetch(url, options);
  return fetch(url, {
    ...options,
    headers: {
      ...options.headers,
      ...(envelope ? { "X-Rpg-Custom-Config": "1" } : {}),
      ...(modIds.length ? { "X-Rpg-Mod-Panel": "1" } : {}),
    },
    body: JSON.stringify({
      ...envelope,
      request,
      ...(modIds.length ? { modIds } : {}),
    }),
  });
}
export function ConfigTools({ scope }: { scope: string }) {
  const [error, setError] = useState("");
  const [paused, setPaused] = useState(
    () => localStorage.getItem("rpg-custom-paused:" + scope) === "1",
  );
  return (
    <details
      className="rpg-config-tools"
      style={{ position: "relative", marginInlineStart: "auto" }}
    >
      <summary
        title="聊天工具"
        aria-label="聊天工具"
        style={{ cursor: "pointer" }}
      >
        ⚙ 工具
      </summary>
      <div
        style={{
          position: "absolute",
          right: 0,
          top: "100%",
          zIndex: 20,
          background: "#fff",
          color: "#253446",
          border: "1px solid #ddd",
          borderRadius: 8,
          padding: 8,
          minWidth: 140,
        }}
      >
        <button
          type="button"
          onClick={async (e) => {
            const menu = e.currentTarget.closest("details");
            try {
              (await plugin()).openEditor(scope);
              if (menu) menu.open = false;
            } catch {
              setError("自定义配置插件未加载，请检查 RPG 服务版本。");
            }
          }}
        >
          自定义配置
        </button>
        <label style={{ display: "block", fontSize: 13, marginTop: 8 }}>
          <input
            type="checkbox"
            checked={!paused}
            onChange={(e) => {
              const off = !e.target.checked;
              localStorage.setItem(
                "rpg-custom-paused:" + scope,
                off ? "1" : "0",
              );
              setPaused(off);
              const channel = new BroadcastChannel("rpg-custom-config");
              channel.postMessage("changed");
              channel.close();
            }}
          />{" "}
          应用已保存配置
        </label>
        {error && <p role="alert">{error}</p>}
      </div>
    </details>
  );
}
function useRevision() {
  const [n, set] = useState(0);
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const b = new BroadcastChannel("rpg-custom-config");
    b.onmessage = () => set((v) => v + 1);
    return () => b.close();
  }, []);
  return n;
}
export function useConfigView<T extends HTMLElement>(scope: string) {
  const ref = useRef<T>(null),
    rev = useRevision();
  useEffect(() => {
    if (
      !scope ||
      !ref.current ||
      !localStorage.getItem("rpg-custom-config-used")
    )
      return;
    const el = ref.current,
      ctrl = new AbortController();
    plugin()
      .then((p) => p.appearance(scope, el, ctrl.signal))
      .catch(() => {});
    return () => ctrl.abort();
  }, [scope, rev]);
  return ref;
}
export function ConfigText({
  scope,
  role,
  source,
  children,
  render,
}: {
  scope: string;
  role: "user" | "assistant";
  source: string;
  children?: ReactNode;
  render?: (value: string) => ReactNode;
}) {
  const [displayFailed,setDisplayFailed]=useState(false),[retry,setRetry]=useState(0);
  const rev = useRevision(),
    [projection, set] = useState<{
      scope: string;
      source: string;
      value: string;
      revision: number;
      role: string;
    } | null>(null);
  useEffect(() => {
    let live = true;
    if (scope && localStorage.getItem("rpg-custom-config-used"))
      plugin()
        .then((p) => p.display(scope, role, source))
        .then((value) => {
          if (live) {set({ scope, source, value, revision: rev, role });setDisplayFailed(false);}
        })
        .catch(() => {
          if (live) {set(previous=>({scope,source,value:render?(previous?.scope===scope&&previous.role===role&&previous.revision===rev?previous.value:""):source,revision:rev,role}));setDisplayFailed(true);}
        });
    return () => {
      live = false;
    };
  }, [scope, source, role, rev, retry]);
  // Render callbacks keep one presentation component mounted across chunks.
  // While display rules catch up, retain their last projection instead of
  // briefly revealing the unprocessed source between updates.
  if (render) {
    const configured = scope && localStorage.getItem("rpg-custom-config-used");
    const valid = projection?.scope === scope && projection.revision === rev && projection.role === role;
    return <>{render(configured ? valid ? projection.value : "" : source)}{configured&&displayFailed&&<p role="status">显示规则暂未应用，已暂停更新画面。<button type="button" className="text-button" onClick={()=>setRetry(n=>n+1)}>重试显示</button></p>}</>;
  }
  return projection &&
    projection.source === source &&
    projection.scope === scope &&
    projection.value !== source ? (
    <span style={{ whiteSpace: "pre-wrap" }}>{projection.value}</span>
  ) : (
    <>{children}</>
  );
}
