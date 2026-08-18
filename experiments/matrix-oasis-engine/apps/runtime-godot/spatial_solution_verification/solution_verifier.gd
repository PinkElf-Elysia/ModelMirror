extends Node3D

const REQUEST_ARGUMENT := "--matrix-oasis-verification-request="
const OUTPUT_ARGUMENT := "--matrix-oasis-verification-output="
const REQUEST_MAX_BYTES := 24 * 1024 * 1024
const GLB_MAX_BYTES := 32 * 1024 * 1024
const READY_MARKER := "MATRIX_OASIS_R14_SPATIAL_VERIFICATION_READY"
const FAILURE_MARKER := "MATRIX_OASIS_R14_SPATIAL_VERIFICATION_FAILED"
const PLAYER_RADIUS := 0.35
const PLAYER_HEIGHT := 1.8
const FLOOR_CONTACT_TOLERANCE := 0.02
const PATH_ENDPOINT_TOLERANCE := 0.1
const INTERACTION_DISTANCE := 3.0
const PATH_SEPARATOR := "\u002f"
const StrictJson := preload("res://runtime/strict_json.gd")
const TerminalGrid := preload("res://playable/action_terminal_grid.gd")

var _environment_body: StaticBody3D
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
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["analysisTransform", "environmentCollider", "floorAnchors", "format", "formatVersion", "navigationMesh", "nodeContexts", "placements", "solutionSha256"]):
		return false
	if value["format"] != "matrix-oasis.godot-spatial-solution-verification-request" or value["formatVersion"] != "0.1.0" or not _hash(value["solutionSha256"]):
		return false
	if not _valid_artifact(value["environmentCollider"]) or not _valid_analysis_transform(value["analysisTransform"]):
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
	return _valid_navigation(value["navigationMesh"])


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
	if typeof(terminal) != TYPE_DICTIONARY or not _exact(terminal, ["actionCount", "approachFloorAnchorId", "floorAnchorId", "footprint", "positionMm", "yawMilliDegrees"]):
		return false
	return typeof(terminal["approachFloorAnchorId"]) == TYPE_STRING and _vector(terminal["positionMm"]) and _bounded_integer(terminal["actionCount"], 0, 64)


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
		_floor_anchors[anchor["id"]] = anchor
	var environment_root := _load_glb(request["environmentCollider"]["path"])
	if environment_root == null:
		return {}
	var environment_faces := _collect_faces(environment_root, _analysis_transform(request["analysisTransform"]))
	environment_root.free()
	if environment_faces.is_empty():
		return {}
	_environment_body = _body_from_faces(environment_faces, 1)
	if _environment_body == null:
		return {}
	add_child(_environment_body)
	if not _build_navigation(request["navigationMesh"]):
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
		"allChecksPassed": true,
	}


func _release_navigation() -> void:
	if _navigation_region.is_valid():
		NavigationServer3D.free_rid(_navigation_region)
		_navigation_region = RID()
	if _navigation_map.is_valid():
		NavigationServer3D.free_rid(_navigation_map)
		_navigation_map = RID()


