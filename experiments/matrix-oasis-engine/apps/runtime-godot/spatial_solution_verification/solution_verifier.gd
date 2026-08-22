extends Node3D

const REQUEST_ARGUMENT := "--matrix-oasis-verification-request="
const OUTPUT_ARGUMENT := "--matrix-oasis-verification-output="
const REQUEST_MAX_BYTES := 24 * 1024 * 1024
const GLB_MAX_BYTES := 32 * 1024 * 1024
const READY_MARKER := "MATRIX_OASIS_R14_SPATIAL_VERIFICATION_READY"
const FAILURE_MARKER := "MATRIX_OASIS_R14_SPATIAL_VERIFICATION_FAILED"
const PLAYER_RADIUS := 0.35
const PLAYER_HEIGHT := 1.8
const PLAYER_EYE_HEIGHT := 1.475
const FLOOR_CONTACT_TOLERANCE := 0.02
const PATH_ENDPOINT_TOLERANCE := 0.1
const FLOOR_SNAP_TOLERANCE := 0.2
const INTERACTION_DISTANCE := 3.0
const TERMINAL_COLUMN_SPACING := 1.7
const TERMINAL_ROW_SPACING := 2.25
const TERMINAL_ORIGIN_Z := -2.4
const TERMINAL_HALF_WIDTH := 0.625
const TERMINAL_HALF_DEPTH := 0.25
const MAX_TERMINAL_APPROACH_CANDIDATES := 256
const MAX_VISUAL_SAFETY_BOXES := 512
const MAX_WALKABLE_NORMAL_Y := 0.7071068
const PATH_SEPARATOR := "\u002f"
const StrictJson := preload("res://runtime/strict_json.gd")
const TerminalGrid := preload("res://playable/action_terminal_grid.gd")

var _environment_body: StaticBody3D
var _visual_safety_body: StaticBody3D
var _asset_bodies := {}
var _floor_anchors := {}
var _navigation_map := RID()
var _navigation_region := RID()


func _ready() -> void:
	var paths := _parse_arguments(OS.get_cmdline_user_args())
	if not paths["ok"]:
		_fail()
		return
	var loaded := _load_request(paths["request"])
	if not loaded["ok"]:
		_fail()
		return
	var result: Dictionary = await _verify(loaded["value"])
	_release_navigation()
	if result.is_empty() or not _write_result(paths["output"], result):
		_fail()
		return
	print(READY_MARKER)
	get_tree().quit(0)


func _fail() -> void:
	print(FAILURE_MARKER)
	get_tree().quit(2)


func _parse_arguments(arguments: PackedStringArray) -> Dictionary:
	var request_path := ""
	var output_path := ""
	for argument in arguments:
		if argument.begins_with(REQUEST_ARGUMENT) and request_path.is_empty():
			request_path = argument.substr(REQUEST_ARGUMENT.length())
		elif argument.begins_with(OUTPUT_ARGUMENT) and output_path.is_empty():
			output_path = argument.substr(OUTPUT_ARGUMENT.length())
		else:
			return {"ok": false}
	if request_path.is_empty() or output_path.is_empty() or not request_path.is_absolute_path() or not output_path.is_absolute_path():
		return {"ok": false}
	if request_path.get_base_dir() != output_path.get_base_dir() or FileAccess.file_exists(output_path):
		return {"ok": false}
	return {"ok": true, "request": request_path, "output": output_path}


func _load_request(path: String) -> Dictionary:
	var bytes := _read_file(path, REQUEST_MAX_BYTES)
	if bytes.is_empty():
		return {"ok": false}
	var parsed: Dictionary = StrictJson.parse_bytes(bytes, "/verificationRequest")
	if not parsed["ok"] or not _valid_request(parsed["value"]):
		return {"ok": false}
	var value: Dictionary = parsed["value"]
	var root := path.get_base_dir()
	var artifacts: Array = [value["environmentCollider"]]
	for placement in value["placements"]:
		artifacts.append(placement["visual"])
		artifacts.append(placement["collider"])
	for artifact in artifacts:
		var artifact_path: String = artifact["path"]
		if not artifact_path.is_absolute_path() or not _is_contained(root, artifact_path):
			return {"ok": false}
		var artifact_bytes := _read_file(artifact_path, GLB_MAX_BYTES)
		if artifact_bytes.is_empty() or artifact_bytes.size() != artifact["byteLength"] or "sha256:" + _sha256(artifact_bytes) != artifact["sha256"]:
			return {"ok": false}
	return {"ok": true, "value": value}


