import path from "node:path";

export const SCENE_EXAMPLES = Object.freeze([
  "mechanics-conformance",
  "last-train-r1",
]);
export const SCENE_READY_MARKER = "MATRIX_OASIS_R7_SCENE_BINDING_READY";
export const SCENE_SMOKE_ARGUMENT = "--matrix-oasis-scene-smoke";

export const SCENE_ASSETS = Object.freeze([
  Object.freeze({id: "kenney-floor", roles: Object.freeze(["visual", "collider"]), path: "assets/floor-square.glb", format: "glb", byteLength: 2340, sha256: "873232210ff286b26bb6bfc371d3c6c96479a5b667f2927de3bcf06b1114d5af"}),
  Object.freeze({id: "kenney-wall", roles: Object.freeze(["visual", "collider"]), path: "assets/wall.glb", format: "glb", byteLength: 2848, sha256: "538dd97f85473999e1e9fe4758dc48daa85a7eed0be50b30c004702ab848f36c"}),
  Object.freeze({id: "kenney-crate", roles: Object.freeze(["visual", "collider"]), path: "assets/crate.glb", format: "glb", byteLength: 18064, sha256: "7dec224fbdd2297524c56fe3b4fa79fe6c5854f4b699a9e2e2c21ce6f008738c"}),
  Object.freeze({id: "kenney-figurine", roles: Object.freeze(["visual", "collider"]), path: "assets/figurine.glb", format: "glb", byteLength: 118936, sha256: "ae0ea82089e66215684b0b2f5a162be9f6c71475085c81c3b80e53abd08b6bd8"}),
]);

export class GodotSceneHarnessError extends Error {
  constructor(code) {
    super(code);
    this.name = "GodotSceneHarnessError";
    this.code = code;
  }
}

function fail(code) {
  throw new GodotSceneHarnessError(code);
}

function freezeJson(value) {
  if (Array.isArray(value)) {
    value.forEach(freezeJson);
    return Object.freeze(value);
  }
  if (value && typeof value === "object") {
    Object.values(value).forEach(freezeJson);
    return Object.freeze(value);
  }
  return value;
}

export function parseSceneExampleArguments(args) {
  if (!Array.isArray(args) || args.length !== 2 || args[0] !== "--example" ||
      !SCENE_EXAMPLES.includes(args[1])) {
    fail("GODOT_SCENE_ARGUMENT_ERROR");
  }
  return args[1];
}

export function sceneGodotArguments({projectRoot, runtimePath, receiptPath, scenePath, smoke = false}) {
  if (![projectRoot, runtimePath, receiptPath, scenePath].every((value) =>
    typeof value === "string" && path.isAbsolute(value) && !value.includes("\0")) ||
      typeof smoke !== "boolean") {
    fail("GODOT_SCENE_PATH_INVALID");
  }
  return Object.freeze([
    ...(smoke ? ["--headless"] : []),
    "--path",
    projectRoot,
    "res://scene_binding/scene_lab.tscn",
    "--",
    `--matrix-oasis-runtime-pack=${runtimePath}`,
    `--matrix-oasis-runtime-receipt=${receiptPath}`,
    `--matrix-oasis-scene-pack=${scenePath}`,
    ...(smoke ? [SCENE_SMOKE_ARGUMENT] : []),
  ]);
}

function placement({id, asset, position, rotation = [0, 0, 0], scale = 1000}) {
  return {
    id,
    visualAssetId: asset,
    colliderAssetId: asset,
    entityId: null,
    transform: {
      positionMm: position,
      rotationMilliDegrees: rotation,
      scalePermille: [scale, scale, scale],
    },
  };
}

export function buildScenePack({example, runtimePack, receipt}) {
  if (!SCENE_EXAMPLES.includes(example) || !runtimePack || !receipt ||
      !Array.isArray(runtimePack.nodes) || runtimePack.nodes.length < 1 ||
      typeof runtimePack.source?.id !== "string" ||
      typeof receipt.artifact?.sha256 !== "string") {
    fail("GODOT_SCENE_INPUT_INVALID");
  }
  const placements = [
    placement({id: "scene-floor", asset: "kenney-floor", position: [0, 0, -6000], scale: 30000}),
    placement({id: "scene-crate", asset: "kenney-crate", position: [4000, 0, -2000], scale: 3000}),
    placement({id: "scene-figurine", asset: "kenney-figurine", position: [-4000, 0, -2000], scale: 3000}),
  ];
  for (let index = 0; index < 5; index += 1) {
    placements.push(placement({
      id: `scene-wall-north-${index}`,
      asset: "kenney-wall",
      position: [-12000 + index * 6000, 0, -20000],
      rotation: [0, 90000, 0],
      scale: 6000,
    }));
  }
  for (const side of [-1, 1]) {
    const sideName = side < 0 ? "west" : "east";
    for (let index = 0; index < 5; index += 1) {
      placements.push(placement({
        id: `scene-wall-${sideName}-${index}`,
        asset: "kenney-wall",
        position: [side * 15000, 0, -17000 + index * 6000],
        scale: 6000,
      }));
    }
  }
  const environmentPlacements = placements
    .map(({id}) => id)
    .filter((id) => id !== "scene-crate" && id !== "scene-figurine");
  const nodeBindings = runtimePack.nodes.map((node, index) => ({
    nodeId: node.id,
    playerSpawn: {
      positionMm: [((index % 3) - 1) * 1500, 1000, 5000],
      yawMilliDegrees: 0,
    },
    actionAnchor: {
      positionMm: [0, 0, 2000],
      yawMilliDegrees: 0,
    },
    visiblePlacementIds: [
      ...environmentPlacements,
      index % 2 === 0 ? "scene-crate" : "scene-figurine",
    ],
  }));
  return freezeJson({
    format: "matrix-oasis.scene-pack",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    scene: {
      id: `r7-${example}-scene`,
      contentVersion: "0.1.0",
      title: `${runtimePack.title} · Local Scene`,
    },
    runtimeIdentity: {
      runtimeFormat: runtimePack.format,
      runtimeFormatVersion: runtimePack.formatVersion,
      packId: runtimePack.source.id,
      packContentVersion: runtimePack.source.contentVersion,
      sourceCanonicalSha256: runtimePack.source.canonicalSha256,
      artifactSha256: receipt.artifact.sha256,
    },
    assets: SCENE_ASSETS.map((asset) => ({...asset, roles: [...asset.roles]})),
    placements,
    nodeBindings,
  });
}
