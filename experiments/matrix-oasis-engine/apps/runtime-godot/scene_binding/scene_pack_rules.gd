class_name MatrixOasisScenePackRules
extends RefCounted

const PATH_SEPARATOR := "\u002f"

const FORMAT := "matrix-oasis.scene-pack"
const VERSION := "0.1.0"
const CANONICALIZATION := "matrix-oasis.canonical-json/1"
const HASH_PATTERN := "^[0-9a-f]{64}$"
const ID_PATTERN := "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"


static func validate(scene: Variant, runtime_pack: Dictionary, artifact_sha256: String) -> Dictionary:
	if typeof(scene) != TYPE_DICTIONARY or not _exact(scene, ["assets", "canonicalization", "format", "formatVersion", "nodeBindings", "placements", "runtimeIdentity", "scene"]):
		return _failure("schema", "PACK_GODOT_SCENE_SCHEMA_INVALID", "/scenePack")
	if scene["format"] != FORMAT or scene["formatVersion"] != VERSION or scene["canonicalization"] != CANONICALIZATION:
		return _failure("schema", "PACK_GODOT_SCENE_SCHEMA_INVALID", "/scenePack")
	if not _valid_scene(scene["scene"]):
		return _failure("schema", "PACK_GODOT_SCENE_SCHEMA_INVALID", "/scenePack/scene")
	if not _valid_runtime_identity(scene["runtimeIdentity"], runtime_pack, artifact_sha256):
		return _failure("semantic", "PACK_GODOT_SCENE_IDENTITY_MISMATCH", "/scenePack/runtimeIdentity")
	if typeof(scene["assets"]) != TYPE_ARRAY or scene["assets"].size() > 16 or typeof(scene["placements"]) != TYPE_ARRAY or scene["placements"].size() > 128 or typeof(scene["nodeBindings"]) != TYPE_ARRAY or scene["nodeBindings"].size() > 4096:
		return _failure("schema", "PACK_GODOT_SCENE_SCHEMA_INVALID", "/scenePack")
	var assets := {}
	for index in scene["assets"].size():
		var asset: Variant = scene["assets"][index]
		if not _valid_asset(asset) or assets.has(asset["id"]):
			return _failure("semantic", "PACK_GODOT_SCENE_ASSET_INVALID", "/scenePack/assets/%d" % index)
		assets[asset["id"]] = asset
	var runtime_entities := {}
	for entity in runtime_pack["entities"]:
		runtime_entities[entity["id"]] = true
	var placements := {}
	for index in scene["placements"].size():
		var placement: Variant = scene["placements"][index]
		if not _valid_placement(placement) or placements.has(placement["id"]):
			return _failure("semantic", "PACK_GODOT_SCENE_PLACEMENT_INVALID", "/scenePack/placements/%d" % index)
		if not assets.has(placement["visualAssetId"]) or not "visual" in assets[placement["visualAssetId"]]["roles"]:
			return _failure("semantic", "PACK_GODOT_SCENE_REFERENCE_INVALID", "/scenePack/placements/%d/visualAssetId" % index)
		if placement["colliderAssetId"] != null and (not assets.has(placement["colliderAssetId"]) or not "collider" in assets[placement["colliderAssetId"]]["roles"]):
			return _failure("semantic", "PACK_GODOT_SCENE_REFERENCE_INVALID", "/scenePack/placements/%d/colliderAssetId" % index)
		if placement["entityId"] != null and not runtime_entities.has(placement["entityId"]):
			return _failure("semantic", "PACK_GODOT_SCENE_REFERENCE_INVALID", "/scenePack/placements/%d/entityId" % index)
		placements[placement["id"]] = true
	var runtime_nodes := {}
	for node in runtime_pack["nodes"]:
		runtime_nodes[node["id"]] = true
	var bindings := {}
	for index in scene["nodeBindings"].size():
		var binding: Variant = scene["nodeBindings"][index]
		if not _valid_binding(binding) or bindings.has(binding["nodeId"]) or not runtime_nodes.has(binding["nodeId"]):
			return _failure("semantic", "PACK_GODOT_SCENE_BINDING_INVALID", "/scenePack/nodeBindings/%d" % index)
		for placement_id in binding["visiblePlacementIds"]:
			if not placements.has(placement_id):
				return _failure("semantic", "PACK_GODOT_SCENE_REFERENCE_INVALID", "/scenePack/nodeBindings/%d/visiblePlacementIds" % index)
		bindings[binding["nodeId"]] = true
	if bindings.size() != runtime_nodes.size():
		return _failure("semantic", "PACK_GODOT_SCENE_BINDING_INVALID", "/scenePack/nodeBindings")
	return {"ok": true}


