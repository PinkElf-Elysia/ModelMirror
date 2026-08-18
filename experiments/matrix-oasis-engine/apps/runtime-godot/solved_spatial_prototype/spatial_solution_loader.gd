class_name MatrixOasisSpatialSolutionLoader
extends RefCounted

const MAX_BYTES := 16 * 1024 * 1024
const REPORT_MAX_BYTES := 256 * 1024
static var SOLUTION_PATH := String.chr(47) + "spatialSolution"
static var VERIFICATION_PATH := String.chr(47) + "spatialVerification"


static func load_from_paths(
	solution_path: String,
	verification_path: String,
	spatial_assembly_path: String,
	prepared_runtime: MatrixOasisPreparedRuntimePack,
	prepared_scene: MatrixOasisPreparedScene,
) -> Dictionary:
	if prepared_runtime == null or prepared_scene == null or not _safe_absolute(solution_path) or not _safe_absolute(verification_path) or not _safe_absolute(spatial_assembly_path):
		return _failure("PACK_GODOT_SOLVED_SPATIAL_INPUT_INVALID")
	var solution_read := MatrixOasisSceneArtifactLoader._read_file(solution_path, MAX_BYTES)
	var verification_read := MatrixOasisSceneArtifactLoader._read_file(verification_path, REPORT_MAX_BYTES)
	var assembly_read := MatrixOasisSceneArtifactLoader._read_file(spatial_assembly_path, 256 * 1024)
	if not solution_read["ok"] or not verification_read["ok"] or not assembly_read["ok"]:
		return _failure("PACK_GODOT_SOLVED_SPATIAL_INPUT_INVALID")
	var parsed := MatrixOasisStrictJson.parse_bytes(solution_read["bytes"], SOLUTION_PATH)
	var verification := MatrixOasisStrictJson.parse_bytes(verification_read["bytes"], VERIFICATION_PATH)
	if not parsed["ok"] or not verification["ok"]:
		return _failure("PACK_GODOT_SOLVED_SPATIAL_JSON_INVALID")
	if typeof(parsed["value"]) != TYPE_DICTIONARY or typeof(verification["value"]) != TYPE_DICTIONARY:
		return _failure("PACK_GODOT_SOLVED_SPATIAL_SCHEMA_INVALID")
	var solution: Dictionary = parsed["value"]
	var runtime := prepared_runtime._copy_pack_for_runtime()
	var scene := prepared_scene._copy_scene_for_runtime()
	if not _valid_solution(solution, runtime, scene, assembly_read["bytes"]):
		return _failure("PACK_GODOT_SOLVED_SPATIAL_SCHEMA_INVALID")
	if not _valid_verification(verification["value"], solution_read["bytes"], solution):
		return _failure("PACK_GODOT_SOLVED_SPATIAL_VERIFICATION_INVALID")
	_make_read_only(solution)
	var result := {"ok": true, "solution": solution}
	result.make_read_only()
	return result


static func _valid_solution(value: Dictionary, runtime: Dictionary, scene: Dictionary, assembly_bytes: PackedByteArray) -> bool:
	if not _exact(value, ["canonicalization", "format", "formatVersion", "metrics", "navigation", "nodeContexts", "placements", "profile", "proof", "source"]):
		return false
	if value["format"] != "matrix-oasis.prototype-spatial-solution" or value["formatVersion"] != "0.1.0" or value["canonicalization"] != "matrix-oasis.canonical-json/1":
		return false
	var source: Variant = value["source"]
	if typeof(source) != TYPE_DICTIONARY or not _exact(source, ["analysisTransformSource", "assetBundle", "environmentFacts", "runtime", "runtimeReceiptSha256", "spatialIntent"]):
		return false
	var runtime_identity: Variant = source["runtime"]
	if typeof(runtime_identity) != TYPE_DICTIONARY or not _exact(runtime_identity, ["artifactSha256", "contentVersion", "format", "formatVersion", "id", "sourceSha256"]):
		return false
	if runtime_identity["format"] != runtime.get("format") or runtime_identity["formatVersion"] != runtime.get("formatVersion") or runtime_identity["id"] != runtime.get("source", {}).get("id") or runtime_identity["contentVersion"] != runtime.get("source", {}).get("contentVersion") or runtime_identity["sourceSha256"] != "sha256:" + str(runtime.get("source", {}).get("canonicalSha256", "")):
		return false
	var scene_identity: Dictionary = scene.get("runtimeIdentity", {})
	if runtime_identity["artifactSha256"] != "sha256:" + str(scene_identity.get("artifactSha256", "")) or not _hash(source["runtimeReceiptSha256"]):
		return false
	var source_formats := {"spatialIntent": "matrix-oasis.prototype-spatial-intent", "environmentFacts": "matrix-oasis.prototype-environment-facts", "assetBundle": "matrix-oasis.prototype-asset-bundle"}
	for key in source_formats:
		var source_identity: Variant = source[key]
		if typeof(source_identity) != TYPE_DICTIONARY or not _exact(source_identity, ["canonicalSha256", "format", "formatVersion"]) or source_identity["format"] != source_formats[key] or source_identity["formatVersion"] != "0.1.0" or not _hash(source_identity["canonicalSha256"]):
			return false
	var transform_source: Variant = source["analysisTransformSource"]
	if typeof(transform_source) != TYPE_DICTIONARY or not _exact(transform_source, ["canonicalSha256", "format", "formatVersion", "profile"]) or transform_source["profile"] != "spatial-assembly-collider-v1" or transform_source["format"] != "matrix-oasis.prototype-spatial-assembly" or transform_source["formatVersion"] != "0.1.0" or transform_source["canonicalSha256"] != "sha256:" + _sha256(assembly_bytes):
		return false
	if not _valid_profile(value["profile"]) or not _valid_proof(value["proof"]):
		return false
	return _valid_placements(value["placements"], scene) and _valid_contexts(value["nodeContexts"], runtime, scene, value["placements"])


