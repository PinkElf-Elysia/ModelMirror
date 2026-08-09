import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DataXImportJob } from "../types/datax";
import DataXProjectPage, {
  hasRunningImportJobs,
  xlsxSheetNotices,
} from "./DataXProjectPage";

function job(status: DataXImportJob["status"]): DataXImportJob {
  return {
    job_id: `job-${status}`,
    project_id: "project-1",
    source_id: `source-${status}`,
    status,
    attempt_count: 1,
    error: status === "failed" ? "解析失败" : "",
    created_at: 1,
    updated_at: 1,
    completed_at: status === "ready" || status === "failed" ? 1 : null,
  };
}

describe("Data X import task refresh", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("polls only while a persisted task remains active", () => {
    expect(hasRunningImportJobs([job("pending")])).toBe(true);
    expect(hasRunningImportJobs([job("processing")])).toBe(true);
    expect(hasRunningImportJobs([job("ready"), job("failed")])).toBe(false);
  });

  it("states exactly which XLSX worksheets entered the snapshot", () => {
    expect(
      xlsxSheetNotices({
        source_id: "source-xlsx",
        project_id: "project-1",
        name: "预算",
        file_name: "预算.xlsx",
        file_type: "xlsx",
        byte_size: 1024,
        row_count: 10,
        column_count: 3,
        status: "ready",
        profile: {
          source: {
            selected_sheet: "数据",
            visible_sheets_ignored: ["其他数据"],
            hidden_sheets_ignored: ["隐藏"],
          },
        },
        error: "",
        created_at: 1,
        updated_at: 1,
      }),
    ).toEqual([
      "已导入工作表：数据",
      "未导入其他可见工作表：其他数据",
      "已忽略隐藏工作表：隐藏",
    ]);
  });

  it("restores a failed import task from the project endpoint after refresh", async () => {
    const failedJob = job("failed");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/datax/projects/project-1/import-jobs") {
        return new Response(JSON.stringify({ items: [failedJob], total: 1 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/datax/projects/project-1") {
        return new Response(
          JSON.stringify({
            project_id: "project-1",
            name: "销售分析",
            description: "",
            status: "active",
            revision: 1,
            created_at: 1,
            updated_at: 1,
            sources: [
              {
                source_id: "source-ready",
                project_id: "project-1",
                name: "季度预算",
                file_name: "季度预算.xlsx",
                file_type: "xlsx",
                byte_size: 2048,
                row_count: 10,
                column_count: 3,
                status: "ready",
                profile: {
                  source: {
                    selected_sheet: "数据",
                    visible_sheets_ignored: ["其他数据"],
                    hidden_sheets_ignored: ["隐藏"],
                  },
                },
                error: "",
                created_at: 1,
                updated_at: 1,
              },
            ],
            models: [],
            indicators: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url === "/api/files/capabilities") {
        return new Response(
          JSON.stringify({
            version: "modelmirror-file-capabilities-v1",
            registry_version: "modelmirror-file-formats-v4",
            requested_purpose: null,
            requested_model_id: null,
            model_specific: false,
            capabilities: [
              {
                purpose: "datax",
                input_kind: "data_source",
                families: ["dataset"],
                max_bytes_per_file: 50 * 1024 * 1024,
                max_files_per_request: 1,
                max_total_bytes_per_request: null,
                size_measure: "binary",
                transport: "multipart",
                retention: "persistent",
                support_level: "specialized",
                interaction_status: "ready",
                parser_id: "datax.snapshot",
                ui_entrypoint: "/datax",
                status_reason: null,
                handling_options: [],
                formats: [
                  {
                    format_id: "xlsx",
                    family: "dataset",
                    extensions: [".xlsx"],
                    media_types: [
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ],
                    interaction_status: "ready",
                    status_reason: null,
                  },
                ],
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      createElement(
        MemoryRouter,
        { initialEntries: ["/datax/project-1"] },
        createElement(
          Routes,
          null,
          createElement(Route, {
            element: createElement(DataXProjectPage),
            path: "/datax/:projectId",
          }),
        ),
      ),
    );

    expect(await screen.findByRole("region", { name: "最近导入任务" })).toBeVisible();
    expect(screen.getByText("已清理的失败导入")).toBeVisible();
    expect(screen.getByText("解析失败")).toBeVisible();
    expect(screen.getByText("已导入工作表：数据")).toBeVisible();
    expect(screen.getByText("未导入其他可见工作表：其他数据")).toBeVisible();
    expect(screen.getByText("已忽略隐藏工作表：隐藏")).toBeVisible();
  });
});
