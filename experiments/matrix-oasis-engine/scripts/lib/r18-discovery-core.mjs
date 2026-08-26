import { createHash } from "node:crypto";
import {
  closeSync,
  fstatSync,
  lstatSync,
  openSync,
  readFileSync,
  realpathSync,
} from "node:fs";
import {
  mkdir,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { V2_LANES } from "@matrix-oasis/v2-landscape-contracts";

const QUERY_SET_PATH = "third-party/v2-landscape-references/discovery-query-set.json";
const EXPECTED_HOSTS = Object.freeze([
  "api.github.com",
  "github.com",
  "godotengine.org",
  "docs.inworld.ai",
  "docs.convai.com",
  "developer.nvidia.com",
  "rosebud.ai",
]);
const GITHUB_API_HOST = EXPECTED_HOSTS[0];
const USER_AGENT = "matrix-oasis-r18-public-discovery";
const MAX_SELECTED_PER_LANE = 4;
const MAX_REPOSITORY_IDENTITIES = 23;
const SAFE_DISCOVERY_ID = /^[a-z][a-z0-9-]{0,63}$/u;
const SAFE_REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/u;

export class R18DiscoveryPlanError extends Error {
  constructor(code, details = null) {
    super(code);
    this.name = "R18DiscoveryPlanError";
    this.code = code;
    this.details = details;
  }
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function canonicalText(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function exactKeys(value, expected, code) {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join(",") !== [...expected].sort().join(",")) {
    throw new R18DiscoveryPlanError(code);
  }
}

function normalizeRepository(item, laneId, rank) {
  const projected = {
    archived: item?.archived,
    default_branch: item?.default_branch,
    disabled: item?.disabled,
    fork: item?.fork,
    full_name: item?.full_name,
    license: item?.license == null ? null : { spdx_id: item.license.spdx_id },
    private: item?.private,
    pushed_at: item?.pushed_at,
    stargazers_count: item?.stargazers_count,
  };
  exactKeys(projected, [
    "archived", "default_branch", "disabled", "fork", "full_name", "license", "private", "pushed_at", "stargazers_count",
  ], "R18_DISCOVERY_GITHUB_RESPONSE_INVALID");
  if (
    typeof projected.full_name !== "string" ||
    !SAFE_REPOSITORY.test(projected.full_name) ||
    typeof projected.default_branch !== "string" ||
    projected.default_branch.length < 1 ||
    projected.default_branch.length > 255 ||
    typeof projected.stargazers_count !== "number" ||
    typeof projected.pushed_at !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/u.test(projected.pushed_at) ||
    typeof projected.archived !== "boolean" ||
    typeof projected.disabled !== "boolean" ||
    typeof projected.fork !== "boolean" ||
    typeof projected.private !== "boolean" ||
    (projected.license !== null && typeof projected.license.spdx_id !== "string")
  ) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_GITHUB_RESPONSE_INVALID");
  }
  return {
    id: projected.full_name.toLowerCase().replaceAll(/[^a-z0-9]+/gu, "-").replaceAll(/^-|-$/gu, ""),
    repository: projected.full_name,
    laneId,
    rank,
    defaultBranch: projected.default_branch,
    licenseSpdx: projected.license?.spdx_id ?? null,
    archived: projected.archived,
    disabled: projected.disabled,
    fork: projected.fork,
    private: projected.private,
    stars: projected.stargazers_count,
    pushedAt: projected.pushed_at,
  };
}

function validateOutputTarget(output) {
  if (typeof output !== "string" || output.length < 1 || output.includes("\0")) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_OUTPUT_INVALID");
  }
  const resolved = path.resolve(output);
  const parent = path.dirname(resolved);
  const parentRoot = path.parse(parent).root;
  if (
    path.basename(parent).toLowerCase() !== "tmp" ||
    path.dirname(parent) !== parentRoot ||
    parentRoot.slice(0, 1).toLowerCase() !== "c" ||
    path.basename(resolved).length < 1 ||
    path.basename(resolved) === "." ||
    path.basename(resolved) === ".."
  ) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_OUTPUT_INVALID");
  }
  const parentStat = lstatSync(parent, { bigint: true });
  if (!parentStat.isDirectory() || parentStat.isSymbolicLink() || realpathSync.native(parent) !== parent) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_OUTPUT_IDENTITY_INVALID");
  }
  try {
    lstatSync(resolved);
    throw new R18DiscoveryPlanError("R18_DISCOVERY_OUTPUT_EXISTS");
  } catch (error) {
    if (error instanceof R18DiscoveryPlanError) throw error;
    if (error?.code !== "ENOENT") throw new R18DiscoveryPlanError("R18_DISCOVERY_OUTPUT_INVALID");
  }
  return {
    resolved,
    parent,
    parentIdentity: `${parentStat.dev}:${parentStat.ino}`,
  };
}

