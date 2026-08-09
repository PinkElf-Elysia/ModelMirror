import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { executeCompilePackCli } from "./lib/runtime-pack-input-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function main() {
  try {
    const [compiler, contracts, validator] = await Promise.all([
      import("@matrix-oasis/game-pack-compiler"),
      import("@matrix-oasis/runtime-pack-contracts"),
      import("@matrix-oasis/runtime-pack-validator"),
    ]);
    return executeCompilePackCli({
      args: process.argv.slice(2),
      moduleRoot,
      readFile: fs.readFile,
      openFile: fs.open,
      mkdir: fs.mkdir,
      mkdtemp: fs.mkdtemp,
      rename: fs.rename,
      rm: fs.rm,
      realpath: fs.realpath,
      stat: fs.stat,
      lstat: fs.lstat,
      compileAuthoringGamePackJson: compiler.compileAuthoringGamePackJson,
      GamePackCompilerOperationalError:
        compiler.GamePackCompilerOperationalError,
      canonicalizeJsonValue: contracts.canonicalizeJsonValue,
      validateRuntimeGamePackJson: validator.validateRuntimeGamePackJson,
      RuntimeGamePackValidatorOperationalError:
        validator.RuntimeGamePackValidatorOperationalError,
    });
  } catch {
    return {
      exitCode: 2,
      stdout: "",
      stderr: "PACK_COMPILE_CLI_INTERNAL_ERROR\n",
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