static func _valid_profile(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["id", "limits", "player", "terminal"]):
		return false
	var player: Variant = value["player"]
	var terminal: Variant = value["terminal"]
	var limits: Variant = value["limits"]
	return value["id"] == "matrix-oasis.spatial-solver/1" and typeof(player) == TYPE_DICTIONARY and typeof(terminal) == TYPE_DICTIONARY and typeof(limits) == TYPE_DICTIONARY and player.get("radiusMm") == 350 and player.get("heightMm") == 1800 and terminal.get("interactionDistanceMm") == 3000 and limits.get("maxSearchStates") == 100000


static func _valid_proof(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["allHardConstraintsSatisfied", "allNodeApproachesReachable", "singleNavigationComponent"]) and value["allHardConstraintsSatisfied"] == true and value["allNodeApproachesReachable"] == true and value["singleNavigationComponent"] == true


static func _valid_placements(values: Variant, scene: Dictionary) -> bool:
	if typeof(values) != TYPE_ARRAY or values.size() > 6:
		return false
	var scene_ids: Dictionary = {}
	for placement: Variant in scene.get("placements", []):
		if typeof(placement) == TYPE_DICTIONARY:
			scene_ids[placement.get("id")] = true
	var captured: Dictionary = {}
	for placement: Variant in values:
		if typeof(placement) != TYPE_DICTIONARY or not _exact(placement, ["anchorId", "anchorKind", "footprint", "placementId", "positionMm", "proof", "rotationMilliDegrees"]):
			return false
		var placement_id: Variant = placement["placementId"]
		if typeof(placement_id) != TYPE_STRING or not scene_ids.has(placement_id) or captured.has(placement_id) or placement["anchorKind"] not in ["floor", "wall"] or not _vector(placement["positionMm"], 1000000) or not _vector(placement["rotationMilliDegrees"], 360000):
			return false
		var footprint: Variant = placement["footprint"]
		if typeof(footprint) != TYPE_DICTIONARY or not _exact(footprint, ["depthMm", "heightMm", "widthMm"]) or not _positive(footprint["widthMm"], 100000) or not _positive(footprint["heightMm"], 100000) or not _positive(footprint["depthMm"], 100000):
			return false
		captured[placement_id] = true
	return true


