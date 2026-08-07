import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { auditBoundary } from "./lib/boundary-core.mjs";

const args = process.argv.slice(2);
let rootArgument = null;
let jsonOutput = false;

for (let index = 0; index < args.length; index += 1) {
  const argument = args[index];
  if (argument === "--json") {
    jsonOutput = true;
  } else if (argument === "--root" && args[index + 1]) {
    rootArgument = args[index + 1];
    index += 1;
  } else {
    console.error("BOUNDARY_CHECK_ARGUMENT_ERROR");
    process.exit(2);
  }
}

const defaultRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const moduleRoot = rootArgument ? path.resolve(rootArgument) : defaultRoot;

try {
  const policy = JSON.parse(
    await fs.readFile(path.join(moduleRoot, "module-boundary.json"), "utf8"),
  );
  const report = await auditBoundary({ moduleRoot, policy });

  if (jsonOutput) {
    console.log(JSON.stringify(report, null, 2));
  } else if (report.ok) {
    console.log(
      `BOUNDARY_OK checked=${report.checkedFiles} tracked=${report.trackedFiles}`,
    );
  } else {
    console.error(`BOUNDARY_FAILED violations=${report.violations.length}`);
    for (const violation of report.violations) {
      console.error(`${violation.rule}: ${violation.path} - ${violation.message}`);
    }
  }

  process.exitCode = report.ok ? 0 : 1;
} catch {
  console.error("BOUNDARY_CHECK_OPERATIONAL_ERROR");
  process.exitCode = 2;
}
