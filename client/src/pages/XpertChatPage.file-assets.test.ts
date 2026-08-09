import { afterEach, describe, expect, it, vi } from "vitest";
import type { XpertFileAsset } from "../types/xpert";
import { deleteXpertFile } from "../utils/xpertApi";
import {
  consumeSelectedXpertFiles,
  isCurrentXpertConversationRequest,
  selectedXpertFilesAfterConversationRestore,
  selectedXpertFilesAfterRefresh,
  xpertConversationNavigationLocked,
  xpertFilesAfterPermanentDelete,
  xpertMessageInputLocked,
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
});
