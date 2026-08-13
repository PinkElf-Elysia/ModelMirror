class_name MatrixOasisSpatialAssemblyLoader
extends RefCounted

const ASSEMBLY_MAX_BYTES := 256 * 1024
const SPLAT_MAX_BYTES := 96 * 1024 * 1024
const PATH_SEPARATOR := "\u002f"
const DIAGNOSTIC_PATH := "/spatialAssembly"
const SPLAT_RELATIVE_PATH := "assets/environment.compressed.ply"
const COLLIDER_RELATIVE_PATH := "assets/environment-collider.glb"
const IMPORTED_RESOURCE_PATH := "res://spatial_run/assets/environment.compressed.ply"


static func load_from_path(
	assembly_path: String,
	imported_resource_path: String,
	prepared_runtime: MatrixOasisPreparedRuntimePack,
	prepared_scene: MatrixOasisPreparedScene,
) -> Dictionary:
	if prepared_runtime == null or prepared_scene == null or assembly_path.is_empty() or not assembly_path.is_absolute_path() or _path_has_link(assembly_path):
		return _failure("PACK_GODOT_SPATIAL_INPUT_INVALID")
	if not _valid_imported_resource_path(imported_resource_path):
		return _failure("PACK_GODOT_SPATIAL_RESOURCE_INVALID")
	var assembly_read := MatrixOasisSceneArtifactLoader._read_file(assembly_path, ASSEMBLY_MAX_BYTES)
	if not assembly_read["ok"]:
		return _failure("PACK_GODOT_SPATIAL_INPUT_INVALID")
	var parsed := MatrixOasisStrictJson.parse_bytes(assembly_read["bytes"], DIAGNOSTIC_PATH)
	if not parsed["ok"]:
		var parse_code: String = parsed["diagnostics"][0]["code"]
		return _failure("GODOT_RUNTIME_UNSUPPORTED_TEXT" if parse_code == "GODOT_RUNTIME_UNSUPPORTED_TEXT" else "PACK_GODOT_SPATIAL_JSON_INVALID")
	var assembly: Dictionary = parsed["value"]
	var scene_pack := prepared_scene._copy_scene_for_runtime()
	var runtime_pack := prepared_runtime._copy_pack_for_runtime()
	if not _valid_assembly(assembly, scene_pack, runtime_pack, prepared_scene):
		return _failure("PACK_GODOT_SPATIAL_SCHEMA_INVALID")
	var source_path := assembly_path.get_base_dir().path_join(SPLAT_RELATIVE_PATH).simplify_path()
	if not _is_contained(assembly_path.get_base_dir(), source_path) or _path_has_link(source_path):
		return _failure("PACK_GODOT_SPATIAL_RESOURCE_INVALID")
	var source_read := MatrixOasisSceneArtifactLoader._read_file(source_path, SPLAT_MAX_BYTES)
	var imported_absolute := ProjectSettings.globalize_path(imported_resource_path).simplify_path()
	var project_root := ProjectSettings.globalize_path("res://").simplify_path()
	if not _is_contained(project_root, imported_absolute) or _path_has_link(imported_absolute):
		return _failure("PACK_GODOT_SPATIAL_RESOURCE_INVALID")
	var imported_read := MatrixOasisSceneArtifactLoader._read_file(imported_absolute, SPLAT_MAX_BYTES)
	if not source_read["ok"] or not imported_read["ok"]:
		return _failure("PACK_GODOT_SPATIAL_RESOURCE_INVALID")
	var expected_hash: String = assembly["environment"]["splat"]["sha256"]
	if _sha256(source_read["bytes"]) != expected_hash or _sha256(imported_read["bytes"]) != expected_hash:
		return _failure("PACK_GODOT_SPATIAL_INTEGRITY_MISMATCH")
	if not ResourceLoader.exists(IMPORTED_RESOURCE_PATH):
		return _failure("PACK_GODOT_SPATIAL_RESOURCE_INVALID")
	var gaussian: Resource = ResourceLoader.load(IMPORTED_RESOURCE_PATH, "Resource", ResourceLoader.CACHE_MODE_IGNORE)
	if gaussian == null or int(gaussian.get("point_count")) != assembly["environment"]["splat"]["numGaussians"]:
		return _failure("PACK_GODOT_SPATIAL_RESOURCE_INVALID")
	var result := {
		"ok": true,
		"assembly": assembly.duplicate(true),
		"gaussian": gaussian,
	}
	_make_read_only(result["assembly"])
	result.make_read_only()
	return result


