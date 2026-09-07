import { openFileSessionStore, sha256 } from "../runtime/node.mjs";
import { computeProposalSha256, validateModelProposal, validateRuntimeSession } from "../runtime/index.mjs";
import { baseRuntimeFixture } from "./runtime-fixtures.mjs";

const rootDirectory = process.argv[2];
const fixture = baseRuntimeFixture(), { cardPackage, playerSetup, session } = fixture;
const proposal = { narrative: "The room is quiet.", suggestedActions: [], informationModules: [], stateProposals: [{ fieldRef: "state.scene-note", proposedValue: "quiet", rationale: "Observed" }], uncertainties: [] };
const input = { kind: "action", text: "wait" };
const exchange = validateModelProposal(proposal, "exchange.1", input, cardPackage).value;
const receipt = (revision) => ({ format: "modelmirror.ai-rpg.generation-receipt", formatVersion: "0.1.0", sessionId: session.sessionId, cardPackageSha256: session.resources.cardPackage.sha256, playerSetupSha256: session.resources.playerSetup.sha256, generationId: "generation.1", exchangeId: "exchange.1", revision, evidenceKind: "mock", status: "succeeded", outcome: "completed", requestedModel: "provider/model", observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, outputSha256: computeProposalSha256(proposal, sha256).value, usage: { input: null, output: null, total: null }, costUsd: null });
const options = { cardPackage, playerSetup };
const opened = await openFileSessionStore({ rootDirectory }); if (!opened.valid) process.exit(11); const store = opened.value;
if (!(await store.write(session, { ...options, expectedRevision: null })).valid) process.exit(12);
session.revision = 1; session.generations.push({ generationId: "generation.1", exchangeId: "exchange.1", inputSha256: "a".repeat(64), modelId: "provider/model", evidenceKind: "mock", status: "active", requestRevision: 0, startedRevision: 1, draftText: "" });
if (!(await store.write(session, { ...options, expectedRevision: 0 })).valid) process.exit(13);
session.revision = 2; session.pending = { generationId: "generation.1", exchangeId: "exchange.1" }; Object.assign(session.generations[0], { status: "pending", finishedRevision: 2, exchange, receipt: receipt(2), draftText: "" });
{ const report = await store.write(session, { ...options, expectedRevision: 1 }); if (!report.valid) { console.error(validateRuntimeSession(session, cardPackage, playerSetup, sha256).diagnostics.map((entry) => entry.code).join(",") || report.diagnostics[0]?.code || "CHILD_PENDING_WRITE_FAILED"); process.exit(14); } }
session.revision = 3; session.pending = null; Object.assign(session.generations[0], { status: "committed", resolvedRevision: 3 }); session.turns.push({ generationId: "generation.1", exchange: structuredClone(exchange), committedRevision: 3, acceptedStateFields: ["state.scene-note"] }); session.state.find((entry) => entry.fieldRef === "state.scene-note").value = "quiet";
if (!(await store.write(session, { ...options, expectedRevision: 2 })).valid) process.exit(15);
session.revision = 4; session.generations.push({ generationId: "generation.2", exchangeId: "exchange.2", inputSha256: "b".repeat(64), modelId: "provider/model", evidenceKind: "mock", status: "active", requestRevision: 3, startedRevision: 4, draftText: "partial draft" });
if (!(await store.write(session, { ...options, expectedRevision: 3 })).valid) process.exit(16);
// Deliberately exit without close so the parent must prove this PID dead and archive the owner lock.
