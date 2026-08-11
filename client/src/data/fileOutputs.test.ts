import { describe, expect, it } from "vitest";
import {
  FILE_OUTPUT_CAPABILITIES_VERSION,
  FILE_OUTPUT_REGISTRY_VERSION,
  parseFileOutput,
  parseFileOutputCapabilities,
} from "./fileOutputs";

function capabilities() {
  return {
    version: FILE_OUTPUT_CAPABILITIES_VERSION,
    registry_version: FILE_OUTPUT_REGISTRY_VERSION,
    requested_purpose: "chat",
    requested_model_id: "provider/tool-model",
    model_specific: true,
    interaction_status: "ready",
    status_reason: null,
    limits: {
      max_files_per_turn: 5,
      max_bytes_per_file: 50 * 1024 * 1024,
      max_total_bytes_per_turn: 100 * 1024 * 1024,
      max_spec_bytes: 2 * 1024 * 1024,
      max_spec_chars: 500_000,
      hard_ttl_seconds: 7 * 24 * 60 * 60,
    },
    formats: [
      {
        format_id: "plain_text",
        media_types: ["text/plain"],
        preview_kind: "text",
        actions: ["preview", "download", "reuse", "save_rag", "delete"],
        generation_kind: "text",
        interaction_status: "ready",
        status_reason: null,
      },
    ],
  };
}

describe("file output contracts", () => {
  it("accepts only the independent v1 capability protocol and v5 registry", () => {
    expect(parseFileOutputCapabilities(capabilities())?.interaction_status).toBe("ready");
    expect(parseFileOutputCapabilities({ ...capabilities(), version: "modelmirror-file-output-capabilities-v2" })).toBeNull();
    expect(parseFileOutputCapabilities({ ...capabilities(), registry_version: "modelmirror-file-formats-v4" })).toBeNull();
  });

  it("fails closed on unknown actions or malformed output metadata", () => {
    const bad = capabilities();
    bad.formats[0].actions = [...bad.formats[0].actions, "open_path"];
    expect(parseFileOutputCapabilities(bad)).toBeNull();
    expect(parseFileOutput({ output_id: "output_1" })).toBeNull();
  });

  it("parses safe output metadata without accepting paths", () => {
    const parsed = parseFileOutput({
      output_id: `output_${"a".repeat(32)}`,
      asset_id: `file_${"b".repeat(32)}`,
      purpose: "chat",
      scope_id: "chat-scope-1",
      producer_kind: "chat_tool",
      display_name: "report.txt",
      format: "plain_text",
      media_type: "text/plain",
      byte_size: 12,
      preview_kind: "text",
      status: "completed",
      expires_at: "2026-08-16T00:00:00+00:00",
      warnings: [],
      error_code: null,
      source_run_id: null,
      source_message_id: "assistant-1",
      source_node_id: null,
      created_at: "2026-08-09T00:00:00+00:00",
      updated_at: "2026-08-09T00:00:00+00:00",
    });
    expect(parsed?.display_name).toBe("report.txt");
    expect(parsed).not.toHaveProperty("storage_key");
  });
});
