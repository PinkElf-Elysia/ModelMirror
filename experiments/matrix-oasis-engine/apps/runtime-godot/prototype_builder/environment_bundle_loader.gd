class_name MatrixOasisPrototypeEnvironmentLoader
extends RefCounted

const BUNDLE_MAX_BYTES := 256 * 1024
const PANORAMA_MAX_BYTES := 64 * 1024 * 1024
const COLLIDER_MAX_BYTES := 32 * 1024 * 1024
const PATH_SEPARATOR := "\u002f"
const DIAGNOSTIC_PATH := "/runtime"


static func load_from_path(bundle_path: String, prepared_scene: MatrixOasisPreparedScene) -> Dictionary:
	if prepared_scene == null or bundle_path.is_empty() or not bundle_path.is_absolute_path() or _path_has_link(bundle_path):
		return _failure("PACK_GODOT_PROTOTYPE_ENVIRONMENT_INPUT_INVALID")
	var bundle_read := MatrixOasisSceneArtifactLoader._read_file(bundle_path, BUNDLE_MAX_BYTES)
	if not bundle_read["ok"]:
		return _failure("PACK_GODOT_PROTOTYPE_ENVIRONMENT_INPUT_INVALID")
	var parsed := MatrixOasisStrictJson.parse_bytes(bundle_read["bytes"], DIAGNOSTIC_PATH)
	if not parsed["ok"]:
		return _failure("PACK_GODOT_PROTOTYPE_ENVIRONMENT_JSON_INVALID")
	var bundle: Dictionary = parsed["value"]
	var scene_pack := prepared_scene._copy_scene_for_runtime()
	if not _valid_bundle(bundle, scene_pack):
		return _failure("PACK_GODOT_PROTOTYPE_ENVIRONMENT_SCHEMA_INVALID")
	var directory := bundle_path.get_base_dir()
	var panorama_path := directory.path_join(bundle["assets"]["panorama"]["path"]).simplify_path()
	var collider_path := directory.path_join(bundle["assets"]["collider"]["path"]).simplify_path()
	if not _is_contained(directory, panorama_path) or not _is_contained(directory, collider_path) or _path_has_link(panorama_path) or _path_has_link(collider_path):
		return _failure("PACK_GODOT_PROTOTYPE_ENVIRONMENT_ASSET_INVALID")
	var panorama_read := MatrixOasisSceneArtifactLoader._read_file(panorama_path, PANORAMA_MAX_BYTES)
	var collider_read := MatrixOasisSceneArtifactLoader._read_file(collider_path, COLLIDER_MAX_BYTES)
	if not panorama_read["ok"] or not collider_read["ok"]:
		return _failure("PACK_GODOT_PROTOTYPE_ENVIRONMENT_ASSET_INVALID")
	var panorama: Dictionary = bundle["assets"]["panorama"]
	var collider: Dictionary = bundle["assets"]["collider"]
	if panorama_read["bytes"].size() != panorama["byteLength"] or collider_read["bytes"].size() != collider["byteLength"]:
		return _failure("PACK_GODOT_PROTOTYPE_ENVIRONMENT_INTEGRITY_MISMATCH")
	if _sha256(panorama_read["bytes"]) != panorama["sha256"] or _sha256(collider_read["bytes"]) != collider["sha256"]:
		return _failure("PACK_GODOT_PROTOTYPE_ENVIRONMENT_INTEGRITY_MISMATCH")
	var image := Image.new()
	if image.load_png_from_buffer(panorama_read["bytes"]) != OK or image.get_width() != panorama["width"] or image.get_height() != panorama["height"] or image.get_width() != image.get_height() * 2:
		return _failure("PACK_GODOT_PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID")
	var result := {
		"ok": true,
		"image": image,
		"environment_placement_id": "r10-environment",
	}
	result.make_read_only()
	return result


static func _valid_bundle(bundle: Dictionary, scene_pack: Dictionary) -> bool:
	if not _exact(bundle, ["assets", "blueprint", "canonicalization", "format", "formatVersion", "provider", "scene"]):
		return false
	if bundle["format"] != "matrix-oasis.prototype-environment-bundle" or bundle["formatVersion"] != "0.1.0" or bundle["canonicalization"] != "matrix-oasis.canonical-json/1":
		return false
	if not _valid_scene(bundle["scene"], scene_pack.get("scene")) or not _valid_blueprint(bundle["blueprint"]) or not _valid_provider(bundle["provider"]):
		return false
	if not _exact(bundle["assets"], ["collider", "panorama"]):
		return false
	var panorama: Variant = bundle["assets"]["panorama"]
	var collider: Variant = bundle["assets"]["collider"]
	if not _valid_panorama(panorama) or not _valid_collider(collider):
		return false
	if typeof(scene_pack.get("assets")) != TYPE_ARRAY or typeof(scene_pack.get("placements")) != TYPE_ARRAY:
		return false
	var environment_assets: Array = scene_pack["assets"].filter(func(asset: Variant) -> bool:
		return typeof(asset) == TYPE_DICTIONARY and asset.get("id") == "r10-environment-collider"
	)
	var environment_placements: Array = scene_pack["placements"].filter(func(placement: Variant) -> bool:
		return typeof(placement) == TYPE_DICTIONARY and placement.get("id") == "r10-environment"
	)
	if environment_assets.size() != 1 or environment_placements.size() != 1:
		return false
	var scene_asset: Dictionary = environment_assets[0]
	var scene_placement: Dictionary = environment_placements[0]
	return scene_asset.get("path") == collider["path"] and scene_asset.get("byteLength") == collider["byteLength"] and (
		"sha256:" + str(scene_asset.get("sha256")) == collider["sha256"]
	) and scene_asset.get("roles") == ["visual", "collider"] and (
		scene_placement.get("visualAssetId") == "r10-environment-collider" and scene_placement.get("colliderAssetId") == "r10-environment-collider"
	)


