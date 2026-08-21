class_name MatrixOasisSpatialSolutionLoader
extends RefCounted

const MAX_BYTES := 16 * 1024 * 1024
const REPORT_MAX_BYTES := 256 * 1024
static var SOLUTION_PATH := String.chr(47) + "spatialSolution"
static var VERIFICATION_PATH := String.chr(47) + "spatialVerification"
static var FACTS_PATH := String.chr(47) + "environmentFacts"
static var ASSEMBLY_PATH := String.chr(47) + "spatialAssembly"


static func load_from_paths(
	solution_path: String,
	verification_path: String,
	facts_path: String,
	spatial_assembly_path: String,
	prepared_runtime: MatrixOasisPreparedRuntimePack,
	prepared_scene: MatrixOasisPreparedScene,
	environment_placement_id: String,
) -> Dictionary:
	if prepared_runtime == null or prepared_scene == null or environment_placement_id.is_empty() or not _safe_absolute(solution_path) or not _safe_absolute(verification_path) or not _safe_absolute(facts_path) or not _safe_absolute(spatial_assembly_path):
		return _failure("PACK_GODOT_SOLVED_SPATIAL_INPUT_INVALID")
	var solution_read := MatrixOasisSceneArtifactLoader._read_file(solution_path, MAX_BYTES)
	var verification_read := MatrixOasisSceneArtifactLoader._read_file(verification_path, REPORT_MAX_BYTES)
	var facts_read := MatrixOasisSceneArtifactLoader._read_file(facts_path, MAX_BYTES)
	var assembly_read := MatrixOasisSceneArtifactLoader._read_file(spatial_assembly_path, 256 * 1024)
	if not solution_read["ok"] or not verification_read["ok"] or not facts_read["ok"] or not assembly_read["ok"]:
		return _failure("PACK_GODOT_SOLVED_SPATIAL_INPUT_INVALID")
	var parsed := MatrixOasisStrictJson.parse_bytes(solution_read["bytes"], SOLUTION_PATH)
	var verification := MatrixOasisStrictJson.parse_bytes(verification_read["bytes"], VERIFICATION_PATH)
	var facts := MatrixOasisStrictJson.parse_bytes(facts_read["bytes"], FACTS_PATH)
	var assembly := MatrixOasisStrictJson.parse_bytes(assembly_read["bytes"], ASSEMBLY_PATH)
	if not parsed["ok"] or not verification["ok"] or not facts["ok"] or not assembly["ok"]:
		return _failure("PACK_GODOT_SOLVED_SPATIAL_JSON_INVALID")
	if typeof(parsed["value"]) != TYPE_DICTIONARY or typeof(verification["value"]) != TYPE_DICTIONARY or typeof(facts["value"]) != TYPE_DICTIONARY or typeof(assembly["value"]) != TYPE_DICTIONARY:
		return _failure("PACK_GODOT_SOLVED_SPATIAL_SCHEMA_INVALID")
	var solution: Dictionary = parsed["value"]
	var runtime := prepared_runtime._copy_pack_for_runtime()
	var scene := prepared_scene._copy_scene_for_runtime()
	var placement_id_map := _placement_id_map(solution.get("placements"), scene, environment_placement_id)
	if placement_id_map.is_empty() and not solution.get("placements", []).is_empty():
		return _failure("PACK_GODOT_SOLVED_SPATIAL_SCHEMA_INVALID")
	if not _valid_solution(solution, runtime, scene, assembly_read["bytes"], placement_id_map, environment_placement_id):
		return _failure("PACK_GODOT_SOLVED_SPATIAL_SCHEMA_INVALID")
	var runtime_support_height_value: Variant = _entry_support_height_mm(solution, runtime)
	if runtime_support_height_value == null:
		return _failure("PACK_GODOT_SOLVED_SPATIAL_SCHEMA_INVALID")
	var runtime_support_height_mm: int = runtime_support_height_value
	if solution["source"]["environmentFacts"]["canonicalSha256"] != "sha256:" + _sha256(facts_read["bytes"]):
		return _failure("PACK_GODOT_SOLVED_SPATIAL_SCHEMA_INVALID")
	var navigation := _navigation_geometry(facts["value"], solution["navigation"], solution["placements"],
		solution["nodeContexts"], solution["profile"]["player"]["heightMm"], runtime_support_height_mm)
	if not navigation["ok"]:
		return _failure("PACK_GODOT_SOLVED_SPATIAL_SCHEMA_INVALID")
	if not _valid_verification(verification["value"], solution_read["bytes"], solution,
		assembly_read["bytes"], assembly["value"]):
		return _failure("PACK_GODOT_SOLVED_SPATIAL_VERIFICATION_INVALID")
	_make_read_only(solution)
	placement_id_map.make_read_only()
	var visual_safety: Dictionary = verification["value"]["visualSafety"].duplicate(true)
	_make_read_only(visual_safety)
	var result := {"ok": true, "navigationBoundaryFaces": navigation["boundaryFaces"], "navigationFaces": navigation["faces"], "placementIdMap": placement_id_map, "runtimeSupportHeightMm": runtime_support_height_mm, "solution": solution, "visualSafety": visual_safety}
	result.make_read_only()
	return result


