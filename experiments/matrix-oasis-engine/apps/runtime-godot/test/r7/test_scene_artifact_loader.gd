class_name MatrixOasisSceneArtifactLoaderTest
extends GdUnitTestSuite

const FIXTURE_ROOT := "res://scene-fixtures/kenney-prototype"
const ASSETS := [
	{"id": "crate", "path": "assets/crate.glb", "byteLength": 18064, "sha256": "7dec224fbdd2297524c56fe3b4fa79fe6c5854f4b699a9e2e2c21ce6f008738c"},
	{"id": "figurine", "path": "assets/figurine.glb", "byteLength": 118936, "sha256": "ae0ea82089e66215684b0b2f5a162be9f6c71475085c81c3b80e53abd08b6bd8"},
	{"id": "floor", "path": "assets/floor-square.glb", "byteLength": 2340, "sha256": "873232210ff286b26bb6bfc371d3c6c96479a5b667f2927de3bcf06b1114d5af"},
	{"id": "wall", "path": "assets/wall.glb", "byteLength": 2848, "sha256": "538dd97f85473999e1e9fe4758dc48daa85a7eed0be50b30c004702ab848f36c"},
]


func test_four_locked_glbs_materialize_visuals_and_explicit_collider_faces() -> void:
	var prepared_runtime := _prepared_runtime()
	var scene := _scene_pack(prepared_runtime)
	var bytes := JSON.stringify(scene, "", true).to_utf8_buffer()
	var result := MatrixOasisSceneArtifactLoader.prepare_from_bytes(
		bytes,
		ProjectSettings.globalize_path(FIXTURE_ROOT),
		prepared_runtime,
	)
	var failure_code: String = "R7_LOADER_DIAGNOSTIC_MISSING" if result["ok"] else result["diagnostics"][0]["code"]
	assert_bool(result["ok"]).append_failure_message(failure_code).is_true()
	if not result["ok"]:
		return
	var prepared: MatrixOasisPreparedScene = result["prepared"]
	assert_int(prepared.manifest_sha256().length()).is_equal(64)
	assert_str(prepared.runtime_artifact_sha256()).is_equal(prepared_runtime.artifact_sha256())
	assert_int(prepared._asset_ids_for_runtime().size()).is_equal(4)
	for asset in ASSETS:
		var visual: Node = auto_free(prepared._instantiate_visual_for_runtime(asset["id"]))
		assert_object(visual).is_not_null()
		assert_bool(visual.find_children("*", "AnimationPlayer", true, false).is_empty()).is_true()
		assert_int(prepared._collider_faces_for_runtime(asset["id"]).size()).is_greater(0)


func test_locked_figurine_animations_are_removed_before_gltf_materialization() -> void:
	var bytes := FileAccess.get_file_as_bytes("%s/assets/figurine.glb" % FIXTURE_ROOT)
	assert_str(MatrixOasisSceneGlbRules.inspect(bytes)["code"]).is_equal("SCENE_PACK_GLB_FEATURE_UNSUPPORTED")
	assert_str(MatrixOasisSceneGlbRules.inspect(bytes, "Textures/colormap.png")["code"]).is_equal("SCENE_PACK_GLB_FEATURE_UNSUPPORTED")
	assert_bool(MatrixOasisSceneGlbRules.inspect(bytes, "Textures/colormap.png", true)["ok"]).is_true()
	var sanitized := MatrixOasisSceneGlbRules.without_animations(bytes)
	assert_int(sanitized.size()).is_greater(0)
	assert_bool(MatrixOasisSceneGlbRules.inspect(sanitized, "Textures/colormap.png")["ok"]).is_true()


func test_scene_identity_and_asset_hash_fail_before_exposing_a_prepared_scene() -> void:
	var prepared_runtime := _prepared_runtime()
	var scene := _scene_pack(prepared_runtime)
	scene["runtimeIdentity"]["artifactSha256"] = "0".repeat(64)
	var mismatch := MatrixOasisSceneArtifactLoader.prepare_from_bytes(
		JSON.stringify(scene, "", true).to_utf8_buffer(),
		ProjectSettings.globalize_path(FIXTURE_ROOT),
		prepared_runtime,
	)
	assert_bool(mismatch["ok"]).is_false()
	assert_str(mismatch["diagnostics"][0]["code"]).is_equal("PACK_GODOT_SCENE_IDENTITY_MISMATCH")
	assert_bool(mismatch.has("prepared")).is_false()
	scene = _scene_pack(prepared_runtime)
	scene["assets"][0]["sha256"] = "0".repeat(64)
	var damaged := MatrixOasisSceneArtifactLoader.prepare_from_bytes(
		JSON.stringify(scene, "", true).to_utf8_buffer(),
		ProjectSettings.globalize_path(FIXTURE_ROOT),
		prepared_runtime,
	)
	assert_bool(damaged["ok"]).is_false()
	assert_str(damaged["diagnostics"][0]["code"]).is_equal("PACK_GODOT_SCENE_INTEGRITY_MISMATCH")
	assert_bool(damaged.has("prepared")).is_false()


