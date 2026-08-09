import {
  CanonicalJsonValueError,
  canonicalizeJsonValue,
} from "@matrix-oasis/runtime-pack-contracts";
import { makeDiagnostic } from "./diagnostics.mjs";

function bytesToHex(bytes) {
  let value = "";
  for (const byte of bytes) {
    value += byte.toString(16).padStart(2, "0");
  }
  return value;
}

async function sha256Hex(bytes) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw new Error("WEB_CRYPTO_UNAVAILABLE");
  }
  const digest = await subtle.digest("SHA-256", bytes);
  return bytesToHex(new Uint8Array(digest));
}

function canonicalTextOrDiagnostic(value, code, path) {
  try {
    return { text: canonicalizeJsonValue(value), diagnostics: [] };
  } catch (error) {
    if (!(error instanceof CanonicalJsonValueError)) {
      throw error;
    }
    return {
      text: undefined,
      diagnostics: [makeDiagnostic({ phase: "integrity", code, path })],
    };
  }
}

export async function validateIntegrity({
  runtimeText,
  runtimePack,
  receiptText,
  receipt,
}) {
  const diagnostics = [];
  const canonicalRuntime = canonicalTextOrDiagnostic(
    runtimePack,
    "RUNTIME_PACK_JSON_NON_CANONICAL",
    "/runtimePack",
  );
  const canonicalReceipt = canonicalTextOrDiagnostic(
    receipt,
    "RUNTIME_RECEIPT_JSON_NON_CANONICAL",
    "/receipt",
  );
  diagnostics.push(
    ...canonicalRuntime.diagnostics,
    ...canonicalReceipt.diagnostics,
  );

  if (
    canonicalRuntime.text !== undefined &&
    runtimeText !== canonicalRuntime.text
  ) {
    diagnostics.push(
      makeDiagnostic({
        phase: "integrity",
        code: "RUNTIME_PACK_JSON_NON_CANONICAL",
        path: "/runtimePack",
      }),
    );
  }
  if (
    canonicalReceipt.text !== undefined &&
    receiptText !== canonicalReceipt.text
  ) {
    diagnostics.push(
      makeDiagnostic({
        phase: "integrity",
        code: "RUNTIME_RECEIPT_JSON_NON_CANONICAL",
        path: "/receipt",
      }),
    );
  }

  const runtimeBytes = new TextEncoder().encode(runtimeText);
  if (receipt.artifact.byteLength !== runtimeBytes.byteLength) {
    diagnostics.push(
      makeDiagnostic({
        phase: "integrity",
        code: "RUNTIME_RECEIPT_ARTIFACT_BYTE_LENGTH_MISMATCH",
        path: "/receipt/artifact/byteLength",
        relatedPath: "/runtimePack",
      }),
    );
  }

  const actualSha256 = await sha256Hex(runtimeBytes);
  if (receipt.artifact.sha256 !== actualSha256) {
    diagnostics.push(
      makeDiagnostic({
        phase: "integrity",
        code: "RUNTIME_RECEIPT_ARTIFACT_SHA256_MISMATCH",
        path: "/receipt/artifact/sha256",
        relatedPath: "/runtimePack",
      }),
    );
  }

  return diagnostics;
}
