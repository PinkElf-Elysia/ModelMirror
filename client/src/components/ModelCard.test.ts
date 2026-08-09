import { describe, expect, it } from "vitest";
import { deriveDocumentInputPresentation } from "./ModelCard";
import type { FileSurfaceSummary } from "../data/fileCapabilities";

function summary(
  overrides: Partial<FileSurfaceSummary> = {},
): FileSurfaceSummary {
  return {
    registryAvailable: true,
    chatDocumentDeclared: true,
    chatDocumentFormats: [".txt", ".md", ".pdf"],
    ragFormats: [".txt", ".md", ".pdf"],
    dataxFormats: [],
    agentFormats: [],
    workflowFormats: [],
    ...overrides,
  };
}

describe("ModelCard document input presentation", () => {
  it("shows Chat extract support only for a callable Chat model", () => {
    expect(deriveDocumentInputPresentation(summary(), true, false)).toEqual({
      label: "文件输入 · Chat 可用（提取后发送）",
      reason: "可在聊天中上传已登记的文档格式；发送前会提取内容并由你预览确认。",
    });
  });

  it("fails closed when the Chat registry is disabled or has no formats", () => {
    expect(
      deriveDocumentInputPresentation(
        summary({ chatDocumentFormats: [] }),
        true,
        false,
      ).label,
    ).toBe("文件输入 · 当前入口未开放");
    expect(deriveDocumentInputPresentation(summary(), false, false).label).toBe(
      "文件输入 · 当前入口未开放",
    );
  });

  it("keeps RAG models on the knowledge-base presentation", () => {
    expect(deriveDocumentInputPresentation(summary(), false, true).label).toBe(
      "文件处理 · 资料库可用",
    );
  });
});
