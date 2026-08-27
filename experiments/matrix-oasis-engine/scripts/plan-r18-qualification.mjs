import path from "node:path";
import { fileURLToPath } from "node:url";
import { planR18Qualification } from "./lib/r18-harness-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

try {
  const argv = process.argv.slice(2);
  if (argv.length !== 2 || argv[0] !== "--candidate") throw Object.assign(new Error("argument"), { code: "R18_QUALIFICATION_PLAN_ARGUMENT_INVALID" });
  process.stdout.write(`${planR18Qualification({ moduleRoot, candidateId: argv[1] }).canonicalJson}\n`);
} catch (error) {
  process.stderr.write(`${error?.code || "R18_QUALIFICATION_PLAN_INTERNAL_ERROR"}\n`);
  process.exitCode = 2;
}
