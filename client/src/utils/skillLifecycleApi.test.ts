import { afterEach, describe, expect, it, vi } from "vitest";
import {
  rollbackSkillLifecycleVersion,
  SkillLifecycleApiError,
} from "./skillLifecycleApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("skillLifecycleApi", () => {
  it("binds rollback to the exact state and immutable package", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ state: {}, installed: { skill_id: "pdf" } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await rollbackSkillLifecycleVersion({
      skillId: "pdf",
      versionId: "skillver_old",
      expectedStateRevision: 7,
      expectedCurrentVersionId: null,
      expectedPackageDigest: "a".repeat(64),
    });

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/skills/pdf/versions/skillver_old/rollback");
    expect(JSON.parse(String(init.body))).toEqual({
      expected_state_revision: 7,
      expected_current_version_id: null,
      expected_package_digest: "a".repeat(64),
      confirmed: true,
    });
  });

  it("preserves structured lifecycle conflicts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "skill_lifecycle_version_conflict",
              message: "Reload the lifecycle state.",
            },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(
      rollbackSkillLifecycleVersion({
        skillId: "pdf",
        versionId: "skillver_old",
        expectedStateRevision: 7,
        expectedCurrentVersionId: "skillver_current",
        expectedPackageDigest: "a".repeat(64),
      }),
    ).rejects.toEqual(
      expect.objectContaining<Partial<SkillLifecycleApiError>>({
        code: "skill_lifecycle_version_conflict",
        message: "Reload the lifecycle state.",
      }),
    );
  });
});
