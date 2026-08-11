import {resolveGodotBinary} from "./lib/godot-core.mjs";
import {
  GodotSplatQualificationError,
  parseSplatQualificationArguments,
  qualifyGodotSplat,
} from "./lib/godot-splat-qualification-core.mjs";

try {
  const request = parseSplatQualificationArguments(process.argv.slice(2));
  const godot = resolveGodotBinary();
  const report = qualifyGodotSplat({...request, godotCommand: godot.command, godotVersion: godot.version});
  console.log(`GODOT_SPLAT_QUALIFICATION_OK recommendation=${report.recommendation} checks=${report.checks.length}`);
} catch (error) {
  const code = error instanceof GodotSplatQualificationError && /^[A-Z][A-Z0-9_]+$/u.test(error.code)
    ? error.code
    : "GODOT_SPLAT_QUALIFICATION_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_SPLAT_QUALIFICATION_ARGUMENT_ERROR" ? 2 : 1;
}
