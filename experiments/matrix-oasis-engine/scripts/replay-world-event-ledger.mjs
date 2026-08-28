import fs from "node:fs/promises";
import * as operations from "@matrix-oasis/npc-authority-runtime";
import { executeReplayWorldEventLedgerCli, R19_TEMP_ROOT } from "./lib/r19-cli-core.mjs";

const result = await executeReplayWorldEventLedgerCli({
  args: process.argv.slice(2),
  tempRoot: R19_TEMP_ROOT,
  services: { lstat: fs.lstat, realpath: fs.realpath, openFile: fs.open, mkdtemp: fs.mkdtemp, rename: fs.rename, rm: fs.rm },
  operations,
});
process.stdout.write(result.stdout);
process.stderr.write(result.stderr);
process.exitCode = result.exitCode;
