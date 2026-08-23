import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rename, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { selectR15EvidenceRun } from "./lib/r15-preview-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporaryRoot = path.resolve(path.parse(moduleRoot).root, "tmp");
function digest(bytes) { return `sha256:${createHash("sha256").update(bytes).digest("hex")}`; }

export function parseR15CaptureArguments(args, tempRoot = temporaryRoot) {
  if (!Array.isArray(args) || args.length !== 4) throw new Error("R15_CAPTURE_ARGUMENT_INVALID");
  const values = Object.create(null); const names = { "--evidence-run-root": "evidenceRunRoot", "--output": "output" };
  for (let index = 0; index < args.length; index += 2) {
    const name = names[args[index]]; const value = args[index + 1];
    if (!name || Object.hasOwn(values, name) || typeof value !== "string" || !path.isAbsolute(value) || value.includes("\0")) {
      throw new Error("R15_CAPTURE_ARGUMENT_INVALID");
    }
    values[name] = path.resolve(value);
  }
  if (Object.values(values).some((value) => path.dirname(value) !== path.resolve(tempRoot))) {
    throw new Error("R15_CAPTURE_ARGUMENT_INVALID");
  }
  return Object.freeze({ ...values });
}

async function main() {
  let staging = null;
  try {
    const parsed = parseR15CaptureArguments(process.argv.slice(2));
    const selected = await selectR15EvidenceRun({ ...parsed, temporaryRoot });
    staging = await mkdtemp(path.join(temporaryRoot, `.${path.basename(parsed.output)}-`));
    await mkdir(path.join(staging, "media"));
    await writeFile(path.join(staging, "runtime-replay-plan.json"), selected.replayPlanJson, { encoding: "utf8", flag: "wx" });
    await writeFile(path.join(staging, "runtime-evidence.json"), selected.canonicalEvidenceJson, { encoding: "utf8", flag: "wx" });
    const media = [];
    for (const [relative, bytes] of [...selected.mediaFiles].sort(([left], [right]) => left.localeCompare(right))) {
      await writeFile(path.join(staging, "media", path.basename(relative)), bytes, { flag: "wx" });
      media.push({ path: relative, byteLength: bytes.byteLength, sha256: digest(bytes) });
    }
    await writeFile(path.join(staging, "capture-manifest.json"), canonicalizeJsonValue({
      format: "matrix-oasis.runtime-evidence-capture", formatVersion: "0.1.0", runId: selected.runId, media,
    }), { encoding: "utf8", flag: "wx" });
    await rename(staging, parsed.output); staging = null;
    process.stdout.write(`R15_CAPTURE_OK run=${selected.runId} files=${media.length}\n`);
  } catch (error) {
    const code = typeof error?.message === "string" && /^R15_(?:CAPTURE|PREVIEW)_[A-Z0-9_]+$/u.test(error.message)
      ? error.message : "R15_CAPTURE_INTERNAL_ERROR";
    process.stderr.write(`${code}\n`); process.exitCode = 2;
  } finally { if (staging) await rm(staging, { recursive: true, force: true }).catch(() => {}); }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) await main();