static func _valid_assembly(
	value: Dictionary,
	scene_pack: Dictionary,
	runtime_pack: Dictionary,
	prepared_scene: MatrixOasisPreparedScene,
) -> bool:
	if not _exact(value, ["canonicalization", "environment", "format", "formatVersion", "runtimeIdentity", "scene", "sources", "transforms"]):
		return false
	if value["format"] != "matrix-oasis.prototype-spatial-assembly" or value["formatVersion"] != "0.1.0" or value["canonicalization"] != "matrix-oasis.canonical-json/1":
		return false
	if not _valid_scene(value["scene"], scene_pack.get("scene")) or not _valid_runtime_identity(value["runtimeIdentity"], scene_pack.get("runtimeIdentity"), runtime_pack):
		return false
	if not _valid_sources(value["sources"], prepared_scene) or not _valid_environment(value["environment"], scene_pack):
		return false
	return _valid_transforms(value["transforms"])


static func _valid_scene(value: Variant, expected: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and typeof(expected) == TYPE_DICTIONARY and _exact(value, ["contentVersion", "id", "title"]) and value == expected


static func _valid_runtime_identity(value: Variant, scene_identity: Variant, runtime_pack: Dictionary) -> bool:
	if typeof(value) != TYPE_DICTIONARY or typeof(scene_identity) != TYPE_DICTIONARY or not _exact(value, ["artifactSha256", "packContentVersion", "packId", "runtimeFormat", "runtimeFormatVersion", "sourceCanonicalSha256"]):
		return false
	return value["runtimeFormat"] == runtime_pack.get("format") and value["runtimeFormatVersion"] == runtime_pack.get("formatVersion") and value["packId"] == runtime_pack.get("source", {}).get("id") and value["packContentVersion"] == runtime_pack.get("source", {}).get("contentVersion") and value["sourceCanonicalSha256"] == "sha256:" + str(scene_identity.get("sourceCanonicalSha256", "")) and value["artifactSha256"] == "sha256:" + str(scene_identity.get("artifactSha256", ""))


static func _valid_sources(value: Variant, prepared_scene: MatrixOasisPreparedScene) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["prototypeAssemblyReportSha256", "sceneBlueprintSha256", "scenePackSha256", "spatialEnvironmentBundleSha256"]):
		return false
	if not _hash(value["prototypeAssemblyReportSha256"]) or not _hash(value["sceneBlueprintSha256"]) or not _hash(value["spatialEnvironmentBundleSha256"]):
		return false
	return value["scenePackSha256"] == "sha256:" + prepared_scene.manifest_sha256()


