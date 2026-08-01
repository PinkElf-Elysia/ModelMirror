import dns from "node:dns/promises";
import ipaddr from "ipaddr.js";

const BLOCKED_HOSTS = new Set([
  "localhost",
  "localhost.localdomain",
  "metadata.google.internal",
  "metadata.google.com",
  "instance-data.ec2.internal",
  "host.docker.internal",
  "gateway.docker.internal",
  "kubernetes.default.svc",
]);

const BLOCKED_IPV4 = new Set(["169.254.169.254", "100.100.100.200"]);
const ALLOWED_SCHEMES = new Set(["http:", "https:"]);
const SYNTHETIC_DNS_RANGE = ipaddr.parseCIDR("198.18.0.0/15");

function syntheticDnsEnabled(options = {}) {
  if (typeof options.allowSyntheticDns === "boolean") return options.allowSyntheticDns;
  return String(process.env.BROWSER_ALLOW_SYNTHETIC_DNS || "").toLowerCase() === "true";
}

export class NetworkPolicyError extends Error {
  constructor(message, code = "browser_network_denied") {
    super(message);
    this.code = code;
  }
}

export function normalizeHostname(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "");
}

export function assertPublicIp(value) {
  const normalized = normalizeHostname(value);
  if (BLOCKED_IPV4.has(normalized)) {
    throw new NetworkPolicyError("Cloud metadata addresses are blocked.");
  }
  let address;
  try {
    address = ipaddr.parse(normalized);
  } catch {
    throw new NetworkPolicyError("DNS returned an invalid address.");
  }
  if (address.kind() === "ipv6" && address.isIPv4MappedAddress()) {
    return assertPublicIp(address.toIPv4Address().toString());
  }
  const range = address.range();
  const publicRanges = new Set(["unicast"]);
  if (!publicRanges.has(range)) {
    throw new NetworkPolicyError(`Address range is blocked: ${range}.`);
  }
  return address.toString();
}

function assertResolvedIp(value, options = {}) {
  try {
    return assertPublicIp(value);
  } catch (error) {
    const normalized = normalizeHostname(value);
    let address;
    try {
      address = ipaddr.parse(normalized);
    } catch {
      throw error;
    }
    if (
      syntheticDnsEnabled(options) &&
      address.kind() === "ipv4" &&
      address.match(SYNTHETIC_DNS_RANGE)
    ) {
      return address.toString();
    }
    throw error;
  }
}

export function assertPublicHostname(value) {
  const hostname = normalizeHostname(value);
  if (!hostname || hostname.length > 253) {
    throw new NetworkPolicyError("A valid public hostname is required.");
  }
  if (
    BLOCKED_HOSTS.has(hostname) ||
    hostname.endsWith(".local") ||
    hostname.endsWith(".localhost") ||
    hostname.endsWith(".internal") ||
    hostname.endsWith(".home.arpa") ||
    hostname.endsWith(".svc") ||
    hostname.endsWith(".cluster.local")
  ) {
    throw new NetworkPolicyError("Local and service hostnames are blocked.");
  }
  if (ipaddr.isValid(hostname)) {
    assertPublicIp(hostname);
    return hostname;
  }
  if (!hostname.includes(".")) {
    throw new NetworkPolicyError("Single-label hostnames are blocked.");
  }
  if (!/^[a-z0-9.-]+$/.test(hostname) || hostname.includes("..")) {
    throw new NetworkPolicyError("Hostname contains unsupported characters.");
  }
  return hostname;
}

export async function resolvePublicHost(hostname, resolver = dns.lookup, options = {}) {
  const clean = assertPublicHostname(hostname);
  if (ipaddr.isValid(clean)) return [assertPublicIp(clean)];
  let records;
  try {
    records = await resolver(clean, { all: true, verbatim: true });
  } catch {
    throw new NetworkPolicyError("Public hostname could not be resolved.", "browser_dns_failed");
  }
  const addresses = Array.from(
    new Set((records || []).map((record) => assertResolvedIp(record.address, options))),
  );
  if (!addresses.length) {
    throw new NetworkPolicyError("Public hostname resolved to no addresses.", "browser_dns_failed");
  }
  return addresses;
}

export async function validatePublicUrl(rawUrl, resolver = dns.lookup, options = {}) {
  let parsed;
  try {
    parsed = new URL(String(rawUrl || ""));
  } catch {
    throw new NetworkPolicyError("Invalid browser URL.", "browser_invalid_url");
  }
  if (!ALLOWED_SCHEMES.has(parsed.protocol)) {
    throw new NetworkPolicyError("Only HTTP and HTTPS URLs are allowed.");
  }
  if (parsed.username || parsed.password) {
    throw new NetworkPolicyError("URL credentials are not allowed.");
  }
  const hostname = assertPublicHostname(parsed.hostname);
  const addresses = await resolvePublicHost(hostname, resolver, options);
  const port = Number(parsed.port || (parsed.protocol === "https:" ? 443 : 80));
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new NetworkPolicyError("Invalid network port.");
  }
  return { parsed, hostname, addresses, port };
}

export async function validatePublicWebSocketUrl(rawUrl, resolver = dns.lookup, options = {}) {
  let parsed;
  try {
    parsed = new URL(String(rawUrl || ""));
  } catch {
    throw new NetworkPolicyError("Invalid WebSocket URL.", "browser_invalid_url");
  }
  if (parsed.protocol !== "ws:" && parsed.protocol !== "wss:") {
    throw new NetworkPolicyError("Only WS and WSS WebSockets are allowed.");
  }
  if (parsed.username || parsed.password) {
    throw new NetworkPolicyError("URL credentials are not allowed.");
  }
  const publicUrl = new URL(parsed.toString());
  publicUrl.protocol = parsed.protocol === "wss:" ? "https:" : "http:";
  const validated = await validatePublicUrl(publicUrl.toString(), resolver, options);
  return { ...validated, parsed };
}

export function domainMatches(hostname, rule) {
  const host = normalizeHostname(hostname);
  const candidate = normalizeHostname(rule).replace(/^\*\./, "");
  return Boolean(candidate) && (host === candidate || host.endsWith(`.${candidate}`));
}
