export class PrototypeSpatialEnvironmentOperationalError extends Error {
  constructor() {
    super("PROTOTYPE_SPATIAL_ENVIRONMENT_INTERNAL_ERROR");
    this.name = "PrototypeSpatialEnvironmentOperationalError";
    this.code = "PROTOTYPE_SPATIAL_ENVIRONMENT_INTERNAL_ERROR";
  }
}