function verifyOutputParent(target) {
  const stat = lstatSync(target.parent, { bigint: true });
  if (
    !stat.isDirectory() ||
    stat.isSymbolicLink() ||
    realpathSync.native(target.parent) !== target.parent ||
    `${stat.dev}:${stat.ino}` !== target.parentIdentity
  ) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_OUTPUT_IDENTITY_INVALID");
  }
  try {
    lstatSync(target.resolved);
    throw new R18DiscoveryPlanError("R18_DISCOVERY_OUTPUT_EXISTS");
  } catch (error) {
    if (error instanceof R18DiscoveryPlanError) throw error;
    if (error?.code !== "ENOENT") throw new R18DiscoveryPlanError("R18_DISCOVERY_OUTPUT_INVALID");
  }
}

function createRequestState(querySet) {
  return {
    querySet,
    counts: Object.fromEntries(EXPECTED_HOSTS.map((host) => [host, 0])),
    responseBytes: 0,
  };
}

async function readBoundedBody(response, state) {
  const declared = Number(response.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > state.querySet.responseMaxBytes) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_RESPONSE_TOO_LARGE");
  }
  if (!response.body) throw new R18DiscoveryPlanError("R18_DISCOVERY_RESPONSE_INVALID");
  const chunks = [];
  let total = 0;
  for await (const chunk of response.body) {
    total += chunk.byteLength;
    state.responseBytes += chunk.byteLength;
    if (total > state.querySet.responseMaxBytes || state.responseBytes > state.querySet.totalResponseMaxBytes) {
      throw new R18DiscoveryPlanError("R18_DISCOVERY_RESPONSE_TOO_LARGE");
    }
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks, total);
}

async function requestPublicBytes(state, { host, requestPath, accept, stage }) {
  if (!EXPECTED_HOSTS.includes(host) || !requestPath.startsWith("/") || requestPath.startsWith("//")) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_REQUEST_INVALID");
  }
  const maximum = state.querySet.requestBudget[host];
  state.counts[host] += 1;
  if (!Number.isSafeInteger(maximum) || state.counts[host] > maximum) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_REQUEST_BUDGET_EXCEEDED");
  }
  const url = new URL(`https://${host}${requestPath}`);
  if (url.protocol !== "https:" || url.hostname !== host || url.username || url.password || url.port) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_REQUEST_INVALID");
  }
  let response;
  try {
    response = await fetch(url, {
      method: "GET",
      redirect: "error",
      credentials: "omit",
      headers: {
        accept,
        "user-agent": USER_AGENT,
      },
      signal: AbortSignal.timeout(state.querySet.timeoutMs),
    });
  } catch (error) {
    const reason = error?.name === "TimeoutError" ? "timeout" : "transport-or-redirect";
    throw new R18DiscoveryPlanError("R18_DISCOVERY_REQUEST_FAILED", {
      stage,
      host,
      reason,
      requestCounts: Object.entries(state.counts).sort(([left], [right]) => left.localeCompare(right)).map(([key, count]) => ({ host: key, count })),
    });
  }
  if (response.url !== url.href || response.status < 200 || response.status > 299) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_RESPONSE_REJECTED", {
      stage,
      host,
      statusClass: `${Math.floor(response.status / 100)}xx`,
      requestCounts: Object.entries(state.counts).sort(([left], [right]) => left.localeCompare(right)).map(([key, count]) => ({ host: key, count })),
    });
  }
  const bytes = await readBoundedBody(response, state);
  return {
    bytes,
    contentType: response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() || "unknown",
    status: response.status,
  };
}

