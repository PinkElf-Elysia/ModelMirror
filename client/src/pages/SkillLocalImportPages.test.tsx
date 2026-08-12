import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SkillTrustReceipt } from "../data/skillTrustIndex";
import type {
  LocalSkillImportRecord,
  LocalSkillImportStatus,
} from "../utils/skillLocalImportApi";
import SkillLocalImportDetailPage from "./SkillLocalImportDetailPage";
import SkillLocalImportIndexPage from "./SkillLocalImportIndexPage";

const api = vi.hoisted(() => ({
  deleteLocalSkillImport: vi.fn(),
  installLocalSkillImport: vi.fn(),
  listLocalSkillImports: vi.fn(),
  previewLocalSkillImportFile: vi.fn(),
  readLocalSkillImport: vi.fn(),
  readLocalSkillImportStatus: vi.fn(),
  rescanLocalSkillImport: vi.fn(),
  uploadLocalSkillFolder: vi.fn(),
  uploadLocalSkillZip: vi.fn(),
}));

vi.mock("../utils/skillLocalImportApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../utils/skillLocalImportApi")>()),
  ...api,
}));

const status: LocalSkillImportStatus = {
  enabled: true,
  available: true,
  version: 1,
  scannerVersion: "skill-trust-scanner-v2",
  supportedTransports: ["zip", "folder"],
  limits: {
    archiveBytes: 64 * 1024 * 1024,
    fileCount: 500,
    fileBytes: 10 * 1024 * 1024,
    expandedBytes: 50 * 1024 * 1024,
    storageBytes: 1024 * 1024 * 1024,
    activeImports: 100,
  },
  errorCode: null,
};

const receipt: SkillTrustReceipt = {
  receiptId: "trust_local_1",
  trustFingerprint: "b".repeat(64),
  riskLevel: "medium",
  trustStatus: "conditional",
  installPolicy: "confirm",
  compatibilityStatus: "conditional",
  routerEligible: true,
  summary: {
    fileCount: 2,
    totalBytes: 320,
    textFileCount: 2,
    scriptCount: 1,
    opaqueResourceCount: 0,
  },
  source: {
    kind: "local_import",
    importId: "skillimport_1",
    importRevision: 1,
    transportKind: "folder",
    transportDigest: "c".repeat(64),
  },
  contentTreeDigest: "d".repeat(64),
  packageDigest: "a".repeat(64),
  scannerVersion: "skill-trust-scanner-v2",
  scripts: [{ path: "scripts/check.py", language: "python" }],
  opaqueResources: [],
  license: null,
  allowedTools: ["sandbox_shell"],
  dependencies: [],
  commands: ["python scripts/check.py"],
  capabilities: { shell: true },
  findings: [
    {
      code: "trust_local_script_present",
      severity: "warning",
      message: "The package contains a local script.",
      path: "scripts/check.py",
    },
  ],
};

function record(
  overrides: Partial<LocalSkillImportRecord> = {},
): LocalSkillImportRecord {
  return {
    version: 1,
    importId: "skillimport_1",
    revision: 1,
    contentRevision: 1,
    state: "confirmation_required",
    transportKind: "folder",
    transportDigest: "c".repeat(64),
    localSkillId: "local-report",
    declaredName: "local-report",
    packageDigest: "a".repeat(64),
    receiptId: receipt.receiptId,
    trustFingerprint: receipt.trustFingerprint,
    trustReceipt: receipt,
    fileManifest: [
      {
        path: "SKILL.md",
        mode: "100644",
        sizeBytes: 240,
        sha256: "e".repeat(64),
      },
      {
        path: "scripts/check.py",
        mode: "100644",
        sizeBytes: 80,
        sha256: "f".repeat(64),
      },
    ],
    ignoredEntries: [],
    errorCode: null,
    installedSkillId: null,
    createdAt: 1,
    updatedAt: 2,
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("Skill local import pages", () => {
  it("shows a disabled status without exposing upload actions", async () => {
    api.readLocalSkillImportStatus.mockResolvedValue({
      ...status,
      enabled: false,
    });

    render(
      <MemoryRouter initialEntries={["/skills/import"]}>
        <SkillLocalImportIndexPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("本地导入已关闭")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择 ZIP" })).toBeDisabled();
    expect(api.listLocalSkillImports).not.toHaveBeenCalled();
  });

  it("uploads a ZIP and navigates to the immutable import record", async () => {
    api.readLocalSkillImportStatus.mockResolvedValue(status);
    api.listLocalSkillImports.mockResolvedValue({ imports: [], total: 0 });
    api.uploadLocalSkillZip.mockResolvedValue(record());

    render(
      <MemoryRouter initialEntries={["/skills/import"]}>
        <Routes>
          <Route element={<SkillLocalImportIndexPage />} path="/skills/import" />
          <Route element={<p>导入详情已打开</p>} path="/skills/import/:importId" />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("还没有本地导入记录");
    const input = document.querySelector('input[accept*=".zip"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["zip"], "report.zip", { type: "application/zip" })] },
    });

    expect(await screen.findByText("导入详情已打开")).toBeInTheDocument();
    expect(api.uploadLocalSkillZip).toHaveBeenCalledTimes(1);
  });

  it("requires immutable risk confirmation before installing", async () => {
    const pending = record();
    api.readLocalSkillImport.mockResolvedValue(pending);
    api.installLocalSkillImport.mockResolvedValue({
      import: record({ state: "installed", installedSkillId: "local-report" }),
      installed: {},
    });
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/skills/import/skillimport_1"]}>
        <Routes>
          <Route element={<SkillLocalImportDetailPage />} path="/skills/import/:importId" />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", { name: "核对风险并安装" }));
    const confirmButton = screen.getByRole("button", { name: "确认风险并安装" });
    expect(confirmButton).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    await user.click(confirmButton);

    await waitFor(() =>
      expect(api.installLocalSkillImport).toHaveBeenCalledWith(pending, {
        confirmed: true,
        expectedInstalledDigest: null,
      }),
    );
    expect(await screen.findByText("当前摘要已安装")).toBeInTheDocument();
  });

  it("keeps blocked imports inspectable but removes the install action", async () => {
    api.readLocalSkillImport.mockResolvedValue(
      record({
        state: "blocked",
        fileManifest: [],
        trustReceipt: { ...receipt, installPolicy: "block", trustStatus: "blocked" },
      }),
    );

    render(
      <MemoryRouter initialEntries={["/skills/import/skillimport_1"]}>
        <Routes>
          <Route element={<SkillLocalImportDetailPage />} path="/skills/import/:importId" />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/只能查看原因并删除/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /安装当前版本|核对风险并安装/ })).not.toBeInTheDocument();
  });
});
