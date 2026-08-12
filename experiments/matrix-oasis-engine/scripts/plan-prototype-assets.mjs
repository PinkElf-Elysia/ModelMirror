import { lstat, open, realpath } from "node:fs/promises";
import path from "node:path";
import { planPrototypeAssets } from "@matrix-oasis/prototype-asset-pipeline";
import { executePlanPrototypeAssetsCli } from "./lib/prototype-asset-cli-core.mjs";

const result = await executePlanPrototypeAssetsCli({
  args: process.argv.slice(2),
  tempRoot: path.resolve(path.parse(process.cwd()).root, "tmp"),
  services: { lstat, openFile: open, realpath },
  planPrototypeAssets,
});
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exitCode = result.exitCode;