static func _navigation_geometry(facts: Dictionary, solution_navigation: Dictionary, placements: Array,
	node_contexts: Array, wall_height_mm: int, runtime_support_height_mm: int) -> Dictionary:
	var invalid := {"ok": false}
	if not _exact(facts, ["analysisProfile", "canonicalization", "coordinateSystem", "environmentBounds", "floorAnchors", "format", "formatVersion", "navigationMesh", "source", "wallAnchors"]) or facts["format"] != "matrix-oasis.prototype-environment-facts" or facts["formatVersion"] != "0.1.0" or facts["canonicalization"] != "matrix-oasis.canonical-json/1":
		return invalid
	if facts["coordinateSystem"] != {"eulerOrder": "YXZ", "handedness": "right", "unit": "millimeter", "upAxis": "Y"} or facts["analysisProfile"] != {"floorSnapMm": 200, "maxSlopeMilliDegrees": 45000, "playerHeightMm": 1800, "playerRadiusMm": 350}:
		return invalid
	var mesh: Variant = facts["navigationMesh"]
	if typeof(mesh) != TYPE_DICTIONARY or not _exact(mesh, ["components", "polygons", "verticesMm"]):
		return invalid
	if not _exact(solution_navigation, ["componentIndex", "zoneDomains", "zoneSeeds"]):
		return invalid
	var component_index: Variant = solution_navigation["componentIndex"]
	var vertices: Variant = mesh["verticesMm"]
	var polygons: Variant = mesh["polygons"]
	var components: Variant = mesh["components"]
	if typeof(vertices) != TYPE_ARRAY or vertices.is_empty() or vertices.size() > 200000 or typeof(polygons) != TYPE_ARRAY or polygons.is_empty() or polygons.size() > 200000 or typeof(components) != TYPE_ARRAY or component_index < 0 or component_index >= components.size() or components.size() > 4096 or wall_height_mm != 1800 or abs(runtime_support_height_mm) > 1000000:
		return invalid
	var points := PackedVector3Array()
	for vertex: Variant in vertices:
		if not _vector(vertex, 1000000):
			return invalid
		points.append(Vector3(float(vertex[0]), float(runtime_support_height_mm), float(vertex[2])) / 1000.0)
	var component: Variant = components[component_index]
	if typeof(component) != TYPE_DICTIONARY or not _exact(component, ["bounds", "index", "polygonIndices"]) or component["index"] != component_index or typeof(component["polygonIndices"]) != TYPE_ARRAY or component["polygonIndices"].is_empty():
		return invalid
	var polygon_indices: Array = component["polygonIndices"].duplicate()
	polygon_indices.sort()
	var polygon_set: Dictionary = {}
	var polygon_vertices: Dictionary = {}
	var captured: Dictionary = {}
	for polygon_index: Variant in polygon_indices:
		if typeof(polygon_index) != TYPE_INT or polygon_index < 0 or polygon_index >= polygons.size() or captured.has(polygon_index):
			return invalid
		captured[polygon_index] = true
		var polygon: Variant = polygons[polygon_index]
		if typeof(polygon) != TYPE_DICTIONARY or not _exact(polygon, ["componentIndex", "vertexIndices"]) or polygon["componentIndex"] != component_index or typeof(polygon["vertexIndices"]) != TYPE_ARRAY or polygon["vertexIndices"].size() < 3 or polygon["vertexIndices"].size() > 64:
			return invalid
		var indices: Array = polygon["vertexIndices"]
		var seen_vertices: Dictionary = {}
		for vertex_index: Variant in indices:
			if typeof(vertex_index) != TYPE_INT or vertex_index < 0 or vertex_index >= points.size() or seen_vertices.has(vertex_index):
				return invalid
			seen_vertices[vertex_index] = true
		polygon_set[polygon_index] = true
		polygon_vertices[polygon_index] = indices.duplicate()
	var selected_polygon_indices := _playable_polygon_indices(facts, solution_navigation, placements,
		node_contexts, polygon_set, polygon_vertices)
	if selected_polygon_indices.is_empty():
		return invalid
	var faces := PackedVector3Array()
	var edges: Dictionary = {}
	for polygon_index: int in selected_polygon_indices:
		var indices: Array = polygon_vertices[polygon_index]
		for triangle_index in range(1, indices.size() - 1):
			faces.append(points[indices[0]])
			faces.append(points[indices[triangle_index]])
			faces.append(points[indices[triangle_index + 1]])
		for edge_index in indices.size():
			var first: int = indices[edge_index]
			var second: int = indices[(edge_index + 1) % indices.size()]
			var key := "%d:%d" % [mini(first, second), maxi(first, second)]
			if edges.has(key):
				edges[key]["count"] += 1
			else:
				edges[key] = {"count": 1, "first": first, "second": second}
	var boundary_faces := PackedVector3Array()
	var wall_height := float(wall_height_mm) / 1000.0
	for key: String in edges:
		var edge: Dictionary = edges[key]
		if edge["count"] != 1:
			continue
		var first: Vector3 = points[edge["first"]]
		var second: Vector3 = points[edge["second"]]
		var first_top := first + Vector3.UP * wall_height
		var second_top := second + Vector3.UP * wall_height
		boundary_faces.append_array(PackedVector3Array([first, second, second_top, first, second_top, first_top]))
	if faces.is_empty() or boundary_faces.is_empty():
		return invalid
	return {"boundaryFaces": boundary_faces, "faces": faces, "ok": true}


