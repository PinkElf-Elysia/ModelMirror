import { lstat, mkdir, mkdtemp, open, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { recoverPrototypeRuns } from "./lib/prototype-cache-core.mjs";
import {
  SpatialCacheOperationalError,
  importSpatialPrototypeCache,
} from "./lib/spatial-cache-core.mjs";

const services = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir });
const temporaryRoot = process.platform === "win32" ? path.join(path.parse(process.cwd()).root, "tmp") : os.tmpdir();

try {
  const result = await importSpatialPrototypeCache({
    args: process.argv.slice(2), temporaryRoot, services, recoverPrototypeRuns,
    assemblePrototypeScene, assemblePrototypeSpatialScene, canonicalizeJsonValue,
  });
  process.stdout.write(`SPATIAL_PROTOTYPE_CACHE_IMPORTED run=${result.runId} files=${result.files}\n`);
} catch (error) {
  const code = error instanceof SpatialCacheOperationalError ? error.code : "SPATIAL_CACHE_INTERNAL_ERROR";
  process.stderr.write(`${code}\n`); process.exitCode = 2;
}
