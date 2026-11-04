import {
  empty,
  decode,
  encode,
  validate,
  checkBook,
  hasContent,
  read,
  save,
} from "./config.mjs";
import { createWorldbookUI, TEMPLATE_FORMAT } from "./worldbook-ui.mjs";
const $ = (id) => document.getElementById(id),
  session = new URL(location.href).searchParams.get("session");
const safeId = session && /^[\w:-]{1,180}$/.test(session) ? session : null;
let scope = "global",
  draft,
  version,
  dirty = false,
  loading = 0;
const toast = (m) => ($("status").textContent = m);
const guard = (fn) => async (e) => {
  try {
    await fn(e);
  } catch (err) {
    toast(err.message);
  }
};
const changed = () => {
  dirty = true;
};
const node = (tag, text) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  return n;
};
const btn = (t, fn) => {
  const b = node("button", t);
  b.type = "button";
  b.onclick = guard(fn);
  return b;
};
function popup(title, body, action, label = "确认") {
  const p = $("popup");
  p.querySelector("h2").textContent = title;
  p.querySelector("div").replaceChildren(body);
  const f = p.querySelector("footer");
  f.replaceChildren(btn("关闭", () => p.close()));
  if (action)
    f.append(
      btn(label, async () => {
        await action();
        p.close();
      }),
    );
  p.showModal();
}
function download(text, name) {
  const u = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const a = node("a");
  a.href = u;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}
