import { useEffect, useRef, useState } from "react";
import { DropInViewer } from "@mkkellogg/gaussian-splats-3d";

interface SplatViewerProps {
  source: string;
  onError?: (message: string) => void;
}

/**
 * Downloads the SPZ/PLY file with a real progress bar (fetch + stream),
 * then hands the local blob to the gaussian-splats-3d viewer.
 *
 * Without this, loading a large real SPZ (up to ~29MB full_res) shows a
 * blank screen for many seconds with no feedback — the progress bar makes
 * the wait visible.
 */
export function SplatViewer({ source, onError }: SplatViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [stage, setStage] = useState<"download" | "parse" | "ready" | "error">("download");

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let disposed = false;
    let objectUrl: string | null = null;
    const target = container;

    async function load() {
      try {
        // 1) Download with real progress.
        setStage("download");
        setProgress(0);
        const response = await fetch(source);
        if (!response.ok || !response.body) {
          throw new Error(`下载失败 HTTP ${response.status}`);
        }
        const total = Number(response.headers.get("Content-Length") || 0);
        const reader = response.body.getReader();
        const chunks: Uint8Array[] = [];
        let received = 0;
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (value) {
            chunks.push(value);
            received += value.length;
            if (total > 0) {
              setProgress(Math.min(100, Math.round((received / total) * 100)));
            }
          }
        }
        const blob = new Blob(chunks, {
          type: source.endsWith(".ply") ? "application/octet-stream" : "application/octet-stream",
        });
        objectUrl = URL.createObjectURL(blob);

        // 2) Parse + render.
        setStage("parse");
        setProgress(null);
        const viewer = new DropInViewer({
          gpuAcceleratedSort: true,
          sharedMemoryForWorkers: false,
        });
        target.appendChild(viewer.container);
        await viewer.addSplatScene(objectUrl, {
          showLoadingUI: true,
          progressiveLoad: true,
        });
        if (!disposed) {
          viewer.start();
          setStage("ready");
          setProgress(null);
        } else {
          viewer.dispose();
        }
      } catch (err) {
        if (!disposed) {
          setStage("error");
          setProgress(null);
          const message = err instanceof Error ? err.message : String(err);
          onError?.(`加载高斯溅射场景失败：${message}`);
        }
      }
    }

    void load();

    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [source, onError]);

  const showOverlay = progress !== null || stage === "download" || stage === "parse";

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      {showOverlay ? (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 bg-ink-950/70">
          <span className="text-sm font-medium text-slate-200">
            {stage === "download"
              ? "正在下载 3D 场景..."
              : stage === "parse"
                ? "正在解析并渲染 3D 场景（大文件可能需要十几秒）..."
                : "加载失败"}
          </span>
          {progress !== null ? (
            <div className="h-1.5 w-48 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-brand-300 transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
          ) : null}
          {progress !== null ? (
            <span className="text-xs text-slate-400">{progress}%</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
