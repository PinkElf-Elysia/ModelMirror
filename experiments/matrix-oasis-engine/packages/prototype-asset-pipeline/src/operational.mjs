export class PrototypeAssetPipelineOperationalError extends Error {
  constructor() {
    super("PROTOTYPE_ASSET_PIPELINE_INTERNAL_ERROR");
    this.name = "PrototypeAssetPipelineOperationalError";
    this.code = "PROTOTYPE_ASSET_PIPELINE_INTERNAL_ERROR";
  }
}