func test_glb_header_and_scene_references_fail_with_static_diagnostics() -> void:
	var invalid := PackedByteArray([0, 1, 2, 3])
	var inspected := MatrixOasisSceneGlbRules.inspect(invalid)
	assert_bool(inspected["ok"]).is_false()
	assert_str(inspected["code"]).is_equal("SCENE_PACK_GLB_INVALID")
	var prepared_runtime := _prepared_runtime()
	var scene := _scene_pack(prepared_runtime)
	scene["placements"][0]["visualAssetId"] = "missing"
	var result := MatrixOasisScenePackRules.validate(
		scene,
		prepared_runtime._copy_pack_for_runtime(),
		prepared_runtime.artifact_sha256(),
	)
	assert_bool(result["ok"]).is_false()
	assert_str(result["diagnostics"][0]["code"]).is_equal("PACK_GODOT_SCENE_REFERENCE_INVALID")


func _scene_pack(prepared_runtime: MatrixOasisPreparedRuntimePack) -> Dictionary:
	var runtime := prepared_runtime._copy_pack_for_runtime()
	var assets: Array = []
	var placements: Array = []
	for index in ASSETS.size():
		var source: Dictionary = ASSETS[index]
		assets.append({"byteLength": source["byteLength"], "format": "glb", "id": source["id"], "path": source["path"], "roles": ["visual", "collider"], "sha256": source["sha256"]})
		placements.append({"colliderAssetId": source["id"], "entityId": null, "id": "placement-%s" % source["id"], "transform": {"positionMm": [index * 2000, 0, 0], "rotationMilliDegrees": [0, 0, 0], "scalePermille": [1000, 1000, 1000]}, "visualAssetId": source["id"]})
	return {
		"assets": assets,
		"canonicalization": "matrix-oasis.canonical-json/1",
		"format": "matrix-oasis.scene-pack",
		"formatVersion": "0.1.0",
		"nodeBindings": [{"actionAnchor": {"positionMm": [0, 0, -2000], "yawMilliDegrees": 0}, "nodeId": "node-start", "playerSpawn": {"positionMm": [0, 1000, 0], "yawMilliDegrees": 0}, "visiblePlacementIds": placements.map(func(item: Dictionary) -> String: return item["id"])}],
		"placements": placements,
		"runtimeIdentity": {"artifactSha256": prepared_runtime.artifact_sha256(), "packContentVersion": runtime["source"]["contentVersion"], "packId": runtime["source"]["id"], "runtimeFormat": runtime["format"], "runtimeFormatVersion": runtime["formatVersion"], "sourceCanonicalSha256": runtime["source"]["canonicalSha256"]},
		"scene": {"contentVersion": "1", "id": "kenney-loader-fixture", "title": "Kenney loader fixture"},
	}


func _prepared_runtime() -> MatrixOasisPreparedRuntimePack:
	var pack := _runtime_pack()
	var runtime_bytes := JSON.stringify(pack, "", true).to_utf8_buffer()
	var receipt := {"artifact": {"byteLength": runtime_bytes.size(), "format": "matrix-oasis.runtime-game-pack", "formatVersion": "0.1.0", "sha256": MatrixOasisRuntimeArtifactLoader._sha256_hex(runtime_bytes)}, "canonicalization": "matrix-oasis.canonical-json/1", "compiler": {"id": "@matrix-oasis/game-pack-compiler", "version": "0.1.0-r3"}, "format": "matrix-oasis.runtime-game-pack-receipt", "formatVersion": "0.1.0"}
	var result := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(runtime_bytes, JSON.stringify(receipt, "", true).to_utf8_buffer())
	assert_bool(result["ok"]).is_true()
	return result["prepared"]


func _runtime_pack() -> Dictionary:
	return {"canonicalization": "matrix-oasis.canonical-json/1", "cues": [], "endings": [{"cueIndexes": [], "id": "ending-done", "text": null, "title": "Done"}], "entities": [], "entryNodeIndex": 0, "format": "matrix-oasis.runtime-game-pack", "formatVersion": "0.1.0", "language": "en", "nodes": [{"actions": [{"effects": [], "entityIndexes": [], "id": "finish", "label": "Finish", "target": {"index": 0, "kind": "ending"}, "when": null}], "entityIndexes": [], "entryCueIndexes": [], "id": "node-start", "text": null, "title": "Start"}], "source": {"canonicalSha256": "1".repeat(64), "contentVersion": "1", "format": "matrix-oasis.authoring-game-pack", "formatVersion": "0.1.0", "id": "kenney-loader-fixture"}, "summary": null, "title": "Kenney loader fixture", "variables": []}
