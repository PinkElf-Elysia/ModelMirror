import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolveGodotBinary } from "./lib/godot-core.mjs";
import {
  parseR16CreatorQualificationArguments,
  qualifyR16Creator,
  R16CreatorQualificationOperationalError,
} from "./lib/r16-creator-core.mjs";

export const R16_CREATOR_QUALIFICATION_MARKER =
  "MATRIX_OASIS_R16_CREATOR_QUALIFIED";

export async function runR16CreatorQualificationCli(args, operations = {}) {
  const parsed = parseR16CreatorQualificationArguments(args, operations.temporaryRoot);
  const godot = (operations.resolveGodotBinary ?? resolveGodotBinary)();
  if (godot?.version !== "4.6.3" || typeof godot.command !== "string" || godot.command.length === 0) {
    throw new R16CreatorQualificationOperationalError("GODOT_4_6_3_NOT_AVAILABLE");
  }
  const stages = [];
  const result = await (operations.qualifyR16Creator ?? qualifyR16Creator)({
    ...parsed,
    godotBin: godot.command,
    godotVersion: godot.version,
    onStage: async (stage) => {
      stages.push(stage);
      await operations.onStage?.(stage);
    },
  }, operations.overrides);
  return Object.freeze({ result, stages: Object.freeze(stages.slice()) });
}

async function main() {
  try {
    const { result, stages } = await runR16CreatorQualificationCli(process.argv.slice(2));
    if (!result?.ok) {
      process.stdout.write(`${JSON.stringify({
        reportVersion: 1,
        valid: false,
        diagnostics: result?.diagnostics ?? [],
        stages,
      })}\n`);
      process.exitCode = 1;
      return;
    }
    process.stdout.write(`${R16_CREATOR_QUALIFICATION_MARKER} ${JSON.stringify({
      cacheLevel: result.cacheLevel,
      reusedQualification: result.reusedQualification,
      spatialSolutionSha256: result.qualification?.hashes?.spatialSolutionSha256,
      evidenceRunId: result.qualification?.evidence?.runId,
      stages,
    })}\n`);
  } catch (error) {
    process.stderr.write(`${error?.code ?? "R16_CREATOR_QUALIFICATION_INTERNAL_ERROR"}\n`);
    process.exitCode = 2;
  }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) {
  await main();
}