function parseJsonResponse(response, code) {
  try {
    return JSON.parse(response.bytes.toString("utf8"));
  } catch {
    throw new R18DiscoveryPlanError(code);
  }
}

function selectRepositories(laneResults) {
  const selected = new Map();
  for (let rank = 0; rank < MAX_SELECTED_PER_LANE; rank += 1) {
    for (const lane of laneResults) {
      const repository = lane.repositories[rank];
      if (repository) {
        if (!selected.has(repository.repository.toLowerCase())) selected.set(repository.repository.toLowerCase(), repository);
      }
      if (selected.size === MAX_REPOSITORY_IDENTITIES) break;
    }
    if (selected.size === MAX_REPOSITORY_IDENTITIES) break;
  }
  return [...selected.values()].sort((left, right) => left.repository.localeCompare(right.repository));
}

async function discoverRepositorySearch(state) {
  const laneResults = [];
  for (const lane of state.querySet.lanes) {
    const requestPath = `/search/repositories?q=${encodeURIComponent(lane.githubQuery)}&sort=stars&order=desc&per_page=10`;
    const response = await requestPublicBytes(state, {
      host: GITHUB_API_HOST,
      requestPath,
      accept: "application/vnd.github+json",
      stage: `search-${lane.id}`,
    });
    const payload = parseJsonResponse(response, "R18_DISCOVERY_GITHUB_RESPONSE_INVALID");
    const projected = {
      incomplete_results: payload?.incomplete_results,
      items: payload?.items,
      total_count: payload?.total_count,
    };
    exactKeys(projected, ["incomplete_results", "items", "total_count"], "R18_DISCOVERY_GITHUB_RESPONSE_INVALID");
    if (
      typeof projected.incomplete_results !== "boolean" ||
      projected.incomplete_results ||
      !Number.isSafeInteger(projected.total_count) ||
      projected.total_count < 0 ||
      !Array.isArray(projected.items) ||
      projected.items.length > 10
    ) {
      throw new R18DiscoveryPlanError("R18_DISCOVERY_GITHUB_RESPONSE_INVALID");
    }
    const repositories = projected.items.map((item, index) => normalizeRepository(item, lane.id, index + 1));
    laneResults.push({
      laneId: lane.id,
      querySha256: sha256(Buffer.from(lane.githubQuery, "utf8")),
      responseSha256: sha256(response.bytes),
      repositories,
    });
  }
  return laneResults;
}

async function discoverRepositoryIdentities(state, selectedRepositories) {
  const identities = [];
  const identityFailures = [];
  for (const repository of selectedRepositories) {
    const [owner, name] = repository.repository.split("/");
    const requestPath = `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/commits/${encodeURIComponent(repository.defaultBranch)}`;
    let response;
    try {
      response = await requestPublicBytes(state, {
        host: GITHUB_API_HOST,
        requestPath,
        accept: "application/vnd.github+json",
        stage: "repository-identity",
      });
    } catch (error) {
      if (error instanceof R18DiscoveryPlanError && ["R18_DISCOVERY_REQUEST_FAILED", "R18_DISCOVERY_RESPONSE_REJECTED"].includes(error.code)) {
        identityFailures.push({ repository: repository.repository, code: error.code });
        continue;
      }
      throw error;
    }
    const payload = parseJsonResponse(response, "R18_DISCOVERY_GITHUB_COMMIT_INVALID");
    const projected = { sha: payload?.sha, gitTreeSha1: payload?.commit?.tree?.sha };
    exactKeys(projected, ["sha", "gitTreeSha1"], "R18_DISCOVERY_GITHUB_COMMIT_INVALID");
    if (!/^[0-9a-f]{40}$/u.test(projected.sha) || !/^[0-9a-f]{40}$/u.test(projected.gitTreeSha1)) {
      throw new R18DiscoveryPlanError("R18_DISCOVERY_GITHUB_COMMIT_INVALID");
    }
    identities.push({
      repository: repository.repository,
      defaultBranch: repository.defaultBranch,
      commit: projected.sha,
      gitTreeSha1: projected.gitTreeSha1,
      responseSha256: sha256(response.bytes),
    });
  }
  return { identities, identityFailures };
}

async function discoverRepositories(state) {
  const laneResults = await discoverRepositorySearch(state);
  return { laneResults, ...await discoverRepositoryIdentities(state, selectRepositories(laneResults)) };
}

