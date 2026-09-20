import path from "node:path";
import { fileURLToPath } from "node:url";
import { trustR22TemporaryRoot } from "./lib/r22-cli-core.mjs";
import { launchR22LivePreview, parseR22LivePreviewArguments } from "./lib/r22-live-preview.mjs";

const scriptFile = fileURLToPath(import.meta.url); const temporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");
export async function runR22Preview(args, overrides = {}) {
  const root = await trustR22TemporaryRoot(overrides.temporaryRoot ?? temporaryRoot, overrides);
  const parsed = parseR22LivePreviewArguments(args, root.path);
  return launchR22LivePreview({ ...parsed, moduleRoot: path.dirname(path.dirname(scriptFile)),
    onPhysicalEvidence: (result) => process.stdout.write(`R22_LIVE_OBSERVATION_CAPTURED ${JSON.stringify({ output: result.output,
      observationSha256: result.observationSha256, medianFpsMilli: result.medianFpsMilli,
      performancePassed: result.performancePassed, cognitionActionVerified: result.cognitionActionVerified })}\n`),
    onPhysicalFailure: (diagnostic) => process.stdout.write(`R22_LIVE_PHYSICAL_REJECTED ${JSON.stringify({
      code: diagnostic.code, stage: diagnostic.stage })}\n`) }, overrides);
}
if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try { const result = await runR22Preview(process.argv.slice(2)); if (result.ready !== true) throw new Error(result.reason ?? "R22_PREVIEW_LAUNCHER_UNAVAILABLE");
    const stop = async () => { try { await result.cleanup(); process.exitCode = 0; } catch { process.exitCode = 2; } };
    process.once("SIGINT", () => { void stop(); }); process.once("SIGTERM", () => { void stop(); });
    void result.termination.then((ended) => { if (ended.ok !== true) process.exitCode = 2;
      process.stdout.write(`R22_LIVE_TERMINATED ${JSON.stringify({ stage: ended.stage, reason: ended.reason,
        exitCode: ended.code, cleanupFailure: ended.cleanupFailure })}\n`); });
    process.stdout.write(`MATRIX_OASIS_R22_COGNITION_PREVIEW_READY ${JSON.stringify({ ready: true, qualificationStatus: result.qualificationStatus, host: result.host, port: result.port, providerMode: result.providerMode })}\n`); }
  catch { process.stderr.write("NPC_COGNITION_INTERNAL_ERROR\n"); process.exitCode = 2; }
}