func _valid_request(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["analysisTransform", "environmentCollider", "floorAnchors", "format", "formatVersion", "navigationMesh", "nodeContexts", "placements", "runtimeSupportHeightMm", "selectedPolygonIndices", "solutionSha256", "visualSafety"]):
		return false
	if value["format"] != "matrix-oasis.godot-spatial-solution-verification-request" or value["formatVersion"] != "0.1.0" or not _hash(value["solutionSha256"]):
		return false
	if not _bounded_integer(value["runtimeSupportHeightMm"], -1000000, 1000000) or typeof(value["selectedPolygonIndices"]) != TYPE_ARRAY or value["selectedPolygonIndices"].is_empty():
		return false
	if not _valid_artifact(value["environmentCollider"]) or not _valid_analysis_transform(value["analysisTransform"]):
		return false
	if not _valid_visual_safety(value["visualSafety"]):
		return false
	if typeof(value["floorAnchors"]) != TYPE_ARRAY or typeof(value["placements"]) != TYPE_ARRAY or typeof(value["nodeContexts"]) != TYPE_ARRAY:
		return false
	if value["placements"].size() > 6 or value["nodeContexts"].is_empty() or value["nodeContexts"].size() > 16:
		return false
	for anchor in value["floorAnchors"]:
		if typeof(anchor) != TYPE_DICTIONARY or not _exact(anchor, ["capsuleClearanceVerified", "ceilingHeightMm", "clearanceHeightMm", "clearanceRadiusMm", "componentIndex", "id", "normalMicros", "polygonIndex", "positionMm"]):
			return false
		if typeof(anchor["id"]) != TYPE_STRING or not _vector(anchor["positionMm"]) or anchor["capsuleClearanceVerified"] != true:
			return false
	for placement in value["placements"]:
		if not _valid_placement(placement):
			return false
	for context in value["nodeContexts"]:
		if not _valid_context(context):
			return false
	if not _valid_navigation(value["navigationMesh"]):
		return false
	var captured_polygons := {}
	for polygon_index in value["selectedPolygonIndices"]:
		if not _bounded_integer(polygon_index, 0, value["navigationMesh"]["polygons"].size() - 1) or captured_polygons.has(polygon_index):
			return false
		captured_polygons[polygon_index] = true
	return true


func _valid_visual_safety(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["acceptedPointCount", "boxes", "cellPointThreshold", "cellSizeMm", "minimumCellPoints", "minimumComponentCells", "minimumVerticalBins", "occupiedCellCount", "peakThresholdPermille", "profile", "sampledPointCount", "sourceSplatSha256", "spatialAssemblySha256", "verticalBandMm", "verticalCellSizeMm", "visualRegistrationOffsetMm"]):
		return false
	if value["profile"] != "gaussian-vertical-occupancy-v1" or value["cellSizeMm"] != 250 or value["verticalCellSizeMm"] != 500 or value["verticalBandMm"] != [350, 3000] or value["minimumCellPoints"] != 16 or value["peakThresholdPermille"] != 25 or value["minimumVerticalBins"] != 3 or value["minimumComponentCells"] != 3:
		return false
	if not _hash(value["sourceSplatSha256"]) or not _hash(value["spatialAssemblySha256"]) or not _bounded_integer(value["visualRegistrationOffsetMm"], -3000, 3000) or not _bounded_integer(value["sampledPointCount"], 1, 640000) or not _bounded_integer(value["acceptedPointCount"], 0, 640000) or not _bounded_integer(value["cellPointThreshold"], 16, 640000) or not _bounded_integer(value["occupiedCellCount"], 0, 1000000):
		return false
	if typeof(value["boxes"]) != TYPE_ARRAY or value["boxes"].size() > MAX_VISUAL_SAFETY_BOXES:
		return false
	for box in value["boxes"]:
		if typeof(box) != TYPE_DICTIONARY or not _exact(box, ["centerMm", "sizeMm"]) or not _vector(box["centerMm"]) or not _vector(box["sizeMm"]):
			return false
		if box["sizeMm"].any(func(item: Variant) -> bool: return not _bounded_integer(item, 1, 2000000)):
			return false
	return true


func _valid_artifact(value: Variant) -> bool:
	return typeof(value) == TYPE_DICTIONARY and _exact(value, ["byteLength", "path", "sha256"]) and typeof(value["path"]) == TYPE_STRING and _bounded_integer(value["byteLength"], 1, GLB_MAX_BYTES) and _hash(value["sha256"])


func _valid_placement(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["anchorId", "anchorKind", "collider", "footprint", "placementId", "positionMm", "rotationMilliDegrees", "visual"]):
		return false
	if typeof(value["placementId"]) != TYPE_STRING or typeof(value["anchorId"]) != TYPE_STRING or value["anchorKind"] not in ["floor", "wall"]:
		return false
	var footprint: Variant = value["footprint"]
	return _vector(value["positionMm"]) and _vector(value["rotationMilliDegrees"]) and _valid_artifact(value["visual"]) and _valid_artifact(value["collider"]) and typeof(footprint) == TYPE_DICTIONARY and _exact(footprint, ["depthMm", "heightMm", "widthMm"])