function validateNormalizedSearchRepository(repository, laneId, rank) {
  exactKeys(repository, [
    "archived", "defaultBranch", "disabled", "fork", "id", "laneId", "licenseSpdx", "private", "pushedAt", "rank", "repository", "stars",
  ], "R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
  if (
    repository.laneId !== laneId ||
    repository.rank !== rank ||
    typeof repository.id !== "string" ||
    !SAFE_REPOSITORY.test(repository.repository) ||
    typeof repository.defaultBranch !== "string" ||
    repository.defaultBranch.length < 1 ||
    repository.defaultBranch.length > 255 ||
    (repository.licenseSpdx !== null && typeof repository.licenseSpdx !== "string") ||
    typeof repository.archived !== "boolean" ||
    typeof repository.disabled !== "boolean" ||
    typeof repository.fork !== "boolean" ||
    typeof repository.private !== "boolean" ||
    !Number.isSafeInteger(repository.stars) ||
    repository.stars < 0 ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/u.test(repository.pushedAt)
  ) throw new R18DiscoveryPlanError("R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
  return structuredClone(repository);
}

function loadSearchEvidence(inputPath, querySetSha256) {
  if (typeof inputPath !== "string" || inputPath.length < 1 || inputPath.includes("\0")) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
  }
  const resolved = path.resolve(inputPath);
  const parent = path.dirname(resolved);
  const temporaryRoot = path.dirname(parent);
  let canonicalParent;
  let parentStat;
  try {
    canonicalParent = realpathSync.native(parent);
    parentStat = lstatSync(parent);
  } catch {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
  }
  if (
    path.basename(resolved) !== "public-search-evidence.json" ||
    path.basename(temporaryRoot).toLowerCase() !== "tmp" ||
    path.dirname(temporaryRoot) !== path.parse(temporaryRoot).root ||
    path.parse(temporaryRoot).root.slice(0, 1).toLowerCase() !== "c" ||
    canonicalParent !== parent ||
    parentStat.isSymbolicLink()
  ) throw new R18DiscoveryPlanError("R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
  let descriptor;
  let bytes;
  try {
    descriptor = openSync(resolved, "r");
    const before = fstatSync(descriptor, { bigint: true });
    if (!before.isFile() || before.size > 4n * 1024n * 1024n) throw new Error("invalid");
    bytes = readFileSync(descriptor);
    const after = fstatSync(descriptor, { bigint: true });
    if (`${before.dev}:${before.ino}:${before.size}` !== `${after.dev}:${after.ino}:${after.size}`) throw new Error("changed");
  } catch {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
  }
  let value;
  try {
    value = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
  }
  return validateR18SearchEvidenceValue(value, querySetSha256);
}

export function validateR18SearchEvidenceValue(value, querySetSha256) {
  exactKeys(value, ["format", "formatVersion", "lanes", "mode", "querySetSha256"], "R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
  if (
    value.format !== "matrix-oasis.r18-public-search-evidence" ||
    value.formatVersion !== "0.1.0" ||
    value.mode !== "search-only" ||
    value.querySetSha256 !== querySetSha256 ||
    !Array.isArray(value.lanes) ||
    value.lanes.length !== V2_LANES.length
  ) throw new R18DiscoveryPlanError("R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
  const lanes = value.lanes.map((lane, laneIndex) => {
    exactKeys(lane, ["laneId", "querySha256", "repositories", "responseSha256"], "R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
    if (
      lane.laneId !== V2_LANES[laneIndex] ||
      !/^[0-9a-f]{64}$/u.test(lane.querySha256) ||
      !/^[0-9a-f]{64}$/u.test(lane.responseSha256) ||
      !Array.isArray(lane.repositories) ||
      lane.repositories.length > 10
    ) throw new R18DiscoveryPlanError("R18_DISCOVERY_SEARCH_EVIDENCE_INVALID");
    return {
      laneId: lane.laneId,
      querySha256: lane.querySha256,
      responseSha256: lane.responseSha256,
      repositories: lane.repositories.map((repository, index) => validateNormalizedSearchRepository(repository, lane.laneId, index + 1)),
    };
  });
  return lanes;
}

async function discoverPublicDocuments(state) {
  const documents = [];
  for (const [index, item] of state.querySet.publicDocuments.entries()) {
    const response = await requestPublicBytes(state, {
      host: item.host,
      requestPath: item.path,
      accept: "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.1",
      stage: `public-document-${index + 1}`,
    });
    documents.push({
      id: item.id,
      source: { host: item.host, path: item.path },
      status: response.status,
      contentType: response.contentType,
      byteLength: response.bytes.byteLength,
      sha256: sha256(response.bytes),
    });
  }
  return documents;
}

async function writeExclusiveJson(directory, name, value) {
  await writeFile(path.join(directory, name), canonicalText(value), { encoding: "utf8", flag: "wx" });
}

export async function executeR18Discovery({ moduleRoot, output, acknowledged, mode = "full", searchEvidencePath = null }) {
  if (acknowledged !== true) throw new R18DiscoveryPlanError("R18_DISCOVERY_APPROVAL_REQUIRED");
  if (!["full", "documents-only", "github-only", "search-only", "identity-only"].includes(mode)) throw new R18DiscoveryPlanError("R18_DISCOVERY_MODE_INVALID");
  const target = validateOutputTarget(output);
  const { bytes: querySetBytes, value: querySet } = readQuerySet(moduleRoot);
  const staging = path.join(target.parent, `.${path.basename(target.resolved)}.staging-${process.pid}`);
  try {
    await mkdir(staging, { recursive: false });
  } catch {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_STAGING_FAILED");
  }
  try {
    const state = createRequestState(querySet);
    let repositories;
    if (mode === "documents-only") {
      repositories = { laneResults: [], identities: [], identityFailures: [] };
    } else if (mode === "search-only") {
      repositories = { laneResults: await discoverRepositorySearch(state), identities: [], identityFailures: [] };
    } else if (mode === "identity-only") {
      const laneResults = loadSearchEvidence(searchEvidencePath, sha256(querySetBytes));
      repositories = { laneResults, ...await discoverRepositoryIdentities(state, selectRepositories(laneResults)) };
    } else {
      repositories = await discoverRepositories(state);
    }
    const publicDocuments = ["github-only", "search-only", "identity-only"].includes(mode) ? [] : await discoverPublicDocuments(state);
    const searchEvidence = {
      format: "matrix-oasis.r18-public-search-evidence",
      formatVersion: "0.1.0",
      mode,
      querySetSha256: sha256(querySetBytes),
      lanes: repositories.laneResults,
    };
    const identityEvidence = {
      format: "matrix-oasis.r18-repository-identity-evidence",
      formatVersion: "0.1.0",
      repositories: repositories.identities,
      failures: repositories.identityFailures,
    };
    const documentEvidence = {
      format: "matrix-oasis.r18-public-document-evidence",
      formatVersion: "0.1.0",
      documents: publicDocuments,
    };
    const files = [
      ["public-search-evidence.json", searchEvidence],
      ["repository-identity-evidence.json", identityEvidence],
      ["public-document-evidence.json", documentEvidence],
    ];
    const fileHashes = [];
    for (const [name, value] of files) {
      const text = canonicalText(value);
      await writeFile(path.join(staging, name), text, { encoding: "utf8", flag: "wx" });
      fileHashes.push({ name, sha256: sha256(Buffer.from(text, "utf8")) });
    }
    const report = {
      format: "matrix-oasis.r18-public-discovery-report",
      formatVersion: "0.1.0",
      querySetSha256: sha256(querySetBytes),
      requestCounts: Object.entries(state.counts).sort(([left], [right]) => left.localeCompare(right)).map(([host, count]) => ({ host, count })),
      responseBytes: state.responseBytes,
      laneCount: repositories.laneResults.length,
      uniqueRepositoryIdentities: repositories.identities.length,
      repositoryIdentityFailures: repositories.identityFailures.length,
      publicDocumentCount: publicDocuments.length,
      credentialsUsed: false,
      loginUsed: false,
      commercialApiCalls: false,
      supplierCalls: false,
      files: fileHashes,
    };
    await writeExclusiveJson(staging, "discovery-report.json", report);
    verifyOutputParent(target);
    await rename(staging, target.resolved);
    return report;
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => undefined);
    if (error instanceof R18DiscoveryPlanError) throw error;
    throw new R18DiscoveryPlanError("R18_DISCOVERY_INTERNAL_ERROR");
  }
}

function readQuerySet(moduleRoot) {
  let bytes;
  let value;
  try {
    bytes = readFileSync(path.join(moduleRoot, ...QUERY_SET_PATH.split("/")));
    value = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_QUERY_SET_INVALID");
  }
  const topKeys = Object.keys(value).sort().join(",");
  if (topKeys !== "format,formatVersion,lanes,publicDocuments,requestBudget,responseMaxBytes,timeoutMs,totalResponseMaxBytes" || value.format !== "matrix-oasis.v2-discovery-query-set" || value.formatVersion !== "0.1.0") {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_QUERY_SET_INVALID");
  }
  if (
    !Array.isArray(value.lanes) ||
    value.lanes.length !== V2_LANES.length ||
    value.lanes.some((lane, index) =>
      !lane ||
      typeof lane !== "object" ||
      Array.isArray(lane) ||
      Object.keys(lane).sort().join(",") !== "githubQuery,id,seedRepositories" ||
      lane.id !== V2_LANES[index] ||
      typeof lane.githubQuery !== "string" ||
      lane.githubQuery.length < 1 ||
      lane.githubQuery.length > 256 ||
      /[\r\n]|:\/\//u.test(lane.githubQuery) ||
      !Array.isArray(lane.seedRepositories) ||
      lane.seedRepositories.some((repository) => typeof repository !== "string" || !SAFE_REPOSITORY.test(repository)) ||
      new Set(lane.seedRepositories.map((repository) => repository.toLowerCase())).size !== lane.seedRepositories.length
    )
  ) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_LANES_INVALID");
  }
  if (
    !Array.isArray(value.publicDocuments) ||
    value.publicDocuments.length < 4 ||
    value.publicDocuments.some((item) =>
      !item ||
      typeof item !== "object" ||
      Array.isArray(item) ||
      Object.keys(item).sort().join(",") !== "host,id,path" ||
      typeof item.id !== "string" ||
      !SAFE_DISCOVERY_ID.test(item.id) ||
      !EXPECTED_HOSTS.includes(item.host) ||
      typeof item.path !== "string" ||
      !item.path.startsWith("/") ||
      item.path.startsWith("//") ||
      item.path.includes("..") ||
      /[?#\r\n]/u.test(item.path)
    ) ||
    new Set(value.publicDocuments.map((item) => item.id)).size !== value.publicDocuments.length
  ) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_DOCUMENTS_INVALID");
  }
  const hosts = Object.keys(value.requestBudget).sort();
  if (hosts.join(",") !== [...EXPECTED_HOSTS].sort().join(",") || hosts.some((host) => !Number.isSafeInteger(value.requestBudget[host]) || value.requestBudget[host] < 1)) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_BUDGET_INVALID");
  }
  if (value.responseMaxBytes !== 2_097_152 || value.totalResponseMaxBytes !== 134_217_728 || value.timeoutMs !== 15_000) {
    throw new R18DiscoveryPlanError("R18_DISCOVERY_LIMIT_INVALID");
  }
  return { bytes, value };
}

export function createR18DiscoveryPlan({ moduleRoot }) {
  const { bytes, value } = readQuerySet(moduleRoot);
  const requests = Object.entries(value.requestBudget)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([host, maximum]) => ({ host, maximum }));
  return Object.freeze({
    format: "matrix-oasis.r18-discovery-plan",
    formatVersion: "0.1.0",
    querySetSha256: sha256(bytes),
    laneQueries: value.lanes.length,
    seedRepositories: new Set(value.lanes.flatMap((lane) => lane.seedRepositories)).size,
    publicDocuments: value.publicDocuments.length,
    requests,
    requestMaximum: requests.reduce((sum, item) => sum + item.maximum, 0),
    responseMaxBytes: value.responseMaxBytes,
    totalResponseMaxBytes: value.totalResponseMaxBytes,
    timeoutMs: value.timeoutMs,
    network: "public-unauthenticated-only",
    credentials: "none",
    login: false,
    commercialApiCalls: false,
    supplierCalls: false,
  });
}
