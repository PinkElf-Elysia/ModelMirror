"use strict";

// Trusted, pinned upstream packages still run behind an adapter-owned DNS
// boundary.  A package may resolve only the fixed service hosts selected by
// the server; update checks, telemetry, alternate endpoints and IP literals
// fail closed.
const dns = require("node:dns");
const net = require("node:net");

const allowed = new Set(
  String(process.env.MCP_ALLOWED_HOSTS || "")
    .split(",")
    .map((value) => value.trim().toLowerCase().replace(/\.$/, ""))
    .filter(Boolean),
);

function normalize(hostname) {
  return String(hostname || "").trim().toLowerCase().replace(/\.$/, "");
}

function assertAllowed(hostname) {
  const host = normalize(hostname);
  if (!host || net.isIP(host) || !allowed.has(host)) {
    const error = new Error("ModelMirror MCP egress policy denied this host.");
    error.code = "EAI_NONAME";
    throw error;
  }
  return host;
}

const originalLookup = dns.lookup.bind(dns);
dns.lookup = function guardedLookup(hostname, options, callback) {
  try {
    assertAllowed(hostname);
  } catch (error) {
    const cb = typeof options === "function" ? options : callback;
    if (typeof cb === "function") return process.nextTick(cb, error);
    throw error;
  }
  return originalLookup(hostname, options, callback);
};

if (dns.promises && typeof dns.promises.lookup === "function") {
  const originalPromiseLookup = dns.promises.lookup.bind(dns.promises);
  dns.promises.lookup = async function guardedPromiseLookup(hostname, options) {
    assertAllowed(hostname);
    return originalPromiseLookup(hostname, options);
  };
}

for (const name of ["resolve", "resolve4", "resolve6", "resolveAny", "resolveCaa", "resolveCname", "resolveMx", "resolveNaptr", "resolveNs", "resolvePtr", "resolveSoa", "resolveSrv", "resolveTxt"]) {
  if (typeof dns[name] !== "function") continue;
  const original = dns[name].bind(dns);
  dns[name] = function guardedResolve(hostname, ...args) {
    try {
      assertAllowed(hostname);
    } catch (error) {
      const callback = args.at(-1);
      if (typeof callback === "function") return process.nextTick(callback, error);
      throw error;
    }
    return original(hostname, ...args);
  };
}

delete process.env.MCP_ALLOWED_HOSTS;
