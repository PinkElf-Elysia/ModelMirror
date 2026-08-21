import {
  type XpertValidationIssue,
  type XpertValidationResult,
  type XpertWorkflowDefinition,
} from "../types/xpert";
import { type WorkflowNodeKind } from "../types/workflow";
import { INDEPENDENT_DEPLOYMENT_NODE_KINDS } from "../components/workflow/workflowXpertConversion";
import { analyzeWorkflowVariables } from "../components/workflow/workflowVariables";

const DERIVED_ENTRY_ISSUE_CODES = new Set([
  "xpert_input_contract",
  "missing_input_node",
]);

/** Collapse the three symptoms of an unconverted callable entry into one action. */
export function consolidateXpertValidation(
  result: XpertValidationResult,
  workflow: XpertWorkflowDefinition,
  inputVariable: string,
): XpertValidationResult {
  const inputNodes = workflow.nodes.filter(
    (node) => (node.data.kind || node.type) === "input",
  );
  const callEntries = workflow.nodes.filter(
    (node) => (node.data.kind || node.type) === "workflow_call_entry",
  );
  const otherDeploymentNodes = workflow.nodes.filter((node) => {
    const kind = (node.data.kind || node.type) as WorkflowNodeKind;
    return (
      kind !== "workflow_call_entry" &&
      INDEPENDENT_DEPLOYMENT_NODE_KINDS.has(kind)
    );
  });
  if (
    inputNodes.length !== 0 ||
    callEntries.length !== 1 ||
    otherDeploymentNodes.length > 0
  ) {
    return result;
  }

  const callEventVariable = String(
    callEntries[0].data.eventVariable || "call_event",
  ).trim();
  const variableInventory = analyzeWorkflowVariables(
    workflow.nodes.map((node) => ({
      ...node,
      type: "workflowNode" as const,
      position: node.position ?? { x: 0, y: 0 },
    })),
    workflow.edges,
    null,
    workflow.variables ?? [],
  );
  if (
    (variableInventory.find((variable) => variable.name === callEventVariable)
      ?.references.length ?? 0) > 0 ||
    (workflow.variables ?? []).some(
      (variable) =>
        variable.kind === "input" &&
        variable.name !== inputVariable &&
        variable.defaultValue === undefined,
    )
  ) {
    return result;
  }

  const callEntryId = callEntries[0].id;
  const inputReference = `variable '${inputVariable}'`;
  let replaced = false;
  const retained: XpertValidationIssue[] = [];
  for (const issue of result.issues) {
    const isInputContract = DERIVED_ENTRY_ISSUE_CODES.has(issue.code);
    const isEntryPolicy =
      issue.code === "deployment_node_xpert_forbidden" &&
      issue.node_id === callEntryId;
    const isUndefinedTaskInput =
      issue.code === "missing_workflow_agent_template_variable" &&
      issue.message.includes(inputReference);
    if (isInputContract || isEntryPolicy || isUndefinedTaskInput) {
      replaced = true;
      continue;
    }
    retained.push(issue);
  }
  if (!replaced) return result;

  return {
    ...result,
    valid: false,
    issues: [
      {
        code: "xpert_entry_conversion_required",
        message:
          "该草稿仍使用子流程入口。请在画布中将它转换为智能体输入，然后保存草稿。",
        severity: "error",
        node_id: callEntryId,
      },
      ...retained,
    ],
  };
}