static func _valid_scene(value: Variant, expected: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and typeof(expected) == TYPE_DICTIONARY and _exact(value, ["contentVersion", "id", "title"]) and value == expected


static func _valid_blueprint(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["canonicalSha256", "format", "formatVersion"]) and value["format"] == "matrix-oasis.scene-blueprint" and value["formatVersion"] == "0.1.0" and _hash(value["canonicalSha256"])


static func _valid_provider(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["environmentPromptSha256", "id", "model"]) and value["id"] == "world-labs-marble" and value["model"] == "marble-1.1" and _hash(value["environmentPromptSha256"])


static func _valid_panorama(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["byteLength", "format", "height", "path", "sha256", "width"]) and value["path"] == "assets/environment-panorama.png" and value["format"] == "png" and _bounded_integer(value["width"], 2, 16384) and _bounded_integer(value["height"], 1, 8192) and value["width"] == value["height"] * 2 and _bounded_integer(value["byteLength"], 1, PANORAMA_MAX_BYTES) and _hash(value["sha256"])


static func _valid_collider(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["byteLength", "format", "metrics", "path", "sha256"]) or value["path"] != "assets/environment-collider.glb" or value["format"] != "glb" or not _bounded_integer(value["byteLength"], 1, COLLIDER_MAX_BYTES) or not _hash(value["sha256"]):
		return false
	var metrics: Variant = value["metrics"]
	return typeof(metrics) == TYPE_DICTIONARY and _exact(metrics, ["meshCount", "nodeCount", "surfaceCount", "triangleCount"]) and _bounded_integer(metrics["nodeCount"], 0, 256) and _bounded_integer(metrics["meshCount"], 0, 64) and _bounded_integer(metrics["surfaceCount"], 0, 128) and _bounded_integer(metrics["triangleCount"], 0, 250000)


static func _exact(value: Dictionary, names: Array) -> bool:
	var keys := value.keys()
	keys.sort_custom(MatrixOasisStrictJson.compare_utf16)
	var expected := names.duplicate()
	expected.sort_custom(MatrixOasisStrictJson.compare_utf16)
	return keys == expected


static func _bounded_integer(value: Variant, minimum: int, maximum: int) -> bool:
	return typeof(value) == TYPE_INT and value >= minimum and value <= maximum


static func _hash(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or value.length() != 71 or not value.begins_with("sha256:"):
		return false
	for index in range(7, value.length()):
		var code: int = value.unicode_at(index)
		if not (code >= 0x30 and code <= 0x39) and not (code >= 0x61 and code <= 0x66):
			return false
	return true


static func _sha256(bytes: PackedByteArray) -> String:
	var context := HashingContext.new()
	if context.start(HashingContext.HASH_SHA256) != OK or context.update(bytes) != OK:
		return ""
	return "sha256:" + context.finish().hex_encode()


static func _path_has_link(path: String) -> bool:
	var current := path
	while not current.is_empty():
		var parent := current.get_base_dir()
		if parent == current:
			break
		var directory := DirAccess.open(parent)
		if directory == null or directory.is_link(current.get_file()):
			return true
		current = parent
	return false


static func _is_contained(root: String, target: String) -> bool:
	var normalized_root := root.replace("\\", PATH_SEPARATOR).simplify_path().trim_suffix(PATH_SEPARATOR)
	var normalized_target := target.replace("\\", PATH_SEPARATOR).simplify_path()
	return normalized_target.begins_with(normalized_root + PATH_SEPARATOR)


static func _failure(code: String) -> Dictionary:
	var diagnostic := {"phase": "runtime", "severity": "error", "code": code, "path": DIAGNOSTIC_PATH, "message": code}
	diagnostic.make_read_only()
	var diagnostics := [diagnostic]
	diagnostics.make_read_only()
	var result := {"ok": false, "diagnostics": diagnostics}
	result.make_read_only()
	return result