static func _entry_support_height_mm(solution: Dictionary, runtime: Dictionary) -> Variant:
	var entry_index: Variant = runtime.get("entryNodeIndex")
	var nodes: Variant = runtime.get("nodes")
	if typeof(entry_index) != TYPE_INT or typeof(nodes) != TYPE_ARRAY or entry_index < 0 or entry_index >= nodes.size() or typeof(nodes[entry_index]) != TYPE_DICTIONARY or typeof(nodes[entry_index].get("id")) != TYPE_STRING:
		return null
	var entry_id: String = nodes[entry_index]["id"]
	var support: Variant = null
	for context: Variant in solution.get("nodeContexts", []):
		if typeof(context) != TYPE_DICTIONARY or context.get("nodeId") != entry_id:
			continue
		var position: Variant = context.get("playerSpawn", {}).get("positionMm")
		if support != null or not _vector(position, 1000000):
			return null
		support = position[1]
	return support


static func _playable_polygon_indices(facts: Dictionary, solution_navigation: Dictionary, placements: Array,
	node_contexts: Array, component_polygon_set: Dictionary, polygon_vertices: Dictionary) -> Array:
	var floor_by_id: Dictionary = {}
	for anchor: Variant in facts["floorAnchors"]:
		if typeof(anchor) != TYPE_DICTIONARY or typeof(anchor.get("id")) != TYPE_STRING or \
			typeof(anchor.get("polygonIndex")) != TYPE_INT or typeof(anchor.get("componentIndex")) != TYPE_INT or \
			floor_by_id.has(anchor["id"]):
			return []
		floor_by_id[anchor["id"]] = anchor
	var wall_by_id: Dictionary = {}
	for anchor: Variant in facts["wallAnchors"]:
		if typeof(anchor) != TYPE_DICTIONARY or typeof(anchor.get("id")) != TYPE_STRING or \
			typeof(anchor.get("nearestFloorAnchorId")) != TYPE_STRING or wall_by_id.has(anchor["id"]):
			return []
		wall_by_id[anchor["id"]] = anchor
	var required_floor_ids: Dictionary = {}
	for seed: Variant in solution_navigation["zoneSeeds"]:
		if typeof(seed) != TYPE_DICTIONARY or typeof(seed.get("floorAnchorId")) != TYPE_STRING:
			return []
		required_floor_ids[seed["floorAnchorId"]] = true
	for placement: Variant in placements:
		if typeof(placement) != TYPE_DICTIONARY or typeof(placement.get("anchorId")) != TYPE_STRING:
			return []
		if placement.get("anchorKind") == "floor":
			required_floor_ids[placement["anchorId"]] = true
		elif placement.get("anchorKind") == "wall" and wall_by_id.has(placement["anchorId"]):
			required_floor_ids[wall_by_id[placement["anchorId"]]["nearestFloorAnchorId"]] = true
		else:
			return []
	for context: Variant in node_contexts:
		if typeof(context) != TYPE_DICTIONARY:
			return []
		for floor_id: Variant in [context.get("playerSpawn", {}).get("floorAnchorId"),
			context.get("actionTerminal", {}).get("floorAnchorId"),
			context.get("actionTerminal", {}).get("approachFloorAnchorId")]:
			if typeof(floor_id) != TYPE_STRING:
				return []
			required_floor_ids[floor_id] = true
		for floor_id: Variant in context.get("approachPathFloorAnchorIds", []):
			if typeof(floor_id) != TYPE_STRING:
				return []
			required_floor_ids[floor_id] = true
		for support: Variant in context.get("actionTerminal", {}).get("terminalSupports", []):
			if typeof(support) != TYPE_DICTIONARY or typeof(support.get("floorAnchorId")) != TYPE_STRING:
				return []
			required_floor_ids[support["floorAnchorId"]] = true
	var required_polygons: Dictionary = {}
	for floor_id: String in required_floor_ids:
		var anchor: Variant = floor_by_id.get(floor_id)
		if typeof(anchor) != TYPE_DICTIONARY or anchor["componentIndex"] != solution_navigation["componentIndex"] or \
			not component_polygon_set.has(anchor["polygonIndex"]):
			return []
		required_polygons[anchor["polygonIndex"]] = true
	if required_polygons.is_empty():
		return []
	var polygons_by_vertex: Dictionary = {}
	var adjacency: Dictionary = {}
	for polygon_index: int in component_polygon_set:
		adjacency[polygon_index] = {}
		for vertex_index: int in polygon_vertices[polygon_index]:
			if not polygons_by_vertex.has(vertex_index):
				polygons_by_vertex[vertex_index] = []
			polygons_by_vertex[vertex_index].append(polygon_index)
	for linked: Array in polygons_by_vertex.values():
		linked.sort()
		for left: int in linked:
			for right: int in linked:
				if left != right:
					adjacency[left][right] = true
	var targets: Array = required_polygons.keys()
	targets.sort()
	var selected: Dictionary = {targets[0]: true}
	for target: int in targets:
		if selected.has(target):
			continue
		var pending: Array = [target]
		var parents: Dictionary = {target: -1}
		var found := -1
		var offset := 0
		while offset < pending.size() and found < 0:
			var current: int = pending[offset]
			offset += 1
			if selected.has(current):
				found = current
				break
			var neighbors: Array = adjacency[current].keys()
			neighbors.sort()
			for neighbor: int in neighbors:
				if not parents.has(neighbor):
					parents[neighbor] = current
					pending.append(neighbor)
		if found < 0:
			return []
		var cursor := found
		while cursor != -1:
			selected[cursor] = true
			cursor = parents[cursor]
	var buffered := selected.duplicate()
	for polygon_index: int in selected:
		for neighbor: int in adjacency[polygon_index]:
			buffered[neighbor] = true
	var output: Array = buffered.keys()
	output.sort()
	return output


