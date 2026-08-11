import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  VendorIntegrityError,
  verifyGodotDemoReference,
  verifyGodotVendor,
} from "./lib/vendor-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

try {
  const manifest = JSON.parse(
    await fs.readFile(path.join(moduleRoot, "third-party", "gdunit4.lock.json"), "utf8"),
  );
  const referenceManifest = JSON.parse(
    await fs.readFile(
      path.join(moduleRoot, "third-party", "godot-demo-projects", "reference.lock.json"),
      "utf8",
    ),
  );
  const [tree, reference] = await Promise.all([
    verifyGodotVendor({ moduleRoot, manifest }),
    verifyGodotDemoReference({ moduleRoot, manifest: referenceManifest }),
  ]);
  console.log(
    `GDUNIT4_VENDOR_OK files=${tree.fileCount} bytes=${tree.byteLength} tree=sha256:${tree.sha256}`,
  );
  console.log(
    `GODOT_DEMO_REFERENCE_OK commit=${reference.commit} bytes=${reference.referenceByteLength} sha256:${reference.referenceSha256}`,
  );
} catch (error) {
  const code = error instanceof VendorIntegrityError
    ? error.code
    : "GDUNIT4_VENDOR_OPERATIONAL_ERROR";
  console.error(code);
  process.exitCode = 1;
}
