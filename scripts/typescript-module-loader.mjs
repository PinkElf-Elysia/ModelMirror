import { access, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import ts from "../server/orchestration_worker/node_modules/typescript/lib/typescript.js";

const SOURCE_SUFFIXES = [".ts", ".tsx", ".json"];

export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    if (!specifier.startsWith(".") || !context.parentURL) throw error;
    for (const suffix of SOURCE_SUFFIXES) {
      const candidate = new URL(`${specifier}${suffix}`, context.parentURL);
      try {
        await access(fileURLToPath(candidate));
        return { url: candidate.href, shortCircuit: true };
      } catch {
        // Continue to the next explicit extension.
      }
    }
    throw error;
  }
}
export async function load(url, context, nextLoad) {
  if (url.endsWith(".json")) {
    const payload = JSON.parse(await readFile(fileURLToPath(url), "utf8"));
    return {
      format: "module",
      source: `export default ${JSON.stringify(payload)};`,
      shortCircuit: true,
    };
  }
  if (url.endsWith(".ts") || url.endsWith(".tsx")) {
    const source = await readFile(fileURLToPath(url), "utf8");
    const output = ts.transpileModule(source, {
      compilerOptions: {
        jsx: ts.JsxEmit.ReactJSX,
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
      },
      fileName: fileURLToPath(url),
      reportDiagnostics: true,
    });
    if (output.diagnostics?.some((diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error)) {
      throw new Error(`Unable to transpile ${fileURLToPath(url)}.`);
    }
    return { format: "module", source: output.outputText, shortCircuit: true };
  }
  return nextLoad(url, context);
}