static func _valid_solution(value: Dictionary, runtime: Dictionary, scene: Dictionary, assembly_bytes: PackedByteArray, placement_id_map: Dictionary, environment_placement_id: String) -> bool:
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
	return _valid_placements(value["placements"], placement_id_map) and _valid_contexts(value["nodeContexts"], runtime, value["placements"])


static func _valid_profile(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["clearanceMm", "id", "limits", "player", "terminal", "tolerances"]):
		return false
	var player: Variant = value["player"]
	var clearance: Variant = value["clearanceMm"]
	var terminal: Variant = value["terminal"]
	var limits: Variant = value["limits"]
	var tolerances: Variant = value["tolerances"]
	return value["id"] == "matrix-oasis.spatial-solver/1" and \
		typeof(player) == TYPE_DICTIONARY and _exact(player, ["eyeHeightMm", "floorSnapMm", "heightMm", "radiusMm"]) and player == {"eyeHeightMm": 1475, "floorSnapMm": 200, "heightMm": 1800, "radiusMm": 350} and \
		typeof(clearance) == TYPE_DICTIONARY and _exact(clearance, ["compact", "human", "large"]) and clearance == {"compact": 250, "human": 350, "large": 600} and \
		typeof(terminal) == TYPE_DICTIONARY and _exact(terminal, ["centerHeightMm", "columnSpacingMm", "columns", "depthMm", "interactionDistanceMm", "originZMm", "rowSpacingMm", "widthMm"]) and terminal == {"centerHeightMm": 850, "columnSpacingMm": 1700, "columns": 8, "depthMm": 500, "interactionDistanceMm": 3000, "originZMm": -2400, "rowSpacingMm": 2250, "widthMm": 1250} and \
		typeof(limits) == TYPE_DICTIONARY and _exact(limits, ["maxCandidatesPerItem", "maxSearchStates"]) and limits == {"maxCandidatesPerItem": 256, "maxSearchStates": 100000} and \
		typeof(tolerances) == TYPE_DICTIONARY and _exact(tolerances, ["floorContactMm", "pathEndpointMm"]) and tolerances == {"floorContactMm": 20, "pathEndpointMm": 100}


