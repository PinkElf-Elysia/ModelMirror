import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";

const entries = [
  [
    "strength",
    "实力系统",
    "追踪境界、功法、装备与资源。在有战斗、修炼或超自然力量体系的世界中生成实力状态栏。",
    "c613da403f8b3f195873a1e96f8e92826f5d7b186db692d36e30bab370a36657",
  ],
  [
    "world",
    "世界观",
    "记录架空世界的法则、势力与局势。世界观确立后保留核心设定，并随剧情更新局势。",
    "ce49c36b2336733fa555445b91b08ac67a7009a1fbad057d51c030bf7c22425e",
  ],
  [
    "dynasty",
    "王朝管理",
    "当玩家成为掌权者时，在财产与方位之间加入王朝状态，追踪国力、朝堂、外交与危机。",
    "afffece2f62a53ace7ed659c818cd28a4a641cd6ee6d58cc4d2d2bab91682480",
  ],
];
let loaded;
async function load() {
  return Promise.all(
    entries.map(async ([slug, name, description, sha256]) => {
      const bytes = await readFile(
        new URL("./official/ancient/" + slug + ".md", import.meta.url),
      );
      if (createHash("sha256").update(bytes).digest("hex") !== sha256)
        throw Error("MOD_RESOURCE_MISMATCH");
      const text = bytes.toString("utf8");
      return Object.freeze({
        id: "ancient." + slug + ".v1",
        card: "ancient",
        name,
        description,
        version: "1.0.0",
        language: "zh-Hans",
        source: "官方示例",
        released: "2026-11-04",
        chars: [...text.trimEnd()].length,
        sha256,
        text,
      });
    }),
  );
}
export const modCatalog = () =>
  (loaded ??= load().catch((e) => {
    loaded = null;
    throw e;
  }));
export async function resolveMods(card, ids) {
  if (
    !Array.isArray(ids) ||
    !ids.length ||
    ids.length > entries.length ||
    new Set(ids).size !== ids.length
  )
    throw Error("MOD_SELECTION_INVALID");
  const list = (await modCatalog()).filter(
    (m) => m.card === card && ids.includes(m.id),
  );
  if (list.length !== ids.length) throw Error("MOD_SELECTION_INVALID");
  return list;
}
export function assembleMods(messages, mods) {
  if (!mods.length) return messages;
  if (messages[0]?.role !== "system" || typeof messages[0].content !== "string")
    throw Error("MOD_STORY_INVALID");
  return [
    {
      ...messages[0],
      content: [messages[0].content, ...mods.map((m) => m.text)].join("\n\n"),
    },
    ...messages.slice(1),
  ];
}
