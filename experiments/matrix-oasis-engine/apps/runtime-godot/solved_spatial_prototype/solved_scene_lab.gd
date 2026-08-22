class_name MatrixOasisSolvedSpatialSceneLab
extends MatrixOasisSceneLab

const PLAYER_RADIUS_MM := 350
const PLAYER_HEIGHT_MM := 1800
const PLAYER_SPAWN_CLEARANCE_MM := 25
const NAVIGATION_DOMAIN_TOLERANCE := 0.05
const TERMINAL_COLUMN_SPACING := 1.7
const TERMINAL_ROW_SPACING := 2.25
const TERMINAL_ORIGIN_Z := -2.4
const TERMINAL_LABEL_VISIBILITY_RANGE := 3.0

var _solution_placements: Dictionary = {}
var _scene_placement_ids: Dictionary = {}
var _solution_contexts: Dictionary = {}
var _current_visible: Dictionary = {}
var _layout_applied := false
var _force_spawn := true
var _spatial_boundary_minimum := Vector3.ZERO
var _spatial_boundary_maximum := Vector3.ZERO
var _spatial_boundary_root := Transform3D.IDENTITY
var _spatial_boundary_configured := false
var _spatial_boundary_active := false
var _navigation_domain_faces := PackedVector3Array()
var _last_safe_player_transform := Transform3D.IDENTITY
var _runtime_support_height_mm := 0


func set_solution_for_runtime(solution: Dictionary, placement_id_map: Dictionary, runtime_support_height_mm: int) -> bool:
	if is_node_ready() or not _solution_contexts.is_empty() or abs(runtime_support_height_mm) > 1000000:
		return false
	_runtime_support_height_mm = runtime_support_height_mm
	for placement: Variant in solution.get("placements", []):
		if typeof(placement) != TYPE_DICTIONARY or _solution_placements.has(placement.get("placementId")):
			return false
		_solution_placements[placement["placementId"]] = placement.duplicate(true)
		if typeof(placement_id_map.get(placement["placementId"])) != TYPE_STRING:
			return false
		_scene_placement_ids[placement["placementId"]] = placement_id_map[placement["placementId"]]
	for context: Variant in solution.get("nodeContexts", []):
		if typeof(context) != TYPE_DICTIONARY or _solution_contexts.has(context.get("nodeId")):
			return false
		_solution_contexts[context["nodeId"]] = context.duplicate(true)
	return not _solution_contexts.is_empty()


func set_spatial_boundary(value: Dictionary, root_value: Dictionary, euler_order: String) -> bool:
	if is_node_ready() or _spatial_boundary_configured or euler_order != "YXZ":
		return false
	var minimum: Variant = value.get("minimumMm")
	var maximum: Variant = value.get("maximumMm")
	var translation: Variant = root_value.get("translationMm")
	var rotation: Variant = root_value.get("rotationMilliDegrees")
	for vector: Variant in [minimum, maximum, translation, rotation]:
		if typeof(vector) != TYPE_ARRAY or vector.size() != 3:
			return false
	_spatial_boundary_minimum = _millimeters(minimum)
	_spatial_boundary_maximum = _millimeters(maximum)
	if _spatial_boundary_minimum.x >= _spatial_boundary_maximum.x or _spatial_boundary_minimum.z >= _spatial_boundary_maximum.z:
		return false
	var root_rotation := _milli_degrees(rotation)
	_spatial_boundary_root = Transform3D(
		Basis.from_euler(root_rotation * PI / 180.0, EULER_ORDER_YXZ), _millimeters(translation))
	_spatial_boundary_configured = true
	return true


func set_navigation_domain(faces: PackedVector3Array) -> bool:
	if is_node_ready() or not _navigation_domain_faces.is_empty() or faces.is_empty() or faces.size() % 3 != 0:
		return false
	for index in range(0, faces.size(), 3):
		var first: Vector3 = faces[index]
		var second: Vector3 = faces[index + 1]
		var third: Vector3 = faces[index + 2]
		if not first.is_finite() or not second.is_finite() or not third.is_finite() or \
			absf((second - first).cross(third - first).y) <= 0.000001:
			return false
	_navigation_domain_faces = faces.duplicate()
	return true