static func _valid_contexts(values: Variant, runtime: Dictionary, scene: Dictionary, placements: Array) -> bool:
	if typeof(values) != TYPE_ARRAY or values.is_empty() or values.size() > 16:
		return false
	var nodes: Dictionary = {}
	for node: Variant in runtime.get("nodes", []):
		if typeof(node) == TYPE_DICTIONARY:
			nodes[node.get("id")] = node.get("actions", []).size()
	var bindings: Dictionary = {}
	for binding: Variant in scene.get("nodeBindings", []):
		if typeof(binding) == TYPE_DICTIONARY:
			bindings[binding.get("nodeId")] = binding.get("visiblePlacementIds", [])
	var selected: Dictionary = {}
	for placement: Variant in placements:
		selected[placement["placementId"]] = true
	var captured: Dictionary = {}
	for context: Variant in values:
		if typeof(context) != TYPE_DICTIONARY or not _exact(context, ["actionTerminal", "approachPathFloorAnchorIds", "nodeId", "playerSpawn", "visiblePlacementIds", "zoneId"]):
			return false
		var node_id: Variant = context["nodeId"]
		if typeof(node_id) != TYPE_STRING or not nodes.has(node_id) or not bindings.has(node_id) or captured.has(node_id):
			return false
		if not _valid_spawn(context["playerSpawn"]) or not _valid_terminal(context["actionTerminal"], nodes[node_id]):
			return false
		var visible: Variant = context["visiblePlacementIds"]
		var authored: Variant = bindings[node_id]
		if typeof(visible) != TYPE_ARRAY or typeof(authored) != TYPE_ARRAY:
			return false
		var expected: Array = authored.filter(func(item: Variant) -> bool: return selected.has(item))
		if expected != visible:
			return false
		captured[node_id] = true
	return captured.size() == nodes.size()


static func _valid_spawn(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["floorAnchorId", "positionMm", "yawMilliDegrees"]) and typeof(value["floorAnchorId"]) == TYPE_STRING and _vector(value["positionMm"], 1000000) and typeof(value["yawMilliDegrees"]) == TYPE_INT and abs(value["yawMilliDegrees"]) <= 180000


static func _valid_terminal(value: Variant, action_count: int) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["actionCount", "approachFloorAnchorId", "floorAnchorId", "footprint", "positionMm", "yawMilliDegrees"]) and value["actionCount"] == action_count and _vector(value["positionMm"], 1000000) and typeof(value["yawMilliDegrees"]) == TYPE_INT and abs(value["yawMilliDegrees"]) <= 180000


static func _valid_verification(value: Dictionary, solution_bytes: PackedByteArray, solution: Dictionary) -> bool:
	if not _exact(value, ["checks", "evidenceSha256", "format", "formatVersion", "solutionSha256", "verifier"]):
		return false
	var verifier: Variant = value["verifier"]
	var checks: Variant = value["checks"]
	if typeof(verifier) != TYPE_DICTIONARY or not _exact(verifier, ["godotVersion", "id", "version"]) or typeof(checks) != TYPE_DICTIONARY or not _exact(checks, ["nodeContextCount", "pathCount", "placementCount", "terminalCount"]):
		return false
	var terminal_count := 0
	for context: Variant in solution["nodeContexts"]:
		terminal_count += context["actionTerminal"]["actionCount"]
	return value["format"] == "matrix-oasis.prototype-spatial-verification-report" and value["formatVersion"] == "0.1.0" and value["solutionSha256"] == "sha256:" + _sha256(solution_bytes) and _hash(value["evidenceSha256"]) and verifier == {"godotVersion": "4.6.3", "id": "godot-spatial-solution-verifier", "version": "0.1.0-r14"} and checks["placementCount"] == solution["placements"].size() and checks["nodeContextCount"] == solution["nodeContexts"].size() and checks["pathCount"] == solution["nodeContexts"].size() and checks["terminalCount"] == terminal_count


static func _safe_absolute(value: String) -> bool:
	return not value.is_empty() and value.is_absolute_path() and not _path_has_link(value)


static func _path_has_link(value: String) -> bool:
	var current := value
	while not current.is_empty():
		var parent := current.get_base_dir()
		if parent == current:
			break
		var directory := DirAccess.open(parent)
		if directory == null or directory.is_link(current.get_file()):
			return true
		current = parent
	return false


static func _vector(value: Variant, maximum: int) -> bool:
	return typeof(value) == TYPE_ARRAY and value.size() == 3 and value.all(func(component: Variant) -> bool: return typeof(component) == TYPE_INT and abs(component) <= maximum)


static func _positive(value: Variant, maximum: int) -> bool:
	return typeof(value) == TYPE_INT and value >= 1 and value <= maximum


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
	return context.finish().hex_encode()


static func _exact(value: Dictionary, names: Array) -> bool:
	var keys := value.keys()
	keys.sort_custom(MatrixOasisStrictJson.compare_utf16)
	var expected := names.duplicate()
	expected.sort_custom(MatrixOasisStrictJson.compare_utf16)
	return keys == expected


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
	var diagnostic := {"phase": "runtime", "severity": "error", "code": code, "path": SOLUTION_PATH, "message": code}
	diagnostic.make_read_only()
	var diagnostics := [diagnostic]
	diagnostics.make_read_only()
	var result := {"ok": false, "diagnostics": diagnostics}
	result.make_read_only()
	return result