func _valid_context(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["actionTerminal", "approachPathFloorAnchorIds", "nodeId", "playerSpawn", "visiblePlacementIds", "zoneId"]):
		return false
	var spawn: Variant = value["playerSpawn"]
	var terminal: Variant = value["actionTerminal"]
	if typeof(value["nodeId"]) != TYPE_STRING or typeof(value["zoneId"]) != TYPE_STRING or typeof(value["visiblePlacementIds"]) != TYPE_ARRAY or typeof(value["approachPathFloorAnchorIds"]) != TYPE_ARRAY:
		return false
	if typeof(spawn) != TYPE_DICTIONARY or not _exact(spawn, ["floorAnchorId", "positionMm", "yawMilliDegrees"]) or typeof(spawn["floorAnchorId"]) != TYPE_STRING or not _vector(spawn["positionMm"]):
		return false
	if typeof(terminal) != TYPE_DICTIONARY or not _exact(terminal, ["actionCount", "approachFloorAnchorId", "floorAnchorId", "footprint", "positionMm", "terminalSupports", "yawMilliDegrees"]):
		return false
	if typeof(terminal["approachFloorAnchorId"]) != TYPE_STRING or not _vector(terminal["positionMm"]) or not _bounded_integer(terminal["actionCount"], 0, 64) or not _valid_terminal_footprint(terminal["footprint"], terminal["actionCount"]):
		return false
	var supports: Variant = terminal["terminalSupports"]
	if typeof(supports) != TYPE_ARRAY or supports.size() != terminal["actionCount"]:
		return false
	for support: Variant in supports:
		if typeof(support) != TYPE_DICTIONARY or not _exact(support, ["baseHeightMm", "floorAnchorId"]) or typeof(support["floorAnchorId"]) != TYPE_STRING or not _bounded_integer(support["baseHeightMm"], -1000000, 1000000):
			return false
	return true


func _valid_terminal_footprint(value: Variant, action_count: int) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["columns", "depthMm", "layoutCenterOffsetMm", "layoutDepthMm", "layoutWidthMm", "widthMm"]):
		return false
	var columns: Variant = value["columns"]
	var maximum_columns := maxi(1, mini(8, action_count))
	if not _bounded_integer(columns, 1, maximum_columns) or value["widthMm"] != 1250 or value["depthMm"] != 500:
		return false
	var rows := maxi(1, ceili(float(action_count) / float(columns)))
	return value["layoutWidthMm"] == 1250 + ((columns - 1) * 1700) and \
		value["layoutDepthMm"] == 500 + ((rows - 1) * 2250) and \
		value["layoutCenterOffsetMm"] == [0, -2400 - (((rows - 1) * 2250) / 2)]


func _valid_navigation(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["components", "polygons", "verticesMm"]) or typeof(value["verticesMm"]) != TYPE_ARRAY or typeof(value["polygons"]) != TYPE_ARRAY or typeof(value["components"]) != TYPE_ARRAY:
		return false
	if value["verticesMm"].is_empty() or value["polygons"].is_empty() or value["components"].is_empty():
		return false
	for vertex in value["verticesMm"]:
		if not _vector(vertex):
			return false
	for polygon in value["polygons"]:
		if typeof(polygon) != TYPE_DICTIONARY or not _exact(polygon, ["componentIndex", "vertexIndices"]) or typeof(polygon["vertexIndices"]) != TYPE_ARRAY or polygon["vertexIndices"].size() < 3:
			return false
		for index in polygon["vertexIndices"]:
			if not _bounded_integer(index, 0, value["verticesMm"].size() - 1):
				return false
	return true


func _valid_analysis_transform(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["collider", "eulerOrder", "profile", "root", "sourceCanonicalSha256"]):
		return false
	if value["profile"] not in ["spatial-environment-calibration-v1", "spatial-assembly-collider-v1"] or value["eulerOrder"] != "YXZ" or not _hash(value["sourceCanonicalSha256"]):
		return false
	var root: Variant = value["root"]
	var collider: Variant = value["collider"]
	return typeof(root) == TYPE_DICTIONARY and _exact(root, ["rotationMilliDegrees", "translationMm"]) and _vector(root["rotationMilliDegrees"]) and _vector(root["translationMm"]) and typeof(collider) == TYPE_DICTIONARY and _exact(collider, ["localTranslationMm", "scaleMicros"]) and _vector(collider["localTranslationMm"]) and _bounded_integer(collider["scaleMicros"], 1, 100000000)


func _verify(request: Dictionary) -> Dictionary:
	for anchor in request["floorAnchors"]:
		var runtime_anchor: Dictionary = anchor.duplicate(true)
		runtime_anchor["positionMm"][1] = request["runtimeSupportHeightMm"]
		_floor_anchors[anchor["id"]] = runtime_anchor
	var environment_root := _load_glb(request["environmentCollider"]["path"])
	if environment_root == null:
		return {}
	var environment_faces := _collect_faces(environment_root, _analysis_transform(request["analysisTransform"]))
	environment_root.free()
	if environment_faces.is_empty():
		return {}
	if not _build_navigation(request["navigationMesh"], request["selectedPolygonIndices"],
		request["runtimeSupportHeightMm"], environment_faces, request["visualSafety"]):
		return {}
	for sync_frame in 8:
		await get_tree().physics_frame
		if NavigationServer3D.map_get_iteration_id(_navigation_map) > 0:
			break
	if NavigationServer3D.map_get_iteration_id(_navigation_map) == 0:
		return {}
	var placement_result := await _build_placements(request["placements"])
	if not placement_result["ok"]:
		return placement_result
	var checked_paths := 0
	var checked_terminals := 0
	for index in request["nodeContexts"].size():
		var checked: Dictionary = await _verify_context(index, request["nodeContexts"][index])
		if not checked["ok"]:
			return checked
		checked_paths += checked["pathCount"]
		checked_terminals += checked["terminalCount"]
	return {
		"ok": true,
		"format": "matrix-oasis.godot-spatial-solution-verification",
		"formatVersion": "0.1.0",
		"solutionSha256": request["solutionSha256"],
		"placementCount": request["placements"].size(),
		"nodeContextCount": request["nodeContexts"].size(),
		"checkedPathCount": checked_paths,
		"checkedTerminalCount": checked_terminals,
		"checkedVisualSafetyBoxCount": request["visualSafety"]["boxes"].size(),
		"allChecksPassed": true,
	}


