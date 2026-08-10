import fs from "node:fs/promises";
import path from "node:path";

const REPORT_PATH = path.resolve(process.argv[2] || "docs/file-readiness.json");
const ROOT = path.resolve(".");

const MODULES = new Set(["chat", "rag", "datax", "agent", "workflow"]);
const INPUT_KINDS = new Set(["document", "data_source", "visual_analysis"]);
const FAMILIES = new Set([
  "text", "structured", "document", "spreadsheet", "presentation",
  "email", "ebook", "archive", "image", "audio", "video", "legacy_office",
]);
const OPERATIONS = new Set([
  "extract_text", "extract_structure", "visual_extract",
  "visual_analysis", "provider_ocr", "native_attachment",
  "structured_analysis", "batch_import",
]);
const INTERACTION_STATUSES = new Set(["ready", "planned", "disabled", "not_applicable"]);
const VERIFICATION_STATUSES = new Set([
  "verified", "contract_verified", "manual_required", "failed", "not_applicable",
]);
const SUPPORT_LEVELS = new Set([
  "native", "converted", "combined", "fallback", "specialized", "unsupported",
]);
const ISOLATIONS = new Set(["in_process", "file_sidecar", "browser", "not_applicable"]);
const CONTAINERS = new Set(["none", "pdf", "zip", "ole", "mime", "archive", "columnar", "image"]);
const SIGNATURE_POLICIES = new Set(["required", "advisory", "not_applicable"]);

function fail(message) {
  throw new Error(message);
}

function assert(condition, message) {
  if (!condition) fail(message);
}

function sorted(values) {
  return [...new Set(values)].sort();
}

function assertEqual(actual, expected, label) {
  const left = JSON.stringify(sorted(actual));
  const right = JSON.stringify(sorted(expected));
  if (left !== right) fail(`${label} mismatch: expected ${right}, got ${left}`);
}

function inputKindFor(item) {
  return item.input_kind || (item.module === "datax" ? "data_source" : "document");
}

function requirementVersions(source) {
  const result = new Map();
  for (const line of source.split(/\r?\n/)) {
    const match = line.trim().match(/^([a-z0-9_-]+)(?:\[[^\]]+\])?==([^\s;]+)/i);
    if (match) result.set(match[1].toLowerCase(), match[2]);
  }
  return result;
}

function workflowDependencyVersion(source, packageName) {
  const match = source.match(new RegExp(`["]${packageName}==([^"]+)["]`, "i"));
  if (!match) fail(`Workflow lacks a pinned ${packageName} dependency`);
  return match[1];
}

function assertBackendRegistryWiring(source, label) {
  assert(/file_assets/.test(source), `${label} does not import the shared file registry`);
  assert(/get_file_format_registry/.test(source), `${label} does not resolve formats from the shared file registry`);
}

function assertFrontendRegistryWiring(source, label, purpose) {
  assert(/fileCapabilities/.test(source), `${label} does not import the shared file capability helper`);
  assert(/fetchFileCapabilities/.test(source), `${label} does not load the shared file capability API`);
  assert(/extensionsForPurpose/.test(source), `${label} does not derive its accept list from shared capabilities`);
  assert(new RegExp(`["']${purpose}["']`).test(source), `${label} does not select the ${purpose} capability scope`);
}