func _build_navigation(value: Dictionary) -> bool:
	var mesh := NavigationMesh.new()
	var vertices := PackedVector3Array()
	for item in value["verticesMm"]:
		vertices.append(_to_vector(item))
	mesh.set_vertices(vertices)
	for item in value["polygons"]:
		var polygon := PackedInt32Array()
		for vertex_index in item["vertexIndices"]:
			polygon.append(vertex_index)
		mesh.add_polygon(polygon)
	_navigation_map = NavigationServer3D.map_create()
	NavigationServer3D.map_set_up(_navigation_map, Vector3.UP)
	NavigationServer3D.map_set_active(_navigation_map, true)
	_navigation_region = NavigationServer3D.region_create()
	NavigationServer3D.region_set_transform(_navigation_region, Transform3D.IDENTITY)
	NavigationServer3D.region_set_navigation_mesh(_navigation_region, mesh)
	NavigationServer3D.region_set_map(_navigation_region, _navigation_map)
	return true


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
		var collider_faces := _collect_faces(collider_root, placement_transform)
		visual_root.free()
		collider_root.free()
		if visual_faces.is_empty() or collider_faces.is_empty():
			return {}
		if value["anchorKind"] == "floor":
			if not _floor_anchors.has(value["anchorId"]):
				return {}
			var floor_y := _to_vector(_floor_anchors[value["anchorId"]]["positionMm"]).y
			var minimum_y := _minimum_y(visual_faces)
			if abs(minimum_y - floor_y) > FLOOR_CONTACT_TOLERANCE:
				return _rejection("PROTOTYPE_SPATIAL_VERIFY_ASSET_GROUNDING_FAILED", "/placements/%d" % index)
		var convex := ConvexPolygonShape3D.new()
		convex.points = collider_faces
		var query := PhysicsShapeQueryParameters3D.new()
		query.shape = convex
		query.collision_mask = 1 | 2
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
	var actions: Array = []
	for action_index in value["actionTerminal"]["actionCount"]:
		actions.append({"id": "action-%02d" % action_index, "label": "Action", "available": true})
	if not terminal_grid.rebuild(actions):
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
	capsule_query.collision_mask = 1 | 2 | 4
	capsule_query.collide_with_areas = true
	if not space.intersect_shape(capsule_query, 1).is_empty():
		terminal_grid.queue_free()
		return _rejection("PROTOTYPE_SPATIAL_VERIFY_SPAWN_COLLISION", "/nodeContexts/%d/playerSpawn" % index)
	if not _floor_anchors.has(value["actionTerminal"]["approachFloorAnchorId"]):
		terminal_grid.queue_free()
		return {}
	var approach := _to_vector(_floor_anchors[value["actionTerminal"]["approachFloorAnchorId"]]["positionMm"])
	var path := _query_path(spawn_floor, approach)
	if path.size() < 2 or path[0].distance_to(spawn_floor) > PATH_ENDPOINT_TOLERANCE or path[path.size() - 1].distance_to(approach) > PATH_ENDPOINT_TOLERANCE:
		terminal_grid.queue_free()
		return _rejection("PROTOTYPE_SPATIAL_VERIFY_PATH_UNREACHABLE", "/nodeContexts/%d" % index)
	for path_index in range(path.size() - 1):
		capsule_query.transform = Transform3D(Basis.IDENTITY, path[path_index] + Vector3.UP * (PLAYER_HEIGHT * 0.5 + 0.025))
		capsule_query.motion = path[path_index + 1] - path[path_index]
		var motion: PackedFloat32Array = space.cast_motion(capsule_query)
		if motion.is_empty() or motion[0] < 0.999:
			terminal_grid.queue_free()
			return _rejection("PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED", "/nodeContexts/%d" % index)
	var sight_start := approach + Vector3.UP * 1.4
	var sight_target: Vector3 = terminal_grid.global_position + Vector3.UP * 0.85
	if sight_start.distance_to(sight_target) > INTERACTION_DISTANCE + PATH_ENDPOINT_TOLERANCE:
		terminal_grid.queue_free()
		return _rejection("PROTOTYPE_SPATIAL_VERIFY_TERMINAL_SIGHT_BLOCKED", "/nodeContexts/%d/actionTerminal" % index)
	var ray := PhysicsRayQueryParameters3D.create(sight_start, sight_target, 1 | 2)
	ray.collide_with_areas = false
	if not space.intersect_ray(ray).is_empty():
		terminal_grid.queue_free()
		return _rejection("PROTOTYPE_SPATIAL_VERIFY_TERMINAL_SIGHT_BLOCKED", "/nodeContexts/%d/actionTerminal" % index)
	var terminal_count := terminal_grid.get_terminal_count()
	remove_child(terminal_grid)
	terminal_grid.free()
	await get_tree().physics_frame
	return {"ok": true, "pathCount": 1, "terminalCount": terminal_count}


func _query_path(start: Vector3, target: Vector3) -> PackedVector3Array:
	var parameters := NavigationPathQueryParameters3D.new()
	parameters.map = _navigation_map
	parameters.start_position = start
	parameters.target_position = target
	parameters.navigation_layers = 1
	var result := NavigationPathQueryResult3D.new()
	NavigationServer3D.query_path(parameters, result)
	return result.get_path()


func _load_glb(path: String) -> Node3D:
	var bytes := _read_file(path, GLB_MAX_BYTES)
	if bytes.is_empty():
		return null
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	if document.append_from_buffer(bytes, path, state) != OK:
		return null
	return document.generate_scene(state)


func _body_from_faces(faces: PackedVector3Array, layer: int) -> StaticBody3D:
	if faces.is_empty() or faces.size() % 3 != 0:
		return null
	var shape := ConcavePolygonShape3D.new()
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
