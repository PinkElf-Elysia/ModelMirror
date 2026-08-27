import path from "node:path";
import { fileURLToPath } from "node:url";
import { executeR18Discovery } from "./lib/r18-discovery-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const args = process.argv.slice(2);
const outputIndex = args.indexOf("--output");
const output = outputIndex >= 0 ? args[outputIndex + 1] : undefined;
const acknowledged = args.includes("--acknowledge-public-network");
const mode = args.includes("--diagnose-documents-only")
  ? "documents-only"
  : args.includes("--diagnose-github-only")
    ? "github-only"
    : args.includes("--search-only")
      ? "search-only"
      : args.includes("--identity-only")
        ? "identity-only"
        : "full";
const searchEvidenceIndex = args.indexOf("--search-evidence");
const searchEvidencePath = searchEvidenceIndex >= 0 ? args[searchEvidenceIndex + 1] : null;

try {
  const report = await executeR18Discovery({ moduleRoot, output, acknowledged, mode, searchEvidencePath });
  process.stdout.write(`R18_DISCOVERY_OK ${JSON.stringify(report)}\n`);
} catch (error) {
  process.stderr.write(`${error?.code || "R18_DISCOVERY_INTERNAL_ERROR"}${error?.details ? ` ${JSON.stringify(error.details)}` : ""}\n`);
  process.exitCode = 2;
}