static func _valid_scene(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["contentVersion", "id", "title"]) and _id(value["id"]) and _string(value["contentVersion"], 64) and _string(value["title"], 256)


static func _valid_runtime_identity(value: Variant, runtime_pack: Dictionary, artifact_sha256: String) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["artifactSha256", "packContentVersion", "packId", "runtimeFormat", "runtimeFormatVersion", "sourceCanonicalSha256"]):
		return false
	return value["runtimeFormat"] == runtime_pack["format"] and value["runtimeFormatVersion"] == runtime_pack["formatVersion"] and value["packId"] == runtime_pack["source"]["id"] and value["packContentVersion"] == runtime_pack["source"]["contentVersion"] and value["sourceCanonicalSha256"] == runtime_pack["source"]["canonicalSha256"] and value["artifactSha256"] == artifact_sha256


static func _valid_asset(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["byteLength", "format", "id", "path", "roles", "sha256"]) or not _id(value["id"]) or value["format"] != "glb" or typeof(value["byteLength"]) != TYPE_INT or value["byteLength"] < 1 or value["byteLength"] > 32 * 1024 * 1024 or not _matches(value["sha256"], HASH_PATTERN) or not _safe_asset_path(value["path"]):
		return false
	if typeof(value["roles"]) != TYPE_ARRAY or value["roles"].is_empty() or value["roles"].size() > 2:
		return false
	var seen := {}
	for role in value["roles"]:
		if role not in ["visual", "collider"] or seen.has(role):
			return false
		seen[role] = true
	return true


static func _valid_placement(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["colliderAssetId", "entityId", "id", "transform", "visualAssetId"]) and _id(value["id"]) and _id(value["visualAssetId"]) and (value["colliderAssetId"] == null or _id(value["colliderAssetId"])) and (value["entityId"] == null or _id(value["entityId"])) and _valid_transform(value["transform"])


static func _valid_transform(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["positionMm", "rotationMilliDegrees", "scalePermille"]) and _integer_triplet(value["positionMm"], -1000000, 1000000) and _integer_triplet(value["rotationMilliDegrees"], -360000, 360000) and _integer_triplet(value["scalePermille"], 1, 100000)


static func _valid_binding(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["actionAnchor", "nodeId", "playerSpawn", "visiblePlacementIds"]) or not _id(value["nodeId"]) or not _valid_anchor(value["playerSpawn"]) or not _valid_anchor(value["actionAnchor"]) or typeof(value["visiblePlacementIds"]) != TYPE_ARRAY or value["visiblePlacementIds"].size() > 128:
		return false
	var seen := {}
	for placement_id in value["visiblePlacementIds"]:
		if not _id(placement_id) or seen.has(placement_id):
			return false
		seen[placement_id] = true
	return true


static func _valid_anchor(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["positionMm", "yawMilliDegrees"]) and _integer_triplet(value["positionMm"], -1000000, 1000000) and typeof(value["yawMilliDegrees"]) == TYPE_INT and value["yawMilliDegrees"] >= -180000 and value["yawMilliDegrees"] <= 180000


static func _integer_triplet(value: Variant, minimum: int, maximum: int) -> bool:
	if typeof(value) != TYPE_ARRAY or value.size() != 3:
		return false
	for item in value:
		if typeof(item) != TYPE_INT or item < minimum or item > maximum:
			return false
	return true


static func _safe_asset_path(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or value.is_empty() or value.length() > 192 or not value.ends_with(".glb") or value.contains("\\") or value.contains(":") or value.begins_with(PATH_SEPARATOR):
		return false
	for part in value.split(PATH_SEPARATOR, false):
		if part.is_empty() or part == "." or part == "..":
			return false
	return true


static func _id(value: Variant) -> bool:
	return typeof(value) == TYPE_STRING and value.length() <= 96 and _matches(value, ID_PATTERN)


static func _string(value: Variant, maximum: int) -> bool:
	return typeof(value) == TYPE_STRING and not value.is_empty() and value.length() <= maximum


static func _matches(value: Variant, pattern: String) -> bool:
	if typeof(value) != TYPE_STRING:
		return false
	var regex := RegEx.new()
	return regex.compile(pattern) == OK and regex.search(value) != null


static func _exact(value: Dictionary, expected: Array) -> bool:
	var keys := value.keys()
	keys.sort()
	var sorted_expected := expected.duplicate()
	sorted_expected.sort()
	return keys == sorted_expected


static func _failure(phase: String, code: String, path: String) -> Dictionary:
	var diagnostic := {"phase": phase, "severity": "error", "code": code, "path": path, "message": code}
	diagnostic.make_read_only()
	var diagnostics := [diagnostic]
	diagnostics.make_read_only()
	var result := {"ok": false, "diagnostics": diagnostics}
	result.make_read_only()
	return result
