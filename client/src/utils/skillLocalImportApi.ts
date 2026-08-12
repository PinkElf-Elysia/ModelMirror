import type {
  InstalledSkillTrustFields,
  SkillTrustReceipt,
} from "../data/skillTrustIndex";

export type LocalSkillImportState =
  | "scanning"
  | "ready"
  | "confirmation_required"
  | "blocked"
  | "failed"
  | "installed"
  | "superseded"
  | "archived"
  | "stale";

export interface LocalSkillImportStatus {
  enabled: boolean;
  available: boolean;
  version: number;
  scannerVersion: string;
  supportedTransports: Array<"zip" | "folder">;
  limits: {
    archiveBytes: number;
    fileCount: number;
    fileBytes: number;
    expandedBytes: number;
    storageBytes: number;
    activeImports: number;
  };
  errorCode: string | null;
}

export interface LocalSkillImportFile {
  path: string;
  mode: string;
  sizeBytes: number;
  sha256: string;
}

export interface LocalSkillReplacementChange {
  path: string;
  status: "added" | "removed" | "changed" | string;
  kind: "text" | "binary" | string;
  oldSize?: number;
  newSize?: number;
  oldSha256?: string;
  newSha256?: string;
  diff?: string;
  diffTruncated?: boolean;
}

export interface LocalSkillReplacementPreview {
  skillId: string;
  sourceKind: string;
  installedDigest: string;
  newDigest: string;
  required: boolean;
  allowed: boolean;
  errorCode: string | null;
  changes: LocalSkillReplacementChange[];
  changesTruncated: boolean;
  diffTruncated: boolean;
}

export interface LocalSkillImportRecord {
  version: number;
  importId: string;
  revision: number;
  contentRevision: number;
  state: LocalSkillImportState;
  transportKind: "zip" | "folder";
  transportDigest: string;
  localSkillId: string | null;
  declaredName: string | null;
  packageDigest: string | null;
  receiptId: string | null;
  trustFingerprint: string | null;
  trustReceipt?: SkillTrustReceipt | null;
  fileManifest: LocalSkillImportFile[];
  ignoredEntries: Array<{ reason?: string; count?: number }>;
  errorCode: string | null;
  installedSkillId: string | null;
  replacementPreview?: LocalSkillReplacementPreview | null;
  createdAt: number;
  updatedAt: number;
}

export interface LocalImportedInstalledSkill extends InstalledSkillTrustFields {
  skill_id: string;
  name: string;
  description: string;
  repo_url: string;
  sub_path: string;
  installed_at: number;
  source_ref?: string | null;
  source_kind: string;
  source_id?: string | null;
  source_revision?: number | null;
  content_digest: string;
}

export class SkillLocalImportApiError extends Error {
  code: string;
  status: number;
  details: Record<string, unknown>;

  constructor(
    message: string,
    options: {
      code?: string;
      status?: number;
      details?: Record<string, unknown>;
    } = {},
  ) {
    super(message);
    this.name = "SkillLocalImportApiError";
    this.code = options.code || "skill_import_scan_failed";
    this.status = options.status || 0;
    this.details = options.details || {};
  }
}

async function readApiError(response: Response) {
  const fallback = `请求失败（${response.status}）`;
  try {
    const payload = (await response.json()) as {
      detail?:
        | string
        | {
            code?: string;
            message?: string;
            details?: Record<string, unknown>;
          };
    };
    if (typeof payload.detail === "string") {
      return new SkillLocalImportApiError(payload.detail || fallback, {
        status: response.status,
      });
    }
    return new SkillLocalImportApiError(
      payload.detail?.message || payload.detail?.code || fallback,
      {
        code: payload.detail?.code,
        details: payload.detail?.details,
        status: response.status,
      },
    );
  } catch {
    return new SkillLocalImportApiError(fallback, { status: response.status });
  }
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit) {
  const response = await fetch(input, init);
  if (!response.ok) throw await readApiError(response);
  return (await response.json()) as T;
}

export const formatImportBytes = (value: number) => {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
};

export function readLocalSkillImportStatus() {
  return requestJson<LocalSkillImportStatus>("/api/skills/imports/status");
}

export function listLocalSkillImports() {
  return requestJson<{ imports: LocalSkillImportRecord[]; total: number }>(
    "/api/skills/imports",
  );
}

export function readLocalSkillImport(importId: string) {
  return requestJson<LocalSkillImportRecord>(
    `/api/skills/imports/${encodeURIComponent(importId)}`,
  );
}

export function uploadLocalSkillZip(file: File, localSkillId = "") {
  const body = new FormData();
  body.append("transport_kind", "zip");
  if (localSkillId.trim()) body.append("local_skill_id", localSkillId.trim());
  body.append("archive", file, file.name);
  return requestJson<LocalSkillImportRecord>("/api/skills/imports", {
    body,
    method: "POST",
  });
}

export function uploadLocalSkillFolder(files: File[], localSkillId = "") {
  const body = new FormData();
  const paths = files.map((file) => file.webkitRelativePath || file.name);
  body.append("transport_kind", "folder");
  body.append("paths_json", JSON.stringify(paths));
  if (localSkillId.trim()) body.append("local_skill_id", localSkillId.trim());
  files.forEach((file) => body.append("files", file, file.name));
  return requestJson<LocalSkillImportRecord>("/api/skills/imports", {
    body,
    method: "POST",
  });
}

export function previewLocalSkillImportFile(importId: string, path: string) {
  const params = new URLSearchParams({ path });
  return requestJson<{ importId: string; path: string; content: string }>(
    `/api/skills/imports/${encodeURIComponent(importId)}/file?${params}`,
  );
}

function optimisticPayload(record: LocalSkillImportRecord) {
  if (!record.packageDigest || !record.trustFingerprint) {
    throw new SkillLocalImportApiError("导入记录缺少可验证的包摘要或信任指纹。", {
      code: "skill_import_stale",
      status: 409,
    });
  }
  return {
    expected_revision: record.revision,
    expected_package_digest: record.packageDigest,
    expected_trust_fingerprint: record.trustFingerprint,
  };
}

export function rescanLocalSkillImport(record: LocalSkillImportRecord) {
  return requestJson<LocalSkillImportRecord>(
    `/api/skills/imports/${encodeURIComponent(record.importId)}/rescan`,
    {
      body: JSON.stringify(optimisticPayload(record)),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    },
  );
}

export function installLocalSkillImport(
  record: LocalSkillImportRecord,
  options: { confirmed: boolean; expectedInstalledDigest?: string | null },
) {
  return requestJson<{
    import: LocalSkillImportRecord;
    installed: LocalImportedInstalledSkill;
  }>(`/api/skills/imports/${encodeURIComponent(record.importId)}/install`, {
    body: JSON.stringify({
      ...optimisticPayload(record),
      confirmed: options.confirmed,
      ...(options.expectedInstalledDigest
        ? { expected_installed_digest: options.expectedInstalledDigest }
        : {}),
    }),
    headers: { "Content-Type": "application/json" },
    method: "POST",
  });
}

export function deleteLocalSkillImport(record: LocalSkillImportRecord) {
  return requestJson<{ ok: boolean }>(
    `/api/skills/imports/${encodeURIComponent(record.importId)}`,
    {
      body: JSON.stringify(optimisticPayload(record)),
      headers: { "Content-Type": "application/json" },
      method: "DELETE",
    },
  );
}