func activate_spatial_boundary() -> bool:
	if not is_node_ready() or player == null or not _spatial_boundary_configured or \
		_navigation_domain_faces.is_empty() or not _inside_runtime_domain(player.global_position):
		return false
	_last_safe_player_transform = player.global_transform
	_spatial_boundary_active = true
	process_physics_priority = 100
	set_physics_process(true)
	return true


func _physics_process(_delta: float) -> void:
	if not _spatial_boundary_active or player == null:
		return
	var inside := _inside_runtime_domain(player.global_position)
	var vertical_delta := absf(player.global_position.y - _last_safe_player_transform.origin.y)
	if inside and player.is_on_floor():
		_last_safe_player_transform = player.global_transform
		return
	if inside and vertical_delta <= float(PLAYER_HEIGHT_MM) / 1000.0:
		return
	player.global_transform = _last_safe_player_transform
	player.velocity = Vector3.ZERO
	player.reset_physics_interpolation()


func _inside_spatial_boundary(global_position: Vector3) -> bool:
	var local := _spatial_boundary_root.affine_inverse() * global_position
	return local.x >= _spatial_boundary_minimum.x and local.x <= _spatial_boundary_maximum.x and \
		local.z >= _spatial_boundary_minimum.z and local.z <= _spatial_boundary_maximum.z


func _inside_runtime_domain(global_position: Vector3) -> bool:
	if not _inside_spatial_boundary(global_position):
		return false
	var point := Vector2(global_position.x, global_position.z)
	for index in range(0, _navigation_domain_faces.size(), 3):
		var first := Vector2(_navigation_domain_faces[index].x, _navigation_domain_faces[index].z)
		var second := Vector2(_navigation_domain_faces[index + 1].x, _navigation_domain_faces[index + 1].z)
		var third := Vector2(_navigation_domain_faces[index + 2].x, _navigation_domain_faces[index + 2].z)
		if _inside_triangle(point, first, second, third) or \
			Geometry2D.get_closest_point_to_segment(point, first, second).distance_to(point) <= NAVIGATION_DOMAIN_TOLERANCE or \
			Geometry2D.get_closest_point_to_segment(point, second, third).distance_to(point) <= NAVIGATION_DOMAIN_TOLERANCE or \
			Geometry2D.get_closest_point_to_segment(point, third, first).distance_to(point) <= NAVIGATION_DOMAIN_TOLERANCE:
			return true
	return false


static func _inside_triangle(point: Vector2, first: Vector2, second: Vector2, third: Vector2) -> bool:
	var denominator := (second.y - third.y) * (first.x - third.x) + \
		(third.x - second.x) * (first.y - third.y)
	if absf(denominator) <= 0.000001:
		return false
	var first_weight := ((second.y - third.y) * (point.x - third.x) + \
		(third.x - second.x) * (point.y - third.y)) / denominator
	var second_weight := ((third.y - first.y) * (point.x - third.x) + \
		(first.x - third.x) * (point.y - third.y)) / denominator
	var third_weight := 1.0 - first_weight - second_weight
	return first_weight >= 0.0 and second_weight >= 0.0 and third_weight >= 0.0


func _reset_session() -> bool:
	_force_spawn = true
	var result := super._reset_session()
	if not result:
		_force_spawn = false
	return result


