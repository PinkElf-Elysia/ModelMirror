/**
 * Minimal type declarations for @mkkellogg/gaussian-splats-3d.
 * The library ships no types; this covers only what the project uses.
 */
declare module "@mkkellogg/gaussian-splats-3d" {
  import type { Object3D, PerspectiveCamera, Vector3 } from "three";

  export const SceneFormat: {
    Splat: number;
    KSplat: number;
    Ply: number;
    Spz: number;
  };

  export const SplatRenderMode: {
    ThreeD: number;
    TwoD: number;
  };

  export const SceneRevealMode: {
    Default: number;
    Gradual: number;
    Instant: number;
  };

  interface ViewerOptions {
    rootElement?: HTMLElement;
    cameraUp?: [number, number, number];
    initialCameraPosition?: [number, number, number];
    initialCameraLookAt?: [number, number, number];
    gpuAcceleratedSort?: boolean;
    sharedMemoryForWorkers?: boolean;
    splatRenderMode?: number;
    sceneRevealMode?: number;
  }

  interface AddSplatSceneOptions {
    format?: number;
    rotation?: [number, number, number, number];
    showLoadingUI?: boolean;
    progressiveLoad?: boolean;
    splatAlphaRemovalThreshold?: number;
  }

  export class Viewer {
    constructor(options?: ViewerOptions);
    camera: PerspectiveCamera;
    controls: {
      target: Vector3;
      screenSpacePanning: boolean;
      enableRotate: boolean;
      enablePan: boolean;
      enableZoom: boolean;
      enableDamping: boolean;
      stopListenToKeyEvents(): void;
      update(): void;
    };
    addSplatScene(path: string, options?: AddSplatSceneOptions): Promise<void>;
    getSplatScene(sceneIndex: number): Object3D & {
      splatBuffer: {
        sceneCenter: Vector3;
        getSplatCount(): number;
        getSplatCenter(
          splatIndex: number,
          outCenter: Vector3,
          transform?: unknown,
        ): Vector3;
      };
    };
    forceRenderNextFrame(): void;
    start(): void;
    dispose(): Promise<void>;
  }

  export class DropInViewer extends Object3D {
    constructor(options?: ViewerOptions);
    addSplatScene(path: string, options?: AddSplatSceneOptions): Promise<void>;
    getSplatScene(sceneIndex: number): Object3D & {
      splatBuffer: {
        sceneCenter: Vector3;
        getSplatCount(): number;
        getSplatCenter(
          splatIndex: number,
          outCenter: Vector3,
          transform?: unknown,
        ): Vector3;
      };
    };
    start(): void;
    dispose(): void;
  }
}
