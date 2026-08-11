import { lstat, mkdtemp, open, readFile, realpath, rename, rm } from "node:fs/promises";
import path from "node:path";
import {
  createOpenAICompatibleProvider,
  generatePrototype,
} from "@matrix-oasis/prototype-generator";
import { executeGeneratePrototypeCli } from "./lib/prototype-cli-core.mjs";

const tempRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
const result = await executeGeneratePrototypeCli({
  args: process.argv.slice(2),
  tempRoot,
  environment: process.env,
  readFile,
  openFile: open,
  mkdtemp,
  rename,
  rm,
  realpath,
  lstat,
  createOpenAICompatibleProvider,
  generatePrototype,
});

if (result.stdout) {
  process.stdout.write(result.stdout);
}
if (result.stderr) {
  process.stderr.write(result.stderr);
}
process.exitCode = result.exitCode;
