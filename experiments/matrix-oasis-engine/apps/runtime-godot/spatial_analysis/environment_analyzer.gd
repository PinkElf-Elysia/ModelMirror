extends Node

signal geometry_parsed
signal navigation_baked

const REQUEST_ARGUMENT := "--matrix-oasis-analysis-request="
const OUTPUT_ARGUMENT := "--matrix-oasis-analysis-output="
const REQUEST_MAX_BYTES := 2 * 1024 * 1024
const FACTS_MAX_BYTES := 16 * 1024 * 1024
const COLLIDER_MAX_BYTES := 32 * 1024 * 1024
const READY_MARKER := "MATRIX_OASIS_R13_SPATIAL_ANALYSIS_READY"
const FAILURE_MARKER := "MATRIX_OASIS_R13_SPATIAL_ANALYSIS_FAILED"
const GRID_MM := 1000
const MAX_FLOOR_ANCHORS := 65536
const MAX_WALL_ANCHORS := 65536
const PLAYER_RADIUS := 0.35
const PLAYER_HEIGHT := 1.8
const FLOOR_SNAP := 0.2
const MAX_SLOPE_DEGREES := 45.0
const WALL_QUERY_DISTANCE := 20.0

var _collision_body: StaticBody3D


func _ready() -> void:
	var paths := _parse_arguments(OS.get_cmdline_user_args())
	if not paths["ok"]:
		_fail()
		return
	var request := _load_request(paths["request"])
	if not request["ok"]:
		_fail()
		return
	var result: Dictionary = await _analyze(request["value"])
	if not result["ok"]:
		_fail()
		return
	var text := JSON.stringify(result["facts"])
	var bytes := text.to_utf8_buffer()
	if bytes.is_empty() or bytes.size() > FACTS_MAX_BYTES:
		_fail()
		return
	var file := FileAccess.open(paths["output"], FileAccess.WRITE)
	if file == null:
		_fail()
		return
	file.store_buffer(bytes)
	file.flush()
	if file.get_error() != OK:
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
	var parsed := MatrixOasisStrictJson.parse_bytes(bytes, "/analysisRequest")
	if not parsed["ok"] or not _valid_request(parsed["value"]):
		return {"ok": false}
	var value: Dictionary = parsed["value"]
	var collider_path: String = value["colliderPath"]
	if not collider_path.is_absolute_path() or collider_path.get_base_dir() != path.get_base_dir():
		return {"ok": false}
	var collider := _read_file(collider_path, COLLIDER_MAX_BYTES)
	if collider.is_empty() or collider.size() != value["source"]["collider"]["byteLength"]:
		return {"ok": false}
	if "sha256:" + _sha256(collider) != value["source"]["collider"]["sha256"]:
		return {"ok": false}
	return {"ok": true, "value": value, "collider": collider}


