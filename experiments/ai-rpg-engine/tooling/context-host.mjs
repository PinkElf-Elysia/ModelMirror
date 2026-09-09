import { createHash } from "node:crypto";
import { APPROVED_HOST_TEMPLATE, RPG04_APPROVED_HOST_SHA256 } from "../context/approved-host.mjs";
import { loadRpg04RuntimeProtocol } from "./context-protocol.mjs";
import { canonicalJson } from "../runtime/contracts.mjs";

const fail = (code) => Object.freeze({
  valid: false,
  diagnostics: Object.freeze([{ phase: "host", severity: "error", code, path: "/host" }]),
});

export function loadRpg04ApprovedHost(...args) {
  if (args.length !== 0) return fail("RPG04_HOST_ARGUMENTS_REJECTED");
  const protocol = loadRpg04RuntimeProtocol();
  if (!protocol.valid) return fail("RPG04_HOST_PROTOCOL_REJECTED");

  const sourceCanonical = canonicalJson(APPROVED_HOST_TEMPLATE);
  if (!sourceCanonical.valid) return fail("RPG04_HOST_CANONICAL_INVALID");
  const sourceSha256 = createHash("sha256").update(Buffer.from(sourceCanonical.value, "utf8")).digest("hex");
  if (sourceSha256 !== RPG04_APPROVED_HOST_SHA256) return fail("RPG04_HOST_HASH_MISMATCH");
  const content = `${protocol.value.content}\n\n${APPROVED_HOST_TEMPLATE.content}`;
  const hostTemplate = Object.freeze({ id: "host.modelmirror-rpg04.approved-composed", version: "0.1.0", content });
  const canonical = canonicalJson(hostTemplate);
  if (!canonical.valid) return fail("RPG04_COMPOSED_HOST_CANONICAL_INVALID");
  const sha256 = createHash("sha256").update(Buffer.from(canonical.value, "utf8")).digest("hex");
  const binding = Object.freeze({ id: hostTemplate.id, version: hostTemplate.version, sha256 });
  return Object.freeze({
    valid: true,
    diagnostics: Object.freeze([]),
    value: Object.freeze({
      hostTemplate,
      binding,
      content,
      contentSha256: createHash("sha256").update(Buffer.from(content, "utf8")).digest("hex"),
      activation: Object.freeze({
        ready: true,
        status: "user_approved_for_bounded_testing",
        blockers: Object.freeze([]),
      }),
      sources: Object.freeze([
        Object.freeze({ id: protocol.value.id, version: protocol.value.version, sha256: protocol.value.sha256, sourceReference: protocol.value.sourceReference }),
        Object.freeze({ id: APPROVED_HOST_TEMPLATE.id, version: APPROVED_HOST_TEMPLATE.version, sha256: sourceSha256, sourceReference: "context/approved-host.mjs" }),
      ]),
    }),
  });
}
