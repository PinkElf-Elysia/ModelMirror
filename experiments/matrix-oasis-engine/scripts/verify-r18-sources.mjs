import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifyR18Sources } from "./lib/r18-source-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

try {
  const report = verifyR18Sources({ moduleRoot });
  process.stdout.write(`R18_SOURCES_OK ${JSON.stringify(report)}\n`);
} catch (error) {
  process.stderr.write(`${error?.code || "R18_SOURCE_INTERNAL_ERROR"}\n`);
  process.exitCode = 2;
}
