class_name MatrixOasisSolvedSpatialSceneLab
extends MatrixOasisSceneLab

const PLAYER_RADIUS_MM := 350
const PLAYER_HEIGHT_MM := 1800

var _solution_placements: Dictionary = {}
var _solution_contexts: Dictionary = {}
var _current_visible: Dictionary = {}
var _layout_applied := false
var _force_spawn := true


func set_solution_for_runtime(solution: Dictionary) -> bool:
	if is_node_ready() or not _solution_contexts.is_empty():
		return false
	for placement: Variant in solution.get("placements", []):
		if typeof(placement) != TYPE_DICTIONARY or _solution_placements.has(placement.get("placementId")):
			return false
		_solution_placements[placement["placementId"]] = placement.duplicate(true)
	for context: Variant in solution.get("nodeContexts", []):
		if typeof(context) != TYPE_DICTIONARY or _solution_contexts.has(context.get("nodeId")):
			return false
		_solution_contexts[context["nodeId"]] = context.duplicate(true)
	return not _solution_contexts.is_empty()


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
	if not world._apply_node_for_runtime(node_id):
		return false
	terminal_grid.global_transform = _anchor_transform(context["actionTerminal"]["positionMm"], context["actionTerminal"]["yawMilliDegrees"])
	if relocate:
		player.set_start_transform(_anchor_transform(context["playerSpawn"]["positionMm"], context["playerSpawn"]["yawMilliDegrees"]))
	_current_visible = next_visible
	_last_active_node_id = node_id
	_force_spawn = false
	return true


func _apply_solution_layout(world: MatrixOasisComposedScene) -> bool:
	var root := world._root_for_runtime()
	if root == null:
		return false
	for placement_id: String in _solution_placements:
		var node := root.get_node_or_null(NodePath(placement_id)) as Node3D
		var placement: Dictionary = _solution_placements[placement_id]
		if node == null:
			return false
		node.position = _millimeters(placement["positionMm"])
		node.rotation_order = EULER_ORDER_YXZ
		node.rotation_degrees = _milli_degrees(placement["rotationMilliDegrees"])
	_layout_applied = true
	return true


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
		var object_min_y: float = float(position[1])
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


static func _millimeters(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0


static func _milli_degrees(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0