func _valid_request(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or not _exact(value, ["colliderPath", "format", "formatVersion", "source"]):
		return false
	if value["format"] != "matrix-oasis.godot-environment-analysis-request" or value["formatVersion"] != "0.1.0" or typeof(value["colliderPath"]) != TYPE_STRING:
		return false
	var source: Variant = value["source"]
	if typeof(source) != TYPE_DICTIONARY or not _exact(source, ["blueprint", "calibration", "collider", "environmentBundleSha256", "runtime", "scene", "spatialEnvironmentBundle"]):
		return false
	return _valid_source_identity(source)


func _valid_source_identity(source: Dictionary) -> bool:
	var scene: Variant = source["scene"]
	var blueprint: Variant = source["blueprint"]
	var runtime: Variant = source["runtime"]
	var bundle: Variant = source["spatialEnvironmentBundle"]
	var collider: Variant = source["collider"]
	var calibration: Variant = source["calibration"]
	if typeof(scene) != TYPE_DICTIONARY or not _exact(scene, ["contentVersion", "id"]):
		return false
	if typeof(blueprint) != TYPE_DICTIONARY or not _exact(blueprint, ["canonicalSha256", "format", "formatVersion"]):
		return false
	if typeof(runtime) != TYPE_DICTIONARY or not _exact(runtime, ["artifactSha256", "contentVersion", "format", "formatVersion", "id", "sourceSha256"]):
		return false
	if typeof(bundle) != TYPE_DICTIONARY or not _exact(bundle, ["canonicalSha256", "format", "formatVersion"]):
		return false
	if typeof(collider) != TYPE_DICTIONARY or not _exact(collider, ["byteLength", "format", "sha256"]):
		return false
	if typeof(calibration) != TYPE_DICTIONARY or not _exact(calibration, ["coordinateTransform", "godotRotationMilliDegrees", "godotTranslationMm", "groundPlaneOffsetMm", "metricScaleMicros"]):
		return false
	if scene["id"] != runtime["id"] or scene["contentVersion"] != runtime["contentVersion"]:
		return false
	if blueprint["format"] != "matrix-oasis.scene-blueprint" or blueprint["formatVersion"] != "0.1.0":
		return false
	if runtime["format"] != "matrix-oasis.runtime-game-pack" or runtime["formatVersion"] != "0.1.0":
		return false
	if bundle["format"] != "matrix-oasis.prototype-spatial-environment-bundle" or bundle["formatVersion"] != "0.1.0":
		return false
	if collider["format"] != "glb" or not _bounded_integer(collider["byteLength"], 1, COLLIDER_MAX_BYTES):
		return false
	for hash_value in [blueprint["canonicalSha256"], runtime["sourceSha256"], runtime["artifactSha256"], bundle["canonicalSha256"], source["environmentBundleSha256"], collider["sha256"]]:
		if not _valid_hash(hash_value):
			return false
	return _valid_calibration(calibration)


func _valid_calibration(value: Dictionary) -> bool:
	if value["coordinateTransform"] != "spz-raw-ply-to-godot-v1":
		return false
	if not _bounded_integer(value["metricScaleMicros"], 1, 100000000) or not _bounded_integer(value["groundPlaneOffsetMm"], -1000000, 1000000):
		return false
	return _integer_vector(value["godotTranslationMm"], -1000000, 1000000) and _integer_vector(value["godotRotationMilliDegrees"], -360000, 360000)


func _analyze(request: Dictionary) -> Dictionary:
	var collider_bytes := _read_file(request["colliderPath"], COLLIDER_MAX_BYTES)
	var scene_root := _load_glb(collider_bytes, request["colliderPath"])
	if scene_root == null:
		return {"ok": false}
	var calibration: Dictionary = request["source"]["calibration"]
	var root_transform := _calibration_transform(calibration)
	var faces := _collect_faces(scene_root, root_transform)
	scene_root.free()
	if faces.is_empty():
		return {"ok": false}
	var mesh := _mesh_from_faces(faces)
	if mesh == null:
		return {"ok": false}
	_collision_body = StaticBody3D.new()
	_collision_body.collision_layer = 1
	_collision_body.collision_mask = 0
	var collision_shape := CollisionShape3D.new()
	var shape := ConcavePolygonShape3D.new()
	shape.set_faces(faces)
	collision_shape.shape = shape
	_collision_body.add_child(collision_shape)
	add_child(_collision_body)
	var geometry_root := Node3D.new()
	var mesh_instance := MeshInstance3D.new()
	mesh_instance.mesh = mesh
	geometry_root.add_child(mesh_instance)
	add_child(geometry_root)
	await get_tree().physics_frame
	var navigation_mesh := NavigationMesh.new()
	navigation_mesh.agent_radius = PLAYER_RADIUS
	navigation_mesh.agent_height = PLAYER_HEIGHT
	navigation_mesh.agent_max_slope = MAX_SLOPE_DEGREES
	navigation_mesh.agent_max_climb = FLOOR_SNAP
	navigation_mesh.cell_size = 0.25
	navigation_mesh.cell_height = 0.1
	navigation_mesh.set_source_geometry_mode(NavigationMesh.SOURCE_GEOMETRY_ROOT_NODE_CHILDREN)
	navigation_mesh.set_parsed_geometry_type(NavigationMesh.PARSED_GEOMETRY_MESH_INSTANCES)
	var source_data := NavigationMeshSourceGeometryData3D.new()
	NavigationServer3D.parse_source_geometry_data(navigation_mesh, source_data, geometry_root, Callable(self, "_on_geometry_parsed"))
	await geometry_parsed
	NavigationServer3D.bake_from_source_geometry_data_async(navigation_mesh, source_data, Callable(self, "_on_navigation_baked"))
	await navigation_baked
	geometry_root.queue_free()
	if navigation_mesh.get_vertices().is_empty() or navigation_mesh.get_polygon_count() < 1:
		return {"ok": false}
	var topology := _stable_navigation(navigation_mesh)
	if not topology["ok"]:
		return {"ok": false}
	var environment_bounds := _bounds_from_faces(faces)
	var floor_anchors: Array = await _floor_anchors(topology, environment_bounds)
	if floor_anchors.is_empty():
		return {"ok": false}
	var wall_anchors := _wall_anchors(floor_anchors, environment_bounds)
	var facts := {
		"format": "matrix-oasis.prototype-environment-facts",
		"formatVersion": "0.1.0",
		"canonicalization": "matrix-oasis.canonical-json/1",
		"source": request["source"].duplicate(true),
		"coordinateSystem": {"handedness": "right", "upAxis": "Y", "unit": "millimeter", "eulerOrder": "YXZ"},
		"analysisProfile": {"playerRadiusMm": 350, "playerHeightMm": 1800, "floorSnapMm": 200, "maxSlopeMilliDegrees": 45000},
		"environmentBounds": environment_bounds,
		"navigationMesh": {"verticesMm": topology["vertices"], "polygons": topology["polygons"], "components": topology["components"]},
		"floorAnchors": floor_anchors,
		"wallAnchors": wall_anchors,
	}
	return {"ok": true, "facts": facts}


func _on_geometry_parsed() -> void:
	geometry_parsed.emit()


func _on_navigation_baked() -> void:
	navigation_baked.emit()


func _calibration_transform(value: Dictionary) -> Transform3D:
	var rotation_values: Array = value["godotRotationMilliDegrees"]
	var rotation := Vector3(
		deg_to_rad(float(rotation_values[0]) / 1000.0),
		deg_to_rad(float(rotation_values[1]) / 1000.0),
		deg_to_rad(float(rotation_values[2]) / 1000.0)
	)
	var scale := float(value["metricScaleMicros"]) / 1000000.0
	var basis := Basis.from_euler(rotation, EULER_ORDER_YXZ).scaled(Vector3.ONE * scale)
	var translation_values: Array = value["godotTranslationMm"]
	var origin := Vector3(float(translation_values[0]), float(translation_values[1] - value["groundPlaneOffsetMm"]), float(translation_values[2])) / 1000.0
	return Transform3D(basis, origin)


func _load_glb(bytes: PackedByteArray, source_path: String) -> Node3D:
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	if document.append_from_buffer(bytes, source_path, state) != OK:
		return null
	return document.generate_scene(state)


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


func _mesh_from_faces(faces: PackedVector3Array) -> ArrayMesh:
	if faces.size() < 3 or faces.size() % 3 != 0:
		return null
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = faces
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return mesh


func _stable_navigation(navigation_mesh: NavigationMesh) -> Dictionary:
	var original: PackedVector3Array = navigation_mesh.get_vertices()
	var vertices: Array = []
	var seen_vertices := {}
	for vector in original:
		var quantized := _vector_mm(vector)
		var key := _vector_key(quantized)
		if not seen_vertices.has(key):
			seen_vertices[key] = quantized
			vertices.append(quantized)
	vertices.sort_custom(_vector_less)
	var index_by_key := {}
	for index in vertices.size():
		index_by_key[_vector_key(vertices[index])] = index
	var polygons: Array = []
	for polygon_index in navigation_mesh.get_polygon_count():
		var raw: PackedInt32Array = navigation_mesh.get_polygon(polygon_index)
		var remapped: Array = []
		for original_index in raw:
			var mapped: int = index_by_key[_vector_key(_vector_mm(original[original_index]))]
			if mapped not in remapped:
				remapped.append(mapped)
		if remapped.size() >= 3:
			polygons.append({"vertexIndices": _normalize_cycle(remapped), "componentIndex": -1})
	polygons.sort_custom(func(left: Dictionary, right: Dictionary) -> bool:
		return _integer_array_key(left["vertexIndices"]) < _integer_array_key(right["vertexIndices"])
	)
	if vertices.is_empty() or polygons.is_empty():
		return {"ok": false}
	var adjacency: Array = []
	for index in polygons.size():
		adjacency.append([])
	for left in polygons.size():
		for right in range(left + 1, polygons.size()):
			var shared := 0
			for vertex_index in polygons[left]["vertexIndices"]:
				if vertex_index in polygons[right]["vertexIndices"]:
					shared += 1
			if shared >= 2:
				adjacency[left].append(right)
				adjacency[right].append(left)
	var components: Array = []
	var assigned := {}
	for start in polygons.size():
		if assigned.has(start):
			continue
		var pending := [start]
		var polygon_indices: Array = []
		while not pending.is_empty():
			var current: int = pending.pop_front()
			if assigned.has(current):
				continue
			assigned[current] = true
			polygon_indices.append(current)
			for neighbor in adjacency[current]:
				if not assigned.has(neighbor):
					pending.append(neighbor)
		polygon_indices.sort()
		var component_index := components.size()
		for polygon_index in polygon_indices:
			polygons[polygon_index]["componentIndex"] = component_index
		components.append({"index": component_index, "polygonIndices": polygon_indices, "bounds": _bounds_for_polygons(vertices, polygons, polygon_indices)})
	return {"ok": true, "vertices": vertices, "polygons": polygons, "components": components}


func _floor_anchors(topology: Dictionary, environment_bounds: Dictionary) -> Array:
	var candidates := {}
	var vertices: Array = topology["vertices"]
	var polygons: Array = topology["polygons"]
	for polygon_index in polygons.size():
		var polygon: Dictionary = polygons[polygon_index]
		var polygon_vertices: Array = polygon["vertexIndices"].map(func(index: int) -> Array: return vertices[index])
		var minimum_x: int = polygon_vertices.map(func(item: Array) -> int: return item[0]).min()
		var maximum_x: int = polygon_vertices.map(func(item: Array) -> int: return item[0]).max()
		var minimum_z: int = polygon_vertices.map(func(item: Array) -> int: return item[2]).min()
		var maximum_z: int = polygon_vertices.map(func(item: Array) -> int: return item[2]).max()
		var average_y := int(round(float(polygon_vertices.reduce(func(total: int, item: Array) -> int: return total + item[1], 0)) / polygon_vertices.size()))
		var start_x := int(ceil(float(minimum_x) / GRID_MM)) * GRID_MM
		var start_z := int(ceil(float(minimum_z) / GRID_MM)) * GRID_MM
		for x in range(start_x, maximum_x + 1, GRID_MM):
			for z in range(start_z, maximum_z + 1, GRID_MM):
				var point := [x, average_y, z]
				if _point_in_polygon_xz(point, polygon_vertices):
					candidates[_vector_key(point)] = {"position": point, "polygonIndex": polygon_index, "componentIndex": polygon["componentIndex"]}
		var centroid := [
			int(round(float(polygon_vertices.reduce(func(total: int, item: Array) -> int: return total + item[0], 0)) / polygon_vertices.size())),
			average_y,
			int(round(float(polygon_vertices.reduce(func(total: int, item: Array) -> int: return total + item[2], 0)) / polygon_vertices.size())),
		]
		candidates[_vector_key(centroid)] = {"position": centroid, "polygonIndex": polygon_index, "componentIndex": polygon["componentIndex"]}
	var ordered: Array = candidates.values()
	ordered.sort_custom(func(left: Dictionary, right: Dictionary) -> bool: return _vector_less(left["position"], right["position"]))
	var output: Array = []
	var space := get_world_3d().direct_space_state
	var capsule := CapsuleShape3D.new()
	capsule.radius = PLAYER_RADIUS
	capsule.height = PLAYER_HEIGHT
	for candidate in ordered:
		if output.size() >= MAX_FLOOR_ANCHORS:
			break
		var source_mm: Array = candidate["position"]
		var source := Vector3(float(source_mm[0]), float(source_mm[1]), float(source_mm[2])) / 1000.0
		var ray := PhysicsRayQueryParameters3D.create(source + Vector3.UP, source - Vector3.UP, 1)
		ray.collide_with_areas = false
		var hit := space.intersect_ray(ray)
		if hit.is_empty():
			continue
		var normal: Vector3 = hit["normal"]
		if normal.y < cos(deg_to_rad(MAX_SLOPE_DEGREES)):
			continue
		var floor_position: Vector3 = hit["position"]
		var shape_query := PhysicsShapeQueryParameters3D.new()
		shape_query.shape = capsule
		shape_query.collision_mask = 1
		shape_query.collide_with_areas = false
		shape_query.transform = Transform3D(Basis.IDENTITY, floor_position + Vector3.UP * (PLAYER_HEIGHT * 0.5 + 0.025))
		if not space.intersect_shape(shape_query, 1).is_empty():
			continue
		var ceiling_ray := PhysicsRayQueryParameters3D.create(floor_position + Vector3.UP * 0.05, floor_position + Vector3.UP * 100.0, 1)
		var ceiling_hit := space.intersect_ray(ceiling_ray)
		var ceiling_mm := 100000 if ceiling_hit.is_empty() else int(round((ceiling_hit["position"] as Vector3).distance_to(floor_position) * 1000.0))
		if ceiling_mm < 1800:
			continue
		var anchor := {
			"id": "floor-%04d" % output.size(),
			"positionMm": _vector_mm(floor_position),
			"normalMicros": _normal_micros(normal),
			"clearanceRadiusMm": 350,
			"clearanceHeightMm": 1800,
			"ceilingHeightMm": ceiling_mm,
			"componentIndex": candidate["componentIndex"],
			"polygonIndex": candidate["polygonIndex"],
			"capsuleClearanceVerified": true,
		}
		if _inside_bounds(anchor["positionMm"], environment_bounds):
			output.append(anchor)
	return output


func _wall_anchors(floor_anchors: Array, environment_bounds: Dictionary) -> Array:
	var output: Array = []
	var seen := {}
	var space := get_world_3d().direct_space_state
	var directions := [Vector3.RIGHT, Vector3.LEFT, Vector3.FORWARD, Vector3.BACK]
	for floor_anchor in floor_anchors:
		var floor_position := Vector3(float(floor_anchor["positionMm"][0]), float(floor_anchor["positionMm"][1]), float(floor_anchor["positionMm"][2])) / 1000.0
		var origin := floor_position + Vector3.UP * 1.2
		for direction in directions:
			if output.size() >= MAX_WALL_ANCHORS:
				return output
			var query := PhysicsRayQueryParameters3D.create(origin, origin + direction * WALL_QUERY_DISTANCE, 1)
			var hit := space.intersect_ray(query)
			if hit.is_empty():
				continue
			var normal: Vector3 = hit["normal"]
			if abs(normal.y) > 0.25:
				continue
			var position_mm := _vector_mm(hit["position"])
			var normal_mm := _normal_micros(normal)
			var key := "%d,%d,%d:%d,%d,%d" % [int(round(float(position_mm[0]) / 500.0)), int(round(float(position_mm[1]) / 500.0)), int(round(float(position_mm[2]) / 500.0)), int(round(float(normal_mm[0]) / 100000.0)), int(round(float(normal_mm[1]) / 100000.0)), int(round(float(normal_mm[2]) / 100000.0))]
			if seen.has(key) or not _inside_bounds(position_mm, environment_bounds):
				continue
			seen[key] = true
			output.append({
				"id": "wall-%04d" % output.size(),
				"positionMm": position_mm,
				"normalMicros": normal_mm,
				"availableWidthMm": _wall_width(space, hit["position"], normal),
				"availableHeightMm": min(3000, floor_anchor["ceilingHeightMm"]),
				"nearestFloorAnchorId": floor_anchor["id"],
			})
	output.sort_custom(func(left: Dictionary, right: Dictionary) -> bool: return _vector_less(left["positionMm"], right["positionMm"]))
	for index in output.size():
		output[index]["id"] = "wall-%04d" % index
	return output


func _wall_width(space: PhysicsDirectSpaceState3D, point: Vector3, normal: Vector3) -> int:
	var tangent := Vector3(normal.z, 0.0, -normal.x).normalized()
	var total_steps := 1
	for sign_value in [-1.0, 1.0]:
		for step in range(1, 21):
			var origin := point + tangent * sign_value * float(step) * 0.25 + normal * 0.1
			var query := PhysicsRayQueryParameters3D.create(origin, origin - normal * 0.25, 1)
			if space.intersect_ray(query).is_empty():
				break
			total_steps += 1
	return max(250, total_steps * 250)


func _bounds_from_faces(faces: PackedVector3Array) -> Dictionary:
	var minimum := faces[0]
	var maximum := faces[0]
	for vector in faces:
		minimum = minimum.min(vector)
		maximum = maximum.max(vector)
	return {"minimumMm": _vector_mm(minimum), "maximumMm": _vector_mm(maximum)}


func _bounds_for_polygons(vertices: Array, polygons: Array, polygon_indices: Array) -> Dictionary:
	var points: Array = []
	for polygon_index in polygon_indices:
		for vertex_index in polygons[polygon_index]["vertexIndices"]:
			points.append(vertices[vertex_index])
	var minimum := points[0].duplicate()
	var maximum := points[0].duplicate()
	for point in points:
		for axis in 3:
			minimum[axis] = min(minimum[axis], point[axis])
			maximum[axis] = max(maximum[axis], point[axis])
	return {"minimumMm": minimum, "maximumMm": maximum}


func _point_in_polygon_xz(point: Array, vertices: Array) -> bool:
	var inside := false
	var previous := vertices.size() - 1
	for index in vertices.size():
		var current: Array = vertices[index]
		var prior: Array = vertices[previous]
		var cross := (point[0] - prior[0]) * (current[2] - prior[2]) - (point[2] - prior[2]) * (current[0] - prior[0])
		if abs(cross) <= 1 and point[0] >= min(prior[0], current[0]) and point[0] <= max(prior[0], current[0]) and point[2] >= min(prior[2], current[2]) and point[2] <= max(prior[2], current[2]):
			return true
		if (current[2] > point[2]) != (prior[2] > point[2]) and float(point[0]) < float(prior[0] - current[0]) * float(point[2] - current[2]) / float(prior[2] - current[2]) + float(current[0]):
			inside = not inside
		previous = index
	return inside


func _normalize_cycle(values: Array) -> Array:
	var minimum_value: int = values.min()
	var start := values.find(minimum_value)
	var forward: Array = []
	var reverse: Array = []
	for offset in values.size():
		forward.append(values[(start + offset) % values.size()])
		reverse.append(values[(start - offset + values.size()) % values.size()])
	return forward if _integer_array_key(forward) <= _integer_array_key(reverse) else reverse


func _integer_array_key(values: Array) -> String:
	var parts := PackedStringArray()
	for value in values:
		parts.append("%08d" % value)
	return ",".join(parts)


func _vector_mm(value: Vector3) -> Array:
	return [int(round(value.x * 1000.0)), int(round(value.y * 1000.0)), int(round(value.z * 1000.0))]


func _normal_micros(value: Vector3) -> Array:
	var normalized := value.normalized()
	return [int(round(normalized.x * 1000000.0)), int(round(normalized.y * 1000000.0)), int(round(normalized.z * 1000000.0))]


func _vector_key(value: Array) -> String:
	return "%d,%d,%d" % [value[0], value[1], value[2]]


func _vector_less(left: Array, right: Array) -> bool:
	for axis in 3:
		if left[axis] != right[axis]:
			return left[axis] < right[axis]
	return false


func _inside_bounds(point: Array, bounds: Dictionary) -> bool:
	for axis in 3:
		if point[axis] < bounds["minimumMm"][axis] or point[axis] > bounds["maximumMm"][axis]:
			return false
	return true


func _read_file(path: String, maximum: int) -> PackedByteArray:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return PackedByteArray()
	var length := file.get_length()
	if length < 1 or length > maximum:
		return PackedByteArray()
	var bytes := file.get_buffer(length)
	return bytes if bytes.size() == length else PackedByteArray()


func _sha256(bytes: PackedByteArray) -> String:
	var context := HashingContext.new()
	if context.start(HashingContext.HASH_SHA256) != OK or context.update(bytes) != OK:
		return ""
	return context.finish().hex_encode()


func _exact(value: Dictionary, keys: Array) -> bool:
	var actual: Array = value.keys()
	actual.sort()
	var expected := keys.duplicate()
	expected.sort()
	return actual == expected


func _bounded_integer(value: Variant, minimum: int, maximum: int) -> bool:
	return typeof(value) == TYPE_INT and value >= minimum and value <= maximum


func _integer_vector(value: Variant, minimum: int, maximum: int) -> bool:
	return typeof(value) == TYPE_ARRAY and value.size() == 3 and value.all(func(item: Variant) -> bool: return _bounded_integer(item, minimum, maximum))


func _valid_hash(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or not value.begins_with("sha256:") or value.length() != 71:
		return false
	for character in value.substr(7):
		if character not in "0123456789abcdef":
			return false
	return true
