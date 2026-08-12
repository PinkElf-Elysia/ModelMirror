import { createElement } from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ModelPreferenceProvider } from "../context/ModelPreferenceContext";
import { models } from "../data/models";
import ModelCard, { deriveDocumentInputPresentation } from "./ModelCard";
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

  it("adds a flagship treatment only when the featured variant is requested", () => {
    const model = models.find((candidate) => candidate.id === "openai/gpt-5.6-sol");
    expect(model).toBeDefined();

    render(
      createElement(
        MemoryRouter,
        null,
        createElement(
          ModelPreferenceProvider,
          null,
          createElement(ModelCard, {
            catalogInvocable: true,
            featured: true,
            model: model!,
          }),
        ),
      ),
    );

    expect(screen.getByText("旗舰推荐")).toBeInTheDocument();
    expect(screen.getByText("GPT-5.6 Sol")).toBeInTheDocument();
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: "OpenAI" })[0]).toHaveAttribute(
      "src",
      "/brand/openai-blossom.svg",
    );
    expect(
      document.querySelector('[data-featured-model-card="true"]'),
    ).toBeInTheDocument();
  });

  it("uses the official Anthropic symbol on its featured card", () => {
    const baseModel = models.find((candidate) => candidate.id === "openai/gpt-5.6-sol");
    expect(baseModel).toBeDefined();
    const model = {
      ...baseModel!,
      id: "anthropic/claude-opus-5",
      model_author: "Anthropic",
      name: "Anthropic: Claude Opus 5",
    };

    render(
      createElement(
        MemoryRouter,
        null,
        createElement(
          ModelPreferenceProvider,
          null,
          createElement(ModelCard, {
            catalogInvocable: true,
            featured: true,
            model,
          }),
        ),
      ),
    );

    expect(screen.getAllByRole("img", { name: "Anthropic" })[0]).toHaveAttribute(
      "src",
      "/brand/anthropic-symbol-slate.svg",
    );
  });
});

describe("ModelCard decision-first layout", () => {
  it("prioritizes task and input capabilities without obsolete region or fake talent labels", () => {
    const model = models.find((candidate) => candidate.id === "openai/gpt-5.6-sol");
    expect(model).toBeDefined();

    render(
      createElement(
        MemoryRouter,
        null,
        createElement(
          ModelPreferenceProvider,
          null,
          createElement(ModelCard, {
            catalogInvocable: true,
            confirmedImageOperations: ["analyze_image"],
            model: model!,
          }),
        ),
      ),
    );

    expect(screen.getByRole("region", { name: "可完成任务" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "可接收输入" })).toBeInTheDocument();
    expect(screen.getByText("推理分析")).toBeInTheDocument();
    expect(screen.getByText("图片")).toBeInTheDocument();
    expect(screen.getByText("文件")).toBeInTheDocument();
    expect(screen.queryByText(/当前地区|国内可用优先/)).not.toBeInTheDocument();
    expect(screen.queryByText(/人气值|已录用/)).not.toBeInTheDocument();
  });
});
