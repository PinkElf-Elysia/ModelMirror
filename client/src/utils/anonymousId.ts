/** 匿名浏览器标识：用于反馈防重复，不暴露任何真实身份。 */

const KEY = "modelmirror-anonymous-id";

export function getOrCreateAnonymousId(): string {
  try {
    let id = window.localStorage.getItem(KEY);
    if (!id) {
      id =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      window.localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    // localStorage 不可用时回退为内存随机 ID（本轮会话内可防重复，刷新后失效）
    return `anon-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}
