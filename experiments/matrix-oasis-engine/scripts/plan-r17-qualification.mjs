import path from "node:path";
import { fileURLToPath } from "node:url";
import { planAllR17Candidates } from "./lib/r17-qualification-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
try {
  if (process.argv.length !== 2) throw Object.assign(new Error("R17_PLAN_ARGUMENT_ERROR"), { code: "R17_PLAN_ARGUMENT_ERROR" });
  console.log(planAllR17Candidates(moduleRoot));
} catch (error) { console.error(error?.code ?? "R17_PLAN_INTERNAL_ERROR"); process.exitCode = 1; }