func _release_navigation() -> void:
	if _navigation_region.is_valid():
		NavigationServer3D.free_rid(_navigation_region)
		_navigation_region = RID()
	if _navigation_map.is_valid():
		NavigationServer3D.free_rid(_navigation_map)
		_navigation_map = RID()


func _build_navigation(value: Dictionary, selected_polygon_indices: Array,
	runtime_support_height_mm: int, environment_faces: PackedVector3Array,
	visual_safety: Dictionary) -> bool:
	var mesh := NavigationMesh.new()
	var vertices := PackedVector3Array()
	for item in value["verticesMm"]:
		vertices.append(Vector3(float(item[0]), float(runtime_support_height_mm), float(item[2])) / 1000.0)
	mesh.set_vertices(vertices)
	var floor_faces := PackedVector3Array()
	var edges := {}
	for polygon_index in selected_polygon_indices:
		var item: Dictionary = value["polygons"][polygon_index]
		var polygon := PackedInt32Array()
		for vertex_index in item["vertexIndices"]:
			polygon.append(vertex_index)
		mesh.add_polygon(polygon)
		for triangle_index in range(1, polygon.size() - 1):
			floor_faces.append(vertices[polygon[0]])
			floor_faces.append(vertices[polygon[triangle_index]])
			floor_faces.append(vertices[polygon[triangle_index + 1]])
		for edge_index in polygon.size():
			var first: int = polygon[edge_index]
			var second: int = polygon[(edge_index + 1) % polygon.size()]
			var key := "%d:%d" % [mini(first, second), maxi(first, second)]
			if edges.has(key):
				edges[key]["count"] += 1
			else:
				edges[key] = {"count": 1, "first": first, "second": second}
	var boundary_faces := PackedVector3Array()
	for key in edges:
		var edge: Dictionary = edges[key]
		if edge["count"] != 1:
			continue
		var first: Vector3 = vertices[edge["first"]]
		var second: Vector3 = vertices[edge["second"]]
		var first_top := first + Vector3.UP * PLAYER_HEIGHT
		var second_top := second + Vector3.UP * PLAYER_HEIGHT
		boundary_faces.append_array(PackedVector3Array([first, second, second_top, first, second_top, first_top]))
	if floor_faces.is_empty() or boundary_faces.is_empty():
		return false
	var collision_faces := floor_faces.duplicate()
	collision_faces.append_array(boundary_faces)
	collision_faces.append_array(_steep_environment_faces(environment_faces))
	var visual_safety_faces := PackedVector3Array()
	for box: Dictionary in visual_safety["boxes"]:
		visual_safety_faces.append_array(_box_faces(box))
	_environment_body = _body_from_faces(collision_faces, 1, true)
	if _environment_body == null:
		return false
	add_child(_environment_body)
	if not visual_safety_faces.is_empty():
		_visual_safety_body = _body_from_faces(visual_safety_faces, 8, true)
		if _visual_safety_body == null:
			return false
		add_child(_visual_safety_body)
	_navigation_map = NavigationServer3D.map_create()
	NavigationServer3D.map_set_up(_navigation_map, Vector3.UP)
	NavigationServer3D.map_set_active(_navigation_map, true)
	_navigation_region = NavigationServer3D.region_create()
	NavigationServer3D.region_set_transform(_navigation_region, Transform3D.IDENTITY)
	NavigationServer3D.region_set_navigation_mesh(_navigation_region, mesh)
	NavigationServer3D.region_set_map(_navigation_region, _navigation_map)
	return true


func _box_faces(box: Dictionary) -> PackedVector3Array:
	var center := _to_vector(box["centerMm"])
	var half := _to_vector(box["sizeMm"]) * 0.5
	var x0 := center.x - half.x
	var x1 := center.x + half.x
	var y0 := center.y - half.y
	var y1 := center.y + half.y
	var z0 := center.z - half.z
	var z1 := center.z + half.z
	var a := Vector3(x0, y0, z0)
	var b := Vector3(x0, y0, z1)
	var c := Vector3(x1, y0, z1)
	var d := Vector3(x1, y0, z0)
	var e := Vector3(x0, y1, z0)
	var f := Vector3(x0, y1, z1)
	var g := Vector3(x1, y1, z1)
	var h := Vector3(x1, y1, z0)
	return PackedVector3Array([
		a, b, c, a, c, d, e, h, g, e, g, f,
		a, d, h, a, h, e, d, c, g, d, g, h,
		c, b, f, c, f, g, b, a, e, b, e, f,
	])


