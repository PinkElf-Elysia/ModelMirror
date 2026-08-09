import crypto from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GodotHarnessError, projectPath, resolveGodotBinary } from "./lib/godot-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const sourceProjectRoot = projectPath(moduleRoot);
const PACKAGES = Object.freeze([
  Object.freeze({
    id: "satelliteoflove",
    name: "@satelliteoflove/godot-mcp",
    version: "4.1.0",
    integrity: "sha512-uq3Gh5n7fos8vIoXpr32/K7r9tL9eYLbERr+Tolksg3Y+FC5coYEkRkbJ1JktMMhoH/BnGWsWhE5E+XJ/nMEPg==",
    entry: "dist/cli.js",
  }),
  Object.freeze({
    id: "minimal",
    name: "@ryanmazzolini/minimal-godot-mcp",
    version: "0.1.6",
    integrity: "sha512-phJf8/ehQE+UiWq6qJw6mPAPtyEKRf+V9KJpy9+DSioJCyrFATxfI01cjvUri76yynxX9do5XEzX6En+xyRyJg==",
    entry: "dist/index.js",
  }),
]);

function fail(code) {
  throw new GodotHarnessError(code);
}

function isContained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function defaultTemporaryRoot() {
  return process.platform === "win32"
    ? path.win32.join(`C:${path.win32.sep}`, "tmp")
    : os.tmpdir();
}

export function parseQualificationArguments(args) {
  if (args.length !== 2 || args[0] !== "--output" || !path.isAbsolute(args[1]) || args[1].includes("\0")) {
    fail("GODOT_MCP_QUALIFICATION_ARGUMENT_ERROR");
  }
  return args[1];
}

export function validateQualificationOutput(output, { temporaryRoot = defaultTemporaryRoot() } = {}) {
  const root = fs.realpathSync(temporaryRoot);
  const absolute = path.resolve(output);
  if (!isContained(root, absolute) || fs.existsSync(absolute)) {
    fail("GODOT_MCP_QUALIFICATION_OUTPUT_INVALID");
  }
  const parent = fs.realpathSync(path.dirname(absolute));
  if (parent !== root && !isContained(root, parent)) {
    fail("GODOT_MCP_QUALIFICATION_OUTPUT_INVALID");
  }
  return absolute;
}

export function sanitizedMcpEnvironment(source = process.env) {
  const allowed = [
    "ALLUSERSPROFILE", "APPDATA", "ComSpec", "CommonProgramFiles", "CommonProgramFiles(x86)",
    "LOCALAPPDATA", "NUMBER_OF_PROCESSORS", "OS", "Path", "PATHEXT", "ProgramData",
    "ProgramFiles", "ProgramFiles(x86)", "SystemDrive", "SystemRoot", "TEMP", "TMP", "windir",
  ];
  const environment = Object.create(null);
  for (const key of allowed) {
    if (typeof source[key] === "string") {
      environment[key] = source[key];
    }
  }
  environment.NO_COLOR = "1";
  return environment;
}

function snapshotTree(root) {
  const records = [];
  const stack = [root];
  while (stack.length > 0) {
    const current = stack.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });
    entries.sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0);
    for (const entry of entries) {
      if (entry.name === ".godot") {
        continue;
      }
      const absolute = path.join(current, entry.name);
      const relative = path.relative(root, absolute).replaceAll("\\", "/");
      if (entry.isSymbolicLink()) {
        fail("GODOT_MCP_QUALIFICATION_PROJECT_LINK");
      }
      if (entry.isDirectory()) {
        stack.push(absolute);
      } else if (entry.isFile()) {
        const digest = crypto.createHash("sha256").update(fs.readFileSync(absolute)).digest("hex");
        records.push(`${relative}\0${digest}`);
      }
    }
  }
  records.sort();
  return crypto.createHash("sha256").update(records.join("\n")).digest("hex");
}

