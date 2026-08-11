import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type WorkflowDefinition } from "../../types/workflow";
import WorkflowRun from "./WorkflowRun";

const definition: WorkflowDefinition = {
  id: "selector-test",
  title: "file selector",
  updatedAt: "2026-08-08T00:00:00.000Z",
  nodes: [
    {
      id: "input",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: {
        kind: "input",
        title: "Input",
        description: "Input",
        variableName: "user_input",
      },
    },
    {
      id: "document",
      type: "workflowNode",
      position: { x: 100, y: 0 },
      data: {
        kind: "document_extractor",
        title: "Document",
        description: "Document",
        assetIdVariable: "document_asset_id",
        outputVariable: "document_text",
      },
    },
    {
      id: "output",
      type: "workflowNode",
      position: { x: 200, y: 0 },
      data: {
        kind: "output",
        title: "Output",
        description: "Output",
        outputVariable: "document_text",
      },
    },
  ],
  edges: [
    { id: "e1", source: "input", target: "document" },
    { id: "e2", source: "document", target: "output" },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WorkflowRun file selector", () => {
  it("lists persistent scope assets without selecting one automatically", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/files/capabilities")) {
        return new Response(
          JSON.stringify({
            capabilities: [
              {
                input_kind: "document",
                interaction_status: "ready",
                max_bytes_per_file: 10 * 1024 * 1024,
                formats: [
                  {
                    extensions: [".txt"],
                    interaction_status: "ready",
                  },
                ],
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/api/files?purpose=workflow")) {
        return new Response(
          JSON.stringify({
            items: [
              {
                asset_id: "file_existing",
                display_name: "existing.txt",
                byte_size: 128,
                format: "txt",
                status: "ready",
              },
            ],
            total: 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkflowRun definition={definition} />);

    const selector = await screen.findByLabelText(
      "document_asset_id 已有文件",
    );
    await waitFor(() => {
      expect(screen.getByText("existing.txt · TXT")).toBeInTheDocument();
    });
    expect(selector).toHaveValue("");
    expect(screen.getByRole("button", { name: "运行工作流" })).toBeDisabled();
    const uploadInput = screen.getByLabelText(
      "为 document_asset_id 上传新文件",
    );
    uploadInput.focus();
    expect(uploadInput).toHaveFocus();
    expect(uploadInput.closest("label")).toHaveClass("focus-within:ring-2");
    expect(screen.getByText("可用")).toHaveAttribute("aria-live", "polite");

    fireEvent.change(selector, { target: { value: "file_existing" } });

    expect(selector).toHaveValue("file_existing");
    expect(screen.getByRole("button", { name: "运行工作流" })).toBeEnabled();
    expect(
      screen.getByText("已选择已有文件用于本轮。"),
    ).toHaveAttribute("role", "status");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/files?purpose=workflow&scope_id=workflow%3Aselector-test",
    );
  });

  it("announces a fail-closed capability error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            detail: {
              code: "workflow_file_assets_disabled",
              message: "工作流文件资产当前未启用。",
            },
          }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    render(<WorkflowRun definition={definition} />);

    const disabledReason = await screen.findByText("工作流文件资产当前未启用。");
    expect(disabledReason).toHaveAttribute("role", "status");
    expect(disabledReason).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("button", { name: "运行工作流" })).toBeDisabled();
  });

  it("ignores a stale asset list after the workflow scope changes", async () => {
    let resolveFirstList!: (response: Response) => void;
    let resolveSecondList!: (response: Response) => void;
    const firstList = new Promise<Response>((resolve) => {
      resolveFirstList = resolve;
    });
    const secondList = new Promise<Response>((resolve) => {
      resolveSecondList = resolve;
    });
    let listRequestCount = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/files/capabilities")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              capabilities: [
                {
                  input_kind: "document",
                  interaction_status: "ready",
                  max_bytes_per_file: 10 * 1024 * 1024,
                  formats: [
                    { extensions: [".txt"], interaction_status: "ready" },
                  ],
                },
              ],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      if (url.includes("/api/files?purpose=workflow")) {
        listRequestCount += 1;
        return listRequestCount === 1 ? firstList : secondList;
      }
      return Promise.reject(new Error(`Unexpected fetch: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    const view = render(<WorkflowRun definition={definition} />);
    await waitFor(() => expect(listRequestCount).toBe(1));

    view.rerender(
      <WorkflowRun definition={{ ...definition, id: "selector-test-next" }} />,
    );
    await waitFor(() => expect(listRequestCount).toBe(2));

    await act(async () => {
      resolveSecondList(
        new Response(
          JSON.stringify({
            items: [
              {
                asset_id: "file_new_scope",
                display_name: "new-scope.txt",
                byte_size: 64,
                format: "txt",
                status: "ready",
              },
            ],
            total: 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    });
    expect(await screen.findByText("new-scope.txt · TXT")).toBeInTheDocument();

    await act(async () => {
      resolveFirstList(
        new Response(
          JSON.stringify({
            items: [
              {
                asset_id: "file_old_scope",
                display_name: "old-scope.txt",
                byte_size: 32,
                format: "txt",
                status: "ready",
              },
            ],
            total: 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    });

    await waitFor(() => {
      expect(screen.queryByText("old-scope.txt · TXT")).not.toBeInTheDocument();
      expect(screen.getByText("new-scope.txt · TXT")).toBeInTheDocument();
    });
  });

  it("does not apply an upload result from a previous workflow scope", async () => {
    let resolveUpload!: (response: Response) => void;
    const uploadResponse = new Promise<Response>((resolve) => {
      resolveUpload = resolve;
    });
    let oldScopeListRequests = 0;
    let newScopeListRequests = 0;
    const fetchMock = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        if (url.includes("/api/files/capabilities")) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                capabilities: [
                  {
                    input_kind: "document",
                    interaction_status: "ready",
                    max_bytes_per_file: 10 * 1024 * 1024,
                    formats: [
                      { extensions: [".txt"], interaction_status: "ready" },
                    ],
                  },
                ],
              }),
              { status: 200, headers: { "Content-Type": "application/json" } },
            ),
          );
        }
        if (url === "/api/files" && init?.method === "POST") {
          return uploadResponse;
        }
        if (
          url.startsWith("/api/files?") &&
          url.includes("scope_id=workflow%3Aselector-test-next")
        ) {
          newScopeListRequests += 1;
          return Promise.resolve(
            new Response(
              JSON.stringify({
                items: [
                  {
                    asset_id: "file_new_scope",
                    display_name: "new-scope.txt",
                    byte_size: 64,
                    format: "txt",
                    status: "ready",
                  },
                ],
                total: 1,
              }),
              { status: 200, headers: { "Content-Type": "application/json" } },
            ),
          );
        }
        if (
          url.startsWith("/api/files?") &&
          url.includes("scope_id=workflow%3Aselector-test")
        ) {
          oldScopeListRequests += 1;
          return Promise.resolve(
            new Response(JSON.stringify({ items: [], total: 0 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected fetch: ${url}`));
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const view = render(<WorkflowRun definition={definition} />);
    const uploadInput = await screen.findByLabelText(
      "为 document_asset_id 上传新文件",
    );
    await waitFor(() => expect(oldScopeListRequests).toBe(1));
    fireEvent.change(uploadInput, {
      target: { files: [new File(["old scope"], "old-scope.txt")] },
    });
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/files",
        expect.objectContaining({ method: "POST" }),
      );
    });

    view.rerender(
      <WorkflowRun definition={{ ...definition, id: "selector-test-next" }} />,
    );
    expect(await screen.findByText("new-scope.txt · TXT")).toBeInTheDocument();

    await act(async () => {
      resolveUpload(
        new Response(
          JSON.stringify({
            asset_id: "file_old_upload",
            display_name: "old-scope.txt",
            byte_size: 9,
            format: "txt",
            status: "ready",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      );
    });

    await waitFor(() => {
      expect(screen.queryByText(/old-scope\.txt/)).not.toBeInTheDocument();
      expect(screen.getByText("new-scope.txt · TXT")).toBeInTheDocument();
      expect(newScopeListRequests).toBe(1);
      expect(oldScopeListRequests).toBe(1);
    });
  });
});
