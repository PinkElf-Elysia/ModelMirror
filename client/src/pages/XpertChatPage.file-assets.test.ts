import { afterEach, describe, expect, it, vi } from "vitest";
import type { XpertFileAsset } from "../types/xpert";
import type { FileOutput } from "../data/fileOutputs";
import { deleteXpertFile } from "../utils/xpertApi";
import {
  consumeSelectedXpertFiles,
  fileOutputsForRun,
  isCurrentXpertConversationRequest,
  selectedXpertFilesAfterConversationRestore,
  selectedXpertFilesAfterRefresh,
  unassociatedXpertFileOutputs,
  xpertConversationNavigationLocked,
  xpertFilesAfterPermanentDelete,
  xpertMessageInputLocked,
  xpertOutputScopeId,
} from "./XpertChatPage";

afterEach(() => {
  vi.unstubAllGlobals();
});

function file(assetId: string): XpertFileAsset {
  return {
    asset_id: assetId,
    artifact_id: `artifact-${assetId}`,
    xpert_id: "xpert-1",
    conversation_id: "conversation-1",
    filename: `${assetId}.txt`,
    size_bytes: 12,
    extension: ".txt",
    mime_type: "text/plain",
    status: "ready",
    character_count: 12,
    extracted_truncated: false,
    created_at: 1,
    archived_at: null,
  };
}

function output(outputId: string, sourceRunId: string | null): FileOutput {
  return {
    output_id: outputId,
    asset_id: `file-${outputId}`,
    purpose: "agent",
    scope_id: "xpert:xpert-1:conversation-1",
    producer_kind: "sandbox_publish_artifact",
    display_name: `${outputId}.txt`,
    format: "plain_text",
    media_type: "text/plain",
    byte_size: 12,
    preview_kind: "text",
    status: "completed",
    expires_at: "2026-08-17T00:00:00Z",
    warnings: [],
    error_code: null,
    source_run_id: sourceRunId,
    source_message_id: null,
    source_node_id: null,
    created_at: "2026-08-10T00:00:00Z",
    updated_at: "2026-08-10T00:00:00Z",
  };
}

describe("Xpert per-turn file selection", () => {
  it("restores the conversation file list without selecting historical files", () => {
    expect(
      selectedXpertFilesAfterConversationRestore([file("file-a"), file("file-b")]),
    ).toEqual([]);
  });

  it("refreshes only explicit selections and never adds another historical file", () => {
    expect(
      selectedXpertFilesAfterRefresh(
        ["file-a", "deleted-file"],
        [file("file-a"), file("file-b")],
      ),
    ).toEqual(["file-a"]);
  });

  it("consumes an explicit selection once and clears it before the next turn", () => {
    expect(consumeSelectedXpertFiles(true, ["file-a", "file-b"])).toEqual({
      fileAssetIdsForRun: ["file-a", "file-b"],
      nextSelectedFileIds: [],
    });
    expect(consumeSelectedXpertFiles(false, ["file-a"])).toEqual({
      fileAssetIdsForRun: [],
      nextSelectedFileIds: [],
    });
  });

  it("rejects stale file responses after the active conversation changes", () => {
    expect(isCurrentXpertConversationRequest(4, 4, "conversation-a", "conversation-a")).toBe(true);
    expect(isCurrentXpertConversationRequest(3, 4, "conversation-a", "conversation-a")).toBe(false);
    expect(isCurrentXpertConversationRequest(4, 4, "conversation-a", "conversation-b")).toBe(false);
  });

  it("locks navigation during file mutations and blocks sending while conversation history loads", () => {
    expect(xpertConversationNavigationLocked(true, false, false, "")).toBe(true);
    expect(xpertConversationNavigationLocked(false, false, true, "")).toBe(true);
    expect(xpertConversationNavigationLocked(false, false, false, "file-a")).toBe(true);
    expect(xpertConversationNavigationLocked(false, false, false, "")).toBe(false);
    expect(xpertMessageInputLocked(true, false)).toBe(true);
    expect(xpertMessageInputLocked(false, false)).toBe(false);
  });

  it("removes a purged file from local state without waiting for refresh", () => {
    expect(
      xpertFilesAfterPermanentDelete([file("file-a"), file("file-b")], "file-a")
        .map((item) => item.asset_id),
    ).toEqual(["file-b"]);
  });

  it("uses only the explicit purge route for permanent deletion", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(
      JSON.stringify({ asset_id: "file-a", deleted: true }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));
    vi.stubGlobal("fetch", fetchMock);

    await deleteXpertFile("xpert-1", "conversation-1", "file-a");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/xperts/xpert-1/conversations/conversation-1/files/file-a/purge",
      { method: "DELETE" },
    );
  });

  it("derives an opaque conversation output scope and groups recovered outputs", () => {
    const outputs = [
      output("output-a", "run-a"),
      output("output-b", "run-missing"),
      output("output-c", null),
    ];
    const messages = [
      { role: "assistant" as const, content: "done", source_run_id: "run-a" },
    ];

    expect(xpertOutputScopeId("xpert-1", "conversation-1")).toBe(
      "xpert:xpert-1:conversation-1",
    );
    expect(fileOutputsForRun(outputs, "run-a").map((item) => item.output_id)).toEqual([
      "output-a",
    ]);
    expect(unassociatedXpertFileOutputs(outputs, messages).map((item) => item.output_id)).toEqual([
      "output-b",
      "output-c",
    ]);
  });
});
