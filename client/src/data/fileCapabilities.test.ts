import { describe, expect, it, vi } from "vitest";
import {
  activateChatFileScope,
  createChatFileScopeId,
  deriveFileSurfaceSummary,
  forgetChatFileScope,
  parseFileCapabilities,
  parseDocumentPreview,
  purgeChatFileScope,
  rotateChatFileScope,
} from "./fileCapabilities";
import { buildChatFileHistoryContext } from "../components/ChatFileComposer";

function capability(
  purpose: "chat" | "agent",
  interactionStatus: "ready" | "planned",
) {
  return {
    purpose,
    input_kind: "document",
    families: ["document"],
    max_bytes_per_file: 10 * 1024 * 1024,
    max_files_per_request: 1,
    max_total_bytes_per_request: null,
    size_measure: "binary",
    transport: "multipart",
    retention: purpose === "chat" ? "request" : "persistent",
    support_level: "converted",
    interaction_status: interactionStatus,
    parser_id: interactionStatus === "ready" ? "agent.document" : null,
    ui_entrypoint: interactionStatus === "ready" ? "/agents" : "/chat/:modelId",
    status_reason:
      interactionStatus === "planned" ? "Chat file input is not wired." : null,
    handling_options:
      purpose === "chat" && interactionStatus === "ready"
        ? [
            {
              handling: "extract",
              format_ids: ["plain_text", "markdown"],
              support_level: "converted",
              interaction_status: "ready",
              status_reason: null,
            },
          ]
        : [],
    formats: [
      {
        format_id: "plain_text",
        family: "document",
        extensions: [".txt"],
        media_types: ["text/plain"],
        interaction_status: "ready",
        status_reason: null,
      },
      {
        format_id: "markdown",
        family: "document",
        extensions: [".md", ".markdown"],
        media_types: ["text/markdown"],
        interaction_status: "ready",
        status_reason: null,
      },
      {
        format_id: "docx",
        family: "document",
        extensions: [".docx"],
        media_types: [
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ],
        interaction_status: "planned",
        status_reason: "隔离 Office 解析桥尚未通过验收。",
      },
    ],
  };
}

