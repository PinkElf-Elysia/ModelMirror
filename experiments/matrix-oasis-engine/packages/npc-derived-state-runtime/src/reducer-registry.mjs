export const NPC_DERIVED_STATE_REDUCERS = Object.freeze({
  memory: Object.freeze({
    id: "npc-memory-actor-self-actions",
    version: "0.1.0",
    sourceSha256: "sha256:f6fd0c6e0e752f81a8710d815ae8f1e2d36b836e7bdb20e908f44421c73199ab",
  }),
  relationship: Object.freeze({
    id: "npc-relationship-explicit-first-accepted",
    version: "0.1.0",
    sourceSha256: "sha256:5906cd013b33ecc6e4fa28497d76de99261a2c765a3183a6225ab972cde49a81",
  }),
});

export const NPC_DERIVED_STATE_PROFILE = Object.freeze({
  timelineMode: "single",
  authorityMode: "runtime-and-ledger-only",
  personaMode: "trusted-static-seed",
  memoryScope: "actor-self-accepted-actions",
  relationshipScope: "accepted-explicit-policy-rules",
  deletionMode: "whole-derived-state",
  selectiveForgetting: false,
  externalModelCalls: false,
  semanticRetrieval: false,
});