function installPackages(runnerRoot) {
  fs.writeFileSync(path.join(runnerRoot, "package.json"), JSON.stringify({ private: true }), { flag: "wx" });
  const npmExecPath = process.env.npm_execpath;
  if (typeof npmExecPath !== "string" || npmExecPath.length === 0) {
    fail("GODOT_MCP_QUALIFICATION_INSTALL_FAILED");
  }
  const specs = PACKAGES.map((item) => `${item.name}@${item.version}`);
  const result = spawnSync(process.execPath, [npmExecPath, "install", "--ignore-scripts", "--no-audit", "--no-fund", "--save-exact", ...specs], {
    cwd: runnerRoot,
    encoding: "utf8",
    shell: false,
    timeout: 180_000,
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    fail("GODOT_MCP_QUALIFICATION_INSTALL_FAILED");
  }
  const lock = JSON.parse(fs.readFileSync(path.join(runnerRoot, "package-lock.json"), "utf8"));
  for (const item of PACKAGES) {
    const key = `node_modules/${item.name}`;
    const record = lock.packages?.[key];
    if (record?.version !== item.version || record?.integrity !== item.integrity || record?.license !== "MIT") {
      fail("GODOT_MCP_QUALIFICATION_PACKAGE_MISMATCH");
    }
  }
}

function enableSatelliteAddon(projectRoot) {
  const projectFile = path.join(projectRoot, "project.godot");
  const source = fs.readFileSync(projectFile, "utf8");
  const updated = source.replace(
    'enabled=PackedStringArray("res://addons/gdUnit4/plugin.cfg")',
    'enabled=PackedStringArray("res://addons/gdUnit4/plugin.cfg", "res://addons/godot_mcp/plugin.cfg")',
  );
  if (updated === source) {
    fail("GODOT_MCP_QUALIFICATION_PLUGIN_CONFIG_FAILED");
  }
  fs.writeFileSync(projectFile, updated, "utf8");
}

function directEntry(runnerRoot, item) {
  return path.join(runnerRoot, "node_modules", ...item.name.split("/"), item.entry);
}

function installSatelliteAddon({ runnerRoot, projectRoot }) {
  const item = PACKAGES[0];
  const result = spawnSync(process.execPath, [directEntry(runnerRoot, item), "--install-addon", projectRoot], {
    cwd: runnerRoot,
    encoding: "utf8",
    shell: false,
    timeout: 30_000,
    windowsHide: true,
    env: sanitizedMcpEnvironment(),
  });
  if (result.error || result.status !== 0 || !fs.existsSync(path.join(projectRoot, "addons", "godot_mcp", "plugin.cfg"))) {
    fail("GODOT_MCP_QUALIFICATION_ADDON_INSTALL_FAILED");
  }
  enableSatelliteAddon(projectRoot);
}

function startProcess(command, args, options) {
  return spawn(command, args, {
    ...options,
    shell: false,
    windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
  });
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function stopProcess(child) {
  if (!child || child.exitCode !== null) {
    return;
  }
  child.stdin?.end();
  await Promise.race([
    new Promise((resolve) => child.once("exit", resolve)),
    wait(2_000),
  ]);
  if (child.exitCode === null) {
    if (process.platform === "win32") {
      spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
    } else {
      child.kill("SIGTERM");
    }
    await Promise.race([
      new Promise((resolve) => child.once("exit", resolve)),
      wait(2_000),
    ]);
  }
  if (child.exitCode === null) {
    fail("GODOT_MCP_QUALIFICATION_PROCESS_CLEANUP_FAILED");
  }
}

function createMcpClient(child) {
  let nextId = 1;
  let buffer = "";
  const pending = new Map();
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    buffer += chunk;
    while (buffer.includes("\n")) {
      const index = buffer.indexOf("\n");
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (!line) continue;
      let message;
      try { message = JSON.parse(line); } catch { continue; }
      if (Number.isSafeInteger(message.id) && pending.has(message.id)) {
        const waiter = pending.get(message.id);
        pending.delete(message.id);
        waiter.resolve(message);
      }
    }
  });
  child.once("exit", () => {
    for (const waiter of pending.values()) {
      waiter.reject(new Error("MCP_PROCESS_EXITED"));
    }
    pending.clear();
  });
  const send = (message) => child.stdin.write(`${JSON.stringify(message)}\n`);
  const request = (method, params, timeout = 15_000) => {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error("MCP_REQUEST_TIMEOUT"));
      }, timeout);
      pending.set(id, {
        resolve: (message) => { clearTimeout(timer); resolve(message); },
        reject: (error) => { clearTimeout(timer); reject(error); },
      });
      send({ jsonrpc: "2.0", id, method, params });
    });
  };
  return { request, notify: (method, params = {}) => send({ jsonrpc: "2.0", method, params }) };
}