static func _valid_proof(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["allHardConstraintsSatisfied", "allNodeApproachesReachable", "singleNavigationComponent"]) and value["allHardConstraintsSatisfied"] == true and value["allNodeApproachesReachable"] == true and value["singleNavigationComponent"] == true


static func _placement_id_map(values: Variant, scene: Dictionary, environment_placement_id: String) -> Dictionary:
	var result: Dictionary = {}
	if typeof(values) != TYPE_ARRAY:
		return result
	var scene_placements: Array = scene.get("placements", [])
	var product_placements: Array = []
	var environment_count := 0
	for placement: Variant in scene_placements:
		if typeof(placement) != TYPE_DICTIONARY or typeof(placement.get("id")) != TYPE_STRING:
			return {}
		if placement["id"] == environment_placement_id:
			environment_count += 1
		else:
			product_placements.append(placement)
	if environment_count != 1 or product_placements.size() != values.size():
		return {}
	for index in range(values.size()):
		var solved: Variant = values[index]
		var product: Dictionary = product_placements[index]
		if typeof(solved) != TYPE_DICTIONARY or typeof(solved.get("placementId")) != TYPE_STRING or result.has(solved["placementId"]) or result.values().has(product["id"]):
			return {}
		result[solved["placementId"]] = product["id"]
	return result


static func _valid_placements(values: Variant, placement_id_map: Dictionary) -> bool:
	if typeof(values) != TYPE_ARRAY or values.size() > 6:
		return false
	var captured: Dictionary = {}
	for placement: Variant in values:
		if typeof(placement) != TYPE_DICTIONARY or not _exact(placement, ["anchorId", "anchorKind", "footprint", "placementId", "positionMm", "proof", "rotationMilliDegrees"]):
			return false
		var placement_id: Variant = placement["placementId"]
		if typeof(placement_id) != TYPE_STRING or not placement_id_map.has(placement_id) or captured.has(placement_id) or placement["anchorKind"] not in ["floor", "wall"] or not _vector(placement["positionMm"], 1000000) or not _vector(placement["rotationMilliDegrees"], 360000):
			return false
		var footprint: Variant = placement["footprint"]
		if typeof(footprint) != TYPE_DICTIONARY or not _exact(footprint, ["depthMm", "heightMm", "widthMm"]) or not _positive(footprint["widthMm"], 100000) or not _positive(footprint["heightMm"], 100000) or not _positive(footprint["depthMm"], 100000):
			return false
		var proof: Variant = placement["proof"]
		if typeof(proof) != TYPE_DICTIONARY or not _exact(proof, ["clearanceVerified", "nonOverlapping", "supportVerified"]) or proof != {"clearanceVerified": true, "nonOverlapping": true, "supportVerified": true}:
			return false
		captured[placement_id] = true
	return captured.size() == placement_id_map.size()


