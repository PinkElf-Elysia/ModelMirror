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
  const nodes = workflow.nodes.map((node) => {
    const task = tasks.find(
      (item) => taskNodes.get(item.task_id)?.id === node.id,
    );
    if (!task) return node;
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
