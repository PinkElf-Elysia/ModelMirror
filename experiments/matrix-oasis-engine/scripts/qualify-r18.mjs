import path from "node:path";
import { fileURLToPath } from "node:url";
import { qualifyR18Candidate } from "./lib/r18-harness-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function parse(argv) {
  if (argv.length !== 6) throw Object.assign(new Error("argument"), { code: "R18_QUALIFICATION_ARGUMENT_INVALID" });
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) values.set(argv[index], argv[index + 1]);
  if (values.size !== 3 || !values.has("--candidate") || !values.has("--source") || !values.has("--output")) throw Object.assign(new Error("argument"), { code: "R18_QUALIFICATION_ARGUMENT_INVALID" });
  return { candidateId: values.get("--candidate"), sourceDir: values.get("--source"), outputDir: values.get("--output") };
}

try {
  const request = parse(process.argv.slice(2));
  await qualifyR18Candidate({ moduleRoot, ...request });
} catch (error) {
  process.stderr.write(`${error?.code || "R18_QUALIFICATION_INTERNAL_ERROR"}\n`);
  process.exitCode = 2;
}
