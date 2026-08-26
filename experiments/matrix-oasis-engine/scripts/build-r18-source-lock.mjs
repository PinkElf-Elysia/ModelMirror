import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildR18SourceLock, canonicalR18SourceLock } from "./lib/r18-source-build-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const args = process.argv.slice(2);
const readOption = (name) => {
  const index = args.indexOf(name);
  if (index < 0 || index === args.length - 1 || args[index + 1].startsWith("--")) throw Object.assign(new Error("invalid"), { code: "R18_SOURCE_BUILD_ARGUMENT_INVALID" });
  return args[index + 1];
};

try {
  const value = buildR18SourceLock({
    moduleRoot,
    searchDirectory: readOption("--search-dir"),
    identityDirectory: readOption("--identity-dir"),
    documentsDirectory: readOption("--documents-dir"),
  });
  const output = path.join(moduleRoot, "third-party", "v2-landscape-references", "reference.lock.json");
  const text = canonicalR18SourceLock(value);
  try {
    if (readFileSync(output, "utf8") !== text) throw Object.assign(new Error("drift"), { code: "R18_SOURCE_BUILD_EXISTING_LOCK_DRIFT" });
    process.stdout.write("R18_SOURCE_LOCK_UNCHANGED\n");
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    writeFileSync(output, text, { encoding: "utf8", flag: "wx" });
    process.stdout.write("R18_SOURCE_LOCK_BUILT\n");
  }
} catch (error) {
  process.stderr.write(`${error?.code || "R18_SOURCE_BUILD_INTERNAL_ERROR"}\n`);
  process.exitCode = 2;
}
