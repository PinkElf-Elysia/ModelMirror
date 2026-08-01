/**
 * Minimal type declarations for @mkkellogg/gaussian-splats-3d.
 * The library ships no types; this covers only what the project uses.
 */
declare module "@mkkellogg/gaussian-splats-3d" {
  import type { Object3D } from "three";

  export class DropInViewer extends Object3D {
    constructor(options?: {
      gpuAcceleratedSort?: boolean;
      sharedMemoryForWorkers?: boolean;
    });
    addSplatScene(
      path: string,
      options?: {
        showLoadingUI?: boolean;
        progressiveLoad?: boolean;
        splatAlphaRemovalThreshold?: number;
      },
    ): Promise<void>;
    start(): void;
    dispose(): void;
  }
}
