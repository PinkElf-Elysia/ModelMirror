import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { importR18QualificationEvidence, R18_QUALIFICATION_LOCK } from "./lib/r18-evidence-import-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

try {
  const args = process.argv.slice(2);
  const replaceExisting = args.length === 3 && args[2] === "--replace-existing";
  if (![2, 3].includes(args.length) || args[0] !== "--evidence-root" || (args.length === 3 && !replaceExisting)) throw Object.assign(new Error("argument"), { code: "R18_QUALIFICATION_IMPORT_ARGUMENT_INVALID" });
  const imported = importR18QualificationEvidence({ moduleRoot, evidenceRoot: args[1] });
  const output = path.join(moduleRoot, ...R18_QUALIFICATION_LOCK.split("/"));
  if (fs.existsSync(output)) {
    if (fs.readFileSync(output, "utf8") !== imported.canonicalJson) {
      if (!replaceExisting) throw Object.assign(new Error("drift"), { code: "R18_QUALIFICATION_LOCK_EXISTING_OUTPUT_DRIFT" });
      const stat = fs.lstatSync(output);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) throw Object.assign(new Error("output"), { code: "R18_QUALIFICATION_LOCK_OUTPUT_INVALID" });
      fs.writeFileSync(output, imported.canonicalJson, { encoding: "utf8", flag: "w" });
      if (fs.readFileSync(output, "utf8") !== imported.canonicalJson) throw Object.assign(new Error("output"), { code: "R18_QUALIFICATION_LOCK_OUTPUT_INVALID" });
    }
  } else {
    fs.writeFileSync(output, imported.canonicalJson, { encoding: "utf8", flag: "wx" });
  }
  process.stdout.write(`R18_QUALIFICATION_IMPORT_OK candidates=${imported.value.entries.length} evidenceSetSha256=${imported.value.evidenceSetSha256}\n`);
} catch (error) {
  process.stderr.write(`${error?.code || "R18_QUALIFICATION_IMPORT_INTERNAL_ERROR"}\n`);
  process.exitCode = 2;
}
