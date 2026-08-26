import path from "node:path";
import { fileURLToPath } from "node:url";
import { createR18DiscoveryPlan } from "./lib/r18-discovery-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

try {
  process.stdout.write(`${JSON.stringify(createR18DiscoveryPlan({ moduleRoot }))}\n`);
} catch {
  process.stderr.write("R18_DISCOVERY_PLAN_INTERNAL_ERROR\n");
  process.exitCode = 2;
}
