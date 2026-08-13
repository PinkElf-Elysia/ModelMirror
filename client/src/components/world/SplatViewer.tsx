import { useEffect, useRef, useState } from "react";
import { Vector3 } from "three";
import {
  SceneFormat,
  SceneRevealMode,
  SplatRenderMode,
  Viewer,
} from "@mkkellogg/gaussian-splats-3d";

interface SplatViewerProps {
  source: string;
  onError?: (message: string) => void;
}

const CAMERA_SAMPLE_LIMIT = 4096;
const MIN_LOOK_DISTANCE = 0.5;
const MARBLE_Z_UP_ROTATION: [number, number, number, number] = [
  -Math.SQRT1_2,
  0,
  0,
  Math.SQRT1_2,
];

interface InteriorFrame {
  min: Vector3;
  max: Vector3;
  position: Vector3;
  target: Vector3;
  moveStep: number;
}

function rotateMarblePointToZUp(point: Vector3) {
  return point.set(point.x, point.z, -point.y);
}

/**
 * Marble SPZ files describe a navigable scene around their capture origin.
 * Frame that origin from inside the sampled bounds instead of using a distant
 * object-viewer camera, which makes room captures look like tiny exterior
 * models.
 */
function frameSpzInterior(viewer: Viewer): InteriorFrame | null {
  const splatBuffer = viewer.getSplatScene(0).splatBuffer;
  const splatCount = splatBuffer.getSplatCount();
  if (splatCount <= 0) return null;

  const min = new Vector3(Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY);
  const max = new Vector3(Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY);
  const point = new Vector3();
  const sampleStep = Math.max(1, Math.floor(splatCount / CAMERA_SAMPLE_LIMIT));
  let sampled = 0;

  for (let index = 0; index < splatCount && sampled < CAMERA_SAMPLE_LIMIT; index += sampleStep) {
    splatBuffer.getSplatCenter(index, point);
    rotateMarblePointToZUp(point);
    if (Number.isFinite(point.x) && Number.isFinite(point.y) && Number.isFinite(point.z)) {
      min.min(point);
      max.max(point);
      sampled += 1;
    }
  }
  if (sampled === 0) return null;

  const size = max.clone().sub(min);
  const diagonal = size.length();
  if (!Number.isFinite(diagonal) || diagonal <= 0) return null;

  const sceneCenter = rotateMarblePointToZUp(splatBuffer.sceneCenter.clone());
  const inset = size.clone().multiplyScalar(0.04);
  const clampInside = (value: number, low: number, high: number, padding: number) => {
    const paddedLow = low + padding;
    const paddedHigh = high - padding;
    if (paddedLow > paddedHigh) return (low + high) / 2;
    return Math.min(paddedHigh, Math.max(paddedLow, value));
  };
  const position = new Vector3(
    clampInside(sceneCenter.x, min.x, max.x, inset.x),
    clampInside(sceneCenter.y, min.y, max.y, inset.y),
    clampInside(sceneCenter.z, min.z, max.z, inset.z),
  );

  // Marble exports use +Z as the capture-forward direction. Picking the
  // longest side of the room can select -Z by a small margin and turn the
  // initial view around by 180 degrees even though the camera is inside.
  const forwardClearance = max.y - position.y;
  const lookDistance = Math.max(MIN_LOOK_DISTANCE, forwardClearance * 0.65);
  const target = position.clone().add(new Vector3(0, lookDistance, 0));

  viewer.camera.position.copy(position);
  viewer.camera.up.set(0, 0, 1);
  viewer.camera.near = Math.max(0.001, diagonal / 10_000);
  viewer.camera.far = Math.max(50, diagonal * 4);
  viewer.camera.updateProjectionMatrix();
  viewer.controls.target.copy(target);
  viewer.controls.screenSpacePanning = false;
  viewer.controls.update();
  viewer.forceRenderNextFrame();

  return {
    min,
    max,
    position,
    target,
    moveStep: Math.min(0.25, Math.max(0.03, diagonal * 0.025)),
  };
}

/**
 * OrbitControls rotates the camera around a target and therefore changes the
 * camera coordinates while looking around. Marble worlds need first-person
 * navigation: pointer drag changes only local yaw/pitch, while keyboard
 * movement remains on the Z-up X-Y ground plane.
 */
