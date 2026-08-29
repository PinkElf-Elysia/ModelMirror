import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const expected = Object.freeze({
  "ai-town-authority-boundary": "9c0b509fc6b0d5e8560fd2e14ea1274350770cf522d98efd4e9ae021eabbd641",
  "cloudevents-core-metadata": "e327435c858d19fd171e4ab9781a01fc22dfa949d23c4220976529ebd16a1aa3",
  "concordia-action-resolution": "b0e8c7a249c409d49dd2db56515daf79959bbc8a77f83200d97566233d9eac84",
  "langgraph-checkpoint-replay": "2083a18b8c2fa4394e37518102ab5370a627009cbe1b5c784047ce34a1c98600",
  "sotopia-evaluation-separation": "43f2daf6f82a35d1c6f14dadf896123686bf8d14db15595f2813859b08b4967a",
});
const expectedLockSha256 = "fa84760c67bcb6787147ed3a0fc4b3527d65d5d021873d77418497f884a81237";
try {
  const lockBytes = readFileSync(path.join(moduleRoot, "third-party", "npc-authority-references", "reference.lock.json"));
  if (createHash("sha256").update(lockBytes).digest("hex") !== expectedLockSha256) throw new Error();
  const lock = JSON.parse(lockBytes.toString("utf8"));
  const ids = lock.references?.map((entry) => entry.id) ?? [];
  if (lock.schemaVersion !== 1 || ids.length !== 5 || new Set(ids).size !== 5 || ids.join("\0") !== [...ids].sort().join("\0")) throw new Error();
  for (const entry of lock.references) {
    if (expected[entry.id] !== entry.fileSha256 || !/^[0-9a-f]{40}$/.test(entry.commit) || entry.reuse === "production-dependency") throw new Error();
  }
  console.log(`R19_REFERENCES_OK references=${ids.length}`);
} catch {
  console.error("R19_REFERENCE_LOCK_INVALID");
  process.exitCode = 1;
}
