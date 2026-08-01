/**
 * Minimal type declarations for @mkkellogg/gaussian-splats-3d.
 * The library ships no types; this covers only what the project uses.
 */
declare module "@mkkellogg/gaussian-splats-3d" {
  export class DropInViewer {
    constructor(options?: {
      gpuAcceleratedSort?: boolean;
      sharedMemoryForWorkers?: boolean;
    });
    container: HTMLDivElement;
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
