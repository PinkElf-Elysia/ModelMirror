import { memo, useEffect, useMemo, useState } from "react";
import { MeshViewer } from "./MeshViewer";
import { PanoramaViewer } from "./PanoramaViewer";
import { PointCloudViewer } from "./PointCloudViewer";
import { SplatViewer } from "./SplatViewer";

export type AssetFormat = "spz" | "ply" | "glb" | "gltf" | "png" | "unknown";
export type AssetKind =
  | "gaussian_splat"
  | "textured_mesh"
  | "panorama"
  | "preview"
  | "other";

export interface FourViewerProps {
  source: string;
  format: AssetFormat;
  kind?: AssetKind;
}

/**
 * Unified 3D viewer entry — dispatches to a format-specific viewer.
 * PNG panorama / GLB / SPZ / PLY all go through here.
 */
export const FourViewer = memo(function FourViewer({
  source,
  format,
  kind = "other",
}: FourViewerProps) {
  const [webgl2, setWebgl2] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const canvas = document.createElement("canvas");
    const gl2 = canvas.getContext("webgl2");
    setWebgl2(gl2 !== null);
  }, []);

  // Gaussian splat PLY vs plain point cloud PLY must be distinguished
  // by kind, not by the .ply extension.
  const isSplatPly = format === "ply" && kind === "gaussian_splat";
  const isSplat = format === "spz" || isSplatPly;

  const view = useMemo(() => {
    if (webgl2 === false && isSplat) {
      return (
        <div className="flex h-full items-center justify-center rounded-lg border border-amber-300/25 bg-amber-300/10 px-4 py-8 text-sm text-amber-100">
          当前浏览器不支持 WebGL2，无法渲染高斯溅射（SPZ/PLY）场景。请使用支持 WebGL2 的浏览器（Chrome / Edge / Firefox 新版）。
        </div>
      );
    }

    if (format === "glb" || format === "gltf") {
      return <MeshViewer source={source} format={format} onError={setError} />;
    }
    if (format === "png") {
      return <PanoramaViewer source={source} onError={setError} />;
    }
    if (isSplat) {
      return <SplatViewer source={source} onError={setError} />;
    }
    if (format === "ply") {
      return <PointCloudViewer source={source} onError={setError} />;
    }
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] px-4 py-8 text-sm text-slate-400">
        暂不支持该文件格式：{format}
      </div>
    );
  }, [format, isSplat, source, webgl2]);

  return (
    <div className="relative h-full w-full overflow-hidden rounded-lg border border-white/10 bg-ink-950">
      {view}
      {error ? (
        <div className="absolute inset-x-0 bottom-0 border-t border-red-300/20 bg-red-950/80 px-3 py-2 text-xs text-red-100">
          {error}
        </div>
      ) : null}
    </div>
  );
});
