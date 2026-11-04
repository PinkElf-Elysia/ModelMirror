import { effective } from "./config.mjs";
const properties = new Set(
  "color background-color font-size font-family font-weight font-style line-height letter-spacing text-align text-decoration text-shadow white-space word-break margin margin-top margin-bottom padding border border-radius border-color border-width border-style box-shadow".split(
    " ",
  ),
);
function localCss(source, root) {
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(source);
  let result = "";
  for (const rule of sheet.cssRules) {
    if (
      !(rule instanceof CSSStyleRule) ||
      !rule.selectorText ||
      !/^[\w\s.#*,:>+~()-]+$/.test(rule.selectorText) ||
      /[:][\w-]+\(/.test(rule.selectorText)
    )
      continue;
    const selectors = rule.selectorText
      .split(",")
      .map((s) => root + " " + s.trim());
    const clean = [];
    for (const prop of Array.from(rule.style)) {
      const value = rule.style.getPropertyValue(prop);
      if (
        properties.has(prop) &&
        !/[\\@{};]|url\s*\(|var\s*\(|image-set\s*\(/i.test(value)
      )
        clean.push(prop + ":" + value);
    }
    result += selectors.join(",") + "{" + clean.join(";") + "}";
  }
  return result;
}
export async function appearance(scope, el, signal) {
  const configs = await effective(scope);
  if (signal.aborted || !configs.length) return;
  const mark = "cfg-" + crypto.randomUUID(),
    root = '[data-config-view="' + mark + '"]',
    s = document.createElement("style");
  el.dataset.configView = mark;
  const g = configs[0];
  s.textContent = configs.map((c) => localCss(c.css, root)).join("\n");
  if (g.showBackground) {
    for (const [key, query] of [
      ["desktop", "(min-width:601px)"],
      ["mobile", "(max-width:600px)"],
    ])
      if (g[key])
        s.textContent +=
          "@media " +
          query +
          "{" +
          root +
          '{background-image:url("' +
          g[key] +
          '");background-size:cover;background-position:center}}';
  }
  if (g.showAvatar)
    s.textContent +=
      root +
      ' :is(.player-message,.cv-player,.cv-story h2,.turn-label)::before{display:inline-grid;place-items:center;vertical-align:middle;margin-right:.45em;width:1.6em;height:1.6em;border-radius:50%;background:#dfeaf6;color:#284a70;font-size:.8em;content:"我"}' +
      root +
      ' :is(.turn-label,.cv-story h2)::before{content:"AI"}';
  if (!g.showAvatar)
    s.textContent += root + " :is(.avatar,[data-avatar]){display:none}";
  document.head.append(s);
  signal.addEventListener(
    "abort",
    () => {
      s.remove();
      if (el.dataset.configView === mark) delete el.dataset.configView;
    },
    { once: true },
  );
}
