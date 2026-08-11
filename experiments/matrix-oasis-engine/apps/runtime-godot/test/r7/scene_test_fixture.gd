class_name MatrixOasisSceneTestFixture
extends RefCounted

const FIXTURE_ROOT := "res://scene-fixtures/kenney-prototype"
const ASSETS := [
	{"id": "floor", "path": "assets/floor-square.glb", "byteLength": 2340, "sha256": "873232210ff286b26bb6bfc371d3c6c96479a5b667f2927de3bcf06b1114d5af"},
	{"id": "crate", "path": "assets/crate.glb", "byteLength": 18064, "sha256": "7dec224fbdd2297524c56fe3b4fa79fe6c5854f4b699a9e2e2c21ce6f008738c"},
	{"id": "figurine", "path": "assets/figurine.glb", "byteLength": 118936, "sha256": "ae0ea82089e66215684b0b2f5a162be9f6c71475085c81c3b80e53abd08b6bd8"},
]


static func prepared_pair() -> Dictionary:
	var pack := runtime_pack()
	var runtime_bytes := JSON.stringify(pack, "", true).to_utf8_buffer()
	var receipt := {"artifact": {"byteLength": runtime_bytes.size(), "format": "matrix-oasis.runtime-game-pack", "formatVersion": "0.1.0", "sha256": MatrixOasisRuntimeArtifactLoader._sha256_hex(runtime_bytes)}, "canonicalization": "matrix-oasis.canonical-json/1", "compiler": {"id": "@matrix-oasis/game-pack-compiler", "version": "0.1.0-r3"}, "format": "matrix-oasis.runtime-game-pack-receipt", "formatVersion": "0.1.0"}
	var runtime_result := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(runtime_bytes, JSON.stringify(receipt, "", true).to_utf8_buffer())
	if not runtime_result["ok"]:
		return {}
	var runtime: MatrixOasisPreparedRuntimePack = runtime_result["prepared"]
	var scene_result := MatrixOasisSceneArtifactLoader.prepare_from_bytes(
		JSON.stringify(scene_pack(runtime), "", true).to_utf8_buffer(),
		ProjectSettings.globalize_path(FIXTURE_ROOT),
		runtime,
	)
	if not scene_result["ok"]:
		return {}
	return {"runtime": runtime, "scene": scene_result["prepared"]}


static func scene_pack(runtime: MatrixOasisPreparedRuntimePack) -> Dictionary:
	var pack := runtime._copy_pack_for_runtime()
	var assets: Array = []
	for asset in ASSETS:
		assets.append({"byteLength": asset["byteLength"], "format": "glb", "id": asset["id"], "path": asset["path"], "roles": ["visual", "collider"], "sha256": asset["sha256"]})
	return {
		"assets": assets,
		"canonicalization": "matrix-oasis.canonical-json/1",
		"format": "matrix-oasis.scene-pack",
		"formatVersion": "0.1.0",
		"nodeBindings": [
			{"actionAnchor": {"positionMm": [0, 0, 1000], "yawMilliDegrees": 0}, "nodeId": "node-start", "playerSpawn": {"positionMm": [0, 1000, 4000], "yawMilliDegrees": 0}, "visiblePlacementIds": ["placement-floor", "placement-crate"]},
			{"actionAnchor": {"positionMm": [-1000, 0, 0], "yawMilliDegrees": 90000}, "nodeId": "node-next", "playerSpawn": {"positionMm": [2000, 1000, 3000], "yawMilliDegrees": -90000}, "visiblePlacementIds": ["placement-floor", "placement-figurine"]},
		],
		"placements": [
			{"colliderAssetId": "floor", "entityId": null, "id": "placement-floor", "transform": {"positionMm": [0, 0, 0], "rotationMilliDegrees": [0, 0, 0], "scalePermille": [12000, 12000, 12000]}, "visualAssetId": "floor"},
			{"colliderAssetId": "crate", "entityId": null, "id": "placement-crate", "transform": {"positionMm": [2000, 0, 0], "rotationMilliDegrees": [0, 0, 0], "scalePermille": [2000, 2000, 2000]}, "visualAssetId": "crate"},
			{"colliderAssetId": "figurine", "entityId": null, "id": "placement-figurine", "transform": {"positionMm": [-2000, 0, 0], "rotationMilliDegrees": [0, 0, 0], "scalePermille": [3000, 3000, 3000]}, "visualAssetId": "figurine"},
		],
		"runtimeIdentity": {"artifactSha256": runtime.artifact_sha256(), "packContentVersion": pack["source"]["contentVersion"], "packId": pack["source"]["id"], "runtimeFormat": pack["format"], "runtimeFormatVersion": pack["formatVersion"], "sourceCanonicalSha256": pack["source"]["canonicalSha256"]},
		"scene": {"contentVersion": "1", "id": "scene-lab-fixture", "title": "Scene lab fixture"},
	}


static func runtime_pack() -> Dictionary:
	return {
		"canonicalization": "matrix-oasis.canonical-json/1",
		"cues": [{"channel": "visual", "id": "cue-entry", "intent": "Show entry."}],
		"endings": [{"cueIndexes": [], "id": "ending-done", "text": "Fixture complete.", "title": "Done"}],
		"entities": [],
		"entryNodeIndex": 0,
		"format": "matrix-oasis.runtime-game-pack",
		"formatVersion": "0.1.0",
		"language": "en",
		"nodes": [
			{"actions": [{"effects": [], "entityIndexes": [], "id": "go-next", "label": "Go next", "target": {"index": 1, "kind": "node"}, "when": null}], "entityIndexes": [], "entryCueIndexes": [0], "id": "node-start", "text": "Start text.", "title": "Start"},
			{"actions": [{"effects": [], "entityIndexes": [], "id": "finish", "label": "Finish", "target": {"index": 0, "kind": "ending"}, "when": null}], "entityIndexes": [], "entryCueIndexes": [], "id": "node-next", "text": "Next text.", "title": "Next"},
		],
		"source": {"canonicalSha256": "1".repeat(64), "contentVersion": "1", "format": "matrix-oasis.authoring-game-pack", "formatVersion": "0.1.0", "id": "scene-lab-fixture"},
		"summary": null,
		"title": "Scene lab fixture",
		"variables": [],
	}
