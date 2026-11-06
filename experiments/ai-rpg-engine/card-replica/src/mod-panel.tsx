import { useEffect, useState } from "react";
import { Modal } from "./modal";
import { acquire, acquired, selectedMods, toggleMod } from "./mod-library";
import "./mod-panel.css";

type Mod = {
  id: string;
  card: string;
  name: string;
  description: string;
  version: string;
  language: string;
  source: string;
  released: string;
  chars: number;
  text: string;
};
type Tab = "专用 Mod" | "公用 Mod" | "我的 Mod";
const tabs: Tab[] = ["专用 Mod", "公用 Mod", "我的 Mod"];
export function ModPanel({
  card,
  session,
  busy,
}: {
  card: string;
  session: string;
  busy: boolean;
}) {
  const [open, show] = useState(false);
  return (
    <>
      <button className="secondary" onClick={() => show(true)}>
        模组
      </button>
      {open && (
        <Panel
          key={card + ":" + session}
          {...{ card, session, busy }}
          close={() => show(false)}
        />
      )}
    </>
  );
}
function Panel({
  card,
  session,
  busy,
  close,
}: {
  card: string;
  session: string;
  busy: boolean;
  close: () => void;
}) {
  const [tab, setTab] = useState<Tab>("专用 Mod"),
    [query, search] = useState(""),
    [recent, filterRecent] = useState(false);
  const [sort, order] = useState("created"),
    [language, lang] = useState(""),
    [items, setItems] = useState<Mod[] | null>(null);
  const [expanded, expand] = useState(""),
    [error, report] = useState(""),
    [notice, tell] = useState(""),
    [attempt, retry] = useState(0);
  const [owned, setOwned] = useState<string[]>([]),
    [active, setActive] = useState<string[]>([]);
  function sync(list: Mod[]) {
    setOwned(list.filter((m) => acquired(m.id)).map((m) => m.id));
    setActive(selectedMods(card, session));
  }
  useEffect(() => {
    const controller = new AbortController();
    report("");
    setItems(null);
    fetch("/rpg-app/mod-panel/catalog", { signal: controller.signal })
      .then(async (r) => {
        if (!r.ok) throw Error();
        const { items: list } = await r.json();
        if (!controller.signal.aborted) {
          sync(list);
          setItems(list);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted)
          report("无法读取 Mod 目录或本地装配记录。");
      });
    return () => controller.abort();
  }, [card, session, attempt]);
  useEffect(() => {
    if (!items) return;
    const refresh = () => {
      try {
        sync(items);
      } catch {
        report("无法读取本地装配记录。");
      }
    };
    window.addEventListener("storage", refresh);
    return () => window.removeEventListener("storage", refresh);
  }, [items, card, session]);
  const wanted = query.trim().toLocaleLowerCase();
  const rows = (items ?? []).filter(
    (m) =>
      (tab === "我的 Mod"
        ? owned.includes(m.id)
        : m.card === (tab === "公用 Mod" ? "*" : card)) &&
      (!language || m.language === language) &&
      (!recent ||
        Date.now() - Date.parse(m.released + "T00:00:00Z") <= 30 * 86400000) &&
      (!wanted ||
        [m.name, m.description, m.source].some((t) =>
          t.toLocaleLowerCase().includes(wanted),
        )),
  );
  rows.sort((a, b) =>
    sort === "name"
      ? a.name.localeCompare(b.name, "zh-Hans")
      : sort === "length"
        ? b.chars - a.chars
        : b.released.localeCompare(a.released),
  );
  function change(m: Mod) {
    try {
      if (!acquired(m.id)) {
        acquire(m.id);
        tell("已获取「" + m.name + "」，可在兼容会话中使用。");
      } else {
        const on = !selectedMods(card, session).includes(m.id);
        toggleMod(card, session, m.id, on);
        tell(
          "「" +
            m.name +
            "」" +
            (on ? "已启用，下次发送剧情时生效。" : "已停用。"),
        );
      }
      sync(items!);
      report("");
    } catch {
      report("操作未保存，请检查浏览器存储权限。");
    }
  }
  return (
    <Modal title="模组" close={close} className="chat-dialog rpg-mod-panel">
      <aside className="mod-info">
        <strong>Mod 说明</strong>
        <details>
          <summary>
            建议适量启用 Mod　<span>更多说明⌄</span>
          </summary>
          <p>
            获取后，在当前会话点击「使用」。可以同时装配多个
            Mod，下一次发送时按示例顺序加入剧情提示词。启用状态按本浏览器和存档分别保存。
          </p>
          <p>古代模拟器的三份示例各有激活条件，可在详情中查看完整正文。</p>
        </details>
      </aside>
      <nav aria-label="Mod 分类">
        {tabs.map((t) => (
          <button
            key={t}
            aria-pressed={tab === t}
            onClick={() => {
              setTab(t);
              expand("");
            }}
          >
            {t}
          </button>
        ))}
      </nav>
      <div className="mod-filters">
        <button aria-pressed={!recent} onClick={() => filterRecent(false)}>
          全部 Mod
        </button>
        <button
          title="最近 30 天"
          aria-pressed={recent}
          onClick={() => filterRecent(true)}
        >
          近期更新
        </button>
        <select
          aria-label="排序"
          value={sort}
          onChange={(e) => order(e.target.value)}
        >
          <option value="created">创建时间</option>
          <option value="name">名称</option>
          <option value="length">字数</option>
        </select>
        <select
          aria-label="语言"
          value={language}
          onChange={(e) => lang(e.target.value)}
        >
          <option value="">全部语言</option>
          <option value="zh-Hans">简体中文</option>
        </select>
        <input
          type="search"
          aria-label="搜索 Mod"
          placeholder="搜索 Mod…"
          value={query}
          onChange={(e) => search(e.target.value)}
        />
      </div>
      {error && (
        <p role="alert">
          {error} <button onClick={() => retry((i) => i + 1)}>重试</button>
        </p>
      )}
      {items === null && !error ? (
        <p role="status">正在读取 Mod…</p>
      ) : (
        items && (
          <>
            <p className="mod-count">展示的 Mod（{rows.length}）</p>
            {rows.length === 0 ? (
              <p className="mod-empty">
                {query || recent || language
                  ? "没有符合筛选条件的 Mod。"
                  : tab === "我的 Mod"
                    ? "尚未获取 Mod。可在专用 Mod 中查看官方示例。"
                    : tab === "公用 Mod"
                      ? "暂无公用 Mod。"
                      : "这张卡片暂无专用 Mod。"}
              </p>
            ) : (
              <div className="mod-scroll">
                <table>
                  <thead>
                    <tr>
                      {["名称", "来源", "简介", "语言", "字数", "操作"].map(
                        (n) => (
                          <th key={n} scope="col">
                            {n}
                          </th>
                        ),
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((m) => (
                      <tr key={m.id}>
                        <th scope="row">
                          {m.name}
                          <small>v{m.version}</small>
                        </th>
                        <td>{m.source}</td>
                        <td>
                          <p>{m.description}</p>
                          <button
                            className="mod-more"
                            aria-expanded={expanded === m.id}
                            onClick={() =>
                              expand(expanded === m.id ? "" : m.id)
                            }
                          >
                            {expanded === m.id ? "收起" : "更多"}
                          </button>
                          {expanded === m.id && <pre>{m.text}</pre>}
                        </td>
                        <td>简体中文</td>
                        <td>{m.chars}</td>
                        <td>
                          <button
                            className={
                              "mod-action" +
                              (active.includes(m.id) ? " on" : "")
                            }
                            disabled={
                              busy ||
                              (owned.includes(m.id) &&
                                m.card !== card &&
                                m.card !== "*")
                            }
                            aria-label={
                              (active.includes(m.id)
                                ? "停用"
                                : owned.includes(m.id)
                                  ? "使用"
                                  : "获取") + m.name
                            }
                            onClick={() => change(m)}
                          >
                            {!owned.includes(m.id)
                              ? "获取"
                              : m.card !== card && m.card !== "*"
                                ? "古代专用"
                                : active.includes(m.id)
                                  ? "停用"
                                  : "使用"}
                          </button>
                          {active.includes(m.id) && <small>当前已启用</small>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )
      )}
      <footer aria-live="polite">
        {notice || "当前会话已启用 " + active.length + " 个 Mod"}
      </footer>
    </Modal>
  );
}
