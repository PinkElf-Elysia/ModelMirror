import path from "node:path";

export const PROTOTYPE_PROMPT_MAX_BYTES = 32_768;
export const PROTOTYPE_QUALIFICATION_PROMPT = Object.freeze(
  "Create a compact topic-neutral first-person prototype description with one bounded environment, one prop, one static character placeholder, at least two interactive nodes, basic actions, and one reachable ending. Keep every title and description neutral. Do not include file paths, hashes, coordinates, images, provider names, credentials, or user data.",
);
export const PROTOTYPE_ARTIFACT_FILES = Object.freeze([
  ["authoringGamePackJson", "authoring-game-pack.json"],
  ["sceneBlueprintJson", "scene-blueprint.json"],
  ["runtimeGamePackJson", "runtime-game-pack.json"],
  ["runtimeReceiptJson", "runtime-receipt.json"],
  ["generationReportJson", "generation-report.json"],
]);

const WINDOWS_RESERVED = new Set([
  "aux",
  "con",
  "nul",
  "prn",
  ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`),
]);

export class PrototypeCliOperationalError extends Error {
  constructor(code) {
    super(code);
    this.name = "PrototypeCliOperationalError";
    this.code = code;
  }
}

function fail(code) {
  throw new PrototypeCliOperationalError(code);
}

function samePath(left, right) {
  const resolvedLeft = path.resolve(left);
  const resolvedRight = path.resolve(right);
  return process.platform === "win32"
    ? resolvedLeft.toLowerCase() === resolvedRight.toLowerCase()
    : resolvedLeft === resolvedRight;
}

function directChild(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return (
    relative !== "" &&
    !path.isAbsolute(relative) &&
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !relative.includes(path.sep)
  );
}

function statIdentity(stat) {
  return stat && typeof stat.dev === "bigint" && typeof stat.ino === "bigint"
    ? `${stat.dev}:${stat.ino}`
    : null;
}

function regularFile(stat) {
  return stat?.isFile?.() === true && stat.isSymbolicLink?.() !== true;
}

function normalDirectory(stat) {
  return stat?.isDirectory?.() === true && stat.isSymbolicLink?.() !== true;
}

function normalizeArgument(value, code) {
  if (typeof value !== "string" || value.length === 0 || value.includes("\0")) {
    fail(code);
  }
  return value;
}

export function parsePlanPrototypeArgs(args) {
  if (!Array.isArray(args)) {
    fail("PROTOTYPE_PLAN_ARGUMENT_INVALID");
  }
  let promptFile;
  for (let index = 0; index < args.length; index += 1) {
    const argument = normalizeArgument(args[index], "PROTOTYPE_PLAN_ARGUMENT_INVALID");
    if (argument !== "--prompt-file" || promptFile !== undefined || index + 1 >= args.length) {
      fail("PROTOTYPE_PLAN_ARGUMENT_INVALID");
    }
    promptFile = normalizeArgument(args[index + 1], "PROTOTYPE_PLAN_PROMPT_INVALID");
    index += 1;
  }
  if (promptFile === undefined) {
    fail("PROTOTYPE_PLAN_PROMPT_REQUIRED");
  }
  return Object.freeze({ promptFile });
}

export function parseGeneratePrototypeArgs(args) {
  if (!Array.isArray(args)) {
    fail("PROTOTYPE_GENERATE_ARGUMENT_INVALID");
  }
  let promptFile;
  let output;
  let acknowledged = false;
  for (let index = 0; index < args.length; index += 1) {
    const argument = normalizeArgument(args[index], "PROTOTYPE_GENERATE_ARGUMENT_INVALID");
    if (argument === "--acknowledge-external-upload") {
      if (acknowledged) {
        fail("PROTOTYPE_GENERATE_ARGUMENT_INVALID");
      }
      acknowledged = true;
      continue;
    }
    if (argument === "--prompt-file") {
      if (promptFile !== undefined || index + 1 >= args.length) {
        fail("PROTOTYPE_GENERATE_PROMPT_INVALID");
      }
      promptFile = normalizeArgument(args[index + 1], "PROTOTYPE_GENERATE_PROMPT_INVALID");
      index += 1;
      continue;
    }
    if (argument === "--output") {
      if (output !== undefined || index + 1 >= args.length) {
        fail("PROTOTYPE_GENERATE_OUTPUT_INVALID");
      }
      output = normalizeArgument(args[index + 1], "PROTOTYPE_GENERATE_OUTPUT_INVALID");
      index += 1;
      continue;
    }
    fail("PROTOTYPE_GENERATE_UNKNOWN_OPTION");
  }
  if (promptFile === undefined) {
    fail("PROTOTYPE_GENERATE_PROMPT_REQUIRED");
  }
  if (output === undefined) {
    fail("PROTOTYPE_GENERATE_OUTPUT_REQUIRED");
  }
  if (!acknowledged) {
    fail("PROTOTYPE_GENERATE_UPLOAD_ACK_REQUIRED");
  }
  return Object.freeze({ promptFile, output });
}

export function parseQualifyPrototypeArgs(args) {
  if (!Array.isArray(args)) {
    fail("PROTOTYPE_QUALIFY_ARGUMENT_INVALID");
  }
  let output;
  let acknowledged = false;
  for (let index = 0; index < args.length; index += 1) {
    const argument = normalizeArgument(args[index], "PROTOTYPE_QUALIFY_ARGUMENT_INVALID");
    if (argument === "--acknowledge-external-upload") {
      if (acknowledged) {
        fail("PROTOTYPE_QUALIFY_ARGUMENT_INVALID");
      }
      acknowledged = true;
      continue;
    }
    if (argument === "--output") {
      if (output !== undefined || index + 1 >= args.length) {
        fail("PROTOTYPE_QUALIFY_OUTPUT_INVALID");
      }
      output = normalizeArgument(args[index + 1], "PROTOTYPE_QUALIFY_OUTPUT_INVALID");
      index += 1;
      continue;
    }
    fail("PROTOTYPE_QUALIFY_UNKNOWN_OPTION");
  }
  if (output === undefined) {
    fail("PROTOTYPE_QUALIFY_OUTPUT_REQUIRED");
  }
  if (!acknowledged) {
    fail("PROTOTYPE_QUALIFY_UPLOAD_ACK_REQUIRED");
  }
  return Object.freeze({ output });
}

function validateOutputName(name) {
  if (
    !/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(name) ||
    WINDOWS_RESERVED.has(name)
  ) {
    fail("PROTOTYPE_GENERATE_OUTPUT_INVALID");
  }
}

async function trustedTempRoot(tempRoot, { realpath, lstat }) {
  const requiredRoot =
    process.platform === "win32"
      ? path.resolve(`${["C", ":"].join("")}${path.sep}`, "tmp")
      : path.resolve(path.parse(process.cwd()).root, "tmp");
  if (
    typeof tempRoot !== "string" ||
    !path.isAbsolute(tempRoot) ||
    !samePath(tempRoot, requiredRoot)
  ) {
    fail("PROTOTYPE_CLI_TEMP_ROOT_INVALID");
  }
  let resolved;
  let stat;
  try {
    resolved = await realpath(tempRoot);
    stat = await lstat(tempRoot, { bigint: true });
  } catch {
    fail("PROTOTYPE_CLI_TEMP_ROOT_INVALID");
  }
  if (!samePath(tempRoot, resolved) || !normalDirectory(stat) || statIdentity(stat) === null) {
    fail("PROTOTYPE_CLI_TEMP_ROOT_INVALID");
  }
  return resolved;
}

async function readPromptFile({ candidate, tempRoot, readFile, realpath, lstat }) {
  if (typeof candidate !== "string" || !path.isAbsolute(candidate)) {
    fail("PROTOTYPE_PROMPT_PATH_INVALID");
  }
  const resolvedCandidate = path.resolve(candidate);
  const relative = path.relative(tempRoot, resolvedCandidate);
  if (
    relative === "" ||
    path.isAbsolute(relative) ||
    relative === ".." ||
    relative.startsWith(`..${path.sep}`)
  ) {
    fail("PROTOTYPE_PROMPT_PATH_INVALID");
  }
  let before;
  let resolved;
  let bytes;
  let after;
  try {
    before = await lstat(resolvedCandidate, { bigint: true });
    resolved = await realpath(resolvedCandidate);
    if (
      !samePath(resolvedCandidate, resolved) ||
      !regularFile(before) ||
      statIdentity(before) === null ||
      before.size > BigInt(PROTOTYPE_PROMPT_MAX_BYTES)
    ) {
      fail("PROTOTYPE_PROMPT_PATH_INVALID");
    }
    bytes = await readFile(resolvedCandidate);
    after = await lstat(resolvedCandidate, { bigint: true });
  } catch (error) {
    if (error instanceof PrototypeCliOperationalError) {
      throw error;
    }
    fail("PROTOTYPE_PROMPT_READ_ERROR");
  }
  if (
    statIdentity(before) !== statIdentity(after) ||
    !regularFile(after) ||
    !(bytes instanceof Uint8Array) ||
    bytes.byteLength > PROTOTYPE_PROMPT_MAX_BYTES ||
    BigInt(bytes.byteLength) !== after.size
  ) {
    fail("PROTOTYPE_PROMPT_READ_ERROR");
  }
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    fail("PROTOTYPE_PROMPT_UTF8_INVALID");
  }
  if (text.trim().length === 0) {
    fail("PROTOTYPE_PROMPT_TEXT_INVALID");
  }
  return Object.freeze({ text, byteLength: bytes.byteLength });
}

function readEnvironment(environment, includeCredential) {
  if (!environment || typeof environment !== "object") {
    fail("PROTOTYPE_MODEL_CONFIG_INVALID");
  }
  let descriptors;
  try {
    descriptors = Object.getOwnPropertyDescriptors(environment);
  } catch {
    fail("PROTOTYPE_MODEL_CONFIG_INVALID");
  }
  const readValue = (key) => {
    const descriptor = descriptors[key];
    return descriptor && "value" in descriptor ? descriptor.value : undefined;
  };
  const endpoint = readValue("MATRIX_OASIS_MODEL_ENDPOINT");
  const model = readValue("MATRIX_OASIS_MODEL_ID");
  const credential = readValue("MATRIX_OASIS_MODEL_API_KEY");
  if (
    typeof endpoint !== "string" ||
    typeof model !== "string" ||
    model.length < 1 ||
    model.length > 256 ||
    /[\u0000-\u001f\u007f]/.test(model) ||
    (includeCredential &&
      (typeof credential !== "string" ||
        credential.length < 1 ||
        credential.length > 8192 ||
        /[\r\n]/.test(credential)))
  ) {
    fail("PROTOTYPE_MODEL_CONFIG_INVALID");
  }
  return { endpoint, model, credential };
}

function safeHost(endpoint) {
  let url;
  try {
    url = new URL(endpoint);
  } catch {
    fail("PROTOTYPE_MODEL_CONFIG_INVALID");
  }
  const loopback = new Set(["127.0.0.1", "localhost", "[::1]"]);
  const hostname = url.hostname.toLowerCase();
  const openRouter =
    url.protocol === "https:" &&
    hostname === "openrouter.ai" &&
    url.pathname === "/api/v1/chat/completions";
  const endpointPathAllowed =
    hostname === "openrouter.ai"
      ? openRouter
      : url.pathname === "/v1/chat/completions";
  if (
    !endpointPathAllowed ||
    url.search !== "" ||
    url.hash !== "" ||
    url.username !== "" ||
    url.password !== "" ||
    (url.protocol !== "https:" &&
      !(url.protocol === "http:" && loopback.has(hostname)))
  ) {
    fail("PROTOTYPE_MODEL_CONFIG_INVALID");
  }
  return url.host;
}

async function assertDirectory({ candidate, parent, identity, lstat, realpath }) {
  const stat = await lstat(candidate, { bigint: true });
  const resolved = await realpath(candidate);
  if (
    !directChild(parent, candidate) ||
    !samePath(candidate, resolved) ||
    !normalDirectory(stat) ||
    statIdentity(stat) !== identity
  ) {
    throw new Error("UNTRUSTED_DIRECTORY");
  }
}

async function assertFilePath({ candidate, parent, identity, lstat, realpath }) {
  const stat = await lstat(candidate, { bigint: true });
  const resolved = await realpath(candidate);
  if (
    !directChild(parent, candidate) ||
    !samePath(candidate, resolved) ||
    !regularFile(stat) ||
    statIdentity(stat) !== identity
  ) {
    throw new Error("UNTRUSTED_FILE");
  }
}

async function assertFileHandle({ handle, candidate, parent, identity, lstat, realpath }) {
  const handleStat = await handle.stat({ bigint: true });
  const handleIdentity = statIdentity(handleStat);
  const expected = identity ?? handleIdentity;
  if (!regularFile(handleStat) || expected === null || handleIdentity !== expected) {
    throw new Error("UNTRUSTED_HANDLE");
  }
  await assertFilePath({ candidate, parent, identity: expected, lstat, realpath });
  return expected;
}

async function readHandle(handle, expectedBytes) {
  const output = new Uint8Array(expectedBytes.byteLength);
  let offset = 0;
  while (offset < output.byteLength) {
    const result = await handle.read(output, offset, output.byteLength - offset, offset);
    if (!result || result.bytesRead < 1 || result.bytesRead > output.byteLength - offset) {
      throw new Error("READBACK_INVALID");
    }
    offset += result.bytesRead;
  }
  const trailing = new Uint8Array(1);
  const tail = await handle.read(trailing, 0, 1, expectedBytes.byteLength);
  if (!tail || tail.bytesRead !== 0) {
    throw new Error("READBACK_INVALID");
  }
  return output;
}

function equalBytes(left, right) {
  return left.byteLength === right.byteLength && left.every((byte, index) => byte === right[index]);
}

async function exists(candidate, lstat) {
  try {
    await lstat(candidate, { bigint: true });
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

async function cleanupStaging({ staging, identity, tempRoot, lstat, realpath, rm }) {
  if (!staging || !identity) {
    return;
  }
  try {
    await assertDirectory({
      candidate: staging,
      parent: tempRoot,
      identity,
      lstat,
      realpath,
    });
    await rm(staging, { recursive: true, force: false });
  } catch {
    // Ambiguous or replaced staging roots are preserved for diagnosis.
  }
}

function normalizeArtifacts(value) {
  if (!value || typeof value !== "object") {
    fail("PROTOTYPE_GENERATE_INTERNAL_ERROR");
  }
  let descriptors;
  try {
    descriptors = Object.getOwnPropertyDescriptors(value);
  } catch {
    fail("PROTOTYPE_GENERATE_INTERNAL_ERROR");
  }
  const keys = Reflect.ownKeys(descriptors);
  const expected = PROTOTYPE_ARTIFACT_FILES.map(([key]) => key);
  if (
    keys.length !== expected.length ||
    keys.some(
      (key) =>
        typeof key !== "string" ||
        !expected.includes(key) ||
        !descriptors[key].enumerable ||
        !("value" in descriptors[key]) ||
        typeof descriptors[key].value !== "string",
    )
  ) {
    fail("PROTOTYPE_GENERATE_INTERNAL_ERROR");
  }
  return Object.fromEntries(expected.map((key) => [key, descriptors[key].value]));
}

export async function publishPrototypeArtifacts({
  tempRoot,
  output,
  artifacts,
  openFile,
  mkdtemp,
  rename,
  rm,
  realpath,
  lstat,
}) {
  const outputPath = path.resolve(output);
  if (!path.isAbsolute(output) || !directChild(tempRoot, outputPath)) {
    fail("PROTOTYPE_GENERATE_OUTPUT_INVALID");
  }
  validateOutputName(path.basename(outputPath));
  try {
    if (await exists(outputPath, lstat)) {
      fail("PROTOTYPE_GENERATE_OUTPUT_EXISTS");
    }
  } catch (error) {
    if (error instanceof PrototypeCliOperationalError) {
      throw error;
    }
    fail("PROTOTYPE_GENERATE_IO_ERROR");
  }

  const normalizedArtifacts = normalizeArtifacts(artifacts);
  let staging;
  let stagingIdentity;
  const handles = [];
  const records = [];
  try {
    staging = await mkdtemp(path.join(tempRoot, `.matrix-oasis-r8-${path.basename(outputPath)}-`));
    const stagingStat = await lstat(staging, { bigint: true });
    stagingIdentity = statIdentity(stagingStat);
    if (stagingIdentity === null) {
      throw new Error("STAGING_IDENTITY_INVALID");
    }
    const trustStaging = () =>
      assertDirectory({
        candidate: staging,
        parent: tempRoot,
        identity: stagingIdentity,
        lstat,
        realpath,
      });
    await trustStaging();

    for (const [key, fileName] of PROTOTYPE_ARTIFACT_FILES) {
      const filePath = path.join(staging, fileName);
      await trustStaging();
      const handle = await openFile(filePath, "wx+");
      handles.push(handle);
      await trustStaging();
      const identity = await assertFileHandle({
        handle,
        candidate: filePath,
        parent: staging,
        identity: null,
        lstat,
        realpath,
      });
      records.push({
        key,
        fileName,
        filePath,
        handle,
        identity,
        bytes: new TextEncoder().encode(normalizedArtifacts[key]),
      });
    }

    for (const record of records) {
      await trustStaging();
      for (const check of records) {
        await assertFileHandle({
          handle: check.handle,
          candidate: check.filePath,
          parent: staging,
          identity: check.identity,
          lstat,
          realpath,
        });
      }
      await record.handle.writeFile(record.bytes);
      await record.handle.sync();
      const readback = await readHandle(record.handle, record.bytes);
      if (!equalBytes(readback, record.bytes)) {
        throw new Error("WRITE_MISMATCH");
      }
    }

    await trustStaging();
    for (const record of records) {
      await assertFileHandle({
        handle: record.handle,
        candidate: record.filePath,
        parent: staging,
        identity: record.identity,
        lstat,
        realpath,
      });
      await record.handle.close();
      handles.splice(handles.indexOf(record.handle), 1);
      await assertFilePath({
        candidate: record.filePath,
        parent: staging,
        identity: record.identity,
        lstat,
        realpath,
      });
    }
    await trustStaging();
    if (await exists(outputPath, lstat)) {
      fail("PROTOTYPE_GENERATE_OUTPUT_EXISTS");
    }
    await rename(staging, outputPath);
    staging = outputPath;
    await assertDirectory({
      candidate: outputPath,
      parent: tempRoot,
      identity: stagingIdentity,
      lstat,
      realpath,
    });
    for (const record of records) {
      const finalPath = path.join(outputPath, record.fileName);
      await assertFilePath({
        candidate: finalPath,
        parent: outputPath,
        identity: record.identity,
        lstat,
        realpath,
      });
      const finalHandle = await openFile(finalPath, "r");
      handles.push(finalHandle);
      await assertFileHandle({
        handle: finalHandle,
        candidate: finalPath,
        parent: outputPath,
        identity: record.identity,
        lstat,
        realpath,
      });
      const readback = await readHandle(finalHandle, record.bytes);
      if (!equalBytes(readback, record.bytes)) {
        throw new Error("FINAL_MISMATCH");
      }
      await finalHandle.close();
      handles.splice(handles.indexOf(finalHandle), 1);
    }
    staging = undefined;
    stagingIdentity = undefined;
    return Object.freeze({ ok: true });
  } catch (error) {
    if (error instanceof PrototypeCliOperationalError) {
      throw error;
    }
    fail("PROTOTYPE_GENERATE_IO_ERROR");
  } finally {
    for (const handle of handles) {
      try {
        await handle.close();
      } catch {
        // The public result remains a static I/O failure.
      }
    }
    await cleanupStaging({
      staging,
      identity: stagingIdentity,
      tempRoot,
      lstat,
      realpath,
      rm,
    });
  }
}

function exactDataRecord(value, keys, code) {
  if (value === null || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) {
    fail(code);
  }
  let descriptors;
  try {
    descriptors = Object.getOwnPropertyDescriptors(value);
  } catch {
    fail(code);
  }
  const actualKeys = Reflect.ownKeys(descriptors);
  if (
    actualKeys.length !== keys.length ||
    actualKeys.some(
      (key) =>
        typeof key !== "string" ||
        !keys.includes(key) ||
        descriptors[key].enumerable !== true ||
        !("value" in descriptors[key]),
    )
  ) {
    fail(code);
  }
  return Object.fromEntries(keys.map((key) => [key, descriptors[key].value]));
}

function denseArray(value, code) {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype) {
    fail(code);
  }
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const length = descriptors.length?.value;
  if (
    !Number.isSafeInteger(length) ||
    length < 0 ||
    Reflect.ownKeys(descriptors).length !== length + 1
  ) {
    fail(code);
  }
  return Array.from({ length }, (_, index) => {
    const descriptor = descriptors[String(index)];
    if (!descriptor || descriptor.enumerable !== true || !("value" in descriptor)) {
      fail(code);
    }
    return descriptor.value;
  });
}

function normalizeDiagnostics(value) {
  const items = denseArray(value, "PROTOTYPE_GENERATE_INTERNAL_ERROR");
  if (items.length < 1 || items.length > 512) {
    fail("PROTOTYPE_GENERATE_INTERNAL_ERROR");
  }
  return Object.freeze(
    items.map((item) => {
      const diagnostic = exactDataRecord(
        item,
        ["phase", "severity", "code", "path", "message"],
        "PROTOTYPE_GENERATE_INTERNAL_ERROR",
      );
      if (
        !new Set(["parse", "schema", "semantic"]).has(diagnostic.phase) ||
        diagnostic.severity !== "error" ||
        typeof diagnostic.code !== "string" ||
        !/^[A-Z][A-Z0-9_]{0,127}$/.test(diagnostic.code) ||
        diagnostic.message !== diagnostic.code ||
        typeof diagnostic.path !== "string" ||
        diagnostic.path.length > 4096 ||
        !/^(?:\/(?:[A-Za-z][A-Za-z0-9]*|0|[1-9][0-9]*))*$/.test(diagnostic.path)
      ) {
        fail("PROTOTYPE_GENERATE_INTERNAL_ERROR");
      }
      return Object.freeze({ code: diagnostic.code, path: diagnostic.path });
    }),
  );
}

function normalizeGenerationResult(result) {
  let descriptors;
  try {
    descriptors = Object.getOwnPropertyDescriptors(result);
  } catch {
    fail("PROTOTYPE_GENERATE_INTERNAL_ERROR");
  }
  const okDescriptor = descriptors?.ok;
  if (okDescriptor && "value" in okDescriptor && okDescriptor.value === true) {
    const success = exactDataRecord(
      result,
      ["ok", "artifacts"],
      "PROTOTYPE_GENERATE_INTERNAL_ERROR",
    );
    if (success.ok !== true) {
      fail("PROTOTYPE_GENERATE_INTERNAL_ERROR");
    }
    return Object.freeze({ ok: true, artifacts: normalizeArtifacts(success.artifacts) });
  }
  const failure = exactDataRecord(
    result,
    ["ok", "diagnostics"],
    "PROTOTYPE_GENERATE_INTERNAL_ERROR",
  );
  if (failure.ok !== false) {
    fail("PROTOTYPE_GENERATE_INTERNAL_ERROR");
  }
  return Object.freeze({ ok: false, diagnostics: normalizeDiagnostics(failure.diagnostics) });
}

function staticFailure(error, fallbackCode) {
  const code =
    error instanceof PrototypeCliOperationalError &&
    typeof error.code === "string" &&
    /^[A-Z][A-Z0-9_]{0,127}$/.test(error.code)
      ? error.code
      : fallbackCode;
  return Object.freeze({ exitCode: 2, stdout: "", stderr: `${code}\n` });
}

export async function executePlanPrototypeCli({
  args,
  tempRoot,
  environment,
  readFile,
  realpath,
  lstat,
}) {
  try {
    const parsed = parsePlanPrototypeArgs(args);
    const trustedRoot = await trustedTempRoot(tempRoot, { realpath, lstat });
    const prompt = await readPromptFile({
      candidate: parsed.promptFile,
      tempRoot: trustedRoot,
      readFile,
      realpath,
      lstat,
    });
    const config = readEnvironment(environment, false);
    const host = safeHost(config.endpoint);
    return Object.freeze({
      exitCode: 0,
      stdout: `PROTOTYPE_CALL_PLAN host=${host} model=${config.model} maxRequests=3 promptBytes=${prompt.byteLength} uploadsPrompt=true\n`,
      stderr: "",
    });
  } catch (error) {
    return staticFailure(error, "PROTOTYPE_PLAN_INTERNAL_ERROR");
  }
}

export async function executeGeneratePrototypeCli({
  args,
  tempRoot,
  environment,
  readFile,
  openFile,
  mkdtemp,
  rename,
  rm,
  realpath,
  lstat,
  createOpenAICompatibleProvider,
  generatePrototype,
}) {
  try {
    const parsed = parseGeneratePrototypeArgs(args);
    const trustedRoot = await trustedTempRoot(tempRoot, { realpath, lstat });
    const prompt = await readPromptFile({
      candidate: parsed.promptFile,
      tempRoot: trustedRoot,
      readFile,
      realpath,
      lstat,
    });
    const config = readEnvironment(environment, true);
    safeHost(config.endpoint);
    const provider = createOpenAICompatibleProvider({
      endpoint: config.endpoint,
      model: config.model,
      apiKey: config.credential,
    });
    const generated = normalizeGenerationResult(
      await generatePrototype({ prompt: prompt.text }, provider),
    );
    if (!generated.ok) {
      return Object.freeze({
        exitCode: 1,
        stdout: "",
        stderr: `${generated.diagnostics.map((item) => `${item.code} ${item.path}`).join("\n")}\n`,
      });
    }
    const report = JSON.parse(generated.artifacts.generationReportJson);
    const requestCount = report?.requestCount;
    if (!Number.isSafeInteger(requestCount) || requestCount < 1 || requestCount > 3) {
      fail("PROTOTYPE_GENERATE_INTERNAL_ERROR");
    }
    await publishPrototypeArtifacts({
      tempRoot: trustedRoot,
      output: parsed.output,
      artifacts: generated.artifacts,
      openFile,
      mkdtemp,
      rename,
      rm,
      realpath,
      lstat,
    });
    return Object.freeze({
      exitCode: 0,
      stdout: `PROTOTYPE_GENERATION_OK requests=${requestCount}\n`,
      stderr: "",
    });
  } catch (error) {
    return staticFailure(error, "PROTOTYPE_GENERATE_INTERNAL_ERROR");
  }
}

export async function executeQualifyPrototypeModelCli({
  args,
  tempRoot,
  environment,
  openFile,
  mkdtemp,
  rename,
  rm,
  realpath,
  lstat,
  createOpenAICompatibleProvider,
  generatePrototype,
}) {
  try {
    const parsed = parseQualifyPrototypeArgs(args);
    const trustedRoot = await trustedTempRoot(tempRoot, { realpath, lstat });
    const config = readEnvironment(environment, true);
    safeHost(config.endpoint);
    const provider = createOpenAICompatibleProvider({
      endpoint: config.endpoint,
      model: config.model,
      apiKey: config.credential,
    });
    const generated = normalizeGenerationResult(
      await generatePrototype({ prompt: PROTOTYPE_QUALIFICATION_PROMPT }, provider),
    );
    if (!generated.ok) {
      return Object.freeze({
        exitCode: 1,
        stdout: "",
        stderr: `${generated.diagnostics.map((item) => `${item.code} ${item.path}`).join("\n")}\n`,
      });
    }
    const report = JSON.parse(generated.artifacts.generationReportJson);
    const requestCount = report?.requestCount;
    if (!Number.isSafeInteger(requestCount) || requestCount < 1 || requestCount > 3) {
      fail("PROTOTYPE_QUALIFY_INTERNAL_ERROR");
    }
    await publishPrototypeArtifacts({
      tempRoot: trustedRoot,
      output: parsed.output,
      artifacts: generated.artifacts,
      openFile,
      mkdtemp,
      rename,
      rm,
      realpath,
      lstat,
    });
    return Object.freeze({
      exitCode: 0,
      stdout: `PROTOTYPE_MODEL_QUALIFIED requests=${requestCount}\n`,
      stderr: "",
    });
  } catch (error) {
    return staticFailure(error, "PROTOTYPE_QUALIFY_INTERNAL_ERROR");
  }
}
