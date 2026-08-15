import type {
  AgencyPlanTask,
  AgencyWorkflow,
} from "./AgencyExpertTeamTypes";

export function syncWorkflowToPlan(
  workflow: AgencyWorkflow,
  tasks: AgencyPlanTask[],
): AgencyWorkflow {
  const taskIds = new Set(tasks.map((task) => task.task_id));
  const taskNodes = new Map<string, AgencyWorkflow["nodes"][number]>();
  workflow.nodes.forEach((node) => {
    const plannerTaskIds = node.data.plannerTaskIds;
    if (
      Array.isArray(plannerTaskIds) &&
      plannerTaskIds.length === 1 &&
      typeof plannerTaskIds[0] === "string"
    ) {
      taskNodes.set(plannerTaskIds[0], node);
    }
  });
  tasks.forEach((task) => {
    if (taskNodes.has(task.task_id)) return;
    const legacyNode = workflow.nodes.find(
      (node) => node.id === `agent_${task.task_id}`,
    );
    if (legacyNode) taskNodes.set(task.task_id, legacyNode);
  });
  const addedNodes = tasks
    .filter((task) => !taskNodes.has(task.task_id))
    .map((task, index) => {
      const interaction = task.task_type === "human_input" || task.task_type === "approval";
      const id = interaction ? `interaction_${task.task_id}` : `agent_${task.task_id}`;
      const node: AgencyWorkflow["nodes"][number] = {
        id,
        type: interaction ? "human_intervention" : "workflow_agent",
        position: { x: 420, y: 180 + index * 180 },
        data: interaction
          ? {
              kind: "human_intervention",
              title: task.title,
              description: task.objective,
              prompt: task.interaction_prompt || task.objective,
              interactionMode: task.task_type === "approval" ? "approval" : "input",
              outputVariable: task.output_variable || `${task.task_id}_output`,
              plannerRef: `hitl_${task.task_id}`,
              plannerTaskIds: [task.task_id],
            }
          : {
              kind: "workflow_agent",
              title: task.title,
              description: task.objective,
              taskInput: task.objective,
              outputVariable: task.output_variable || `${task.task_id}_output`,
              plannerRef: `agent_${task.task_id}`,
              plannerTaskIds: [task.task_id],
            },
      };
      taskNodes.set(task.task_id, node);
      return node;
    });
  const nodeId = (taskId: string) =>
    taskNodes.get(taskId)?.id || `agent_${taskId}`;
  const outputVariables = new Map(
    tasks.map((task) => {
      const node = taskNodes.get(task.task_id);
      return [
        task.task_id,
        String(node?.data.outputVariable || `${task.task_id}_output`),
      ] as const;
    }),
  );
  const nodes = [...workflow.nodes, ...addedNodes]
    .filter((node) => {
      const plannerTaskIds = node.data.plannerTaskIds;
      return !(
        Array.isArray(plannerTaskIds)
        && plannerTaskIds.length === 1
        && typeof plannerTaskIds[0] === "string"
        && !taskIds.has(plannerTaskIds[0])
      );
    })
    .map((node) => {
    const task = tasks.find(
      (item) => taskNodes.get(item.task_id)?.id === node.id,
    );
    if (!task) return node;
    if (task.task_type === "human_input" || task.task_type === "approval") {
      return {
        ...node,
        type: "human_intervention",
        data: {
          ...node.data,
          kind: "human_intervention",
          title: task.title,
          description: task.objective,
          prompt: task.interaction_prompt || task.objective,
          interactionMode: task.task_type === "approval" ? "approval" : "input",
          outputVariable: task.output_variable || `${task.task_id}_output`,
          plannerRef: `hitl_${task.task_id}`,
          plannerTaskIds: [task.task_id],
        },
      };
    }
    const dependencyText = task.depends_on
      .map((dependency) => {
        const variable = outputVariables.get(dependency) || `${dependency}_output`;
        return `${dependency}: {{${variable}}}`;
      })
      .join("\n");
    return {
      ...node,
      data: {
        ...node.data,
        title: task.title,
        description: task.objective,
        taskInput: dependencyText
          ? `${task.objective}\n\n依赖结果：\n${dependencyText}`
          : `${task.objective}\n\n用户任务：\n{{user_input}}`,
        acceptanceCriteria: task.acceptance,
        methodSkillIds: task.method_skill_ids || [],
        plannerRef: `agent_${task.task_id}`,
        plannerTaskIds: [task.task_id],
      },
    };
  });
  const preservedEdges = workflow.edges.filter(
    (edge) =>
      edge.id.startsWith("edge_resource_") ||
      edge.id.startsWith("edge_middleware_"),
  );
  const controlEdges = tasks.flatMap((task) =>
    task.depends_on.length > 0
      ? task.depends_on
          .filter((dependency) => taskIds.has(dependency))
          .map((dependency) => ({
            id: `edge_${dependency}_${task.task_id}`,
            source: nodeId(dependency),
            target: nodeId(task.task_id),
          }))
      : [
          {
            id: `edge_input_${task.task_id}`,
            source: "input",
            target: nodeId(task.task_id),
          },
        ],
  );
  const dependedOn = new Set(tasks.flatMap((task) => task.depends_on));
  const sink = [...tasks].reverse().find((task) => !dependedOn.has(task.task_id));
  if (sink) {
    controlEdges.push({
      id: `edge_${sink.task_id}_output`,
      source: nodeId(sink.task_id),
      target: "output",
    });
  }
  return { ...workflow, nodes, edges: [...preservedEdges, ...controlEdges] };
}
