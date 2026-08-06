import { describe, expect, it } from "vitest";
import type { RuntimeMiddlewareNode } from "../types/runtimeMiddleware";
import type { WorkflowNode } from "../types/workflow";
import { reconcileRuntimeMiddlewareNodes } from "./runtimeMiddlewareMigration";

describe("reconcileRuntimeMiddlewareNodes", () => {
  it("upgrades an existing Skill Runtime field snapshot without losing user config", () => {
    const existing: WorkflowNode = {
      id: "skills-runtime",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: {
        kind: "runtime_middleware",
        title: "Skill 执行指导",
        description: "旧说明",
        runtimeMiddlewareId: "skills_runtime",
        runtimeMiddlewareKind: "runtime_middleware.skills_runtime",
        runtimeMiddlewareFields: [
          { name: "skill_ids", label: "已安装 Skill ID", type: "textarea" },
          { name: "auto_discover", label: "允许发现", type: "boolean", default: false },
        ],
        runtimeMiddlewareConfig: {
          skill_ids: "local-pdf",
          auto_discover: true,
          legacy_extension: "preserve-me",
        },
        middlewarePriority: "100",
      },
    };
    const definition: RuntimeMiddlewareNode = {
      id: "skills_runtime",
      kind: "runtime_middleware.skills_runtime",
      title: "Skill 执行指导",
      description: "按需读取并检索已核验目录。",
      category: "tool",
      icon: "BookOpenCheck",
      enabled: true,
      fields: [
        { name: "skill_ids", label: "已安装 Skill ID", type: "textarea" },
        { name: "auto_discover", label: "允许发现", type: "boolean", default: false },
        { name: "catalog_search", label: "允许按需检索", type: "boolean", default: false },
        { name: "catalog_install", label: "允许审批安装", type: "boolean", default: false },
        { name: "max_catalog_installs", label: "最多安装数", type: "number", default: 3 },
      ],
      metadata: { runtime_hook: "agent_tools" },
    };

    const [migrated] = reconcileRuntimeMiddlewareNodes([existing], [definition]);

    expect(migrated.data.runtimeMiddlewareFields?.map((field) => field.name)).toEqual([
      "skill_ids",
      "auto_discover",
      "catalog_search",
      "catalog_install",
      "max_catalog_installs",
    ]);
    expect(migrated.data.runtimeMiddlewareConfig).toEqual({
      skill_ids: "local-pdf",
      auto_discover: true,
      legacy_extension: "preserve-me",
      catalog_search: false,
      catalog_install: false,
      max_catalog_installs: 3,
    });
    expect(migrated.data.description).toBe("按需读取并检索已核验目录。");
    expect(migrated.data.runtimeMiddlewareMetadata).toEqual({
      runtime_hook: "agent_tools",
    });
  });

  it("leaves unknown and non-middleware nodes unchanged", () => {
    const input: WorkflowNode = {
      id: "input",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: {
        kind: "input",
        title: "输入",
        description: "测试输入节点",
        variableName: "user_input",
      },
    };
    const unknown: WorkflowNode = {
      id: "custom-runtime",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: {
        kind: "runtime_middleware",
        title: "私有中间件",
        description: "未注册的私有中间件",
        runtimeMiddlewareId: "private_runtime",
      },
    };

    const migrated = reconcileRuntimeMiddlewareNodes([input, unknown], []);

    expect(migrated[0]).toBe(input);
    expect(migrated[1]).toBe(unknown);
  });
});
