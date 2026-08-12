import { describe, expect, it } from "vitest";
import {
  compactFilterGroup,
  normalizeFilterGroup,
  parseLiteral,
} from "./workflowDataTableConfig";

describe("workflow Agent Table configuration helpers", () => {
  it("preserves nested and/or filters and variable bindings", () => {
    const filter = normalizeFilterGroup({
      logic: "or",
      items: [
        {
          field: "status",
          operator: "eq",
          value: { source: "literal", value: "open" },
        },
        {
          logic: "and",
          items: [
            {
              field: "owner_id",
              operator: "eq",
              value: { source: "variable", variable: "current_owner" },
            },
          ],
        },
      ],
    });

    expect(compactFilterGroup(filter)).toEqual({
      logic: "or",
      items: [
        {
          field: "status",
          operator: "eq",
          value: { source: "literal", value: "open" },
        },
        {
          logic: "and",
          items: [
            {
              field: "owner_id",
              operator: "eq",
              value: { source: "variable", variable: "current_owner" },
            },
          ],
        },
      ],
    });
  });

  it("compacts an empty filter to the runtime-compatible empty object", () => {
    expect(compactFilterGroup(normalizeFilterGroup(undefined))).toEqual({});
  });

  it("parses typed literal values without converting invalid input", () => {
    expect(parseLiteral("42", "integer")).toEqual({ ok: true, value: 42 });
    expect(parseLiteral("42.5", "number")).toEqual({ ok: true, value: 42.5 });
    expect(parseLiteral('{"ok":true}', "json")).toEqual({
      ok: true,
      value: { ok: true },
    });
    expect(parseLiteral("42.5", "integer")).toEqual({
      ok: false,
      error: "请输入整数。",
    });
  });
});
