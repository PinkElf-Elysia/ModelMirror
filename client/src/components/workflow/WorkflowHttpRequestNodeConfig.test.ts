import { describe, expect, it } from "vitest";

import type { WorkflowNodeData } from "../../types/workflow";
import { analyzeLegacyHttpMigration } from "./WorkflowHttpRequestNodeConfig";


function legacy(patch: Partial<WorkflowNodeData> = {}): WorkflowNodeData {
  return {
    kind: "http_request",
    title: "旧 HTTP 请求",
    description: "旧合同",
    method: "POST",
    url: "https://api.example.test/items/{{item_id}}",
    headersJson: '{"X-Tenant":"{{tenant_id}}","X-Retry":2}',
    bodyVariable: "payload",
    outputVariable: "legacy_result",
    ...patch,
  };
}

describe("analyzeLegacyHttpMigration", () => {
  it("converts scalar headers and exact variable templates without changing node identity data", () => {
    const result = analyzeLegacyHttpMigration(legacy());

    expect(result).toMatchObject({
      canMigrate: true,
      patch: {
        contractVersion: 2,
        method: "POST",
        bodyMode: "text",
        bodyBinding: { source: "variable", variable: "payload" },
        outputVariable: "legacy_result",
        headerItems: [
          {
            id: "header_1",
            name: "X-Tenant",
            binding: { source: "variable", variable: "tenant_id" },
          },
          {
            id: "header_2",
            name: "X-Retry",
            binding: { source: "literal", valueType: "number", value: 2 },
          },
        ],
      },
    });
    expect(result.patch).not.toHaveProperty("kind");
    expect(result.patch).not.toHaveProperty("title");
  });

  it.each([
    [{ url: "https://{{host}}/items" }, "协议、主机和端口"],
    [{ url: "not-an-http-url" }, "有效的 HTTP"],
    [{ headersJson: '{"Authorization":"secret"}' }, "运行器管理"],
    [{ headersJson: '{"X-API-Key":"plain-secret"}' }, "加密凭据"],
    [{ headersJson: '{"X-Trace":"prefix-{{trace_id}}"}' }, "无法无损迁移"],
    [{ method: "GET", bodyVariable: "payload" }, "不能携带正文"],
  ] as const)("rejects an unsafe or lossy legacy definition %#", (patch, reason) => {
    const result = analyzeLegacyHttpMigration(legacy(patch));
    expect(result.canMigrate).toBe(false);
    expect(result.reasons.join(" ")).toContain(reason);
    expect(result.patch).toBeUndefined();
  });
});