function setupFirstPersonControls(
  viewer: Viewer,
  rootElement: HTMLDivElement,
  frame: InteriorFrame,
) {
  viewer.controls.enableRotate = false;
  viewer.controls.enablePan = false;
  viewer.controls.enableZoom = false;
  viewer.controls.enableDamping = false;
  viewer.controls.stopListenToKeyEvents();

  const forward = frame.target.clone().sub(frame.position).normalize();
  let yaw = Math.atan2(forward.x, forward.y);
  let pitch = Math.asin(Math.max(-1, Math.min(1, forward.z)));
  let draggingPointer: number | null = null;
  let lastPointerX = 0;
  let lastPointerY = 0;

  const applyOrientation = () => {
    const cosPitch = Math.cos(pitch);
    const direction = new Vector3(
      Math.sin(yaw) * cosPitch,
      Math.cos(yaw) * cosPitch,
      Math.sin(pitch),
    );
    const target = viewer.camera.position.clone().add(direction);
    viewer.camera.up.set(0, 0, 1);
    viewer.camera.lookAt(target);
    viewer.controls.target.copy(target);
    viewer.forceRenderNextFrame();
  };

  const onPointerDown = (event: PointerEvent) => {
    if (event.button !== 0) return;
    draggingPointer = event.pointerId;
    lastPointerX = event.clientX;
    lastPointerY = event.clientY;
    rootElement.focus();
    rootElement.setPointerCapture?.(event.pointerId);
    rootElement.style.cursor = "grabbing";
    event.preventDefault();
  };
  const onPointerMove = (event: PointerEvent) => {
    if (draggingPointer !== event.pointerId) return;
    const deltaX = event.clientX - lastPointerX;
    const deltaY = event.clientY - lastPointerY;
    lastPointerX = event.clientX;
    lastPointerY = event.clientY;
    yaw += deltaX * 0.003;
    pitch = Math.max(-Math.PI * 0.47, Math.min(Math.PI * 0.47, pitch - deltaY * 0.003));
    applyOrientation();
    event.preventDefault();
  };
  const stopDragging = (event: PointerEvent) => {
    if (draggingPointer !== event.pointerId) return;
    rootElement.releasePointerCapture?.(event.pointerId);
    draggingPointer = null;
    rootElement.style.cursor = "grab";
  };
  const onKeyDown = (event: KeyboardEvent) => {
    const forwardFlat = new Vector3(Math.sin(yaw), Math.cos(yaw), 0);
    const rightFlat = new Vector3(Math.cos(yaw), -Math.sin(yaw), 0);
    const movement = new Vector3();
    if (event.code === "KeyW" || event.code === "ArrowUp") movement.copy(forwardFlat);
    else if (event.code === "KeyS" || event.code === "ArrowDown") movement.copy(forwardFlat).negate();
    else if (event.code === "KeyA" || event.code === "ArrowLeft") movement.copy(rightFlat).negate();
    else if (event.code === "KeyD" || event.code === "ArrowRight") movement.copy(rightFlat);
    else return;

    const insetX = (frame.max.x - frame.min.x) * 0.04;
    const insetY = (frame.max.y - frame.min.y) * 0.04;
    const next = viewer.camera.position.clone().addScaledVector(movement, frame.moveStep);
    next.x = Math.min(frame.max.x - insetX, Math.max(frame.min.x + insetX, next.x));
    next.y = Math.min(frame.max.y - insetY, Math.max(frame.min.y + insetY, next.y));
    next.z = frame.position.z;
    viewer.camera.position.copy(next);
    applyOrientation();
    event.preventDefault();
    event.stopPropagation();
  };

  rootElement.tabIndex = 0;
  rootElement.setAttribute("aria-label", "3D 场景预览：拖动旋转视角，WASD 或方向键移动");
  rootElement.style.cursor = "grab";
  rootElement.style.touchAction = "none";
  rootElement.addEventListener("pointerdown", onPointerDown);
  rootElement.addEventListener("pointermove", onPointerMove);
  rootElement.addEventListener("pointerup", stopDragging);
  rootElement.addEventListener("pointercancel", stopDragging);
  rootElement.addEventListener("keydown", onKeyDown);

  return () => {
    rootElement.removeEventListener("pointerdown", onPointerDown);
    rootElement.removeEventListener("pointermove", onPointerMove);
    rootElement.removeEventListener("pointerup", stopDragging);
    rootElement.removeEventListener("pointercancel", stopDragging);
    rootElement.removeEventListener("keydown", onKeyDown);
    rootElement.removeAttribute("aria-label");
    rootElement.removeAttribute("tabindex");
    rootElement.style.removeProperty("cursor");
    rootElement.style.removeProperty("touch-action");
  };
}

/**
 * Downloads the SPZ/PLY file with a real progress bar (fetch + stream),
 * then renders it with gaussian-splats-3d.
 */
export function SplatViewer({ source, onError }: SplatViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [stage, setStage] = useState<"download" | "parse" | "ready" | "error">("download");

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const rootElement = container;

    let disposed = false;
    let objectUrl: string | null = null;
    let viewer: Viewer | null = null;
    let cleanupFirstPersonControls: (() => void) | null = null;

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

        // 2) Parse and render with the library's self-driven Viewer. Its own
        // renderer/controls lifecycle is required for the first SPZ sort.
        setStage("parse");
        setProgress(null);
        viewer = new Viewer({
          rootElement,
          cameraUp: [0, 0, 1],
          initialCameraPosition: [0, 0, 0],
          initialCameraLookAt: [0, 1, 0],
          gpuAcceleratedSort: false,
          sharedMemoryForWorkers: false,
          // Marble's current SPZ exports are 2D Gaussian surfels (the third
          // scale axis is quantized to zero for most splats). Rendering them
          // with the library's traditional 3D covariance path produces an
          // apparently successful but empty frame.
          splatRenderMode: SplatRenderMode.TwoD,
          sceneRevealMode: SceneRevealMode.Instant,
        });
        await viewer.addSplatScene(objectUrl, {
          format: SceneFormat.Spz,
          rotation: MARBLE_Z_UP_ROTATION,
          showLoadingUI: true,
          progressiveLoad: true,
        });
        if (!disposed) {
          const frame = frameSpzInterior(viewer);
          if (frame) cleanupFirstPersonControls = setupFirstPersonControls(viewer, rootElement, frame);
          viewer.start();
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

    void load();

    return () => {
      disposed = true;
      cleanupFirstPersonControls?.();
      if (viewer) {
        try {
          void viewer.dispose();
        } catch {
          /* ignore dispose errors */
        }
      }
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