async function qualifyServer({ item, runnerRoot, projectRoot }) {
  const args = item.id === "satelliteoflove" ? [directEntry(runnerRoot, item), "--read-only"] : [directEntry(runnerRoot, item)];
  const environment = sanitizedMcpEnvironment();
  if (item.id === "minimal") {
    environment.GODOT_WORKSPACE_PATH = projectRoot;
    environment.GODOT_LSP_PORT = "6005";
  } else {
    environment.GODOT_MCP_READ_ONLY = "1";
  }
  const child = startProcess(process.execPath, args, { cwd: projectRoot, env: environment });
  try {
    const client = createMcpClient(child);
    const initialized = await client.request("initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "matrix-oasis-r4-qualification", version: "0.4.0-r4" },
    });
    if (initialized.error || !initialized.result?.serverInfo?.name) {
      throw new Error("MCP_INITIALIZE_INVALID");
    }
    client.notify("notifications/initialized");
    const listed = await client.request("tools/list", {});
    const tools = listed.result?.tools;
    if (!Array.isArray(tools) || tools.length === 0) {
      throw new Error("MCP_TOOLS_INVALID");
    }
    const toolNames = tools.map((tool) => tool.name).filter((name) => typeof name === "string").sort();
    let observed = false;
    if (item.id === "satelliteoflove") {
      if (toolNames.some((name) => /(?:_edit$|godot_exec|godot_input|godot_game_time)/u.test(name))) {
        throw new Error("MCP_READ_ONLY_TOOLS_INVALID");
      }
      for (let attempt = 0; attempt < 5 && !observed; attempt += 1) {
        const called = await client.request("tools/call", { name: "godot_project", arguments: { action: "get_info" } }, 20_000);
        observed = called.result?.isError !== true && JSON.stringify(called.result ?? {}).includes("Matrix Oasis R4 Foundation");
        if (!observed) await wait(1_000);
      }
    } else {
      const called = await client.request("tools/call", { name: "scan_workspace_diagnostics", arguments: {} }, 20_000);
      const text = called.result?.content?.find((part) => part.type === "text")?.text ?? "";
      observed = called.result?.isError !== true && text.includes("files_scanned") && !text.includes("LSP_NOT_RUNNING");
    }
    return Object.freeze({
      package: item.name,
      version: item.version,
      license: "MIT",
      integrity: item.integrity,
      startup: "passed",
      handshake: "passed",
      toolCount: toolNames.length,
      readOnlyObservation: observed ? "passed" : "not_ready",
      processCleanup: "passed",
      recommendation: observed ? "recommend" : "defer",
    });
  } catch {
    return Object.freeze({
      package: item.name,
      version: item.version,
      license: "MIT",
      integrity: item.integrity,
      startup: child.exitCode === null ? "passed" : "failed",
      handshake: "failed",
      toolCount: 0,
      readOnlyObservation: "not_ready",
      processCleanup: "passed",
      recommendation: "defer",
    });
  } finally {
    await stopProcess(child);
  }
}

function isDirectExecution() {
  return process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
}

if (isDirectExecution()) {
  let editor = null;
  try {
    const output = validateQualificationOutput(parseQualificationArguments(process.argv.slice(2)));
    fs.mkdirSync(output);
    const runnerRoot = path.join(output, "runner");
    const projectRoot = path.join(output, "project");
    fs.mkdirSync(runnerRoot);
    fs.cpSync(sourceProjectRoot, projectRoot, { recursive: true, filter: (source) => path.basename(source) !== ".godot" });
    installPackages(runnerRoot);
    installSatelliteAddon({ runnerRoot, projectRoot });
    const godot = resolveGodotBinary();
    const importResult = spawnSync(godot.command, ["--headless", "--editor", "--path", projectRoot, "--quit"], {
      cwd: output,
      encoding: "utf8",
      shell: false,
      timeout: 120_000,
      windowsHide: true,
      env: sanitizedMcpEnvironment(),
    });
    if (importResult.error || importResult.status !== 0 || /\b(?:SCRIPT ERROR|ERROR:)\b/u.test(`${importResult.stdout ?? ""}${importResult.stderr ?? ""}`)) {
      fail("GODOT_MCP_QUALIFICATION_IMPORT_FAILED");
    }
    editor = startProcess(godot.command, ["--headless", "--editor", "--path", projectRoot], {
      cwd: output,
      env: sanitizedMcpEnvironment(),
    });
    await wait(8_000);
    const before = snapshotTree(projectRoot);
    const results = [];
    for (const item of PACKAGES) {
      results.push(await qualifyServer({ item, runnerRoot, projectRoot }));
    }
    await stopProcess(editor);
    editor = null;
    const after = snapshotTree(projectRoot);
    const report = Object.freeze({
      qualificationVersion: 1,
      godotVersion: godot.version,
      loopbackOnly: true,
      credentialsProvided: false,
      projectTreeUnchanged: before === after,
      sourceTreeBeforeSha256: before,
      sourceTreeAfterSha256: after,
      results: Object.freeze(results),
    });
    fs.writeFileSync(path.join(output, "qualification-report.json"), `${JSON.stringify(report, null, 2)}\n`, { flag: "wx" });
    if (!report.projectTreeUnchanged) {
      fail("GODOT_MCP_QUALIFICATION_PROJECT_CHANGED");
    }
    console.log(`GODOT_MCP_QUALIFICATION_OK candidates=${results.length}`);
  } catch (error) {
    await stopProcess(editor);
    const code = error instanceof GodotHarnessError ? error.code : "GODOT_MCP_QUALIFICATION_INTERNAL_ERROR";
    console.error(code);
    process.exitCode = code === "GODOT_MCP_QUALIFICATION_ARGUMENT_ERROR" ? 2 : 1;
  }
}