func _apply_world_candidate(world: MatrixOasisComposedScene, inspection: Dictionary) -> bool:
	if inspection["status"] == "ended":
		terminal_grid.clear_terminals()
		return true
	var node_id: String = inspection["location"]["id"]
	var context: Variant = _solution_contexts.get(node_id)
	if typeof(context) != TYPE_DICTIONARY or not world._can_apply_node_for_runtime(node_id):
		return false
	if not _layout_applied and not _apply_solution_layout(world):
		return false
	var next_visible: Dictionary = {}
	for placement_id: Variant in context["visiblePlacementIds"]:
		next_visible[placement_id] = true
	var relocate := _force_spawn or _last_active_node_id.is_empty() or _new_visibility_overlaps_player(next_visible)
	if not terminal_grid.rebuild(inspection["actions"]):
		return false
	if not _apply_terminal_layout(context["actionTerminal"]["footprint"],
		context["actionTerminal"]["terminalSupports"], inspection["actions"].size()):
		return false
	if not _apply_solution_visibility(world, next_visible):
		return false
	terminal_grid.global_transform = _anchor_transform(_with_runtime_support(context["actionTerminal"]["positionMm"]), context["actionTerminal"]["yawMilliDegrees"])
	if relocate:
		var player_transform := _player_transform(_with_runtime_support(context["playerSpawn"]["positionMm"]), context["playerSpawn"]["yawMilliDegrees"])
		player.set_start_transform(player_transform)
		if _spatial_boundary_active:
			_last_safe_player_transform = player_transform
	_current_visible = next_visible
	_last_active_node_id = node_id
	_force_spawn = false
	return true


func _apply_solution_visibility(world: MatrixOasisComposedScene, next_visible: Dictionary) -> bool:
	var root := world._root_for_runtime()
	if root == null:
		return false
	for placement_id: String in _solution_placements:
		var node := root.get_node_or_null(NodePath(_scene_placement_ids[placement_id])) as Node3D
		if node == null:
			return false
		var shown := next_visible.has(placement_id)
		node.visible = shown
		for child: Node in node.find_children("*", "StaticBody3D", true, false):
			(child as StaticBody3D).collision_layer = 1 if shown else 0
	return true


func _apply_terminal_layout(footprint: Dictionary, terminal_supports: Array,
	action_count: int) -> bool:
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
		# Keep the world-space label available at interaction range without letting
		# distant terminals cover the runtime HUD on narrow viewports.
		terminal.label_3d.visibility_range_end = TERMINAL_LABEL_VISIBILITY_RANGE
	return true


func _apply_solution_layout(world: MatrixOasisComposedScene) -> bool:
	var root := world._root_for_runtime()
	if root == null:
		return false
	for placement_id: String in _solution_placements:
		var node := root.get_node_or_null(NodePath(_scene_placement_ids[placement_id])) as Node3D
		var placement: Dictionary = _solution_placements[placement_id]
		if node == null:
			return false
		var runtime_position: Array = placement["positionMm"]
		if placement["anchorKind"] == "floor":
			runtime_position = _with_runtime_support(runtime_position)
		node.position = _millimeters(runtime_position)
		node.rotation_order = EULER_ORDER_YXZ
		node.rotation_degrees = _milli_degrees(placement["rotationMilliDegrees"])
		if placement["anchorKind"] == "floor" and not _ground_visual_instance(node):
			return false
	_layout_applied = true
	return true


func _visible_solution_placement_count_for_test() -> int:
	if _composed_world == null:
		return 0
	var root := _composed_world._root_for_runtime()
	if root == null:
		return 0
	var count := 0
	for placement_id: String in _solution_placements:
		var node := root.get_node_or_null(NodePath(_scene_placement_ids[placement_id])) as Node3D
		if node != null and node.visible:
			count += 1
	return count


