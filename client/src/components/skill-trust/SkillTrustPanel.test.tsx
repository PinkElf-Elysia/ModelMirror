import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import SkillTrustPanel, { SkillTrustBadge } from "./SkillTrustPanel";
import type { SkillTrustReceipt } from "../../data/skillTrustIndex";

const receipt: SkillTrustReceipt = {
  receiptId: "skill-trust-fixture",
  trustFingerprint: "a".repeat(64),
  riskLevel: "high",
  trustStatus: "conditional",
  installPolicy: "confirm",
  compatibilityStatus: "conditional",
  routerEligible: false,
  summary: {
    fileCount: 3,
    totalBytes: 2048,
    textFileCount: 3,
    scriptCount: 1,
    opaqueResourceCount: 0,
  },
  source: {
    repoUrl: "https://github.com/example/skills",
    subPath: "skills/report",
    verifiedCommit: "b".repeat(40),
  },
  directoryTreeSha: "c".repeat(40),
  packageDigest: "d".repeat(64),
  scannerVersion: "skill-trust-scanner-v2",
  scripts: [{ path: "scripts/render.py", language: "python" }],
  opaqueResources: [],
  license: "Apache-2.0",
  allowedTools: ["sandbox_shell"],
  dependencies: ["python>=3.11"],
  commands: ["python scripts/render.py"],
  capabilities: { shell: true, network: false },
  findings: [
    {
      code: "trust_shell_required",
      severity: "error",
      message: "The Skill declares shell capability.",
      path: "SKILL.md",
      line: 12,
    },
  ],
};

describe("SkillTrustPanel", () => {
  it("keeps a risky install immutable and requires explicit confirmation", () => {
    const onConfirm = vi.fn();
    render(
      <SkillTrustPanel
        action="install"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
        receipt={receipt}
        title="事故复盘"
      />,
    );

    expect(screen.getByText("高风险 · 需确认")).toBeInTheDocument();
    expect(screen.getByText("不纳入自动发现")).toBeInTheDocument();
    expect(screen.getByText(/trust_shell_required/)).toBeInTheDocument();
    expect(screen.getByText(/固定 SHA/)).toHaveTextContent("b".repeat(40));
    expect(screen.getByRole("dialog", { name: "事故复盘" })).toBeInTheDocument();
    const install = screen.getByRole("button", { name: "接受风险并安装" });
    expect(install).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(install);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("renders a fail-closed unknown badge", () => {
    render(<SkillTrustBadge summary={null} />);
    expect(screen.getByText("信任状态未知")).toBeInTheDocument();
  });

  it("renders a local receipt without inventing a Git source", () => {
    const localReceipt: SkillTrustReceipt = {
      ...receipt,
      source: {
        kind: "local_import",
        importId: "skillimport_local",
        importRevision: 2,
        transportKind: "zip",
        transportDigest: "e".repeat(64),
      },
      contentTreeDigest: "f".repeat(64),
      directoryTreeSha: undefined,
    };

    render(
      <SkillTrustPanel
        action="inspect"
        onCancel={vi.fn()}
        receipt={localReceipt}
        title="本地报告"
      />,
    );

    expect(screen.getByText("信任凭据")).toBeInTheDocument();
    expect(screen.getByText(/本地 ZIP导入/)).toBeInTheDocument();
    expect(screen.getByText(/skillimport_local/)).toBeInTheDocument();
    expect(screen.queryByText(/固定 SHA/)).not.toBeInTheDocument();
  });

  it("closes with Escape and restores focus to the trigger", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)} type="button">查看凭据</button>
          {open ? (
            <SkillTrustPanel
              action="inspect"
              onCancel={() => setOpen(false)}
              receipt={receipt}
              title="事故复盘"
            />
          ) : null}
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "查看凭据" });
    trigger.focus();
    fireEvent.click(trigger);
    await waitFor(() => expect(screen.getByRole("button", { name: "关闭信任凭据" })).toHaveFocus());
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
