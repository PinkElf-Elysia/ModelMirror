import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { executePackCli } from "./lib/pack-input-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function main() {
  let validateAuthoringGamePackJson;
  let AuthoringGamePackOperationalError;
  try {
    ({
      validateAuthoringGamePackJson,
      AuthoringGamePackOperationalError,
    } = await import("@matrix-oasis/game-pack-validator"));
  } catch {
    return {
      exitCode: 2,
      stdout: "",
      stderr: "PACK_CLI_INTERNAL_ERROR\n",
    };
  }

  return executePackCli({
    args: process.argv.slice(2),
    moduleRoot,
    readFile: fs.readFile,
    realpath: fs.realpath,
    stat: fs.stat,
    validateAuthoringGamePackJson,
    AuthoringGamePackOperationalError,
  });
}

const result = await main();

if (result.stdout !== "") {
  process.stdout.write(result.stdout);
}
if (result.stderr !== "") {
  process.stderr.write(result.stderr);
}
process.exitCode = result.exitCode;