const world = createWorldbookUI({
  changed,
  toast,
  api: async (path, data) => {
    if (path.endsWith("/inspect")) {
      const x = JSON.parse(data.payload);
      if (x.format !== TEMPLATE_FORMAT) throw Error("世界书模板格式不符");
      return { worldbook: checkBook(x.worldbook) };
    }
    const e = data.worldbook.entries.find((e) => e.id === data.entryId),
      total = e.contents.reduce((n, c) => n + c.weight, 0);
    if (!Number.isFinite(total) || !total) throw Error("需要至少一个正权重");
    const units = e.contents.map((c) => (10000 * c.weight) / total),
      ints = units.map(Math.floor),
      order = units.map((v, i) => [i, v - ints[i]]).sort((a, b) => b[1] - a[1]);
    for (let i = 0, n = 10000 - ints.reduce((a, b) => a + b, 0); i < n; i++)
      ints[order[i][0]]++;
    return {
      contents: e.contents.map((c, i) => ({ ...c, weight: ints[i] / 100 })),
    };
  },
});
const tools = [
  ["¶", "标题", "## ", ""],
  ["B", "加粗", "**", "**"],
  ["𝑰", "斜体", "*", "*"],
  ["U", "下划线", "<u>", "</u>"],
  ["S", "删除线", "~~", "~~"],
  ["•", "项目列表", "- ", ""],
  ["1.", "编号列表", "1. ", ""],
  ["❞", "引用", "> ", ""],
  ["↵", "换行", "\n", ""],
  ["‹›", "行内代码", "`", "`"],
  ["</>", "代码块", "```\n", "\n```"],
  ["▦", "表格", "| 标题 |\n| --- |\n| ", " |"],
  ["▧", "图片", "![描述](", ")"],
  ["↗", "链接", "[链接文字](", ")"],
];
function rich(key, title, hint) {
  const box = node("section");
  box.className = "editor";
  const label = node("label", title);
  label.htmlFor = key;
  const help = node("p", hint);
  help.className = "hint";
  const wrap = node("div");
  wrap.className = "editorbox";
  const bar = node("div");
  bar.className = "toolbar";
  const t = node("textarea");
  t.id = key;
  t.value = draft[key];
  const count = node("output");
  const sync = () => {
    draft[key] = t.value;
    count.textContent = [...t.value].length + " 字";
    changed();
  };
  count.textContent = [...t.value].length + " 字";
  t.oninput = sync;
  let history = [t.value],
    index = 0;
  function checkpoint() {
    if (history[index] !== t.value) {
      history.splice(index + 1);
      history.push(t.value);
      if (history.length > 60) history.shift();
      index = history.length - 1;
    }
  }
  t.onchange = checkpoint;
  for (const [icon, name, before, after] of tools) {
    const b = btn(icon, () => {
      checkpoint();
      const a = t.selectionStart,
        z = t.selectionEnd;
      t.setRangeText(before + t.value.slice(a, z) + after, a, z, "select");
      sync();
      checkpoint();
      t.focus();
    });
    b.title = b.ariaLabel = name;
    bar.append(b);
  }
  for (const [icon, name, action] of [
    [
      "⌫",
      "清空",
      () => {
        checkpoint();
        t.value = "";
        sync();
        checkpoint();
      },
    ],
    [
      "↶",
      "撤销",
      () => {
        checkpoint();
        t.value = history[(index = Math.max(0, index - 1))];
        sync();
      },
    ],
    [
      "↷",
      "重做",
      () => {
        t.value = history[(index = Math.min(history.length - 1, index + 1))];
        sync();
      },
    ],
    ["⛶", "切换全屏", () => box.classList.toggle("full-editor")],
  ]) {
    const b = btn(icon, action);
    b.title = b.ariaLabel = name;
    bar.append(b);
  }
  wrap.append(bar, t, count);
  box.append(label, help, wrap);
  return box;
}
function scripts() {
  const list = $("rules");
  list.replaceChildren();
  draft.rules.forEach((r, i) => {
    const card = node("section");
    card.className = "script";
    const head = node("header");
    head.append(
      node("b", "正则脚本 " + String(i + 1).padStart(2, "0")),
      btn("删除", () => {
        draft.rules.splice(i, 1);
        changed();
        scripts();
      }),
    );
    card.append(head);
    for (const [k, title] of [
      ["enabled", "启用脚本"],
      ["name", "标题"],
      ["pattern", "匹配模式"],
      ["flags", "正则标志"],
      ["replacement", "替换文本"],
      ["scope", "影响范围"],
      ["renderOnly", "仅渲染"],
    ]) {
      const l = node("label", title + " "),
        el = node(
          k === "scope" ? "select" : k === "replacement" ? "textarea" : "input",
        );
      if (k === "scope") {
        for (const [v, n] of [
          ["all", "全部"],
          ["user", "用户输入"],
          ["assistant", "AI 输出"],
        ]) {
          const opt = node("option", n);
          opt.value = v;
          el.append(opt);
        }
      }
      if (typeof r[k] === "boolean") {
        el.type = "checkbox";
        el.checked = r[k];
      } else {
        if (el.tagName === "INPUT") el.type = "text";
        el.value = r[k];
      }
      el.oninput = () => {
        r[k] = el.type === "checkbox" ? el.checked : el.value;
        changed();
      };
      l.append(el);
      card.append(l);
    }
    list.append(card);
  });
}
function render() {
  $("prompts").replaceChildren(
    rich("prefix", "前置词", "自动拼接在本轮用户输入之前，也可触发世界书。"),
    rich("suffix", "后置词", "自动拼接在本轮用户输入之后，也可触发世界书。"),
    rich("system", "提示词", "设定故事的世界观、角色和叙述规则。"),
  );
  world.load(draft.worldbook, scope);
  world.render($("book"));
  scripts();
  for (const k of [
    "css",
    "prefill",
    "usePrefill",
    "depth",
    "showAvatar",
    "showBackground",
  ]) {
    const el = $(k);
    if (el.type === "checkbox") el.checked = draft[k];
    else el.value = draft[k];
    el.oninput = () => {
      draft[k] =
        el.type === "checkbox"
          ? el.checked
          : el.type === "number"
            ? el.valueAsNumber
            : el.value;
      $("prefill").disabled = !draft.usePrefill;
      changed();
    };
  }
  $("prefill").disabled = !draft.usePrefill;
  $("appearance").hidden = scope !== "global";
  const images = document.querySelector(".images");
  images.replaceChildren();
  for (const [key, title, ratio] of [
    ["desktop", "桌面背景图", "16:9"],
    ["mobile", "移动设备背景图", "9:16"],
  ]) {
    const field = node("section");
    field.append(node("h2", title));
    const label = node("label");
    label.className = "imagepick " + key;
    if (draft[key]) label.style.backgroundImage = "url(" + draft[key] + ")";
    const input = node("input");
    input.type = "file";
    input.accept = "image/png,image/jpeg,image/webp";
    input.onchange = guard(async () => {
      const f = input.files[0];
      if (!f) return;
      if (
        f.size > 4 * 1024 * 1024 ||
        !["image/png", "image/jpeg", "image/webp"].includes(f.type)
      )
        throw Error("请选择 4 MB 以内的 PNG、JPEG 或 WebP");
      const image = await createImageBitmap(f);
      image.close();
      const u = await new Promise((ok, no) => {
        const r = new FileReader();
        r.onload = () => ok(r.result);
        r.onerror = no;
        r.readAsDataURL(f);
      });
      draft[key] = u;
      label.style.backgroundImage = "url(" + u + ")";
      changed();
    });
    label.append(node("span", "建议 " + ratio + " · 最大 4 MB"), input);
    field.append(
      label,
      btn("移除图片", () => {
        draft[key] = "";
        label.style.backgroundImage = "";
        changed();
      }),
    );
    images.append(field);
  }
  document
    .querySelectorAll("[data-scope]")
    .forEach((b) =>
      b.setAttribute(
        "aria-selected",
        String((b.dataset.scope === "global") === (scope === "global")),
      ),
    );
}
function collect() {
  world.finish();
  draft.worldbook = world.read();
  return validate(draft);
}
async function load(key) {
  const ticket = ++loading;
  document.querySelector("main").inert = true;
  $("save").disabled = true;
  let v;
  try {
    v = await read(key);
  } finally {
    if (ticket === loading) {
      document.querySelector("main").inert = false;
      $("save").disabled = false;
    }
  }
  if (ticket !== loading) return;
  scope = key;
  draft = v?.config ?? empty();
  version = v?.version ?? 0;
  dirty = false;
  render();
  toast("");
}
function exit() {
  if (dirty && !confirm("放弃未保存的配置？")) return;
  if (parent !== window)
    parent.postMessage({ type: "rpg-config-close" }, location.origin);
  else if (opener) window.close();
  else history.back();
}
$("exit").onclick = $("cancel").onclick = exit;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !document.querySelector("dialog[open]")) exit();
});
document.querySelectorAll("[data-scope]").forEach((b) => {
  if (b.dataset.scope === "session" && !safeId) b.hidden = true;
  b.onclick = guard(() => {
    const key = b.dataset.scope === "global" ? "global" : safeId;
    if (key !== scope && (!dirty || confirm("切换前放弃未保存的更改？")))
      return load(key);
  });
});
$("save").onclick = guard(async () => {
  const c = collect();
  if (!hasContent(c))
    throw Error("请在提示词、前后置词或世界书中至少保留一个字符。");
  version = await save(scope, c, version);
  dirty = false;
  notify();
  toast("已保存");
  exit();
});
function notify() {
  const channel = new BroadcastChannel("rpg-custom-config");
  channel.postMessage("changed");
  channel.close();
  localStorage.setItem("rpg-custom-config-used", "1");
  parent.postMessage({ type: "rpg-config-changed" }, location.origin);
}
$("clear").onclick = guard(() =>
  popup(
    "清空自定义配置",
    node("p", "清空当前编辑草稿？只有保存后才会覆盖已保存的配置。"),
    async () => {
      draft = empty();
      render();
      dirty = true;
      toast("草稿已清空，可以重新填写或取消。");
    },
    "确认清空",
  ),
);
$("addRule").onclick = () => {
  draft.rules.push({
    id: crypto.randomUUID(),
    name: "",
    pattern: "",
    replacement: "",
    flags: "g",
    enabled: true,
    scope: "all",
    renderOnly: false,
  });
  changed();
  scripts();
};
function imported(c) {
  draft = c;
  render();
  changed();
  toast("已导入草稿，点击保存后生效。");
}
$("paste").onclick = () => {
  const t = node("textarea");
  t.placeholder = "粘贴导出的配置 JSON";
  popup("导入配置", t, () => imported(decode(t.value)), "导入");
};
$("upload").onclick = () => $("file").click();
$("file").onchange = guard(async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  e.target.value = "";
  if (f.size > 16 * 1024 * 1024) throw Error("文件过大");
  imported(decode(await f.text()));
});
$("export").onclick = guard(() => {
  const content = encode(collect()),
    box = node("div"),
    t = node("textarea");
  t.readOnly = true;
  t.value = content;
  box.append(
    t,
    btn("复制配置", async () => {
      await navigator.clipboard.writeText(content);
      toast("已复制配置");
    }),
  );
  popup(
    "导出配置",
    box,
    () => download(content, "RPG-自定义配置.json"),
    "下载 JSON",
  );
});
$("help").onclick = () => {
  const box = node("section");
  for (const [h, p] of [
    [
      "Markdown 排版",
      "用 # 写标题、用 - 写列表，或选中文字后点击工具栏。比如「## 角色」下面写「- 来自东域」，模型会收到带有层级的说明。",
    ],
    [
      "选择范围",
      "全局配置用于此浏览器中的聊天，会话配置只用于当前存档。两份配置均已保存时，先应用全局，再应用会话。",
    ],
    [
      "编写提示词",
      "在前置词中写「使用第一人称」，后置词中写「用一句对白收尾」。提示词用于补充系统设定。",
    ],
    [
      "按关键词触发世界书",
      "新增条目，输入「青云宗」并按回车，填写门规，选择用户触发和提示词位置。与要求所有关键词命中，或只需一个。",
    ],
    [
      "正则、预填充和样式",
      "正则按列表顺序执行，仅渲染的规则只影响聊天显示。预填充会作为 assistant 消息发给模型。CSS 用于聊天内容，背景图可分别为桌面和移动设备设置。",
    ],
    [
      "本地迁移",
      "导出配置可复制或下载 JSON。在另一浏览器选择导入配置粘贴文本，或直接导入 JSON 文件，核对后保存。世界书模板单独导入时追加条目。",
    ],
  ])
    box.append(node("h3", h), node("p", p));
  popup("自定义配置教程", box);
};
load("global").catch((e) => toast(e.message));
