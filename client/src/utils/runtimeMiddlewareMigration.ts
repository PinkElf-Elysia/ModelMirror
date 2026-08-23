import type { RuntimeMiddlewareNode } from "../types/runtimeMiddleware";
import type { WorkflowNode } from "../types/workflow";
import { SKILL_CREATOR_MIDDLEWARE_ID } from "./skillCreatorMiddleware";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function reconcileRuntimeMiddlewareNodes(
  nodes: WorkflowNode[],
  registryNodes: RuntimeMiddlewareNode[],
): WorkflowNode[] {
  const registryById = new Map(
    registryNodes
      .filter((definition) => definition.enabled)
      .map((definition) => [definition.id, definition]),
  );

  return nodes.map((node) => {
    if (node.data.kind !== "runtime_middleware") return node;
    const middlewareId = String(node.data.runtimeMiddlewareId ?? "");
    const definition = registryById.get(middlewareId);
    if (!definition) return node;

    const existingConfig = isRecord(node.data.runtimeMiddlewareConfig)
      ? node.data.runtimeMiddlewareConfig
      : {};
    const nextConfig: Record<string, unknown> = { ...existingConfig };
    const preserveLegacySkillCreatorMode =
      middlewareId === SKILL_CREATOR_MIDDLEWARE_ID
      && existingConfig.authoring_mode === undefined;
    const preserveLegacyPluginHookMode =
      middlewareId === "plugin_hooks"
      && existingConfig.hook_mode === undefined;
    definition.fields.forEach((field) => {
      if (
        preserveLegacySkillCreatorMode
        && field.name === "authoring_mode"
      ) return;
      if (preserveLegacyPluginHookMode && field.name === "hook_mode") return;
      if (nextConfig[field.name] === undefined && field.default !== undefined) {
        nextConfig[field.name] = field.default;
      }
    });

    return {
      ...node,
      data: {
        ...node.data,
        title: definition.title,
        description: definition.description,
        runtimeMiddlewareKind: definition.kind,
        runtimeMiddlewareFields: definition.fields,
        runtimeMiddlewareMetadata: definition.metadata ?? {},
        runtimeMiddlewareConfig: nextConfig,
      },
    };
  });
}
