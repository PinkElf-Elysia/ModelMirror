import { fileURLToPath } from "node:url";
import path from "node:path";
import { resolveGodotBinary } from "./lib/godot-core.mjs";
import {
  launchR15EvidencePreview,
  parseR15PreviewArguments,
  R15_PREVIEW_READY_MARKER,
  selectR15EvidenceRun,
} from "./lib/r15-preview-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporaryRoot = path.resolve(path.parse(moduleRoot).root, "tmp");

async function main() {
  let launched = null;
  try {
    const parsed = parseR15PreviewArguments(process.argv.slice(2), temporaryRoot);
    const selected = await selectR15EvidenceRun({ ...parsed, temporaryRoot });
    launched = await launchR15EvidencePreview({ selected, godot: resolveGodotBinary(), moduleRoot });
    process.stdout.write(`${R15_PREVIEW_READY_MARKER} run=${selected.runId}\n`);
    const stop = async () => { if (launched) await launched.cleanup(); };
    process.once("SIGINT", () => { void stop(); }); process.once("SIGTERM", () => { void stop(); });
    await new Promise((resolve) => launched.child.once("exit", resolve));
  } catch (error) {
    const code = typeof error?.message === "string" && /^R15_PREVIEW_[A-Z0-9_]+$/u.test(error.message)
      ? error.message : "R15_PREVIEW_INTERNAL_ERROR";
    process.stderr.write(`${code}\n`); process.exitCode = 2;
  } finally {
    if (launched) await launched.cleanup().catch(() => {});
  }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) await main();
