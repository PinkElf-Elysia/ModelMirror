import type { GraphPatchOperationV1 } from "./metaAuthoring";

type Json = Record<string, unknown>;
const record = (value: unknown): value is Json => value !== null && typeof value === "object" && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === "string");
const digest = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export interface RecipeRepairContract {
  protocol_version: "recipe_edits_v1";
  edit_contract: {
    operations: Record<string, { required_fields: string[]; optional_fields: string[] }>;
    max_operations: number; max_bytes: number;
    update_node_refs: string[]; cloneable_agent_refs: string[];
  };
  operations_schema: Json;
}

export function normalizeRecipeRepairContract(value: unknown): RecipeRepairContract | undefined {
  if (!record(value) || value.protocol_version !== "recipe_edits_v1" || !record(value.edit_contract) || !record(value.operations_schema)) return;
  const contract = value.edit_contract;
  if (!record(contract.operations) || contract.max_operations !== 16 || contract.max_bytes !== 65536 ||
      !strings(contract.update_node_refs) || !strings(contract.cloneable_agent_refs)) return;
  const names = Object.keys(contract.operations);
  if (names.length !== 4 || !["update_node", "clone_agent", "replace_control_flow", "set_final_output"].every(op => names.includes(op))) return;
  const operations: RecipeRepairContract["edit_contract"]["operations"] = {};
  for (const [op, fields] of Object.entries(contract.operations)) {
    if (!record(fields) || !strings(fields.required_fields) || !strings(fields.optional_fields) || !fields.required_fields.includes("op")) return;
    operations[op] = { required_fields: fields.required_fields, optional_fields: fields.optional_fields };
  }
  return { protocol_version: "recipe_edits_v1", operations_schema: value.operations_schema,
    edit_contract: { operations, max_operations: 16, max_bytes: 65536,
      update_node_refs: contract.update_node_refs, cloneable_agent_refs: contract.cloneable_agent_refs } };
}

export function recipeRepairOperations(value: unknown, contract: RecipeRepairContract): GraphPatchOperationV1[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > contract.edit_contract.max_operations ||
      new TextEncoder().encode(JSON.stringify({ operations: value })).length > contract.edit_contract.max_bytes) return null;
  for (const item of value) {
    if (!record(item) || typeof item.op !== "string" || !Object.hasOwn(contract.edit_contract.operations, item.op)) return null;
    const fields = contract.edit_contract.operations[item.op];
    if (fields.required_fields.some(key => !Object.hasOwn(item, key)) ||
        Object.keys(item).some(key => !fields.required_fields.includes(key) && !fields.optional_fields.includes(key))) return null;
  }
  // Nested config, port types and authority are checked by the same server kernel.
  return value as GraphPatchOperationV1[];
}

export function normalizeRecipeRepairPatch(value: unknown, contract: RecipeRepairContract) {
  if (!record(value) || value.protocol_version !== contract.protocol_version ||
      Object.keys(value).some(key => !["protocol_version", "proposal_revision", "expected_graph_checksum", "expected_candidate_checksum", "operations"].includes(key)) ||
      !Number.isInteger(value.proposal_revision) || Number(value.proposal_revision) < 1 ||
      !digest(value.expected_graph_checksum) || !digest(value.expected_candidate_checksum)) return null;
  const operations = recipeRepairOperations(value.operations, contract);
  return operations ? { protocol_version: contract.protocol_version, proposal_revision: Number(value.proposal_revision),
    expected_graph_checksum: String(value.expected_graph_checksum), expected_candidate_checksum: String(value.expected_candidate_checksum), operations } : null;
}