static func _valid_environment(value: Variant, scene_pack: Dictionary) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["collider", "panoramaVisible", "renderer", "splat"]) or value["panoramaVisible"] != false:
		return false
	var renderer: Variant = value["renderer"]
	if typeof(renderer) != TYPE_DICTIONARY or not _exact(renderer, ["depthBiasMicros", "depthCaptureAlphaPermille", "depthTestMinAlphaPermille", "profile"]):
		return false
	if renderer["profile"] != "opaque-depth-compose-v1" or renderer["depthBiasMicros"] != 0 or renderer["depthTestMinAlphaPermille"] != 50 or renderer["depthCaptureAlphaPermille"] != 500:
		return false
	var splat: Variant = value["splat"]
	var collider: Variant = value["collider"]
	if typeof(splat) != TYPE_DICTIONARY or not _exact(splat, ["derivation", "numGaussians", "path", "sha256"]) or splat["path"] != SPLAT_RELATIVE_PATH or not _bounded_integer(splat["numGaussians"], 1, 2500000) or not _hash(splat["sha256"]) or not _valid_splat_derivation(splat["derivation"], splat["numGaussians"]):
		return false
	if typeof(collider) != TYPE_DICTIONARY or not _exact(collider, ["assetId", "path", "placementId", "sha256"]) or collider["path"] != COLLIDER_RELATIVE_PATH or not _hash(collider["sha256"]):
		return false
	var matching_assets: Array = scene_pack.get("assets", []).filter(func(asset: Variant) -> bool:
		return typeof(asset) == TYPE_DICTIONARY and asset.get("id") == collider["assetId"] and asset.get("path") == collider["path"] and "collider" in asset.get("roles", []) and "sha256:" + str(asset.get("sha256", "")) == collider["sha256"]
	)
	var matching_placements: Array = scene_pack.get("placements", []).filter(func(placement: Variant) -> bool:
		return typeof(placement) == TYPE_DICTIONARY and placement.get("id") == collider["placementId"] and placement.get("visualAssetId") == collider["assetId"] and placement.get("colliderAssetId") == collider["assetId"]
	)
	if matching_assets.size() != 1 or matching_placements.size() != 1:
		return false
	for binding in scene_pack.get("nodeBindings", []):
		if typeof(binding) != TYPE_DICTIONARY or collider["placementId"] not in binding.get("visiblePlacementIds", []):
			return false
	return true


static func _valid_splat_derivation(value: Variant, runtime_count: int) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["fullResolutionCompressedPly", "profile", "sourceNumGaussians", "targetNumGaussians"]):
		return false
	if value["profile"] not in ["identity-v1", "mpmm-uniform-v1"] or value["targetNumGaussians"] != 640000 or not _bounded_integer(value["sourceNumGaussians"], runtime_count, 2500000):
		return false
	var full: Variant = value["fullResolutionCompressedPly"]
	if typeof(full) != TYPE_DICTIONARY or not _exact(full, ["byteLength", "numGaussians", "sha256"]) or not _bounded_integer(full["byteLength"], 1, SPLAT_MAX_BYTES) or not _hash(full["sha256"]) or full["numGaussians"] != value["sourceNumGaussians"]:
		return false
	if value["sourceNumGaussians"] <= value["targetNumGaussians"]:
		return value["profile"] == "identity-v1" and runtime_count == value["sourceNumGaussians"]
	return value["profile"] == "mpmm-uniform-v1" and runtime_count == value["targetNumGaussians"]


static func _valid_transforms(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["alignment", "collider", "coordinateTransform", "eulerOrder", "placementGroundTargetMm", "root", "splat", "walkableEnvelope"]) or value["coordinateTransform"] != "spz-raw-ply-to-godot-v1" or value["eulerOrder"] != "YXZ" or value["placementGroundTargetMm"] != 150:
		return false
	if not _valid_alignment(value["alignment"]):
		return false
	var root: Variant = value["root"]
	var splat: Variant = value["splat"]
	var collider: Variant = value["collider"]
	if typeof(root) != TYPE_DICTIONARY or not _exact(root, ["rotationMilliDegrees", "translationMm"]) or not _vector(root["translationMm"], -2000000, 2000000) or not _vector(root["rotationMilliDegrees"], -360000, 360000):
		return false
	if typeof(splat) != TYPE_DICTIONARY or not _exact(splat, ["localRotationMilliDegrees", "localTranslationMm", "scaleMicros"]) or not _vector(splat["localTranslationMm"], -1000000, 1000000) or splat["localRotationMilliDegrees"] != [0, 0, 0] or not _bounded_integer(splat["scaleMicros"], 1, 100000000):
		return false
	if typeof(collider) != TYPE_DICTIONARY or not _exact(collider, ["localTranslationMm", "scaleMicros"]) or not _vector(collider["localTranslationMm"], -2000000, 2000000) or not _bounded_integer(collider["scaleMicros"], 1, 100000000):
		return false
	return _valid_walkable_envelope(value["walkableEnvelope"])


