import { build } from "../client/node_modules/esbuild/lib/main.js";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import {
  buildSkillSearchClientSummary,
  buildSkillSearchIndex,
} from "./skill-search-index.mjs";
import { buildSkillRuntimeIndex } from "./skill-runtime-index.mjs";

const OUTPUT_PATH = resolve("server/skills/data/skill_runtime_index.json");
const SEARCH_OUTPUT_PATH = resolve("server/skills/data/skill_search_index.json");
const CLIENT_SUMMARY_PATH = resolve(
  "client/src/data/skillSearchIndex.generated.json",
);

async function loadNeedCandidates() {
  const entry = `
    import { loadSkillNeedCandidates } from "./client/src/data/skillNeedCandidates.ts";
    import { loadSkillSetMemberIndex } from "./client/src/data/skillSetMembers.ts";
    const [candidates, memberIndex] = await Promise.all([
      loadSkillNeedCandidates(),
      loadSkillSetMemberIndex(),
    ]);
    export default { candidates, memberIndexFingerprint: memberIndex.fingerprint };
  `;
  const result = await build({
    absWorkingDir: resolve("."),
    bundle: true,
    format: "esm",
    logLevel: "silent",
    platform: "node",
    stdin: {
      contents: entry,
      loader: "ts",
      resolveDir: resolve("."),
      sourcefile: "skill-runtime-index-entry.ts",
    },
    target: "node22",
    write: false,
  });
  const bundled = result.outputFiles?.[0]?.text;
  if (!bundled) throw new Error("Unable to bundle Skill candidate loader.");
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(bundled).toString("base64")}`;
  return (await import(moduleUrl)).default;
}

async function main() {
  const source = await loadNeedCandidates();
  const trustIndex = JSON.parse(
    await readFile("server/skills/data/skill_trust_index.json", "utf8"),
  );
  const index = buildSkillRuntimeIndex({ ...source, trustIndex });
  const searchIndex = buildSkillSearchIndex({
    ...source,
    runtimeIndex: index,
    trustIndex,
  });
  const clientSummary = buildSkillSearchClientSummary(searchIndex);
  const entries = [
    [OUTPUT_PATH, `${JSON.stringify(index)}\n`],
    [SEARCH_OUTPUT_PATH, `${JSON.stringify(searchIndex)}\n`],
    [CLIENT_SUMMARY_PATH, `${JSON.stringify(clientSummary)}\n`],
  ];
  const previous = new Map();
  const replaced = [];
  try {
    for (const [targetPath, contents] of entries) {
      await mkdir(dirname(targetPath), { recursive: true });
      try {
        previous.set(targetPath, await readFile(targetPath));
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
        previous.set(targetPath, null);
      }
      await writeFile(`${targetPath}.tmp`, contents, "utf8");
    }
    for (const [targetPath] of entries) {
      await rename(`${targetPath}.tmp`, targetPath);
      replaced.push(targetPath);
    }
  } catch (error) {
    for (const targetPath of replaced.reverse()) {
      const contents = previous.get(targetPath);
      if (contents === null) {
        await rm(targetPath, { force: true });
      } else {
        await writeFile(`${targetPath}.rollback`, contents);
        await rename(`${targetPath}.rollback`, targetPath);
      }
    }
    for (const [targetPath] of entries) {
      await rm(`${targetPath}.tmp`, { force: true });
      await rm(`${targetPath}.rollback`, { force: true });
    }
    throw error;
  }
  console.log(
    `Skill indexes published: ${index.candidates.length} runtime / ${searchIndex.candidates.length} search candidates, fingerprint ${searchIndex.fingerprint}`,
  );
}

await main();
