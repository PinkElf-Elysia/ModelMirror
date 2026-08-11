import fs from "node:fs/promises";
import path from "node:path";

const ROOT = path.resolve(".");
const REPORT_PATH = path.resolve(process.argv[2] || "docs/file-output-readiness.json");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function read(relativePath) {
  return fs.readFile(path.join(ROOT, relativePath), "utf8");
}

const report = JSON.parse(await fs.readFile(REPORT_PATH, "utf8"));
assert(report.schema_version === 1, "output readiness schema_version must be 1");
assert(report.audit_only === true, "output readiness must remain audit-only");
assert(
  report.protocol_version === "modelmirror-file-output-capabilities-v1",
  "unexpected output capability protocol",
);
assert(report.input_protocol_version === "modelmirror-file-capabilities-v2", "input protocol drift");
assert(report.input_registry_version === "modelmirror-file-formats-v5", "input registry drift");
assert(report.feature_flags.FILE_OUTPUT_ASSETS_ENABLED === false, "master output flag must default false");
assert(report.feature_flags.CHAT_FILE_OUTPUT_TOOL_ENABLED === false, "Chat output tool flag must default false");

const modules = new Map(report.module_readiness.map((item) => [item.purpose, item]));
for (const purpose of ["chat", "agent", "workflow"]) {
  assert(modules.has(purpose), `missing ${purpose} output readiness`);
}
assert(modules.get("chat").verification_status === "provider_canary_verified", "Chat canary evidence is missing");
assert(modules.get("chat").interaction_status === "ready", "verified Chat target must be ready");
assert(report.verified_chat_targets?.length === 1, "exactly one Chat output target may be verified");
const verifiedTarget = report.verified_chat_targets[0];
assert(verifiedTarget.endpoint === "https://openrouter.ai/api/v1/chat/completions", "verified Chat endpoint drift");
assert(verifiedTarget.model_id === "openai/gpt-5.6-luna", "verified Chat model drift");
assert(verifiedTarget.provider === "openai", "verified Chat provider drift");
assert(verifiedTarget.fallback_allowed === false, "verified Chat target must disable fallback");

const groups = report.format_groups.flatMap((item) => item.formats);
assert(new Set(groups).size === groups.length, "output formats must not occur in multiple groups");
assert(groups.length === 21, `expected 21 output formats, got ${groups.length}`);
for (const group of report.format_groups) {
  if (group.group.startsWith("captured_")) {
    assert(group.reuse === "chat_only", `${group.group} must remain Chat-only for reuse`);
    assert(group.save_rag === false, `${group.group} must keep RAG save disabled`);
  }
}

const [envRoot, envServer, compose, contracts, service, api, main, chatOutput, ragApi, tray] = await Promise.all([
  read(".env.example"),
  read("server/.env.example"),
  read("docker-compose.yml"),
  read("server/file_assets/output_contracts.py"),
  read("server/file_assets/output_service.py"),
  read("server/file_assets/api.py"),
  read("server/main.py"),
  read("server/file_assets/chat_output.py"),
  read("server/rag/api.py"),
  read("client/src/components/FileOutputTray.tsx"),
]);

for (const [label, source] of [["root env", envRoot], ["server env", envServer]]) {
  assert(/^FILE_OUTPUT_ASSETS_ENABLED=false$/m.test(source), `${label} master flag is not fail-closed`);
  assert(/^CHAT_FILE_OUTPUT_TOOL_ENABLED=false$/m.test(source), `${label} Chat flag is not fail-closed`);
}
assert(/FILE_OUTPUT_ASSETS_ENABLED:\s*\$\{FILE_OUTPUT_ASSETS_ENABLED:-false\}/.test(compose), "Compose master flag is not fail-closed");
assert(/CHAT_FILE_OUTPUT_TOOL_ENABLED:\s*\$\{CHAT_FILE_OUTPUT_TOOL_ENABLED:-false\}/.test(compose), "Compose Chat flag is not fail-closed");
assert(contracts.includes('modelmirror-file-output-capabilities-v1'), "output protocol constant is missing");
assert(service.includes('clean_purpose is FilePurpose.CHAT'), "Chat-only media reuse guard is missing");
assert(service.includes('item.preview_kind in {"image", "audio", "video"}'), "media reuse format guard is missing");

for (const endpoint of ["/output-capabilities", "/outputs", "/preview", "/download", "/retry", "/confirm-reuse"]) {
  assert(api.includes(endpoint), `missing output API marker ${endpoint}`);
}
assert(main.includes('event: output_file'), "Chat output_file SSE event is missing");
assert(chatOutput.includes('modelmirror_create_file'), "allowlisted Chat file tool is missing");
assert(chatOutput.includes('"openai/gpt-5.6-luna": "openai"'), "verified Chat target is missing from runtime");
assert(chatOutput.includes('"allow_fallbacks": False'), "verified Chat provider fallback guard is missing");
assert(ragApi.includes('/documents/from-file-output'), "RAG file-output import endpoint is missing");
assert(tray.includes('该模块没有与此输出类型对应的输入流程。'), "non-Chat media reuse UI reason is missing");

for (const evidence of report.evidence) {
  await fs.access(path.join(ROOT, evidence));
}

console.log(`PASS file-output readiness: ${groups.length} formats / ${report.module_readiness.length} modules`);
