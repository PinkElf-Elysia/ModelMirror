import assert from "node:assert/strict";
import test from "node:test";

import {
  NetworkPolicyError,
  assertPublicHostname,
  assertPublicIp,
  resolvePublicHost,
  validatePublicUrl,
  validatePublicWebSocketUrl,
} from "./policy.mjs";

test("public addresses and hosts are accepted", async () => {
  assert.equal(assertPublicIp("93.184.216.34"), "93.184.216.34");
  assert.equal(assertPublicHostname("Example.COM."), "example.com");
  const result = await validatePublicUrl(
    "https://example.com/path",
    async () => [{ address: "93.184.216.34", family: 4 }],
  );
  assert.equal(result.hostname, "example.com");
});

test("private, local, metadata, and unsupported schemes are blocked", async () => {
  for (const value of [
    "127.0.0.1",
    "10.0.0.1",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.169.254",
    "::1",
    "fc00::1",
    "fe80::1",
  ]) {
    assert.throws(() => assertPublicIp(value), NetworkPolicyError);
  }
  for (const value of ["localhost", "server", "new-api", "host.docker.internal", "foo.local"] ) {
    assert.throws(() => assertPublicHostname(value), NetworkPolicyError);
  }
  await assert.rejects(() => validatePublicUrl("file:///etc/passwd"), NetworkPolicyError);
  await assert.rejects(() => validatePublicUrl("https://user:pass@example.com"), NetworkPolicyError);
});

test("mixed public and private DNS answers fail closed", async () => {
  await assert.rejects(
    () => resolvePublicHost("example.com", async () => [
      { address: "93.184.216.34", family: 4 },
      { address: "127.0.0.1", family: 4 },
    ]),
    NetworkPolicyError,
  );
});

test("opt-in synthetic DNS supports public hostnames without allowing literal reserved IPs", async () => {
  const result = await validatePublicUrl(
    "https://example.com/path",
    async () => [{ address: "198.18.0.155", family: 4 }],
    { allowSyntheticDns: true },
  );
  assert.deepEqual(result.addresses, ["198.18.0.155"]);
  await assert.rejects(
    () => validatePublicUrl("https://198.18.0.155/path", undefined, { allowSyntheticDns: true }),
    NetworkPolicyError,
  );
  await assert.rejects(
    () => resolvePublicHost(
      "example.com",
      async () => [
        { address: "198.18.0.155", family: 4 },
        { address: "10.0.0.5", family: 4 },
      ],
      { allowSyntheticDns: true },
    ),
    NetworkPolicyError,
  );
});

test("WebSockets use the same DNS and credential policy", async () => {
  const result = await validatePublicWebSocketUrl(
    "wss://example.com/socket",
    async () => [{ address: "93.184.216.34", family: 4 }],
  );
  assert.equal(result.hostname, "example.com");
  assert.equal(result.parsed.protocol, "wss:");
  await assert.rejects(
    () => validatePublicWebSocketUrl("ws://127.0.0.1/socket"),
    NetworkPolicyError,
  );
  await assert.rejects(
    () => validatePublicWebSocketUrl("wss://user:pass@example.com/socket"),
    NetworkPolicyError,
  );
});
