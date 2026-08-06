const MAX_SKILL_DOCUMENT_BYTES = 512 * 1024;
const MAX_NAME_LENGTH = 160;
const MAX_DESCRIPTION_LENGTH = 1200;
const MAX_TAG_COUNT = 20;

function cleanScalar(value) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return "";
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    const inner = trimmed.slice(1, -1);
    return trimmed.startsWith('"')
      ? inner.replace(/\\n/g, " ").replace(/\\"/g, '"')
      : inner.replace(/''/g, "'");
  }
  return trimmed.replace(/\s+#.*$/, "").trim();
}

function parseInlineList(value) {
  const scalar = cleanScalar(value);
  if (!scalar.startsWith("[") || !scalar.endsWith("]")) return [];
  return scalar
    .slice(1, -1)
    .split(",")
    .map(cleanScalar)
    .filter(Boolean);
}

export function parseSkillFrontmatter(markdown) {
  const normalized = markdown.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
  if (!normalized.startsWith("---\n")) {
    return { attributes: {}, body: normalized };
  }
  const lines = normalized.split("\n");
  const closingIndex = lines.findIndex(
    (line, index) => index > 0 && (line.trim() === "---" || line.trim() === "..."),
  );
  if (closingIndex < 0) return { attributes: {}, body: normalized };

  const attributes = {};
  for (let index = 1; index < closingIndex; index += 1) {
    const match = lines[index].match(/^([A-Za-z][\w-]*):(?:\s*(.*))?$/);
    if (!match) continue;
    const [, rawKey, rawValue = ""] = match;
    const key = rawKey.toLowerCase();
    if (!["name", "description", "tags", "keywords"].includes(key)) continue;

    if (rawValue === ">" || rawValue === "|" || rawValue === ">-" || rawValue === "|-") {
      const block = [];
      while (index + 1 < closingIndex && /^\s+/.test(lines[index + 1])) {
        index += 1;
        block.push(lines[index].trim());
      }
      attributes[key] = rawValue.startsWith(">")
        ? block.join(" ").replace(/\s+/g, " ").trim()
        : block.join("\n").trim();
      continue;
    }

    if ((key === "tags" || key === "keywords") && !rawValue.trim()) {
      const list = [];
      while (index + 1 < closingIndex) {
        const item = lines[index + 1].match(/^\s+-\s+(.+)$/);
        if (!item) break;
        index += 1;
        list.push(cleanScalar(item[1]));
      }
      attributes[key] = list.filter(Boolean);
      continue;
    }

    attributes[key] =
      key === "tags" || key === "keywords"
        ? parseInlineList(rawValue)
        : cleanScalar(rawValue);
  }
  return {
    attributes,
    body: lines.slice(closingIndex + 1).join("\n"),
  };
}

function stripMarkdown(value) {
  return value
    .replace(/<!--[^]*?-->/g, " ")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/[`*_~]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function firstMeaningfulParagraph(body) {
  const withoutFences = body.replace(/```[^]*?```/g, "\n").replace(/~~~[^]*?~~~/g, "\n");
  const paragraphs = withoutFences.split(/\n\s*\n+/);
  for (const paragraph of paragraphs) {
    const lines = paragraph
      .split("\n")
      .map((line) => line.trim())
      .filter(
        (line) =>
          line &&
          !line.startsWith("#") &&
          !line.startsWith("[") &&
          !/^[-*+]\s/.test(line) &&
          !/^<[^>]+>$/.test(line),
      );
    const cleaned = stripMarkdown(lines.join(" "));
    if (cleaned.length >= 24) return cleaned;
  }
  return "";
}

function pathTags(subPath) {
  return subPath
    .split("/")
    .flatMap((segment) => segment.split(/[-_.]+/))
    .map((tag) => tag.trim())
    .filter((tag) => tag.length >= 2 && !["skill", "skills"].includes(tag.toLowerCase()));
}

function uniqueTags(values) {
  const seen = new Set();
  const tags = [];
  for (const rawValue of values.flat()) {
    const value = cleanScalar(rawValue);
    const key = value.toLocaleLowerCase("en-US");
    if (!value || seen.has(key)) continue;
    seen.add(key);
    tags.push(value.slice(0, 80));
    if (tags.length >= MAX_TAG_COUNT) break;
  }
  return tags;
}

export function extractSkillNeedMemberMetadata({ bytes, member }) {
  if (!(bytes instanceof Uint8Array)) {
    throw new TypeError(`Skill 文档不是字节数据：${member.id}`);
  }
  if (bytes.byteLength > MAX_SKILL_DOCUMENT_BYTES) {
    throw new Error(`Skill 文档超过 ${MAX_SKILL_DOCUMENT_BYTES} 字节：${member.id}`);
  }
  let markdown;
  try {
    markdown = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    throw new Error(`Skill 文档不是有效 UTF-8：${member.id}`, { cause: error });
  }
  const { attributes, body } = parseSkillFrontmatter(markdown);
  const displayName = cleanScalar(attributes.name || member.name).slice(
    0,
    MAX_NAME_LENGTH,
  );
  const sourceDescription = cleanScalar(
    attributes.description || firstMeaningfulParagraph(body),
  ).slice(0, MAX_DESCRIPTION_LENGTH);
  if (!displayName) throw new Error(`Skill 文档缺少可用名称：${member.id}`);
  if (!sourceDescription) throw new Error(`Skill 文档缺少可用说明：${member.id}`);
  return {
    displayName,
    sourceDescription,
    tags: uniqueTags([
      Array.isArray(attributes.tags) ? attributes.tags : [],
      Array.isArray(attributes.keywords) ? attributes.keywords : [],
      pathTags(member.subPath),
    ]),
  };
}

export async function buildSkillNeedMemberSearchIndex({
  memberIndex,
  readDocument,
}) {
  if (memberIndex.version !== 2 || !memberIndex.fingerprint) {
    throw new Error("SkillSet 成员注册表必须先升级到版本 2 并包含指纹");
  }
  const entries = Object.entries(memberIndex.members).sort(([left], [right]) =>
    left.localeCompare(right, "en"),
  );
  const resolved = await Promise.all(
    entries.map(async ([id, member]) => {
      const bytes = await readDocument(member);
      return [id, extractSkillNeedMemberMetadata({ bytes, member })];
    }),
  );
  return {
    version: 1,
    memberIndexFingerprint: memberIndex.fingerprint,
    members: Object.fromEntries(resolved),
  };
}

export { MAX_SKILL_DOCUMENT_BYTES };
