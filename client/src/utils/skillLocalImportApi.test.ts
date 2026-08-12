import { afterEach, describe, expect, it, vi } from "vitest";
import {
  installLocalSkillImport,
  SkillLocalImportApiError,
  uploadLocalSkillFolder,
  type LocalSkillImportRecord,
} from "./skillLocalImportApi";

const digest = "a".repeat(64);
const fingerprint = "b".repeat(64);

const record: LocalSkillImportRecord = {
  version: 1,
  importId: "skillimport_123",
  revision: 3,
  contentRevision: 2,
  state: "confirmation_required",
  transportKind: "folder",
  transportDigest: "c".repeat(64),
  localSkillId: "local-report",
  declaredName: "local-report",
  packageDigest: digest,
  receiptId: "trust_local_123",
  trustFingerprint: fingerprint,
  fileManifest: [],
  ignoredEntries: [],
  errorCode: null,
  installedSkillId: null,
  createdAt: 1,
  updatedAt: 2,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("skillLocalImportApi", () => {
  it("keeps folder paths in a server-validated multipart manifest", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(record), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["body"], "SKILL.md", { type: "text/markdown" });
    Object.defineProperty(file, "webkitRelativePath", {
      configurable: true,
      value: "wrapper/SKILL.md",
    });

    await uploadLocalSkillFolder([file], "local-report");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = init.body as FormData;
    expect(body.get("transport_kind")).toBe("folder");
    expect(body.get("local_skill_id")).toBe("local-report");
    expect(JSON.parse(String(body.get("paths_json")))).toEqual([
      "wrapper/SKILL.md",
    ]);
  });

  it("binds install confirmation to both package versions", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ import: record, installed: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await installLocalSkillImport(record, {
      confirmed: true,
      expectedInstalledDigest: "d".repeat(64),
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      expected_revision: 3,
      expected_package_digest: digest,
      expected_trust_fingerprint: fingerprint,
      confirmed: true,
      expected_installed_digest: "d".repeat(64),
    });
  });

  it("preserves structured import errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "skill_import_stale",
              message: "The receipt changed.",
              details: { expectedRevision: 4 },
            },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(
      installLocalSkillImport(record, { confirmed: false }),
    ).rejects.toMatchObject({
      code: "skill_import_stale",
      status: 409,
      details: { expectedRevision: 4 },
    });
  });
});
