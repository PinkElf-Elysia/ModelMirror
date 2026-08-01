import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { DropInViewer } from "@mkkellogg/gaussian-splats-3d";

interface SplatViewerProps {
  source: string;
  onError?: (message: string) => void;
}

/**
 * Downloads the SPZ/PLY file with a real progress bar (fetch + stream),
 * then renders it with gaussian-splats-3d.
 *
 * DropInViewer extends THREE.Group (an Object3D, NOT a DOM node), so it
 * must be added to our own Three.js scene and rendered by our own
 * WebGLRenderer — never appendChild'd directly.
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
    let viewer: DropInViewer | null = null;
    let renderer: THREE.WebGLRenderer | null = null;

    // Render loop (recursive requestAnimationFrame so the scene keeps painting).
    let raf = 0;
    const render = () => {
      if (disposed) return;
      controls.update();
      if (renderer) renderer.render(scene, camera);
      raf = requestAnimationFrame(render);
    };

    // Scene setup
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0e1524);
    const camera = new THREE.PerspectiveCamera(
      60,
      container.clientWidth / container.clientHeight,
      0.1,
      1000,
    );
    camera.position.set(1, 1, 2);
    const controls = new OrbitControls(camera, container);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

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
        const blob = new Blob(chunks, { type: "application/octet-stream" });
        objectUrl = URL.createObjectURL(blob);

        // 2) Parse + add DropInViewer to OUR scene.
        setStage("parse");
        setProgress(null);
        viewer = new DropInViewer({
          gpuAcceleratedSort: true,
          sharedMemoryForWorkers: false,
        });
        await viewer.addSplatScene(objectUrl, {
          showLoadingUI: true,
          progressiveLoad: true,
        });
        scene.add(viewer);
        if (!disposed) {
          // Center the splat on the scene.
          viewer.position.set(0, 0, 0);
          viewer.updateMatrixWorld(true);
          const box = new THREE.Box3().setFromObject(viewer);
          const size = box.getSize(new THREE.Vector3());
          const maxDim = Math.max(size.x, size.y, size.z) || 1;
          camera.near = maxDim * 0.001;
          camera.far = maxDim * 20;
          camera.position.set(maxDim * 0.8, maxDim * 0.6, maxDim * 0.8);
          camera.updateProjectionMatrix();
          controls.target.set(0, 0, 0);
          controls.update();
          setStage("ready");
          setProgress(null);
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

    raf = requestAnimationFrame(render);
    void load();

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      controls.dispose();
      if (viewer) {
        try {
          scene.remove(viewer);
          viewer.dispose();
        } catch {
          /* ignore dispose errors */
        }
      }
      if (renderer) {
        renderer.dispose();
        if (renderer.domElement.parentElement === container) {
          container.removeChild(renderer.domElement);
        }
      }
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose();
          const mat = obj.material;
          if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
          else if (mat) mat.dispose();
        }
      });
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