function assertChatFileWiring({
  chatPage,
  composer,
  capabilityClient,
  streamClient,
  mainSource,
  fileApi,
  fileService,
}) {
  assert(/import\s+ChatFileComposer/.test(chatPage), "ChatPage does not import ChatFileComposer");
  assert(/<ChatFileComposer\b/.test(chatPage), "ChatPage does not render ChatFileComposer");
  assert(/type:\s*["']input_file["']/.test(chatPage), "ChatPage does not build input_file content parts");
  assert(/fileScopeId:\s*selectedFiles/.test(chatPage), "ChatPage does not bind file requests to the active scope");

  for (const symbol of [
    "fetchFileCapabilities",
    "uploadChatFile",
    "parseChatFile",
    "deleteChatFile",
  ]) {
    assert(new RegExp(`\\b${symbol}\\b`).test(composer), `ChatFileComposer does not use ${symbol}`);
  }
  assert(/purpose:\s*["']chat["']/.test(composer), "ChatFileComposer does not request Chat capabilities");

  for (const route of [
    "/api/files/capabilities",
    "/api/files",
    "/api/files/scopes/",
  ]) {
    assert(capabilityClient.includes(route), `fileCapabilities does not call ${route}`);
  }
  assert(/includesInputFile/.test(streamClient), "fetchChatStream does not detect file content parts");
  assert(/requiresExplicitMessageEnd/.test(streamClient), "fetchChatStream does not require an explicit file completion event");
  assert(/eventName\s*===\s*["']message_end["']/.test(streamClient), "fetchChatStream does not handle message_end");

  assert(/app\.include_router\(\s*file_assets_router\s*\)/.test(mainSource), "server/main.py does not mount file_assets_router");
  assert(/class\s+InputFileContentPart\b/.test(mainSource), "server/main.py lacks the input_file request contract");
  assert(/validate_chat_file_request\(payload\)/.test(mainSource), "server/main.py does not validate Chat file requests");
  assert(/get_file_asset_service\(\)/.test(mainSource), "server/main.py does not resolve the shared file asset service");
  assert(/APIRouter\(\s*\n?\s*prefix=["']\/api\/files["']/.test(fileApi), "file_assets/api.py does not own /api/files");
  assert(/def\s+resolve_chat_inputs\b/.test(fileService), "file_assets/service.py lacks Chat input resolution");
  assert(/def\s+finalize_chat_inputs\b/.test(fileService), "file_assets/service.py lacks Chat input finalization");
}

function assertWorkflowFileWiring({
  workflowRun,
  workflowEditor,
  workflowNodeRegistry,
  mainSource,
  fileApi,
  fileService,
}) {
  assert(/\/api\/files\/capabilities\?purpose=workflow/.test(workflowRun), "WorkflowRun does not load the workflow capability scope");
  assert(/\/api\/files\?purpose=workflow/.test(workflowRun), "WorkflowRun does not list scope-bound file assets");
  assert(/\.append\(["']purpose["'],\s*["']workflow["']\)/.test(workflowRun), "WorkflowRun does not upload workflow file assets");
  assert(/assetIdVariable/.test(workflowRun), "WorkflowRun does not bind uploaded asset IDs to workflow inputs");
  assert(/sourcePathVariable/.test(workflowEditor), "WorkflowEditor does not preserve the legacy path compatibility field");
  assert(/assetIdVariable/.test(workflowEditor), "WorkflowEditor does not author the asset ID contract");
  assert(/WORKFLOW_FILE_ASSETS_ENABLED/.test(workflowNodeRegistry), "workflow node registry does not apply the feature gate");
  assert(/workflow_file_scope_id/.test(mainSource), "server/main.py lacks a fixed workflow file scope");
  assert(/resolve_workflow_document/.test(mainSource), "server/main.py does not use the scoped workflow resolver");
  assert(/def\s+resolve_workflow_document\b/.test(fileService), "file_assets/service.py lacks Workflow input resolution");
  assert(/@router\.get\(["']["'],\s*response_model=FileAssetListResponse/.test(fileApi), "file_assets/api.py does not expose scope-bound asset listing");
}

async function assertEvidencePaths(report) {
  for (const format of report.formats) {
    for (const item of format.module_readiness || []) {
      for (const group of ["implementation", "ui", "tests", "security_tests"]) {
        for (const relative of item.evidence?.[group] || []) {
          assert(typeof relative === "string" && relative.trim(), `${format.format_id} has an invalid ${group} path`);
          const absolute = path.resolve(ROOT, relative);
          const insideRoot = absolute === ROOT || absolute.startsWith(`${ROOT}${path.sep}`);
          assert(insideRoot, `${format.format_id} evidence escapes the repository: ${relative}`);
          try {
            const stat = await fs.stat(absolute);
            assert(stat.isFile(), `${format.format_id} evidence is not a file: ${relative}`);
          } catch {
            fail(`${format.format_id} evidence does not exist: ${relative}`);
          }
        }
      }
    }
  }
}

function validateReport(report) {
  assert(report.schema_version === 1, "file readiness schema_version must be 1");
  assert(/^\d{4}-\d{2}-\d{2}$/.test(report.reviewed_at || ""), "reviewed_at must be YYYY-MM-DD");
  assert(!Number.isNaN(Date.parse(`${report.reviewed_at}T00:00:00Z`)), "reviewed_at is invalid");
  assert(report.scope?.audit_only === true, "docs/file-readiness.json must remain audit-only");
  assert(report.scope?.format_support_is_module_scoped === true, "format readiness must be module-scoped");
  assert(report.summary_unit === "module_format_operation", "summary_unit must be module_format_operation");
  assertEqual(report.scope?.modules || [], MODULES, "scope modules");
  assertEqual(report.scope?.input_kinds || [], INPUT_KINDS, "scope input kinds");
  assert(report.scope?.multimodal_readiness === "docs/multimodal-readiness.json", "multimodal readiness boundary is missing");
  assert(Array.isArray(report.module_policies), "module_policies must be an array");
  assertEqual(report.module_policies.map((item) => item.module), MODULES, "module policy coverage");
  for (const policy of report.module_policies) {
    assert(INTERACTION_STATUSES.has(policy.interaction_status), `${policy.module} has invalid module policy status`);
    assert(typeof policy.status_reason === "string" && policy.status_reason.trim(), `${policy.module} module policy lacks status_reason`);
  }
  const workflowPolicy = report.module_policies.find((item) => item.module === "workflow");
  assert(workflowPolicy?.interaction_status === "ready", "workflow must be ready after the scoped asset contract is implemented");
  assert(Array.isArray(report.formats) && report.formats.length > 0, "formats must be a non-empty array");

  const formatIds = new Set();
  const scopedExtensionOwners = new Map();
  const readinessKeys = new Set();
  const counts = {
    format_count: report.formats.length,
    module_operation_count: 0,
    ready_count: 0,
    verified_count: 0,
    contract_verified_count: 0,
    planned_count: 0,
    disabled_count: 0,
  };

  for (const format of report.formats) {
    assert(typeof format.format_id === "string" && /^[a-z0-9_]+$/.test(format.format_id), "format_id must use lowercase snake_case");
    assert(!formatIds.has(format.format_id), `duplicate format_id: ${format.format_id}`);
    formatIds.add(format.format_id);
    assert(typeof format.label === "string" && format.label.trim(), `${format.format_id} lacks label`);
    assert(FAMILIES.has(format.family), `${format.format_id} has invalid family`);
    assert(CONTAINERS.has(format.container_kind), `${format.format_id} has invalid container_kind`);
    assert(SIGNATURE_POLICIES.has(format.signature_policy), `${format.format_id} has invalid signature_policy`);
    assert(Array.isArray(format.extensions) && format.extensions.length > 0, `${format.format_id} lacks extensions`);
    assert(Array.isArray(format.mime_types) && format.mime_types.length > 0, `${format.format_id} lacks mime_types`);
    for (const extension of format.extensions) {
      assert(/^\.[a-z0-9.+-]+$/.test(extension), `${format.format_id} has invalid extension: ${extension}`);
    }
    for (const mime of format.mime_types) {
      assert(typeof mime === "string" && mime === mime.toLowerCase() && mime.includes("/"), `${format.format_id} has invalid MIME: ${mime}`);
    }
    assert(Array.isArray(format.module_readiness) && format.module_readiness.length > 0, `${format.format_id} lacks module_readiness`);

    for (const item of format.module_readiness) {
      counts.module_operation_count += 1;
      assert(MODULES.has(item.module), `${format.format_id} has invalid module: ${item.module}`);
      assert(OPERATIONS.has(item.operation), `${format.format_id} has invalid operation: ${item.operation}`);
      assert(INTERACTION_STATUSES.has(item.interaction_status), `${format.format_id} has invalid interaction_status`);
      assert(VERIFICATION_STATUSES.has(item.verification_status), `${format.format_id} has invalid verification_status`);
      assert(SUPPORT_LEVELS.has(item.support_level), `${format.format_id} has invalid support_level`);
      assert(ISOLATIONS.has(item.isolation), `${format.format_id} has invalid isolation`);
      const inputKind = inputKindFor(item);
      assert(INPUT_KINDS.has(inputKind), `${format.format_id} has invalid input_kind: ${inputKind}`);
      const key = `${format.format_id}:${item.module}:${item.operation}:${inputKind}`;
      assert(!readinessKeys.has(key), `duplicate readiness entry: ${key}`);
      readinessKeys.add(key);
      for (const extension of format.extensions) {
        const scopedKey = `${item.module}:${item.operation}:${inputKind}:${extension}`;
        const owner = scopedExtensionOwners.get(scopedKey);
        assert(!owner || owner === format.format_id, `${scopedKey} is assigned to both ${owner} and ${format.format_id}`);
        scopedExtensionOwners.set(scopedKey, format.format_id);
      }
      assert(Array.isArray(item.known_gaps), `${key} known_gaps must be an array`);
      assert(Array.isArray(item.security_controls), `${key} security_controls must be an array`);
      assert(item.evidence && typeof item.evidence === "object", `${key} lacks evidence`);

      if (["planned", "disabled"].includes(item.interaction_status)) {
        assert(typeof item.status_reason === "string" && item.status_reason.trim(), `${key} lacks status_reason`);
      }
      if (item.interaction_status === "ready") {
        counts.ready_count += 1;
        assert(["verified", "contract_verified"].includes(item.verification_status), `${key} ready status lacks usable verification`);
        assert(typeof item.parser_id === "string" && item.parser_id.trim(), `${key} ready status lacks parser_id`);
        assert(typeof item.ui_entrypoint === "string" && item.ui_entrypoint.trim(), `${key} ready status lacks ui_entrypoint`);
        assert(Number(item.limits?.max_input_bytes) > 0, `${key} ready status lacks max_input_bytes`);
        assert((item.security_controls || []).length > 0, `${key} ready status lacks security controls`);
        assert((item.evidence?.implementation || []).length > 0, `${key} ready status lacks implementation evidence`);
        assert((item.evidence?.ui || []).length > 0, `${key} ready status lacks UI evidence`);
        assert((item.evidence?.tests || []).length > 0, `${key} ready status lacks test evidence`);
      }
      if (item.verification_status === "verified") {
        counts.verified_count += 1;
        assert(item.interaction_status === "ready", `${key} verified status must be ready`);
        assert((item.evidence?.security_tests || []).length > 0, `${key} verified status lacks security tests`);
        assert(item.known_gaps.length === 0, `${key} verified status cannot retain known gaps`);
      }
      if (item.verification_status === "contract_verified") {
        counts.contract_verified_count += 1;
        if (item.interaction_status === "ready") {
          assert(typeof item.status_reason === "string" && item.status_reason.trim(), `${key} contract_verified ready status lacks explanation`);
          assert(item.known_gaps.length > 0, `${key} contract_verified ready status must list known gaps`);
        }
      }
      if (item.interaction_status === "planned") counts.planned_count += 1;
      if (item.interaction_status === "disabled") counts.disabled_count += 1;
    }
  }

  for (const [key, value] of Object.entries(counts)) {
    assert(report.summary?.[key] === value, `summary.${key} mismatch: expected ${value}, got ${report.summary?.[key]}`);
  }
  return counts;
}

async function main() {
  const [
    reportSource,
    ragParser,
    ragVisionProcessor,
    ragPage,
    dataxService,
    dataxPage,
    xpertApi,
    xpertSettings,
    mainSource,
    dockerfileSource,
    requirementsSource,
    workflowSource,
    chatPage,
    chatFileComposer,
    fileCapabilities,
    fetchChatStream,
    fileAssetApi,
    fileAssetService,
    workflowRun,
    workflowEditor,
    workflowNodeRegistry,
  ] = await Promise.all([
    fs.readFile(REPORT_PATH, "utf8"),
    fs.readFile(path.resolve("server/rag/document_parser.py"), "utf8"),
    fs.readFile(path.resolve("server/rag/vision_processor.py"), "utf8"),
    fs.readFile(path.resolve("client/src/pages/RagPage.tsx"), "utf8"),
    fs.readFile(path.resolve("server/datax/service.py"), "utf8"),
    fs.readFile(path.resolve("client/src/pages/DataXProjectPage.tsx"), "utf8"),
    fs.readFile(path.resolve("server/xperts/api.py"), "utf8"),
    fs.readFile(path.resolve("client/src/components/xpert/XpertFeatureSettings.tsx"), "utf8"),
    fs.readFile(path.resolve("server/main.py"), "utf8"),
    fs.readFile(path.resolve("server/Dockerfile"), "utf8"),
    fs.readFile(path.resolve("server/requirements.txt"), "utf8"),
    fs.readFile(path.resolve(".github/workflows/file-readiness.yml"), "utf8"),
    fs.readFile(path.resolve("client/src/pages/ChatPage.tsx"), "utf8"),
    fs.readFile(path.resolve("client/src/components/ChatFileComposer.tsx"), "utf8"),
    fs.readFile(path.resolve("client/src/data/fileCapabilities.ts"), "utf8"),
    fs.readFile(path.resolve("client/src/utils/fetchChatStream.ts"), "utf8"),
    fs.readFile(path.resolve("server/file_assets/api.py"), "utf8"),
    fs.readFile(path.resolve("server/file_assets/service.py"), "utf8"),
    fs.readFile(path.resolve("client/src/components/workflow/WorkflowRun.tsx"), "utf8"),
    fs.readFile(path.resolve("client/src/components/workflow/WorkflowEditor.tsx"), "utf8"),
    fs.readFile(path.resolve("server/xpert_runtime/workflow_node_registry.py"), "utf8"),
  ]);
  const report = JSON.parse(reportSource);
  const counts = validateReport(report);
  await assertEvidencePaths(report);

  assertBackendRegistryWiring(ragParser, "server/rag/document_parser.py");
  assertBackendRegistryWiring(ragVisionProcessor, "server/rag/vision_processor.py");
  assertBackendRegistryWiring(dataxService, "server/datax/service.py");
  assertBackendRegistryWiring(xpertApi, "server/xperts/api.py");
  assertFrontendRegistryWiring(ragPage, "client/src/pages/RagPage.tsx", "rag");
  assertFrontendRegistryWiring(dataxPage, "client/src/pages/DataXProjectPage.tsx", "datax");
  assertFrontendRegistryWiring(xpertSettings, "client/src/components/xpert/XpertFeatureSettings.tsx", "agent");
  assertChatFileWiring({
    chatPage,
    composer: chatFileComposer,
    capabilityClient: fileCapabilities,
    streamClient: fetchChatStream,
    mainSource,
    fileApi: fileAssetApi,
    fileService: fileAssetService,
  });
  assertWorkflowFileWiring({
    workflowRun,
    workflowEditor,
    workflowNodeRegistry,
    mainSource,
    fileApi: fileAssetApi,
    fileService: fileAssetService,
  });

  assert(/app\.include_router\(\s*file_assets_router\s*\)/.test(mainSource), "server/main.py does not mount file_assets_router");
  assert(/COPY\s+file_assets\s+\.\/file_assets/.test(dockerfileSource), "server/Dockerfile does not copy file_assets");
  const versions = requirementVersions(requirementsSource);
  for (const dependency of ["fastapi", "httpx", "pytest"]) {
    const expected = versions.get(dependency);
    assert(expected, `server/requirements.txt lacks pinned ${dependency}`);
    assert(workflowDependencyVersion(workflowSource, dependency) === expected, `Workflow ${dependency} pin does not match server/requirements.txt`);
  }

  process.stdout.write(`${JSON.stringify({
    status: "ok",
    report: path.relative(ROOT, REPORT_PATH),
    reviewed_at: report.reviewed_at,
    summary: counts,
    wiring: {
      backend: ["chat.document", "rag.document", "rag.vision", "datax.source", "agent.context", "workflow.document"],
      frontend: ["chat", "rag", "datax", "agent", "workflow"],
      runtime_report_test: "server/tests/test_file_assets.py",
    },
  }, null, 2)}\n`);
}

await main();