static func _valid_contexts(values: Variant, runtime: Dictionary, placements: Array) -> bool:
	if typeof(values) != TYPE_ARRAY or values.is_empty() or values.size() > 16:
		return false
	var nodes: Dictionary = {}
	for node: Variant in runtime.get("nodes", []):
		if typeof(node) == TYPE_DICTIONARY:
			nodes[node.get("id")] = node.get("actions", []).size()
	var selected: Dictionary = {}
	for placement: Variant in placements:
		selected[placement["placementId"]] = true
	var captured: Dictionary = {}
	for context: Variant in values:
		if typeof(context) != TYPE_DICTIONARY or not _exact(context, ["actionTerminal", "approachPathFloorAnchorIds", "nodeId", "playerSpawn", "visiblePlacementIds", "zoneId"]):
			return false
		var node_id: Variant = context["nodeId"]
		if typeof(node_id) != TYPE_STRING or not nodes.has(node_id) or captured.has(node_id):
			return false
		if not _valid_spawn(context["playerSpawn"]) or not _valid_terminal(context["actionTerminal"], nodes[node_id]):
			return false
		var visible: Variant = context["visiblePlacementIds"]
		if typeof(visible) != TYPE_ARRAY or visible.size() > selected.size():
			return false
		var visible_set: Dictionary = {}
		for solution_placement_id: Variant in visible:
			if typeof(solution_placement_id) != TYPE_STRING or not selected.has(solution_placement_id) or visible_set.has(solution_placement_id):
				return false
			visible_set[solution_placement_id] = true
		captured[node_id] = true
	return captured.size() == nodes.size()


static func _valid_spawn(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["floorAnchorId", "positionMm", "yawMilliDegrees"]) and typeof(value["floorAnchorId"]) == TYPE_STRING and _vector(value["positionMm"], 1000000) and typeof(value["yawMilliDegrees"]) == TYPE_INT and abs(value["yawMilliDegrees"]) <= 180000


static func _valid_terminal(value: Variant, action_count: int) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["actionCount", "approachFloorAnchorId", "floorAnchorId", "footprint", "positionMm", "terminalSupports", "yawMilliDegrees"]) or value["actionCount"] != action_count or not _vector(value["positionMm"], 1000000) or typeof(value["yawMilliDegrees"]) != TYPE_INT or abs(value["yawMilliDegrees"]) > 180000:
		return false
	var footprint: Variant = value["footprint"]
	if typeof(footprint) != TYPE_DICTIONARY or not _exact(footprint, ["columns", "depthMm", "layoutCenterOffsetMm", "layoutDepthMm", "layoutWidthMm", "widthMm"]):
		return false
	var columns: Variant = footprint["columns"]
	var maximum_columns := maxi(1, mini(8, action_count))
	if typeof(columns) != TYPE_INT or columns < 1 or columns > maximum_columns or footprint["widthMm"] != 1250 or footprint["depthMm"] != 500:
		return false
	var rows := maxi(1, ceili(float(action_count) / float(columns)))
	if footprint["layoutWidthMm"] != 1250 + ((columns - 1) * 1700) or \
		footprint["layoutDepthMm"] != 500 + ((rows - 1) * 2250) or \
		footprint["layoutCenterOffsetMm"] != [0, -2400 - (((rows - 1) * 2250) / 2)]:
		return false
	var supports: Variant = value["terminalSupports"]
	if typeof(supports) != TYPE_ARRAY or supports.size() != action_count:
		return false
	for support: Variant in supports:
		if typeof(support) != TYPE_DICTIONARY or not _exact(support, ["baseHeightMm", "floorAnchorId"]) or typeof(support["floorAnchorId"]) != TYPE_STRING or typeof(support["baseHeightMm"]) != TYPE_INT or abs(support["baseHeightMm"]) > 1000000:
			return false
	return true


