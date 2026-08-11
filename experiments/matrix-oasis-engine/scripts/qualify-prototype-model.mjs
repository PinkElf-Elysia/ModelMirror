import { lstat, mkdtemp, open, realpath, rename, rm } from "node:fs/promises";
import path from "node:path";
import {
  createOpenAICompatibleProvider,
  generatePrototype,
} from "@matrix-oasis/prototype-generator";
import { executeQualifyPrototypeModelCli } from "./lib/prototype-cli-core.mjs";

const tempRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
const result = await executeQualifyPrototypeModelCli({
  args: process.argv.slice(2),
  tempRoot,
  environment: process.env,
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
