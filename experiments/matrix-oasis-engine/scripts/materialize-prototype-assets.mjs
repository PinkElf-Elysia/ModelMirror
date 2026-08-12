import { lstat, mkdir, mkdtemp, open, realpath, rename } from "node:fs/promises";
import path from "node:path";
import {
  materializePrototypeAssetBundle,
  planPrototypeAssets,
} from "@matrix-oasis/prototype-asset-pipeline";
import { executeMaterializePrototypeAssetsCli } from "./lib/prototype-asset-cli-core.mjs";

const moduleRoot = path.dirname(import.meta.dirname);
const result = await executeMaterializePrototypeAssetsCli({
  args: process.argv.slice(2),
  tempRoot: path.resolve(path.parse(process.cwd()).root, "tmp"),
  environmentRoot: path.join(moduleRoot, "examples", "scene-bundles", "kenney-prototype", "assets"),
  services: { lstat, mkdir, mkdtemp, openFile: open, realpath, rename },
  planPrototypeAssets,
  materializePrototypeAssetBundle,
});
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exitCode = result.exitCode;
