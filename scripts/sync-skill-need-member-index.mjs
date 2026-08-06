import { readFile, rename, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { buildSkillSetMemberIndex } from "./skill-member-index.mjs";
import {
  MAX_SKILL_DOCUMENT_BYTES,
  buildSkillNeedMemberSearchIndex,
} from "./skill-need-member-metadata.mjs";

const MEMBER_INDEX_PATH = resolve(
  "client/src/data/skillSetMembers.generated.json",
);
const SEARCH_INDEX_PATH = resolve(
  "client/src/data/skillNeedMembers.generated.json",
);
const REQUEST_CONCURRENCY = 20;
const MAX_ATTEMPTS = 3;

function githubRepository(repoUrl) {
  const parsed = new URL(repoUrl);
  const parts = parsed.pathname.replace(/\.git$/i, "").split("/").filter(Boolean);
  if (parsed.hostname.toLowerCase() !== "github.com" || parts.length !== 2) {
    throw new Error(`成员来源不是标准 GitHub 仓库：${repoUrl}`);
  }
  return { owner: parts[0], repository: parts[1] };
}

function rawDocumentUrl(member) {
  const { owner, repository } = githubRepository(member.repoUrl);
  const encodedPath = `${member.subPath}/SKILL.md`
    .split("/")
    .map(encodeURIComponent)
    .join("/");
  return `https://raw.githubusercontent.com/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/${member.verifiedCommit}/${encodedPath}`;
}

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

async function fetchMemberDocument(member) {
  const url = rawDocumentUrl(member);
  let lastError;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: { "User-Agent": "ModelMirror-Skill-Need-Indexer/1.0" },
        signal: AbortSignal.timeout(30_000),
      });
      if (!response.ok) {
        const error = new Error(
          `读取固定提交 Skill 文档失败（HTTP ${response.status}）：${member.id}`,
        );
        if (response.status !== 408 && response.status !== 429 && response.status < 500) {
          throw error;
        }
        lastError = error;
      } else {
        const declaredLength = Number(response.headers.get("content-length") ?? 0);
        if (declaredLength > MAX_SKILL_DOCUMENT_BYTES) {
          throw new Error(`Skill 文档过大：${member.id}`);
        }
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (bytes.byteLength > MAX_SKILL_DOCUMENT_BYTES) {
          throw new Error(`Skill 文档过大：${member.id}`);
        }
        return bytes;
      }
    } catch (error) {
      lastError = error;
      const message = error instanceof Error ? error.message : String(error);
      if (
        /HTTP 4\d\d/.test(message) &&
        !/HTTP (408|429)/.test(message)
      ) {
        throw error;
      }
    }
    if (attempt < MAX_ATTEMPTS) await delay(250 * 2 ** (attempt - 1));
  }
  throw new Error(`成员元数据读取失败，未发布部分索引：${member.id}`, {
    cause: lastError,
  });
}

function concurrentReader(limit) {
  let active = 0;
  const queued = [];
  const advance = () => {
    while (active < limit && queued.length > 0) {
      const item = queued.shift();
      active += 1;
      void item
        .read()
        .then(item.resolve, item.reject)
        .finally(() => {
          active -= 1;
          advance();
        });
    }
  };
  return (member) =>
    new Promise((resolveRead, rejectRead) => {
      queued.push({
        read: () => fetchMemberDocument(member),
        resolve: resolveRead,
        reject: rejectRead,
      });
      advance();
    });
}

async function main() {
  const previous = JSON.parse(await readFile(MEMBER_INDEX_PATH, "utf8"));
  const memberIndex = buildSkillSetMemberIndex(
    previous.skillSets,
    previous.members,
  );
  const repositoryCount = new Set(
    Object.values(memberIndex.members).map(
      (member) => `${member.repoUrl.toLowerCase()}@${member.verifiedCommit}`,
    ),
  ).size;
  console.log(
    `开始读取 ${Object.keys(memberIndex.members).length} 个成员的固定提交 SKILL.md，涉及 ${repositoryCount} 个仓库提交……`,
  );
  let completed = 0;
  const readWithLimit = concurrentReader(REQUEST_CONCURRENCY);
  const searchIndex = await buildSkillNeedMemberSearchIndex({
    memberIndex,
    readDocument: async (member) => {
      const bytes = await readWithLimit(member);
      completed += 1;
      if (completed % 250 === 0) {
        console.log(`已读取 ${completed}/${Object.keys(memberIndex.members).length}`);
      }
      return bytes;
    },
  });

  const memberTemporary = `${MEMBER_INDEX_PATH}.tmp`;
  const searchTemporary = `${SEARCH_INDEX_PATH}.tmp`;
  await Promise.all([
    writeFile(memberTemporary, `${JSON.stringify(memberIndex, null, 2)}\n`, "utf8"),
    writeFile(searchTemporary, `${JSON.stringify(searchIndex, null, 2)}\n`, "utf8"),
  ]);
  await rename(memberTemporary, MEMBER_INDEX_PATH);
  await rename(searchTemporary, SEARCH_INDEX_PATH);
  console.log(
    `成员需求索引已完整发布：${Object.keys(searchIndex.members).length} 项，指纹 ${memberIndex.fingerprint}`,
  );
}

await main();
