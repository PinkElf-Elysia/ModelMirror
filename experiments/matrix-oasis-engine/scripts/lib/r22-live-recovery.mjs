import { createHash } from "node:crypto";
import path from "node:path";
import { lstat, open, readdir, realpath } from "node:fs/promises";
import { createNpcAuthoritySession, restoreNpcAuthoritySession, verifyNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { buildR20BridgeArtifacts } from "./r20-cli-core.mjs";
import { closeR22CallStore, inspectR22CallStore, openR22CallStore, readR22ActiveCallArtifacts, readR22FinalizedTurnReceipt } from "./r22-call-store.mjs";
import { rebuildR22RecoveredBehavior } from "./r22-recovered-behavior.mjs";

const CODE = "R22_LIVE_RECOVERY_INVALID";
const SHA = /^sha256:[0-9a-f]{64}$/u;
const RUN = /^[0-9a-f]{64}$/u;
function fail() { throw new Error(CODE); }
function sha(text) { return `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`; }
function canonical(text) { let value; try { value = JSON.parse(text); } catch { fail(); } if (canonicalizeJsonValue(value) !== text) fail(); return value; }
function freeze(value) { if (!value || typeof value !== "object" || Object.isFrozen(value)) return value; for (const child of Object.values(value)) freeze(child); return Object.freeze(value); }
function point(authority) { const ledger = canonical(authority.canonicalWorldEventLedgerJson); return { revision: ledger.revision, headSha256: ledger.headSha256, runtimeSnapshotSha256: sha(canonicalizeJsonValue(authority.runtimeSnapshot)) }; }

function matchesActiveBudget(entry, artifacts) {
  const { active, dispatchRecordJson, validatedProposalRecordJson, turnReceiptJson } = artifacts;
  const hasDispatch = dispatchRecordJson !== null, validated = validatedProposalRecordJson === null ? null : canonical(validatedProposalRecordJson);
  // A durable record can be one step ahead of its checkpoint. Only admit the
  // exact write windows of reserve, validation and receipt publication.
  const allowed = [];
  if (active.stage === "planned" && !hasDispatch && validated === null) allowed.push(null, ["reserved", 0]);
  if (["reserved", "dispatching"].includes(active.stage)) allowed.push(["reserved", 0]);
  if (validated !== null && hasDispatch && ["dispatching", "validated", "queued_for_r20"].includes(active.stage)) {
    allowed.push(["charged", validated.actualMicrousd]);
  }
  if (turnReceiptJson !== null) {
    const receipt = canonical(turnReceiptJson);
    if (receipt.budget.reservedMicrousd === 0) allowed.push(null);
    else allowed.push([receipt.requestCount === 1 ? "charged" : "released", receipt.budget.actualMicrousd]);
  }
  return allowed.some((state) => state === null ? entry === null : entry !== null && entry.state === state[0] && entry.chargedMicrousd === state[1]);
}

async function stableFile(file, maximum = 32 * 1024 * 1024) {
  let handle;
  try {
    handle = await open(file, "r"); const before = await handle.stat({ bigint: true }), linked = await lstat(file, { bigint: true });
    if (!before.isFile() || linked.isSymbolicLink() || before.dev !== linked.dev || before.ino !== linked.ino || before.size !== linked.size ||
        before.size < 1n || before.size > BigInt(maximum) || path.resolve(await realpath(file)) !== path.resolve(file)) fail();
    const bytes = await handle.readFile(), text = new TextDecoder("utf-8", { fatal: true }).decode(bytes), after = await handle.stat({ bigint: true });
    const linkedAfter = await lstat(file, { bigint: true });
    if (after.dev !== before.dev || after.ino !== before.ino || after.size !== before.size || after.mtimeNs !== before.mtimeNs ||
        after.ctimeNs !== before.ctimeNs || linkedAfter.isSymbolicLink() || linkedAfter.dev !== before.dev || linkedAfter.ino !== before.ino ||
        path.resolve(await realpath(file)) !== path.resolve(file) || bytes.byteLength !== Number(before.size)) fail();
    return Object.freeze({ path: path.resolve(file), text, dev: String(before.dev), ino: String(before.ino), size: String(before.size), mtimeNs: String(before.mtimeNs), ctimeNs: String(before.ctimeNs) });
  } catch (error) { if (error?.message === CODE) throw error; fail(); } finally { await handle?.close().catch(() => {}); }
}
async function stableDirectory(directory) {
  try { const linked = await lstat(directory, { bigint: true }); if (!linked.isDirectory() || linked.isSymbolicLink() || path.resolve(await realpath(directory)) !== path.resolve(directory)) fail(); const names = (await readdir(directory)).sort(), after = await lstat(directory, { bigint: true }); if (after.isSymbolicLink() || after.dev !== linked.dev || after.ino !== linked.ino || after.mtimeNs !== linked.mtimeNs || after.ctimeNs !== linked.ctimeNs || path.resolve(await realpath(directory)) !== path.resolve(directory)) fail(); return Object.freeze({ path: path.resolve(directory), names: Object.freeze(names), dev: String(linked.dev), ino: String(linked.ino), mtimeNs: String(linked.mtimeNs), ctimeNs: String(linked.ctimeNs) }); }
  catch (error) { if (error?.message === CODE) throw error; fail(); }
}

export async function loadR22LiveRecoveryHistory(input) {
  try {
    const { npcRunRoot, cognitionRunRoot, temporaryRoot, source, manifestFor, hostRunId, cognitionPolicyJson, preparedBehavior, initialBehaviorState, recovery } = input ?? {};
    if (![npcRunRoot, cognitionRunRoot, temporaryRoot, hostRunId, cognitionPolicyJson].every((v) => typeof v === "string") ||
        typeof manifestFor !== "function" || !source || !preparedBehavior || !initialBehaviorState || !recovery ||
        ![source.runtimeGamePackJson, source.runtimeReceiptJson, source.authorityPolicyJson, source.behaviorPolicyJson, source.npcEntityBindingJson].every((v) => typeof v === "string")) fail();
    const temp = path.resolve(temporaryRoot), npc = path.resolve(npcRunRoot), cognition = path.resolve(cognitionRunRoot);
    if (path.dirname(npc) !== temp || path.dirname(cognition) !== temp || !npc.endsWith("-npc") || !cognition.endsWith("-cognition")) fail();
    const records = [], directories = [], remember = async (file, maximum) => { const record = await stableFile(file, maximum); records.push(record); return record.text; };
    const rememberDirectory = async (directory) => { const record = await stableDirectory(directory); directories.push(record); return record.names; };
    const timelineIds = await rememberDirectory(path.join(npc, "timelines"));
    if (!timelineIds.length || timelineIds.some((id) => !RUN.test(id))) fail();
    const histories = [], seenTimelineIds = new Set();
    for (const manifestId of timelineIds) {
      const root = path.join(npc, "timelines", manifestId); await rememberDirectory(root); const manifestJson = await remember(path.join(root, "authority-manifest.json"));
      if (sha(manifestJson).slice(7) !== manifestId) fail(); const manifest = canonical(manifestJson);
      if (seenTimelineIds.has(manifest.timelineId) || manifestJson !== manifestFor(manifest.timelineId)) fail(); seenTimelineIds.add(manifest.timelineId);
      if (await remember(path.join(root, "behavior-policy.json")) !== source.behaviorPolicyJson ||
          await remember(path.join(root, "entity-bindings.json")) !== source.npcEntityBindingJson) fail();
      const isActive = path.resolve(recovery.timelineRoot ?? "") === path.resolve(root);
      if (isActive) {
        if (manifestJson !== recovery.authorityManifestJson || recovery.behaviorPolicyJson !== source.behaviorPolicyJson ||
            recovery.entityBindingJson !== source.npcEntityBindingJson) fail();
        const rebuilt = await rebuildR22RecoveredBehavior({ source, recovery, preparedBehavior, initialBehaviorState });
        const artifacts = buildR20BridgeArtifacts({ snapshot: { authority: rebuilt.authority, behaviorState: rebuilt.behaviorState, commands: rebuilt.commands },
          behaviorPolicyJson: source.behaviorPolicyJson, entityBindingJson: source.npcEntityBindingJson });
        if (artifacts.canonicalBehaviorTraceJson !== canonicalizeJsonValue(recovery.behaviorTrace)) fail();
      } else {
        const sealedJson = await remember(path.join(root, "sealed-checkpoint.json")), sealed = canonical(sealedJson);
        const ledgerJson = await remember(path.join(root, "world-event-ledger.json")), traceJson = await remember(path.join(root, "behavior-trace.json"));
        const sealedKeys = ["format", "formatVersion", "timelineId", "manifestSha256", "revision", "headSha256", "commit"].sort();
        if (Object.keys(sealed).sort().join("\0") !== sealedKeys.join("\0") || sealed.format !== "matrix-oasis.npc-authority-sealed-checkpoint" || sealed.formatVersion !== "0.1.0") fail();
        const restored = await restoreNpcAuthoritySession({ runtimeGamePackJson: source.runtimeGamePackJson, runtimeReceiptJson: source.runtimeReceiptJson,
          policyJson: source.authorityPolicyJson, worldEventLedgerJson: ledgerJson });
        const verified = restored?.ok ? verifyNpcAuthoritySession(restored.session) : null;
        if (!verified?.ok || verified.canonicalWorldEventLedgerJson !== ledgerJson) fail();
        const trace = canonical(traceJson), rebuilt = await rebuildR22RecoveredBehavior({ source,
          recovery: { canonicalWorldEventLedgerJson: ledgerJson, behaviorTrace: trace }, preparedBehavior, initialBehaviorState });
        const artifacts = buildR20BridgeArtifacts({ snapshot: { authority: rebuilt.authority, behaviorState: rebuilt.behaviorState, commands: rebuilt.commands },
          behaviorPolicyJson: source.behaviorPolicyJson, entityBindingJson: source.npcEntityBindingJson });
        const ledger = canonical(ledgerJson), commit = `${String(ledger.revision).padStart(5, "0")}-${ledger.headSha256 === null ? "genesis" : ledger.headSha256.slice(7)}`;
        if (artifacts.canonicalBehaviorTraceJson !== traceJson || sealed.timelineId !== manifest.timelineId || sealed.manifestSha256 !== `sha256:${manifestId}` ||
            sealed.revision !== ledger.revision || sealed.headSha256 !== ledger.headSha256 || sealed.commit !== commit) fail();
        const commitsRoot = path.join(root, "commits"), commitNames = await rememberDirectory(commitsRoot);
        if (commitNames.length !== ledger.revision + 1 || commitNames.at(-1) !== sealed.commit) fail();
        for (let revision = 0; revision < commitNames.length; revision += 1) {
          const headSha256 = revision === 0 ? null : ledger.entries[revision - 1].entrySha256;
          const expectedCommit = `${String(revision).padStart(5, "0")}-${headSha256 === null ? "genesis" : headSha256.slice(7)}`;
          if (commitNames[revision] !== expectedCommit) fail();
          const commitRoot = path.join(commitsRoot, commitNames[revision]);
          const names = await rememberDirectory(commitRoot); if (names.join("\0") !== "behavior-trace.json\0world-event-ledger.json") fail();
          const commitLedger = await remember(path.join(commitRoot, "world-event-ledger.json")), commitTrace = await remember(path.join(commitRoot, "behavior-trace.json"));
          const restoredCommit = await restoreNpcAuthoritySession({ runtimeGamePackJson: source.runtimeGamePackJson, runtimeReceiptJson: source.runtimeReceiptJson, policyJson: source.authorityPolicyJson, worldEventLedgerJson: commitLedger });
          const expectedLedger = canonicalizeJsonValue({ ...ledger, revision, headSha256, entries: ledger.entries.slice(0, revision) });
          if (!restoredCommit?.ok || !verifyNpcAuthoritySession(restoredCommit.session)?.ok || commitLedger !== expectedLedger) fail();
          const prefixArtifacts = buildR20BridgeArtifacts({ snapshot: { authority: restoredCommit, commands: trace.commands.slice(0, revision) },
            behaviorPolicyJson: source.behaviorPolicyJson, entityBindingJson: source.npcEntityBindingJson });
          if (commitTrace !== prefixArtifacts.canonicalBehaviorTraceJson) fail();
          if (revision === ledger.revision && (commitLedger !== ledgerJson || commitTrace !== traceJson)) fail();
        }
      }
      histories.push({ manifestId, timelineId: manifest.timelineId, manifestJson, active: isActive });
    }
    if (histories.filter((item) => item.active).length !== 1) fail();
    const budgetJson = await remember(path.join(cognition, "host-budget.json"), 1024 * 1024), budget = canonical(budgetJson);
    if (budget.hostRunId !== hostRunId || !Array.isArray(budget.entries)) fail();
    const receipts = new Map(), activeBudgets = new Map();
    const expectedCognitionTimelines = histories.map((item) => sha(item.manifestJson).slice(7)).sort();
    const actualCognitionTimelines = await rememberDirectory(path.join(cognition, "timelines"));
    if (actualCognitionTimelines.join("\0") !== expectedCognitionTimelines.join("\0")) fail();
    for (const history of histories) {
      const expectedManifest = history.manifestJson, authoritySessionSha256 = sha(expectedManifest);
      const genesis = await createNpcAuthoritySession({ runtimeGamePackJson: source.runtimeGamePackJson, runtimeReceiptJson: source.runtimeReceiptJson,
        policyJson: source.authorityPolicyJson, timelineId: history.timelineId,
        stepLimit: canonical(history.active ? recovery.canonicalWorldEventLedgerJson : await stableFile(path.join(npc, "timelines", history.manifestId, "world-event-ledger.json")).then((r) => r.text)).timeline.stepLimit });
      if (!genesis?.ok) fail();
      const checkpointPath = path.join(cognition, "timelines", authoritySessionSha256.slice(7), "cognition-checkpoint.json");
      await remember(checkpointPath, 1024 * 1024);
      let store;
      try {
        store = await openR22CallStore({ temporaryRoot: temp, cognitionRunRoot: cognition, hostRunId, timelineId: history.timelineId,
          authoritySessionSha256, cognitionPolicySha256: sha(cognitionPolicyJson), initialLedgerPoint: point(genesis) });
        const inspected = inspectR22CallStore(store), checkpoint = inspected.checkpoint;
        if (!history.active && checkpoint.active !== null) fail();
        if (history.active && checkpoint.active !== null) {
          const activeArtifacts = await readR22ActiveCallArtifacts(store);
          if (!activeArtifacts || activeArtifacts.active.callPlanSha256 !== checkpoint.active.callPlanSha256) fail();
          activeBudgets.set(`${authoritySessionSha256}:${checkpoint.active.callPlanSha256}`, activeArtifacts);
        }
        for (const item of checkpoint.finalized) {
          const found = await readR22FinalizedTurnReceipt(store, { timelineId: history.timelineId, turnId: item.turnId, callPlanSha256: item.callPlanSha256 });
          if (!found) fail(); const receipt = canonical(found.turnReceiptJson), key = `${authoritySessionSha256}:${item.callPlanSha256}`;
          if (receipts.has(key) || receipt.requestCount !== item.requestCount || receipt.budget?.actualMicrousd !== item.actualMicrousd) fail();
          receipts.set(key, { requestCount: item.requestCount, chargedMicrousd: item.actualMicrousd, reservedMicrousd: receipt.budget.reservedMicrousd });
          const turnRoot = path.join(cognition, "timelines", authoritySessionSha256.slice(7), "turns",
            `${String(item.sequence).padStart(6, "0")}-${item.callPlanSha256.slice(7)}`);
          const turnNames = await rememberDirectory(turnRoot);
          for (const name of turnNames) await remember(path.join(turnRoot, name));
          if (receipt.mappedIntentSha256 !== null) {
            const ledgerJson = history.active ? recovery.canonicalWorldEventLedgerJson : (await stableFile(path.join(npc, "timelines", history.manifestId, "world-event-ledger.json"))).text;
            const ledger = canonical(ledgerJson), matches = ledger.entries.filter((entry) => sha(canonicalizeJsonValue(entry.intent)) === receipt.mappedIntentSha256);
            if (receipt.adjudicationResultSha256 === null) { if (matches.length !== 0) fail(); continue; }
            if (matches.length !== 1) fail(); const entry = matches[0], before = receipt.ledger?.before, after = receipt.ledger?.after;
            const resultJson = canonicalizeJsonValue({ format: "matrix-oasis.npc-adjudication-result", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
              timelineId: ledger.timeline.id, intentId: entry.intent.id, replayed: false, revision: entry.revision, headSha256: entry.entrySha256,
              decision: entry.decision, beforeSnapshotSha256: entry.beforeSnapshotSha256, afterSnapshotSha256: entry.afterSnapshotSha256, transition: entry.transition });
            if (!before || !after || before.revision !== entry.revision - 1 || before.headSha256 !== entry.previousEntrySha256 || before.runtimeSnapshotSha256 !== entry.beforeSnapshotSha256 ||
                after.revision !== entry.revision || after.headSha256 !== entry.entrySha256 || after.runtimeSnapshotSha256 !== entry.afterSnapshotSha256 || sha(resultJson) !== receipt.adjudicationResultSha256) fail();
          }
        }
        const expectedTurns = checkpoint.finalized.map((item) => `${String(item.sequence).padStart(6, "0")}-${item.callPlanSha256.slice(7)}`);
        if (checkpoint.active) expectedTurns.push(`${String(checkpoint.active.sequence).padStart(6, "0")}-${checkpoint.active.callPlanSha256.slice(7)}`);
        const turnsRoot = path.join(cognition, "timelines", authoritySessionSha256.slice(7), "turns");
        if ((await rememberDirectory(turnsRoot)).join("\0") !== expectedTurns.sort().join("\0")) fail();
        if (checkpoint.active) {
          const activeRoot = path.join(turnsRoot, `${String(checkpoint.active.sequence).padStart(6, "0")}-${checkpoint.active.callPlanSha256.slice(7)}`);
          const activeNames = await rememberDirectory(activeRoot), stages = activeNames.filter((name) => /^\.s-[A-Za-z0-9]{6}$/u.test(name));
          const ordinary = new Set(["call-plan.json", "dispatch-record.json", "validated-proposal-record.json", "display-ack-record.json", "turn-receipt.json"]);
          if (stages.length > 1 || activeNames.some((name) => !ordinary.has(name) && !stages.includes(name))) fail();
          for (const name of activeNames.filter((name) => ordinary.has(name))) await remember(path.join(activeRoot, name));
          if (stages.length === 1) {
            const stageRoot = path.join(activeRoot, stages[0]), stagedNames = await rememberDirectory(stageRoot);
            if (!(stagedNames.join("\0") === "turn-receipt.json" || stagedNames.length === 0 && ordinary.has("turn-receipt.json") && activeNames.includes("turn-receipt.json"))) fail();
            if (stagedNames.length === 1) await remember(path.join(stageRoot, "turn-receipt.json"));
          }
        }
      } finally { if (store) await closeR22CallStore(store); }
    }
    for (const entry of budget.entries) {
      const key = `${entry.authoritySessionSha256}:${entry.callPlanSha256}`, receipt = receipts.get(key);
      if (!receipt && !activeBudgets.has(key)) fail();
      if (receipt && (entry.chargedMicrousd !== receipt.chargedMicrousd || entry.state !== (receipt.requestCount === 1 ? "charged" : "released"))) fail();
    }
    for (const [key, receipt] of receipts) if (receipt.reservedMicrousd > 0 && !budget.entries.some((entry) => `${entry.authoritySessionSha256}:${entry.callPlanSha256}` === key)) fail();
    for (const [key, artifacts] of activeBudgets) {
      const entry = budget.entries.find((item) => `${item.authoritySessionSha256}:${item.callPlanSha256}` === key) ?? null;
      if (!matchesActiveBudget(entry, artifacts)) fail();
    }
    const revalidate = async () => {
      try { for (const record of records) { const next = await stableFile(record.path); if (next.text !== record.text || next.dev !== record.dev || next.ino !== record.ino || next.size !== record.size || next.mtimeNs !== record.mtimeNs || next.ctimeNs !== record.ctimeNs) fail(); } for (const record of directories) { const next = await stableDirectory(record.path); if (next.names.join("\0") !== record.names.join("\0") || next.dev !== record.dev || next.ino !== record.ino || next.mtimeNs !== record.mtimeNs || next.ctimeNs !== record.ctimeNs) fail(); } return true; }
      catch { fail(); }
    };
    return freeze({ auditedTimelineIds: histories.map((item) => item.timelineId).sort(), revalidate });
  } catch (error) { if (error?.message === CODE) throw error; fail(); }
}
