import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Vector3 } from "three";
import { SplatViewer } from "./SplatViewer";

const addSplatScene = vi.fn(async () => undefined);
const start = vi.fn();
const dispose = vi.fn(async () => undefined);
const ViewerConstructor = vi.fn();
const cameraPositionCopy = vi.fn();
const cameraUpSet = vi.fn();
const cameraLookAt = vi.fn();
const cameraPosition = new Vector3();
const updateProjectionMatrix = vi.fn();
const controlsTargetCopy = vi.fn();
const controlsUpdate = vi.fn();
const forceRenderNextFrame = vi.fn();
const stopListenToKeyEvents = vi.fn();
const controls = {
  target: new Vector3(),
  screenSpacePanning: true,
  enableRotate: true,
  enablePan: true,
  enableZoom: true,
  enableDamping: true,
  stopListenToKeyEvents,
  update: controlsUpdate,
};

const sampledCenters = [
  [-1.5, -0.8, -3.2],
  [1.6, 0.6, 2.8],
  [-1.2, 0.4, 2.5],
  [1.3, -0.6, -3],
];

vi.mock("@mkkellogg/gaussian-splats-3d", () => ({
  Viewer: class {
    camera = {
      position: cameraPosition,
      up: { set: cameraUpSet },
      near: 0.1,
      far: 500,
      lookAt: cameraLookAt,
      updateProjectionMatrix,
    };
    controls = controls;
    constructor(options: unknown) {
      ViewerConstructor(options);
    }
    addSplatScene = addSplatScene;
    getSplatScene = () => ({
      splatBuffer: {
        sceneCenter: new Vector3(0, 0, 0),
        getSplatCount: () => sampledCenters.length,
        getSplatCenter: (index: number, point: { set: (x: number, y: number, z: number) => void }) => {
          const [x, y, z] = sampledCenters[index];
          point.set(x, y, z);
          return point;
        },
      },
    });
    forceRenderNextFrame = forceRenderNextFrame;
    start = start;
    dispose = dispose;
  },
  SceneFormat: { Spz: 3 },
  SceneRevealMode: { Default: 0, Gradual: 1, Instant: 2 },
  SplatRenderMode: { ThreeD: 0, TwoD: 1 },
}));

beforeEach(() => {
  addSplatScene.mockClear();
  start.mockClear();
  dispose.mockClear();
  ViewerConstructor.mockClear();
  cameraPositionCopy.mockClear();
  cameraUpSet.mockClear();
  updateProjectionMatrix.mockClear();
  controlsTargetCopy.mockClear();
  controlsUpdate.mockClear();
  forceRenderNextFrame.mockClear();
  cameraLookAt.mockClear();
  stopListenToKeyEvents.mockClear();
  cameraPosition.set(0, 0, 0);
  vi.spyOn(cameraPosition, "copy").mockImplementation((value) => {
    cameraPositionCopy(value);
    return cameraPosition.set(value.x, value.y, value.z);
  });
  vi.spyOn(controls.target, "copy").mockImplementation((value) => {
    controlsTargetCopy(value);
    return controls.target.set(value.x, value.y, value.z);
  });
  controls.enableRotate = true;
  controls.enablePan = true;
  controls.enableZoom = true;
  controls.enableDamping = true;
  vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array([1, 2, 3]))));
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:http://localhost/scene"),
    revokeObjectURL: vi.fn(),
  });
});

describe("SplatViewer", () => {
  it("uses the self-driven 2D viewer and explicit SPZ format", async () => {
    const rendered = render(<SplatViewer source="https://assets.example/world.spz" />);

    await waitFor(() => expect(start).toHaveBeenCalledOnce());
    expect(ViewerConstructor).toHaveBeenCalledWith(
      expect.objectContaining({
        gpuAcceleratedSort: false,
        sceneRevealMode: 2,
        splatRenderMode: 1,
      }),
    );
    expect(addSplatScene).toHaveBeenCalledWith(
      "blob:http://localhost/scene",
      expect.objectContaining({
        format: 3,
        rotation: [-Math.SQRT1_2, 0, 0, Math.SQRT1_2],
      }),
    );
    expect(ViewerConstructor).toHaveBeenCalledWith(
      expect.objectContaining({
        initialCameraPosition: [0, 0, 0],
        initialCameraLookAt: [0, 1, 0],
        cameraUp: [0, 0, 1],
      }),
    );
    expect(cameraPositionCopy).toHaveBeenCalledOnce();
    expect(controlsTargetCopy).toHaveBeenCalledOnce();
    expect(controlsUpdate).toHaveBeenCalledOnce();
    expect(forceRenderNextFrame).toHaveBeenCalledOnce();

    const position = cameraPositionCopy.mock.calls[0][0];
    const target = controlsTargetCopy.mock.calls[0][0];
    expect(position.x).toBeGreaterThan(-1.5);
    expect(position.x).toBeLessThan(1.6);
    expect(position.z).toBeGreaterThan(-3.2);
    expect(position.z).toBeLessThan(2.8);
    expect(target.y).toBeGreaterThan(position.y);
    expect(cameraUpSet).toHaveBeenCalledWith(0, 0, 1);

    const viewport = rendered.getByLabelText("3D 场景预览：拖动旋转视角，WASD 或方向键移动");
    const positionBeforeRotate = cameraPosition.clone();
    fireEvent.pointerDown(viewport, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(viewport, { pointerId: 1, clientX: 140, clientY: 80 });
    fireEvent.pointerUp(viewport, { pointerId: 1 });
    expect(cameraLookAt).toHaveBeenCalled();
    expect(cameraPosition.toArray()).toEqual(positionBeforeRotate.toArray());

    const zBeforeMove = cameraPosition.z;
    fireEvent.keyDown(viewport, { code: "KeyW" });
    expect(cameraPosition.y).not.toBe(positionBeforeRotate.y);
    expect(cameraPosition.z).toBe(zBeforeMove);
    expect(stopListenToKeyEvents).toHaveBeenCalledOnce();
    expect(controls.enableRotate).toBe(false);
    expect(controls.enablePan).toBe(false);
    expect(controls.enableZoom).toBe(false);
  });
});
