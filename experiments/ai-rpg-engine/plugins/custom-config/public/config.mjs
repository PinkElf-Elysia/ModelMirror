export const FORMAT = "modelmirror.rpg.custom-config/1";
export const empty = () => ({
  system: "",
  prefix: "",
  suffix: "",
  css: "",
  prefill: "",
  usePrefill: false,
  depth: 6,
  showAvatar: true,
  showBackground: true,
  desktop: "",
  mobile: "",
  rules: [],
  worldbook: {
    defaults: { roles: ["user"], rule: "or", targets: ["system"] },
    categories: [],
    entries: [],
  },
});
const fail = (m) => {
  throw Error(m);
};
const text = (x, n) =>
  typeof x === "string" &&
  x.isWellFormed() &&
  x.length <= 2 * n &&
  [...x].length <= n;
const id = (x) => typeof x === "string" && /^[\w-]{1,96}$/.test(x);
const list = (x, n) => Array.isArray(x) && x.length <= n;
const num = (x, min, max) => Number.isInteger(x) && x >= min && x <= max;
function selectors(e) {
  for (const [key, allowed] of [
    ["roles", ["system", "user", "assistant"]],
    ["targets", ["system", "prefix", "suffix"]],
  ]) {
    if (
      !list(e[key], 3) ||
      !e[key].length ||
      e[key].some((t) => !allowed.includes(t))
    )
      fail("请至少选择一个有效区域");
  }
  if (!["or", "and"].includes(e.rule)) fail("匹配模式无效");
}
export function checkBook(b) {
  if (!b || !list(b.entries, 2000) || !list(b.categories, 2000))
    fail("世界书最多 2000 条");
  if (
    !b.defaults ||
    Object.keys(b.defaults).some(
      (k) => !["roles", "rule", "targets"].includes(k),
    )
  )
    fail("世界书默认样式无效");
  selectors(b.defaults);
  const groups = new Set(),
    ids = new Set();
  for (const c of b.categories) {
    if (!id(c.id) || groups.has(c.id) || !text(c.name, 120)) fail("分类无效");
    groups.add(c.id);
  }
  for (const e of b.entries) {
    if (
      !e ||
      !id(e.id) ||
      ids.has(e.id) ||
      !text(e.name, 120) ||
      !(e.categoryId === null || groups.has(e.categoryId)) ||
      !list(e.keywords, 200) ||
      !e.keywords.every((k) => text(k, 1000) && k.trim()) ||
      !text(e.body, 40000) ||
      !num(e.depth, 0, 20) ||
      !num(e.order, 0, 9999) ||
      !["regular", "weighted"].includes(e.mode) ||
      typeof e.enabled !== "boolean" ||
      typeof e.regex !== "boolean" ||
      !Number.isFinite(e.chance) ||
      e.chance < 0 ||
      e.chance > 100 ||
      !list(e.contents, 200) ||
      !e.contents.every(
        (v) =>
          v &&
          text(v.text, 40000) &&
          Number.isFinite(v.weight) &&
          v.weight >= 0,
      )
    )
      fail("世界书条目格式或长度无效");
    ids.add(e.id);
    selectors(e);
  }
  return b;
}
export function validate(c) {
  if (!c || Object.keys(c).some((k) => !Object.hasOwn(empty(), k)))
    fail("配置含有无法识别的字段");
  for (const k of ["system", "prefix", "suffix", "css", "prefill"])
    if (!text(c[k], 200000)) fail("文本过长或格式无效");
  for (const k of ["usePrefill", "showAvatar", "showBackground"])
    if (typeof c[k] !== "boolean") fail("开关格式无效");
  if (!num(c.depth, 0, 200)) fail("记忆设定须为 0 至 200 的整数");
  for (const k of ["desktop", "mobile"])
    if (
      typeof c[k] !== "string" ||
      c[k].length > 5592450 ||
      (c[k] &&
        !/^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+=*$/.test(c[k]))
    )
      fail("背景图须为 4 MB 以内的本地 PNG、JPEG 或 WebP");
  checkBook(c.worldbook);
  if (
    !list(c.rules, 100) ||
    c.rules.some(
      (r) =>
        !r ||
        !id(r.id) ||
        !text(r.name, 120) ||
        !text(r.pattern, 4000) ||
        !text(r.replacement, 40000) ||
        !["all", "assistant", "user"].includes(r.scope) ||
        typeof r.enabled !== "boolean" ||
        typeof r.renderOnly !== "boolean" ||
        typeof r.flags !== "string" ||
        !/^[gimsu]*$/.test(r.flags) ||
        new Set(r.flags).size !== r.flags.length,
    )
  )
    fail("正则脚本无效");
  for (const r of c.rules)
    if (r.pattern)
      try {
        new RegExp(r.pattern, r.flags);
      } catch {
        fail("正则表达式有误：" + r.name);
      }
  return c;
}
export function decode(source) {
  if (typeof source !== "string" || source.length > 16 * 1024 * 1024)
    fail("配置文件超过 16 MB");
  const v = JSON.parse(source);
  if (v?.format !== FORMAT) fail("请选择本插件导出的配置 JSON");
  return validate(v.config);
}
export function encode(config) {
  return JSON.stringify({ format: FORMAT, config: validate(config) }, null, 2);
}
export const hasContent = (c) =>
  [c.system, c.prefix, c.suffix].some((x) => x.trim()) ||
  c.worldbook.entries.some(
    (e) => e.body.trim() || e.contents.some((v) => v.text.trim()),
  );
let connection;
function db() {
  return (connection ??= new Promise((ok, no) => {
    const req = indexedDB.open("rpg-user-custom-config-v1", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("profiles");
    req.onsuccess = () => ok(req.result);
    req.onerror = () => {
      connection = null;
      no(req.error);
    };
  }));
}
export async function read(key) {
  const d = await db();
  return new Promise((ok, no) => {
    const q = d.transaction("profiles").objectStore("profiles").get(key);
    q.onsuccess = () => ok(q.result ?? null);
    q.onerror = () => no(q.error);
  });
}
export async function save(key, config, version) {
  const d = await db();
  return new Promise((ok, no) => {
    const tx = d.transaction("profiles", "readwrite"),
      os = tx.objectStore("profiles"),
      q = os.get(key);
    let conflict = false;
    q.onsuccess = () => {
      if ((q.result?.version ?? 0) !== version) {
        conflict = true;
        tx.abort();
        return;
      }
      os.put(
        { version: version + 1, config: config ? validate(config) : null },
        key,
      );
    };
    tx.oncomplete = () => ok(version + 1);
    tx.onabort = tx.onerror = () =>
      no(
        Error(
          conflict
            ? "配置已在另一窗口更新，请重新打开后再编辑。"
            : "配置未保存，请检查浏览器存储空间。",
        ),
      );
  });
}
export async function effective(session) {
  const [g, s] = await Promise.all([
    read("global"),
    session ? read(session) : null,
  ]);
  return localStorage.getItem("rpg-custom-paused:" + session) === "1"
    ? []
    : [g?.config, s?.config].filter(Boolean);
}
