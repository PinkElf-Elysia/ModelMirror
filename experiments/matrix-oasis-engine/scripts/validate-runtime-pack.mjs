import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { executeRuntimePackCli } from "./lib/runtime-pack-input-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function main() {
  try {
    const validator = await import("@matrix-oasis/runtime-pack-validator");
    return executeRuntimePackCli({
      args: process.argv.slice(2),
      moduleRoot,
      readFile: fs.readFile,
      realpath: fs.realpath,
      stat: fs.stat,
      validateRuntimeGamePackJson: validator.validateRuntimeGamePackJson,
      RuntimeGamePackValidatorOperationalError:
        validator.RuntimeGamePackValidatorOperationalError,
    });
  } catch {
    return {
      exitCode: 2,
      stdout: "",
      stderr: "RUNTIME_PACK_CLI_INTERNAL_ERROR\n",
    };
  }
}

const result = await main();
if (result.stdout !== "") {
  process.stdout.write(result.stdout);
}
if (result.stderr !== "") {
  process.stderr.write(result.stderr);
}
process.exitCode = result.exitCode;