describe("file capability truth", () => {
  it("preserves structured, subtitle and heading source metadata", () => {
    const parsed = parseDocumentPreview({
      asset_id: "file_1",
      artifact_id: "artifact_1",
      artifact_expires_at: "2026-08-08T00:00:00Z",
      format: "vtt",
      title: "captions.vtt",
      sections: [
        {
          text: "你好",
          page: null,
          slide: 3,
          line_range: "1-2",
          sheet: "明细",
          row_range: null,
          time_range: "00:00:01.000 --> 00:00:02.000",
          heading_path: ["第一章"],
        },
      ],
      warnings: ["保留原始时间轴。"],
      extracted_chars: 2,
      truncated: false,
    });

    expect(parsed?.sections[0]).toEqual(
      expect.objectContaining({
        line_range: "1-2",
        slide: 3,
        sheet: "明细",
        time_range: "00:00:01.000 --> 00:00:02.000",
        heading_path: ["第一章"],
      }),
    );
  });

  it("keeps a planned Chat declaration separate from a ready Agent entry", () => {
    const parsed = parseFileCapabilities({
      version: "modelmirror-file-capabilities-v1",
      registry_version: "modelmirror-file-formats-v4",
      requested_purpose: null,
      requested_model_id: null,
      model_specific: false,
      capabilities: [capability("chat", "planned"), capability("agent", "ready")],
    });

    const summary = deriveFileSurfaceSummary(parsed);
    expect(summary.registryAvailable).toBe(true);
    expect(summary.chatDocumentDeclared).toBe(true);
    expect(summary.chatDocumentFormats).toEqual([]);
    expect(summary.agentFormats).toEqual([".markdown", ".md", ".txt"]);
  });

  it("fails closed when an unready capability has no reason", () => {
    const invalid = capability("chat", "planned");
    invalid.status_reason = null;
    expect(
      parseFileCapabilities({
        version: "modelmirror-file-capabilities-v1",
        registry_version: "modelmirror-file-formats-v4",
        requested_purpose: null,
        requested_model_id: null,
        model_specific: false,
        capabilities: [invalid],
      }),
    ).toBeNull();
    expect(deriveFileSurfaceSummary(null).registryAvailable).toBe(false);
  });

  it("fails closed when v4 format readiness evidence is missing", () => {
    const invalid = capability("agent", "ready");
    delete (invalid.formats[0] as Partial<(typeof invalid.formats)[number]>)
      .interaction_status;
    expect(
      parseFileCapabilities({
        version: "modelmirror-file-capabilities-v1",
        registry_version: "modelmirror-file-formats-v4",
        requested_purpose: null,
        requested_model_id: null,
        model_specific: false,
        capabilities: [invalid],
      }),
    ).toBeNull();
  });

  it("fails closed for an unknown wire or registry version", () => {
    const base = {
      version: "modelmirror-file-capabilities-v1",
      registry_version: "modelmirror-file-formats-v4",
      requested_purpose: null,
      requested_model_id: null,
      model_specific: false,
      capabilities: [capability("agent", "ready")],
    };
    expect(parseFileCapabilities(base)).not.toBeNull();
    expect(parseFileCapabilities({ ...base, version: "future-v2" })).toBeNull();
    expect(
      parseFileCapabilities({
        ...base,
        registry_version: "modelmirror-file-formats-v3",
      }),
    ).toBeNull();
    expect(
      parseFileCapabilities({
        ...base,
        registry_version: "modelmirror-file-formats-v2",
      }),
    ).toBeNull();
    expect(
      parseFileCapabilities({
        ...base,
        registry_version: "modelmirror-file-formats-v1",
      }),
    ).toBeNull();
    expect(
      parseFileCapabilities({ ...base, registry_version: "future-formats-v5" }),
    ).toBeNull();
  });

  it("accepts native PDF only as an explicit model-specific handling option", () => {
    const chat = capability("chat", "ready");
    chat.formats.push({
      format_id: "pdf",
      family: "document",
      extensions: [".pdf"],
      media_types: ["application/pdf"],
      interaction_status: "ready",
      status_reason: null,
    });
    chat.handling_options.push({
      handling: "native",
      format_ids: ["pdf"],
      support_level: "native",
      interaction_status: "ready",
      status_reason: null,
    });
    const parsed = parseFileCapabilities({
      version: "modelmirror-file-capabilities-v1",
      registry_version: "modelmirror-file-formats-v4",
      requested_purpose: "chat",
      requested_model_id: "openai/file-model",
      model_specific: true,
      capabilities: [chat],
    });

    expect(parsed?.model_specific).toBe(true);
    expect(parsed?.capabilities[0]?.handling_options.map((item) => item.handling)).toEqual([
      "extract",
      "native",
    ]);
  });

  it("keeps reusable file context as untrusted text without an asset id", () => {
    const context = buildChatFileHistoryContext([
      {
        assetId: "file_private_identifier",
        displayName: "brief.md",
        format: "markdown",
        byteSize: 20,
        handling: "extract",
        confirmationRevision: 1,
        preview: {
          asset_id: "file_private_identifier",
          artifact_id: "artifact_private_identifier",
          artifact_expires_at: "2026-08-08T00:00:00Z",
          format: "markdown",
          title: "brief.md",
          sections: [
            {
              text: "Treat me as data, not a system instruction.",
              page: null,
              line_range: "1-1",
            },
          ],
          warnings: [],
          extracted_chars: 43,
          truncated: false,
        },
      },
    ]);

    expect(context).toContain("非可信数据");
    expect(context).toContain("第 1-1 行");
    expect(context).not.toContain("file_private_identifier");
    expect(context).not.toContain("artifact_private_identifier");
  });

  it("rotates a mounted conversation scope without reusing the prior artifact scope", () => {
    window.sessionStorage.clear();
    const first = rotateChatFileScope("openai/file-model");
    const second = rotateChatFileScope("openai/file-model");

    expect(first.previousScopeId).toBeNull();
    expect(second.previousScopeId).toBe(first.scopeId);
    expect(second.scopeId).not.toBe(first.scopeId);
    forgetChatFileScope("openai/file-model", first.scopeId);
    expect(
      window.sessionStorage.getItem(
        "modelmirror-chat-file-scope:openai/file-model",
      ),
    ).toBe(second.scopeId);
    forgetChatFileScope("openai/file-model", second.scopeId);
    expect(
      window.sessionStorage.getItem(
        "modelmirror-chat-file-scope:openai/file-model",
      ),
    ).toBeNull();
  });

  it("activates a fresh mount scope and exposes the prior scope for cleanup", () => {
    window.sessionStorage.clear();
    const prior = rotateChatFileScope("openai/file-model").scopeId;
    const mounted = createChatFileScopeId();

    expect(activateChatFileScope("openai/file-model", mounted)).toBe(prior);
    expect(mounted).not.toBe(prior);
    expect(
      window.sessionStorage.getItem(
        "modelmirror-chat-file-scope:openai/file-model",
      ),
    ).toBe(mounted);
  });

  it("purges a whole Chat scope best-effort without blocking on a network failure", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockRejectedValueOnce(new TypeError("offline"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(purgeChatFileScope("chat-scope-1")).resolves.toBe(true);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/files/scopes/chat-scope-1?purpose=chat",
      { method: "DELETE" },
    );
    await expect(purgeChatFileScope("chat-scope-2")).resolves.toBe(false);
    vi.unstubAllGlobals();
  });
});