func _build_placements(values: Array) -> Dictionary:
	var space: PhysicsDirectSpaceState3D = get_world_3d().direct_space_state
	for index in values.size():
		var value: Dictionary = values[index]
		var visual_root := _load_glb(value["visual"]["path"])
		var collider_root := _load_glb(value["collider"]["path"])
		if visual_root == null or collider_root == null:
			if visual_root != null:
				visual_root.free()
			if collider_root != null:
				collider_root.free()
			return {}
		var placement_transform := _placement_transform(value)
		var visual_faces := _collect_faces(visual_root, placement_transform)
		if value["anchorKind"] == "floor":
			if not _floor_anchors.has(value["anchorId"]) or visual_faces.is_empty():
				visual_root.free()
				collider_root.free()
				return {}
			var floor_position := _to_vector(_floor_anchors[value["anchorId"]]["positionMm"])
			if Vector2(placement_transform.origin.x, placement_transform.origin.z).distance_to(
				Vector2(floor_position.x, floor_position.z)) > PATH_ENDPOINT_TOLERANCE:
				visual_root.free()
				collider_root.free()
				return _rejection("PROTOTYPE_SPATIAL_VERIFY_ASSET_GROUNDING_FAILED", "/placements/%d" % index)
			var floor_y := floor_position.y
			placement_transform.origin.y += floor_y - _minimum_y(visual_faces)
			visual_faces = _collect_faces(visual_root, placement_transform)
		var collider_faces := _collect_faces(collider_root, placement_transform)
		visual_root.free()
		collider_root.free()
		if visual_faces.is_empty() or collider_faces.is_empty():
			return {}
		if value["anchorKind"] == "floor":
			var floor_y := _to_vector(_floor_anchors[value["anchorId"]]["positionMm"]).y
			var minimum_y := _minimum_y(visual_faces)
			if abs(minimum_y - floor_y) > FLOOR_CONTACT_TOLERANCE:
				return _rejection("PROTOTYPE_SPATIAL_VERIFY_ASSET_GROUNDING_FAILED", "/placements/%d" % index)
		var convex := ConvexPolygonShape3D.new()
		convex.points = collider_faces
		var query := PhysicsShapeQueryParameters3D.new()
		query.shape = convex
		query.collision_mask = 1 | 2 | 8
		query.collide_with_areas = false
		query.transform = Transform3D(Basis.IDENTITY, Vector3.UP * (FLOOR_CONTACT_TOLERANCE + 0.001 if value["anchorKind"] == "floor" else 0.0))
		var collision := space.intersect_shape(query, 1)
		if not collision.is_empty():
			var collided_body := collision[0].get("collider") as CollisionObject3D
			if collided_body == null:
				return {}
			var code := "PROTOTYPE_SPATIAL_VERIFY_ASSET_PENETRATION" if collided_body.collision_layer == 1 else "PROTOTYPE_SPATIAL_VERIFY_ASSET_OVERLAP"
			return _rejection(code, "/placements/%d" % index)
		var body := _body_from_faces(collider_faces, 2)
		if body == null:
			return {}
		body.name = value["placementId"]
		add_child(body)
		_asset_bodies[value["placementId"]] = body
		await get_tree().physics_frame
	return {"ok": true}


