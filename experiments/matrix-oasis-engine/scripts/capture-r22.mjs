import path from "node:path";
import { fileURLToPath } from "node:url";
import { decodeCanonicalR22Record, publishR22Artifacts, readStableR22File, trustR22TemporaryRoot } from "./lib/r22-cli-core.mjs";
import { R22_QUALIFICATION_FILES, verifyR22OfflineQualification } from "./lib/r22-qualification-core.mjs";

const scriptFile = fileURLToPath(import.meta.url); const temporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");
export async function runR22Capture(args, overrides = {}) {
  if (!Array.isArray(args) || args.length !== 6 || args[0] !== "--qualified-root" || args[2] !== "--case-spec" ||
      args[4] !== "--output" || !path.isAbsolute(args[1]) || !path.isAbsolute(args[3]) ||
      !path.isAbsolute(args[5])) throw new Error("R22_CLI_ARGUMENT_INVALID");
  const root = await trustR22TemporaryRoot(overrides.temporaryRoot ?? temporaryRoot, overrides);
  const source = path.resolve(args[1]); const caseSpecPath = path.resolve(args[3]);
  const verificationOverrides = { ...overrides, caseSpecPath };
  const before = await verifyR22OfflineQualification(source, root.path, verificationOverrides); const artifacts = new Map();
  for (const name of R22_QUALIFICATION_FILES) artifacts.set(name, decodeCanonicalR22Record(await readStableR22File(path.join(source, name), 16 * 1024 * 1024, root, overrides)).text);
  const published = await publishR22Artifacts({ output: path.resolve(args[5]), temporaryRoot: root.path, artifacts,
    beforeRename: async () => { const now = await verifyR22OfflineQualification(source, root.path, verificationOverrides); if (now.reportSha256 !== before.reportSha256) throw new Error("R22_SOURCE_CHANGED"); },
    verifyPublished: (output) => verifyR22OfflineQualification(output, root.path, verificationOverrides) }, overrides);
  return Object.freeze({ ok: true, output: published.output, reportSha256: before.reportSha256,
    readyForPreview: before.readyForPreview, r22GodotQualified: before.r22GodotQualified,
    r22PerformanceQualified: before.r22PerformanceQualified });
}
if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try { process.stdout.write(`R22_CAPTURE_READY ${JSON.stringify(await runR22Capture(process.argv.slice(2)))}\n`); }
  catch { process.stderr.write("NPC_COGNITION_INTERNAL_ERROR\n"); process.exitCode = 2; }
}
