import { build } from "../client/node_modules/esbuild/lib/main.js";
import { mkdir, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { buildSkillRuntimeIndex } from "./skill-runtime-index.mjs";

const OUTPUT_PATH = resolve("server/skills/data/skill_runtime_index.json");

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
  const index = buildSkillRuntimeIndex(source);
  await mkdir(dirname(OUTPUT_PATH), { recursive: true });
  const temporaryPath = `${OUTPUT_PATH}.tmp`;
  await writeFile(temporaryPath, `${JSON.stringify(index)}\n`, "utf8");
  await rename(temporaryPath, OUTPUT_PATH);
  console.log(
    `Skill runtime index published: ${index.candidates.length} candidates, fingerprint ${index.fingerprint}`,
  );
}

await main();
