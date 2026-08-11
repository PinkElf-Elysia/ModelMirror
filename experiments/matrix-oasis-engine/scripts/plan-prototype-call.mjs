import { lstat, readFile, realpath } from "node:fs/promises";
import path from "node:path";
import { executePlanPrototypeCli } from "./lib/prototype-cli-core.mjs";

const tempRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
const result = await executePlanPrototypeCli({
  args: process.argv.slice(2),
  tempRoot,
  environment: process.env,
  readFile,
  realpath,
  lstat,
});

if (result.stdout) {
  process.stdout.write(result.stdout);
}
if (result.stderr) {
  process.stderr.write(result.stderr);
}
process.exitCode = result.exitCode;
