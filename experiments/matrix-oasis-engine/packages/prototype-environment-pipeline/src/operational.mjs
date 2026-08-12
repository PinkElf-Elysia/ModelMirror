export class PrototypeEnvironmentPipelineOperationalError extends Error {
  constructor() {
    super("PROTOTYPE_ENVIRONMENT_PIPELINE_INTERNAL_ERROR");
    this.name = "PrototypeEnvironmentPipelineOperationalError";
    this.code = "PROTOTYPE_ENVIRONMENT_PIPELINE_INTERNAL_ERROR";
  }
}
