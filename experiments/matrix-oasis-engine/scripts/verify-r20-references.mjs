import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
try {
  const bytes = readFileSync(path.join(moduleRoot, "third-party", "npc-behavior-references", "reference.lock.json"));
  const lock = JSON.parse(bytes.toString("utf8"));
  const ids = lock.references?.map((entry) => entry.id) ?? [];
  if (lock.schemaVersion !== 1 || ids.length !== 4 || new Set(ids).size !== 4 || ids.join("\0") !== [...ids].sort().join("\0")) throw new Error();
  const beehave = lock.references.find((entry) => entry.id === "beehave-compatibility");
  const limbo = lock.references.find((entry) => entry.id === "limboai-godot-4.6.3");
  if (beehave?.commit !== "773a5f6dd9b3433cdb8735ab35e9043d4cd60674" || beehave?.reuse !== "backup-reference-only") throw new Error();
  if (limbo?.commit !== "e2be164b736ccc00c945612a7269280ed5378a9b" || limbo?.releaseAssetSha256 !== "ceb1757103744454d87ef12884e9d30978ff6edda6b4f09ff8da4d88e11a814e") throw new Error();
  if (lock.references.some((entry) => entry.reuse === "production-dependency")) throw new Error();
  console.log(`R20_REFERENCES_OK references=${ids.length} lockSha256=${createHash("sha256").update(bytes).digest("hex")}`);
} catch {
  console.error("R20_REFERENCE_LOCK_INVALID");
  process.exitCode = 1;
}
