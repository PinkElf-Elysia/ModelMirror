import { lstatSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildR18FinalLandscape, R18_FINAL_OUTPUTS, verifyR18FinalLandscape } from "./lib/r18-finalize-core.mjs";
import { buildR18DesktopLandscape, R18_LANDSCAPE_OUTPUTS, verifyR18DesktopLandscape } from "./lib/r18-landscape-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const args = process.argv.slice(2);
const replaceExisting = args.length === 1 && args[0] === "--replace-existing";

function publish(relative, text) {
  const output = path.join(moduleRoot, ...relative.split("/"));
  try {
    if (readFileSync(output, "utf8") !== text) {
      if (!replaceExisting) throw Object.assign(new Error("drift"), { code: "R18_LANDSCAPE_EXISTING_OUTPUT_DRIFT" });
      const stat = lstatSync(output);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) throw Object.assign(new Error("output"), { code: "R18_LANDSCAPE_OUTPUT_INVALID" });
      writeFileSync(output, text, { encoding: "utf8", flag: "w" });
      if (readFileSync(output, "utf8") !== text) throw Object.assign(new Error("output"), { code: "R18_LANDSCAPE_OUTPUT_INVALID" });
    }
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    writeFileSync(output, text, { encoding: "utf8", flag: "wx" });
  }
}

try {
  if (args.length > 1 || (args.length === 1 && !replaceExisting)) throw Object.assign(new Error("argument"), { code: "R18_LANDSCAPE_ARGUMENT_INVALID" });
  const built = buildR18DesktopLandscape({ moduleRoot });
  const final = buildR18FinalLandscape({ moduleRoot });
  publish(R18_LANDSCAPE_OUTPUTS.catalog, built.catalogText);
  publish(R18_LANDSCAPE_OUTPUTS.audit, built.auditText);
  publish(R18_FINAL_OUTPUTS.decision, final.landscapeText);
  publish(R18_FINAL_OUTPUTS.roadmap, final.roadmapText);
  process.stdout.write(`R18_LANDSCAPE_OK ${JSON.stringify({ desktop: verifyR18DesktopLandscape({ moduleRoot }), final: verifyR18FinalLandscape({ moduleRoot }) })}\n`);
} catch (error) {
  process.stderr.write(`${error?.code || "R18_LANDSCAPE_INTERNAL_ERROR"}\n`);
  process.exitCode = 2;
}