func _new_visibility_overlaps_player(next_visible: Dictionary) -> bool:
	if player == null:
		return true
	var player_mm := player.global_position * 1000.0
	var player_min_y := player_mm.y - float(PLAYER_HEIGHT_MM) * 0.5
	var player_max_y := player_mm.y + float(PLAYER_HEIGHT_MM) * 0.5
	for placement_id: String in next_visible:
		if _current_visible.has(placement_id):
			continue
		var placement: Variant = _solution_placements.get(placement_id)
		if typeof(placement) != TYPE_DICTIONARY:
			continue
		var position: Array = placement["positionMm"]
		var footprint: Dictionary = placement["footprint"]
		var object_min_y: float = float(_runtime_support_height_mm if placement["anchorKind"] == "floor" else position[1])
		var object_max_y: float = object_min_y + float(footprint["heightMm"])
		if player_max_y < object_min_y or player_min_y > object_max_y:
			continue
		var offset := Vector2(player_mm.x - float(position[0]), player_mm.z - float(position[2]))
		var yaw := -deg_to_rad(float(placement["rotationMilliDegrees"][1]) / 1000.0)
		var local := offset.rotated(yaw)
		if absf(local.x) <= float(footprint["widthMm"]) * 0.5 + PLAYER_RADIUS_MM and absf(local.y) <= float(footprint["depthMm"]) * 0.5 + PLAYER_RADIUS_MM:
			return true
	return false


static func _anchor_transform(position_mm: Array, yaw_milli_degrees: int) -> Transform3D:
	return Transform3D(Basis(Vector3.UP, deg_to_rad(float(yaw_milli_degrees) / 1000.0)), _millimeters(position_mm))


static func _player_transform(floor_position_mm: Array, yaw_milli_degrees: int) -> Transform3D:
	var body_position_mm: Array = floor_position_mm.duplicate()
	body_position_mm[1] += PLAYER_HEIGHT_MM / 2 + PLAYER_SPAWN_CLEARANCE_MM
	return _anchor_transform(body_position_mm, yaw_milli_degrees)


func _with_runtime_support(position_mm: Array) -> Array:
	var supported: Array = position_mm.duplicate()
	supported[1] = _runtime_support_height_mm
	return supported


func _ground_visual_instance(node: Node3D) -> bool:
	var minimum_y := INF
	var mesh_count := 0
	for child: Node in node.find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := child as MeshInstance3D
		if mesh_instance == null or mesh_instance.mesh == null:
			continue
		var bounds := mesh_instance.get_aabb()
		for endpoint_index in 8:
			var world_endpoint := mesh_instance.global_transform * bounds.get_endpoint(endpoint_index)
			minimum_y = minf(minimum_y, world_endpoint.y)
		mesh_count += 1
	if mesh_count == 0 or not is_finite(minimum_y):
		return false
	var grounded_position := node.global_position
	grounded_position.y += float(_runtime_support_height_mm) / 1000.0 - minimum_y
	node.global_position = grounded_position
	return true


func _runtime_support_consistent_for_test() -> bool:
	if player == null or terminal_grid == null or absf(terminal_grid.global_position.y - float(_runtime_support_height_mm) / 1000.0) > 0.001:
		return false
	if absf(player.global_position.y - (float(_runtime_support_height_mm + PLAYER_HEIGHT_MM / 2 + PLAYER_SPAWN_CLEARANCE_MM) / 1000.0)) > 0.05:
		return false
	if _composed_world == null:
		return false
	var root := _composed_world._root_for_runtime()
	if root == null:
		return false
	for placement_id: String in _solution_placements:
		var placement: Dictionary = _solution_placements[placement_id]
		var node := root.get_node_or_null(NodePath(_scene_placement_ids[placement_id])) as Node3D
		if node == null:
			return false
		if placement["anchorKind"] == "floor":
			var minimum_y := INF
			for child: Node in node.find_children("*", "MeshInstance3D", true, false):
				var mesh_instance := child as MeshInstance3D
				if mesh_instance == null or mesh_instance.mesh == null:
					continue
				var bounds := mesh_instance.get_aabb()
				for endpoint_index in 8:
					minimum_y = minf(minimum_y, (mesh_instance.global_transform * bounds.get_endpoint(endpoint_index)).y)
			if not is_finite(minimum_y) or absf(minimum_y - float(_runtime_support_height_mm) / 1000.0) > 0.02:
				return false
	for index in terminal_grid.get_terminal_count():
		var terminal := terminal_grid.get_terminal(index)
		if terminal == null or absf(terminal.position.y - 0.85) > 0.001:
			return false
	return true


static func _millimeters(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0


static func _milli_degrees(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0
