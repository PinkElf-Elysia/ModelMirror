import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { V2QualificationOperationalError } from "./source.mjs";

function sanitizedEnvironment(sandboxDir, additions = {}) {
  const environment = {};
  for (const name of ["SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "PATH"]) if (typeof process.env[name] === "string") environment[name] = process.env[name];
  for (const name of ["TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"]) environment[name] = sandboxDir;
  for (const [name, value] of Object.entries(additions)) {
    if (!/^[A-Z][A-Z0-9_]{0,63}$/u.test(name) || typeof value !== "string" || /(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)/iu.test(name)) throw new V2QualificationOperationalError("R17_PROCESS_ENV_FORBIDDEN");
    environment[name] = value;
  }
  return environment;
}

export function runBoundedCommand({ executable, args = [], cwd, sandboxDir, timeoutMs, outputMaxBytes, environment = {} }) {
  if (!path.isAbsolute(executable) || !Array.isArray(args) || args.some((arg) => typeof arg !== "string")) return Promise.reject(new V2QualificationOperationalError("R17_PROCESS_ARGUMENT_INVALID"));
  fs.mkdirSync(sandboxDir, { recursive: true });
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { cwd, env: sanitizedEnvironment(sandboxDir, environment), shell: false, windowsHide: true, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"] });
    const chunks = [];
    let bytes = 0;
    let settled = false;
    const finish = (error, value) => { if (settled) return; settled = true; clearTimeout(timer); if (error) reject(error); else resolve(Object.freeze(value)); };
    const terminateTree = () => {
      if (child.pid === undefined) return false;
      if (process.platform === "win32") {
        const killed = spawnSync("taskkill.exe", ["/pid", String(child.pid), "/t", "/f"], { encoding: "utf8", windowsHide: true, timeout: 5000 });
        return killed.status === 0 || killed.status === 128;
      }
      try { process.kill(-child.pid, "SIGKILL"); return true; } catch {
        try { child.kill("SIGKILL"); return true; } catch { return false; }
      }
    };
    const stop = (code) => {
      const processTreeTerminated = terminateTree();
      const error = new V2QualificationOperationalError(code);
      error.processTreeTerminated = processTreeTerminated;
      finish(error);
    };
    const onData = (chunk) => {
      if (settled) return;
      bytes += chunk.length;
      if (bytes > outputMaxBytes) stop("R17_PROCESS_OUTPUT_EXCEEDED");
      else chunks.push(Buffer.from(chunk));
    };
    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    child.on("error", () => finish(new V2QualificationOperationalError("R17_PROCESS_START_FAILED")));
    child.on("exit", (exitCode, signal) => finish(null, {
      exitCode,
      signal: signal ?? "",
      output: Buffer.concat(chunks).toString("utf8"),
      outputBytes: bytes,
      securityObservations: Object.freeze({
        environmentSecretsInherited: false,
        filesystemIsolation: "not-proven",
        networkIsolation: "not-proven",
        processTreeResiduals: "not-proven",
      }),
    }));
    const timer = setTimeout(() => stop("R17_PROCESS_TIMEOUT"), timeoutMs);
  });
}
