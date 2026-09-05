import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  decodeCanonicalR22Record,
  directR22TemporaryChild,
  parseR22Pairs,
  publishR22Artifacts,
  readStableR22File,
  trustR22TemporaryRoot,
  sha256,
} from "./lib/r22-cli-core.mjs";
import { synthesizeNpcCognitionPolicyDocument } from "./lib/r22-qualification-core.mjs";

const scriptFile = fileURLToPath(import.meta.url);
const defaultTemporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");

export async function runSynthesizeNpcCognitionPolicy(args, overrides = {}) {
  const temporaryRoot = overrides.temporaryRoot ?? defaultTemporaryRoot;
  const root = await trustR22TemporaryRoot(temporaryRoot, overrides);
  const names = {
    "--runtime-pack": "runtimeGamePack", "--runtime-receipt": "runtimeReceipt", "--authority-policy": "authorityPolicy",
    "--behavior-policy": "behaviorPolicy", "--entity-binding": "npcEntityBinding", "--derived-state-bundle": "derivedStateBundle", "--output": "output",
  };
  const required = ["runtimeGamePack", "runtimeReceipt", "authorityPolicy", "behaviorPolicy", "npcEntityBinding", "derivedStateBundle", "output"];
  const values = parseR22Pairs(args, names, required); const records = new Map();
  for (const key of required.filter((value) => value !== "output")) records.set(key, await readStableR22File(directR22TemporaryChild(values[key], root), 16 * 1024 * 1024, root, overrides));
  const input = Object.fromEntries([...records].map(([key, record]) => [`${key}Json`, decodeCanonicalR22Record(record).text]));
  const result = synthesizeNpcCognitionPolicyDocument(input);
  const output = directR22TemporaryChild(values.output, root);
  await publishR22Artifacts({ output, temporaryRoot: root.path, artifacts: new Map([["npc-cognition-policy.json", result.canonicalNpcCognitionPolicyJson]]) }, overrides);
  return Object.freeze({ ok: true, output, policySha256: sha256(result.canonicalNpcCognitionPolicyJson) });
}

if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try { process.stdout.write(`${JSON.stringify(await runSynthesizeNpcCognitionPolicy(process.argv.slice(2)))}\n`); }
  catch { process.stderr.write("NPC_COGNITION_INTERNAL_ERROR\n"); process.exitCode = 2; }
}