func _verify_context(index: int, value: Dictionary) -> Dictionary:
	for placement_id in _asset_bodies:
		(_asset_bodies[placement_id] as StaticBody3D).collision_layer = 2 if placement_id in value["visiblePlacementIds"] else 0
	var terminal_grid := TerminalGrid.new() as MatrixOasisActionTerminalGrid
	add_child(terminal_grid)
	terminal_grid.global_transform = _yaw_transform(value["actionTerminal"]["positionMm"], value["actionTerminal"]["yawMilliDegrees"])
	for support: Dictionary in value["actionTerminal"]["terminalSupports"]:
		if not _floor_anchors.has(support["floorAnchorId"]):
			terminal_grid.queue_free()
			return {}
	var actions: Array = []
	for action_index in value["actionTerminal"]["actionCount"]:
		actions.append({"id": "action-%02d" % action_index, "label": "Action", "available": true})
	if not terminal_grid.rebuild(actions):
		terminal_grid.queue_free()
		return {}
	if not _apply_terminal_layout(terminal_grid, value["actionTerminal"]["footprint"],
		value["actionTerminal"]["terminalSupports"], actions.size()):
		terminal_grid.queue_free()
		return {}
	await get_tree().physics_frame
	var space: PhysicsDirectSpaceState3D = get_world_3d().direct_space_state
	for terminal_index in terminal_grid.get_terminal_count():
		var terminal := terminal_grid.get_terminal(terminal_index)
		var collision_shape := terminal.get_node_or_null("CollisionShape3D") as CollisionShape3D
		if collision_shape == null or collision_shape.shape == null:
			terminal_grid.queue_free()
			return {}
		var terminal_query := PhysicsShapeQueryParameters3D.new()
		terminal_query.shape = collision_shape.shape
		terminal_query.transform = collision_shape.global_transform
		terminal_query.collision_mask = 1 | 2
		terminal_query.collide_with_areas = false
		if not space.intersect_shape(terminal_query, 1).is_empty():
			terminal_grid.queue_free()
			return _rejection("PROTOTYPE_SPATIAL_VERIFY_TERMINAL_COLLISION", "/nodeContexts/%d/actionTerminal" % index)
	var spawn_floor := _to_vector(value["playerSpawn"]["positionMm"])
	var capsule := CapsuleShape3D.new()
	capsule.radius = PLAYER_RADIUS
	capsule.height = PLAYER_HEIGHT
	var capsule_query := PhysicsShapeQueryParameters3D.new()
	capsule_query.shape = capsule
	capsule_query.transform = Transform3D(Basis.IDENTITY, spawn_floor + Vector3.UP * (PLAYER_HEIGHT * 0.5 + 0.025))
	capsule_query.collision_mask = 1 | 2 | 4 | 8
	capsule_query.collide_with_areas = true
	if not space.intersect_shape(capsule_query, 1).is_empty():
		terminal_grid.queue_free()
		return _rejection("PROTOTYPE_SPATIAL_VERIFY_SPAWN_COLLISION", "/nodeContexts/%d/playerSpawn" % index)
	var navigation_spawn: Variant = _navigation_projection(spawn_floor)
	if navigation_spawn == null:
		terminal_grid.queue_free()
		return _rejection("PROTOTYPE_SPATIAL_VERIFY_PATH_UNREACHABLE", "/nodeContexts/%d/playerSpawn" % index)
	var approach_id: String = value["actionTerminal"]["approachFloorAnchorId"]
	if not _floor_anchors.has(approach_id):
		terminal_grid.queue_free()
		return _rejection("PROTOTYPE_SPATIAL_VERIFY_PATH_UNREACHABLE", "/nodeContexts/%d" % index)
	var approach: Vector3 = _to_vector(_floor_anchors[approach_id]["positionMm"])
	var access: Dictionary = _verify_anchor_access(approach, navigation_spawn, capsule_query, space)
	if not access["clear"]:
		var access_code: String = "PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED" if access["navigation"] else \
			"PROTOTYPE_SPATIAL_VERIFY_PATH_UNREACHABLE"
		terminal_grid.queue_free()
		return _rejection(access_code, "/nodeContexts/%d" % index)
	var navigation_approach: Vector3 = access["projection"]
	var terminal_count: int = terminal_grid.get_terminal_count()
	for terminal_index in terminal_count:
		var terminal: MatrixOasisActionTerminal3D = terminal_grid.get_terminal(terminal_index)
		var terminal_result: Dictionary = _verify_terminal_access(index, terminal, terminal_grid,
			navigation_approach, capsule_query, space)
		if not terminal_result["ok"]:
			terminal_grid.queue_free()
			return terminal_result
	remove_child(terminal_grid)
	terminal_grid.free()
	await get_tree().physics_frame
	return {"ok": true, "pathCount": terminal_count, "terminalCount": terminal_count}


func _verify_terminal_access(index: int, terminal: MatrixOasisActionTerminal3D,
	terminal_grid: MatrixOasisActionTerminalGrid, navigation_approach: Vector3,
	capsule_query: PhysicsShapeQueryParameters3D, space: PhysicsDirectSpaceState3D) -> Dictionary:
	if terminal == null:
		return {}
	var sight_target: Vector3 = terminal.global_position
	var candidates: Array[Dictionary] = []
	for anchor_id: String in _floor_anchors:
		var candidate: Vector3 = _to_vector(_floor_anchors[anchor_id]["positionMm"])
		if _candidate_overlaps_terminal_grid(candidate, terminal_grid):
			continue
		var eye_distance: float = (candidate + Vector3.UP * PLAYER_EYE_HEIGHT).distance_to(sight_target)
		if eye_distance <= INTERACTION_DISTANCE + 0.001:
			candidates.append({"id": anchor_id, "position": candidate, "distance": eye_distance})
	candidates.sort_custom(func(left: Dictionary, right: Dictionary) -> bool:
		var left_distance: float = left["distance"]
		var right_distance: float = right["distance"]
		return left_distance < right_distance or (is_equal_approx(left_distance, right_distance) and left["id"] < right["id"])
	)
	if candidates.size() > MAX_TERMINAL_APPROACH_CANDIDATES:
		candidates.resize(MAX_TERMINAL_APPROACH_CANDIDATES)
	var saw_navigation := false
	var saw_clear_path := false
	for candidate: Dictionary in candidates:
		var access: Dictionary = _verify_anchor_access(candidate["position"], navigation_approach, capsule_query, space)
		if not access["navigation"]:
			continue
		saw_navigation = true
		if not access["clear"]:
			continue
		saw_clear_path = true
		if _line_of_sight_clear(candidate["position"], sight_target, space):
			return {"ok": true}
	if saw_clear_path:
		return _rejection("PROTOTYPE_SPATIAL_VERIFY_TERMINAL_SIGHT_BLOCKED",
			"/nodeContexts/%d/actionTerminal" % index)
	if saw_navigation:
		return _rejection("PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED", "/nodeContexts/%d" % index)
	return _rejection("PROTOTYPE_SPATIAL_VERIFY_PATH_UNREACHABLE", "/nodeContexts/%d" % index)


