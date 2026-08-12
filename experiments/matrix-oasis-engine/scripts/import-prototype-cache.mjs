import { mkdir, mkdtemp, open, realpath, rename, rm, rmdir, lstat } from "node:fs/promises";
import os from "node:os";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { PrototypeCacheOperationalError, importPrototypeCache } from "./lib/prototype-cache-core.mjs";

const services = Object.freeze({ mkdir, mkdtemp, openFile: open, realpath, rename, rm, rmdir, lstat });

try {
  const result = await importPrototypeCache({ args: process.argv.slice(2), temporaryRoot: os.tmpdir(), services,
    assemblePrototypeScene, canonicalizeJsonValue });
  process.stdout.write(`PROTOTYPE_CACHE_IMPORTED run=${result.runId} files=${result.files}\n`);
} catch (error) {
  const code = error instanceof PrototypeCacheOperationalError ? error.code : "PROTOTYPE_CACHE_INTERNAL_ERROR";
  process.stderr.write(`${code}\n`); process.exitCode = 2;
}
