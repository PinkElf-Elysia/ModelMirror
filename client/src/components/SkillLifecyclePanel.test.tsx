import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SkillLifecyclePanel from "./SkillLifecyclePanel";

const digest = "a".repeat(64);
const state = {
  skill_id: "recovery-skill",
  revision: 3,
  status: "uninstalled",
  current_version_id: null,
  recovery_version_id: "skillver_one",
  protected_version_ids: [],
  version_ids: ["skillver_one"],
  migration_code: null,
  events: [],
  created_at: 1,
  updated_at: 2,
};
const status = {
  enabled: true,
  available: true,
  version: "skill-lifecycle-v1",
  storeVersion: 2,
  limits: {
    nonCurrentVersionsPerSkill: 5,
    storageBytes: 1024 * 1024,
    fileCount: 500,
    fileBytes: 1024,
    packageBytes: 1024,
  },
  counts: {
    skills: 1,
    versions: 1,
    packages: 1,
    quarantinedRecords: 0,
    migrationBlocked: 0,
  },
  storageBytes: 120,
  pendingTransactions: 0,
  errorCode: null,
};
const version = {
  version_id: "skillver_one",
  skill_id: "recovery-skill",
  ordinal: 1,
  package_digest: digest,
  file_count: 2,
  total_bytes: 120,
  source_kind: "workspace_draft",
  source_id: "draft-one",
  source_revision: 2,
  repo_url: "workspace://draft/draft-one",
  sub_path: "",
  source_ref: null,
  trust_receipt_id: null,
  trust_fingerprint: null,
  trust_evidence_frozen: false,
  trust_risk_level: null,
  trust_status: null,
  trust_install_policy: null,
  trust_compatibility_status: null,
  trust_router_eligible: false,
  quality_required: false,
  quality_evidence_status: "not_applicable",
  quality_status: null,
  quality_decision_id: null,
  quality_run_id: null,
  created_at: 2,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SkillLifecyclePanel", () => {
  it("keeps uninstalled history discoverable and restores one exact version", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/lifecycle/skills") {
        return new Response(JSON.stringify({ status, items: [state] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/skills/lifecycle/migration") {
        return new Response(
          JSON.stringify({
            version: "skill-lifecycle-v1",
            applied: false,
            counts: { total: 0, eligible: 0, migrated: 0, blocked: 0, ignored: 0 },
            items: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url === "/api/skills/recovery-skill/versions") {
        return new Response(JSON.stringify({ state, versions: [version] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/rollback") && init?.method === "POST") {
        return new Response(
          JSON.stringify({ state: { ...state, status: "active" }, installed: { skill_id: "recovery-skill" } }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onChanged = vi.fn();

    render(<SkillLifecyclePanel onChanged={onChanged} />);

    expect(await screen.findByText("保留恢复点 · 1 个版本")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "恢复此版本" }));
    expect(screen.getByText(/正在运行的任务继续使用原绑定/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认恢复版本" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    const rollbackCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/rollback"));
    expect(rollbackCall).toBeTruthy();
    expect(JSON.parse(String(rollbackCall?.[1]?.body))).toEqual({
      expected_state_revision: 3,
      expected_current_version_id: null,
      expected_package_digest: digest,
      confirmed: true,
    });
  });
});
