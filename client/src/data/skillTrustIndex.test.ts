import { afterEach, describe, expect, it, vi } from "vitest";

describe("Skill trust API index", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("loads the server summary and resolves duplicate sources", async () => {
    const sourceKey = `https://github.com/example/skills#skills/report#${"b".repeat(40)}`;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          gateMode: "enforce",
          index: {
            version: 1,
            scannerVersion: "skill-trust-scanner-v2",
            catalogFingerprint: "c".repeat(64),
            trustIndexFingerprint: "d".repeat(64),
            candidateReceipts: { "catalog:project:report": "skill-trust-report" },
            receipts: [
              {
                receiptId: "skill-trust-report",
                trustFingerprint: "a".repeat(64),
                riskLevel: "low",
                trustStatus: "verified",
                installPolicy: "allow",
                compatibilityStatus: "portable",
                routerEligible: true,
                summary: {
                  fileCount: 1,
                  totalBytes: 100,
                  textFileCount: 1,
                  scriptCount: 0,
                  opaqueResourceCount: 0,
                },
              },
            ],
            fingerprint: "e".repeat(64),
          },
          sourceReceipts: { [sourceKey]: "skill-trust-report" },
        }),
      }),
    );
    const module = await import("./skillTrustIndex");
    const index = await module.loadSkillTrustSummaryIndex();
    expect(module.trustSummaryForCandidate(index, "catalog:project:report")?.riskLevel).toBe("low");
    expect(
      module.trustSummaryForSource(index, {
        repoUrl: "https://github.com/example/skills.git",
        subPath: "/skills/report/",
        verifiedCommit: "B".repeat(40),
      })?.receiptId,
    ).toBe("skill-trust-report");
  });

  it("does not cache a failed index request", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        json: async () => ({ detail: { code: "skill_trust_index_unavailable" } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          index: {
            version: 1,
            scannerVersion: "skill-trust-scanner-v2",
            catalogFingerprint: "c".repeat(64),
            trustIndexFingerprint: "d".repeat(64),
            candidateReceipts: {},
            receipts: [],
            fingerprint: "e".repeat(64),
          },
          sourceReceipts: {},
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    const module = await import("./skillTrustIndex");

    await expect(module.loadSkillTrustSummaryIndex()).rejects.toThrow(
      "skill_trust_index_unavailable",
    );
    await expect(module.loadSkillTrustSummaryIndex()).resolves.toMatchObject({
      version: 1,
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps off mode usable when the trust index is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          gateMode: "off",
          index: null,
          sourceReceipts: {},
          warning: { code: "skill_trust_index_unavailable" },
        }),
      }),
    );
    const module = await import("./skillTrustIndex");

    const index = await module.loadSkillTrustSummaryIndex();

    expect(index.gateMode).toBe("off");
    expect(module.effectiveTrustInstallPolicy(index.gateMode, null)).toBe("allow");
  });
});