static func _valid_verification(value: Dictionary, solution_bytes: PackedByteArray, solution: Dictionary,
	assembly_bytes: PackedByteArray, assembly: Dictionary) -> bool:
	if not _exact(value, ["checks", "evidenceSha256", "format", "formatVersion", "solutionSha256", "verifier", "visualSafety"]):
		return false
	var verifier: Variant = value["verifier"]
	var checks: Variant = value["checks"]
	var visual_safety: Variant = value["visualSafety"]
	if typeof(verifier) != TYPE_DICTIONARY or not _exact(verifier, ["godotVersion", "id", "version"]) or typeof(checks) != TYPE_DICTIONARY or not _exact(checks, ["nodeContextCount", "pathCount", "placementCount", "terminalCount", "visualSafetyBoxCount"]) or not _valid_visual_safety(visual_safety, assembly_bytes, assembly):
		return false
	var terminal_count := 0
	for context: Variant in solution["nodeContexts"]:
		terminal_count += context["actionTerminal"]["actionCount"]
	return value["format"] == "matrix-oasis.prototype-spatial-verification-report" and value["formatVersion"] == "0.1.0" and value["solutionSha256"] == "sha256:" + _sha256(solution_bytes) and _hash(value["evidenceSha256"]) and verifier == {"godotVersion": "4.6.3", "id": "godot-spatial-solution-verifier", "version": "0.1.0-r14"} and checks["placementCount"] == solution["placements"].size() and checks["nodeContextCount"] == solution["nodeContexts"].size() and checks["pathCount"] == terminal_count and checks["terminalCount"] == terminal_count and checks["visualSafetyBoxCount"] == visual_safety["boxes"].size()


static func _valid_visual_safety(value: Variant, assembly_bytes: PackedByteArray, assembly: Dictionary) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["acceptedPointCount", "boxes", "cellPointThreshold", "cellSizeMm", "minimumCellPoints", "minimumComponentCells", "minimumVerticalBins", "occupiedCellCount", "peakThresholdPermille", "profile", "sampledPointCount", "sourceSplatSha256", "spatialAssemblySha256", "verticalBandMm", "verticalCellSizeMm", "visualRegistrationOffsetMm"]):
		return false
	if value["profile"] != "gaussian-vertical-occupancy-v1" or value["cellSizeMm"] != 250 or value["verticalCellSizeMm"] != 500 or value["verticalBandMm"] != [350, 3000] or value["minimumCellPoints"] != 16 or value["peakThresholdPermille"] != 25 or value["minimumVerticalBins"] != 3 or value["minimumComponentCells"] != 3:
		return false
	var splat_hash: Variant = assembly.get("environment", {}).get("splat", {}).get("sha256")
	if value["spatialAssemblySha256"] != "sha256:" + _sha256(assembly_bytes) or value["sourceSplatSha256"] != splat_hash or not _hash(splat_hash):
		return false
	if typeof(value["visualRegistrationOffsetMm"]) != TYPE_INT or abs(value["visualRegistrationOffsetMm"]) > 3000 or not _positive(value["sampledPointCount"], 640000) or typeof(value["acceptedPointCount"]) != TYPE_INT or value["acceptedPointCount"] < 0 or value["acceptedPointCount"] > 640000 or not _positive(value["cellPointThreshold"], 640000) or value["cellPointThreshold"] < 16 or typeof(value["occupiedCellCount"]) != TYPE_INT or value["occupiedCellCount"] < 0 or value["occupiedCellCount"] > 1000000:
		return false
	if typeof(value["boxes"]) != TYPE_ARRAY or value["boxes"].size() > 512:
		return false
	for box: Variant in value["boxes"]:
		if typeof(box) != TYPE_DICTIONARY or not _exact(box, ["centerMm", "sizeMm"]) or not _vector(box["centerMm"], 2000000) or not _vector(box["sizeMm"], 2000000) or box["sizeMm"].any(func(item: Variant) -> bool: return item < 1):
			return false
	return true


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


static func _millimeters(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0


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