func _candidate_overlaps_terminal_grid(candidate: Vector3, terminal_grid: MatrixOasisActionTerminalGrid) -> bool:
	for terminal_index in terminal_grid.get_terminal_count():
		var terminal: MatrixOasisActionTerminal3D = terminal_grid.get_terminal(terminal_index)
		if terminal == null:
			return true
		var local: Vector3 = terminal.to_local(candidate)
		if absf(local.x) < TERMINAL_HALF_WIDTH + PLAYER_RADIUS and \
			absf(local.z) < TERMINAL_HALF_DEPTH + PLAYER_RADIUS:
			return true
	return false


func _line_of_sight_clear(approach: Vector3, sight_target: Vector3,
	space: PhysicsDirectSpaceState3D) -> bool:
	var ray: PhysicsRayQueryParameters3D = PhysicsRayQueryParameters3D.create(approach + Vector3.UP * PLAYER_EYE_HEIGHT,
		sight_target, 1 | 2 | 8)
	ray.collide_with_areas = false
	return space.intersect_ray(ray).is_empty()


func _verify_anchor_access(approach: Vector3, navigation_spawn: Vector3,
	capsule_query: PhysicsShapeQueryParameters3D, space: PhysicsDirectSpaceState3D) -> Dictionary:
	var navigation_approach: Variant = _navigation_projection(approach)
	if navigation_approach == null:
		return {"navigation": false, "clear": false, "projection": Vector3.ZERO}
	capsule_query.transform = Transform3D(Basis.IDENTITY,
		approach + Vector3.UP * (PLAYER_HEIGHT * 0.5 + 0.025))
	capsule_query.motion = Vector3.ZERO
	capsule_query.collision_mask = 1 | 2 | 8
	capsule_query.collide_with_areas = false
	if not space.intersect_shape(capsule_query, 1).is_empty():
		return {"navigation": true, "clear": false, "projection": navigation_approach}
	var path: PackedVector3Array = _query_path(navigation_spawn, navigation_approach)
	if path.is_empty() or path[0].distance_to(navigation_spawn) > PATH_ENDPOINT_TOLERANCE or \
		path[path.size() - 1].distance_to(navigation_approach) > PATH_ENDPOINT_TOLERANCE:
		return {"navigation": false, "clear": false, "projection": navigation_approach}
	var clear: bool = _capsule_path_clear(path, capsule_query, space)
	return {"navigation": true, "clear": clear, "projection": navigation_approach}


func _capsule_path_clear(path: PackedVector3Array, capsule_query: PhysicsShapeQueryParameters3D,
	space: PhysicsDirectSpaceState3D) -> bool:
	for path_index in range(path.size() - 1):
		capsule_query.transform = Transform3D(Basis.IDENTITY,
			path[path_index] + Vector3.UP * (PLAYER_HEIGHT * 0.5 + 0.025))
		capsule_query.motion = path[path_index + 1] - path[path_index]
		var motion: PackedFloat32Array = space.cast_motion(capsule_query)
		if motion.is_empty() or motion[0] < 0.999:
			return false
	return true


func _apply_terminal_layout(terminal_grid: MatrixOasisActionTerminalGrid, footprint: Dictionary,
	terminal_supports: Array, action_count: int) -> bool:
	if terminal_supports.size() != action_count:
		return false
	var columns: int = footprint["columns"]
	for index in action_count:
		var terminal := terminal_grid.get_terminal(index)
		if terminal == null:
			return false
		var row := index / columns
		var column := index % columns
		var first_index := row * columns
		var row_count := mini(columns, action_count - first_index)
		var centered_column := float(column) - float(row_count - 1) * 0.5
		terminal.position = Vector3(centered_column * TERMINAL_COLUMN_SPACING, 0.85,
			TERMINAL_ORIGIN_Z - float(row) * TERMINAL_ROW_SPACING)
	return true


func _query_path(start: Vector3, target: Vector3) -> PackedVector3Array:
	var parameters := NavigationPathQueryParameters3D.new()
	parameters.map = _navigation_map
	parameters.start_position = start
	parameters.target_position = target
	parameters.navigation_layers = 1
	var result := NavigationPathQueryResult3D.new()
	NavigationServer3D.query_path(parameters, result)
	return result.get_path()


func _navigation_projection(position: Vector3) -> Variant:
	var projected := NavigationServer3D.map_get_closest_point(_navigation_map, position)
	var horizontal := Vector2(projected.x, projected.z).distance_to(Vector2(position.x, position.z))
	if horizontal > PATH_ENDPOINT_TOLERANCE or absf(projected.y - position.y) > FLOOR_SNAP_TOLERANCE:
		return null
	return projected


func _load_glb(path: String) -> Node3D:
	var bytes := _read_file(path, GLB_MAX_BYTES)
	if bytes.is_empty():
		return null
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	if document.append_from_buffer(bytes, path, state) != OK:
		return null
	return document.generate_scene(state)


