import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildR18DesktopLandscape, R18_LANDSCAPE_OUTPUTS, verifyR18DesktopLandscape } from "./lib/r18-landscape-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function publish(relative, text) {
  const output = path.join(moduleRoot, ...relative.split("/"));
  try {
    if (readFileSync(output, "utf8") !== text) throw Object.assign(new Error("drift"), { code: "R18_LANDSCAPE_EXISTING_OUTPUT_DRIFT" });
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    writeFileSync(output, text, { encoding: "utf8", flag: "wx" });
  }
}

try {
  const built = buildR18DesktopLandscape({ moduleRoot });
  publish(R18_LANDSCAPE_OUTPUTS.catalog, built.catalogText);
  publish(R18_LANDSCAPE_OUTPUTS.audit, built.auditText);
  process.stdout.write(`R18_LANDSCAPE_OK ${JSON.stringify(verifyR18DesktopLandscape({ moduleRoot }))}\n`);
} catch (error) {
  process.stderr.write(`${error?.code || "R18_LANDSCAPE_INTERNAL_ERROR"}\n`);
  process.exitCode = 2;
}