static func _valid_walkable_envelope(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["adjacentBins", "binSizeMm", "floorThicknessMm", "lateralBandMm", "maximumMm", "minimumBinCount", "minimumMm", "peakThresholdPermille", "profile", "verticalBandMm", "wallThicknessMm"]):
		return false
	if value["profile"] != "source-density-first-surface-v1" or value["adjacentBins"] != 2 or value["binSizeMm"] != 250 or value["minimumBinCount"] != 64 or value["peakThresholdPermille"] != 5 or value["lateralBandMm"] != 4000 or value["verticalBandMm"] != [350, 3000] or value["wallThicknessMm"] != 700 or value["floorThicknessMm"] != 200 or not _vector(value["minimumMm"], -2000000, 2000000) or not _vector(value["maximumMm"], -2000000, 2000000):
		return false
	if value["minimumMm"][1] != 0 or value["maximumMm"][1] < 3000 or value["maximumMm"][1] > 12000:
		return false
	for index in range(3):
		if value["minimumMm"][index] >= value["maximumMm"][index]:
			return false
	return true


static func _valid_alignment(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["centerFloorSampleSourceMm", "colliderBoundsMm", "maximumHorizontalSpanMm", "profile", "splatBoundsMm", "splatBoundsProfile", "splatProfile", "targetFloorSpanMm"]):
		return false
	if value["profile"] != "collider-fit-30m-v1" or value["targetFloorSpanMm"] != 30000 or value["maximumHorizontalSpanMm"] != 90000:
		return false
	var bounds: Variant = value["colliderBoundsMm"]
	if typeof(bounds) != TYPE_DICTIONARY or not _exact(bounds, ["maximumMm", "minimumMm"]) or not _vector(bounds["minimumMm"], -1000000, 1000000) or not _vector(bounds["maximumMm"], -1000000, 1000000):
		return false
	for index in range(3):
		if bounds["minimumMm"][index] > bounds["maximumMm"][index]:
			return false
	var splat_bounds: Variant = value["splatBoundsMm"]
	if value["splatProfile"] != "splat-robust-fit-30m-v1" or value["splatBoundsProfile"] != "source-position-percentile-1-99-v1" or typeof(splat_bounds) != TYPE_DICTIONARY or not _exact(splat_bounds, ["maximumMm", "minimumMm"]) or not _vector(splat_bounds["minimumMm"], -1000000, 1000000) or not _vector(splat_bounds["maximumMm"], -1000000, 1000000):
		return false
	for index in range(3):
		if splat_bounds["minimumMm"][index] > splat_bounds["maximumMm"][index]:
			return false
	return _vector(value["centerFloorSampleSourceMm"], -1000000, 1000000)


static func _valid_imported_resource_path(path: String) -> bool:
	return path == IMPORTED_RESOURCE_PATH


static func _vector(value: Variant, minimum: int, maximum: int) -> bool:
	if typeof(value) != TYPE_ARRAY or value.size() != 3:
		return false
	return value.all(func(component: Variant) -> bool: return _bounded_integer(component, minimum, maximum))


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


static func _exact(value: Dictionary, names: Array) -> bool:
	var keys := value.keys()
	keys.sort_custom(MatrixOasisStrictJson.compare_utf16)
	var expected := names.duplicate()
	expected.sort_custom(MatrixOasisStrictJson.compare_utf16)
	return keys == expected


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


static func _sha256(bytes: PackedByteArray) -> String:
	var context := HashingContext.new()
	if context.start(HashingContext.HASH_SHA256) != OK or context.update(bytes) != OK:
		return ""
	return "sha256:" + context.finish().hex_encode()


static func _make_read_only(value: Variant) -> void:
	if typeof(value) == TYPE_DICTIONARY:
		for key in value.keys():
			_make_read_only(value[key])
		value.make_read_only()
	elif typeof(value) == TYPE_ARRAY:
		for child in value:
			_make_read_only(child)
		value.make_read_only()


static func _failure(code: String) -> Dictionary:
	var diagnostic := {"phase": "runtime", "severity": "error", "code": code, "path": DIAGNOSTIC_PATH, "message": code}
	diagnostic.make_read_only()
	var diagnostics := [diagnostic]
	diagnostics.make_read_only()
	var result := {"ok": false, "diagnostics": diagnostics}
	result.make_read_only()
	return result