func _body_from_faces(faces: PackedVector3Array, layer: int, backface_collision := false) -> StaticBody3D:
	if faces.is_empty() or faces.size() % 3 != 0:
		return null
	var shape := ConcavePolygonShape3D.new()
	shape.backface_collision = backface_collision
	shape.set_faces(faces)
	var collision := CollisionShape3D.new()
	collision.shape = shape
	var body := StaticBody3D.new()
	body.collision_layer = layer
	body.collision_mask = 0
	body.add_child(collision)
	return body


func _collect_faces(node: Node, parent_transform: Transform3D) -> PackedVector3Array:
	var local_transform := parent_transform
	if node is Node3D:
		local_transform = parent_transform * (node as Node3D).transform
	var faces := PackedVector3Array()
	if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
		for vertex in (node as MeshInstance3D).mesh.get_faces():
			faces.append(local_transform * vertex)
	for child in node.get_children():
		faces.append_array(_collect_faces(child, local_transform))
	return faces


func _steep_environment_faces(faces: PackedVector3Array) -> PackedVector3Array:
	var output := PackedVector3Array()
	for index in range(0, faces.size(), 3):
		if index + 2 >= faces.size():
			return PackedVector3Array()
		var first: Vector3 = faces[index]
		var normal := (faces[index + 1] - first).cross(faces[index + 2] - first)
		if normal.length_squared() <= 0.000000000001:
			continue
		if absf(normal.normalized().y) < MAX_WALKABLE_NORMAL_Y:
			output.append(first)
			output.append(faces[index + 1])
			output.append(faces[index + 2])
	return output


func _analysis_transform(value: Dictionary) -> Transform3D:
	var root: Dictionary = value["root"]
	var collider: Dictionary = value["collider"]
	var root_basis := Basis.from_euler(_rotation(root["rotationMilliDegrees"]), EULER_ORDER_YXZ)
	var root_transform := Transform3D(root_basis, _to_vector(root["translationMm"]))
	var scale := float(collider["scaleMicros"]) / 1000000.0
	return root_transform * Transform3D(Basis.IDENTITY.scaled(Vector3.ONE * scale), _to_vector(collider["localTranslationMm"]))


func _placement_transform(value: Dictionary) -> Transform3D:
	return Transform3D(Basis.from_euler(_rotation(value["rotationMilliDegrees"]), EULER_ORDER_YXZ), _to_vector(value["positionMm"]))


func _yaw_transform(position: Array, yaw_milli_degrees: int) -> Transform3D:
	return Transform3D(Basis(Vector3.UP, deg_to_rad(float(yaw_milli_degrees) / 1000.0)), _to_vector(position))


func _rotation(value: Array) -> Vector3:
	return Vector3(deg_to_rad(float(value[0]) / 1000.0), deg_to_rad(float(value[1]) / 1000.0), deg_to_rad(float(value[2]) / 1000.0))


func _to_vector(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0


func _minimum_y(faces: PackedVector3Array) -> float:
	var output := INF
	for vertex in faces:
		output = minf(output, vertex.y)
	return output


func _write_result(path: String, result: Dictionary) -> bool:
	var bytes := JSON.stringify(result).to_utf8_buffer()
	if bytes.is_empty() or bytes.size() > 1024 * 1024:
		return false
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		return false
	file.store_buffer(bytes)
	file.flush()
	return file.get_error() == OK


func _rejection(code: String, path: String) -> Dictionary:
	return {"ok": false, "code": code, "path": path}


func _read_file(path: String, maximum: int) -> PackedByteArray:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return PackedByteArray()
	var length := file.get_length()
	if length < 1 or length > maximum:
		return PackedByteArray()
	var bytes := file.get_buffer(length)
	return bytes if bytes.size() == length and file.get_position() == length else PackedByteArray()


func _sha256(bytes: PackedByteArray) -> String:
	var context := HashingContext.new()
	if context.start(HashingContext.HASH_SHA256) != OK or context.update(bytes) != OK:
		return ""
	return context.finish().hex_encode()


func _is_contained(root: String, target: String) -> bool:
	var normalized_root := root.replace("\\", PATH_SEPARATOR).simplify_path().trim_suffix(PATH_SEPARATOR)
	var normalized_target := target.replace("\\", PATH_SEPARATOR).simplify_path()
	return normalized_target.begins_with(normalized_root + PATH_SEPARATOR)


func _exact(value: Dictionary, keys: Array) -> bool:
	if value.size() != keys.size():
		return false
	for key in keys:
		if not value.has(key):
			return false
	return true


func _bounded_integer(value: Variant, minimum: int, maximum: int) -> bool:
	return typeof(value) == TYPE_INT and value >= minimum and value <= maximum


func _vector(value: Variant) -> bool:
	return typeof(value) == TYPE_ARRAY and value.size() == 3 and value.all(func(item: Variant) -> bool: return typeof(item) == TYPE_INT and item >= -1000000 and item <= 1000000)


func _hash(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or not value.begins_with("sha256:") or value.length() != 71:
		return false
	for index in range(7, 71):
		if value.unicode_at(index) not in range(48, 58) and value.unicode_at(index) not in range(97, 103):
			return false
	return true
