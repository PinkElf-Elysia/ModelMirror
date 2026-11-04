const prefix = "rpg.mod-panel.v1.";
const own = (id: string) => prefix + "owned." + id;
const slot = (card: string, session: string, id: string) =>
  prefix + card + "." + session + "." + id;
export const acquired = (id: string) => localStorage.getItem(own(id)) === "1";
export const acquire = (id: string) => localStorage.setItem(own(id), "1");
export function selectedMods(card: string, session: string): string[] {
  const key = slot(card, session, "");
  const ids: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (
      k?.startsWith(key) &&
      localStorage.getItem(k) === "1" &&
      acquired(k.slice(key.length))
    )
      ids.push(k.slice(key.length));
  }
  return ids.sort();
}
export function toggleMod(
  card: string,
  session: string,
  id: string,
  enabled: boolean,
) {
  if (!acquired(id)) throw Error("请先获取这个 Mod。");
  const k = slot(card, session, id);
  if (enabled) localStorage.setItem(k, "1");
  else localStorage.removeItem(k);
}
export function modRequest(
  card: string,
  session: string,
  url: string,
  options: RequestInit,
  scope: string,
) {
  const modIds = selectedMods(card, session);
  if (scope)
    return import("./custom-config").then((module) =>
      module.configRequest(scope, url, options, modIds),
    );
  if (!modIds.length) return fetch(url, options);
  return fetch(url, {
    ...options,
    headers: { ...options.headers, "X-Rpg-Mod-Panel": "1" },
    body: JSON.stringify({ modIds, request: JSON.parse(String(options.body)) }),
  });
}
