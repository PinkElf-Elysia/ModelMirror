export { appearance } from "./appearance.mjs";
import { effective } from "./config.mjs";
const base = new URL(".", import.meta.url);
export function openEditor(scope) {
  const outer =
    window.parent !== window &&
    window.parent.location.origin === location.origin
      ? window.parent
      : window;
  if (
    outer === window &&
    location.pathname.startsWith("/rpg-app/") &&
    !location.pathname.startsWith("/rpg-app/custom-config/")
  ) {
    const page = new URL("editor.html", base);
    if (scope) page.searchParams.set("session", scope);
    window.open(page, "_blank", "noopener");
    return;
  }
  const doc = outer.document,
    dialog = doc.createElement("dialog");
  dialog.ariaLabel = "自定义配置";
  dialog.style.cssText =
    "padding:0;border:0;border-radius:14px;width:min(980px,96vw);height:92dvh;max-height:96dvh;background:white;box-shadow:0 12px 80px #13223555";
  const frame = doc.createElement("iframe");
  frame.title = "自定义配置";
  frame.style.cssText = "display:block;width:100%;height:100%;border:none";
  const url = new URL("editor.html", base);
  if (scope) url.searchParams.set("session", scope);
  frame.src = url.href;
  dialog.append(frame);
  doc.body.append(dialog);
  const returnTo = document.activeElement;
  const receive = (e) => {
    if (e.origin !== location.origin || e.source !== frame.contentWindow)
      return;
    if (e.data?.type === "rpg-config-close") dialog.close();
  };
  outer.addEventListener("message", receive);
  dialog.oncancel = (e) => {
    e.preventDefault();
    frame.contentDocument?.getElementById("exit")?.click();
  };
  dialog.onclose = () => {
    outer.removeEventListener("message", receive);
    dialog.remove();
    returnTo?.focus?.();
  };
  dialog.showModal();
}
export async function prepare(scope, request) {
  const values = await effective(scope);
  if (!values.length) return null;
  return {
    profiles: values.map((v) => ({
      ...v,
      desktop: "",
      mobile: "",
      css: "",
      rules: v.rules.filter((r) => !r.renderOnly),
    })),
    request,
  };
}
export async function display(scope, role, source) {
  const configs = await effective(scope);
  if (
    !configs.some((c) =>
      c.rules.some((r) => r.enabled && [role, "all"].includes(r.scope)),
    )
  )
    return source;
  const w = new Worker(new URL("worker.mjs", base), { type: "module" });
  let timer;
  try {
    return await new Promise((ok, no) => {
      timer = setTimeout(() => no(Error("显示替换超时")), 1500);
      w.onmessage = (e) =>
        e.data.error ? no(Error(e.data.error)) : ok(e.data.value);
      w.onerror = no;
      w.postMessage({
        kind: "display",
        source,
        role,
        configs: configs.map((v) => ({ ...v, desktop: "", mobile: "" })),
      });
    });
  } finally {
    clearTimeout(timer);
    w.terminate();
  }
}
