class_name MatrixOasisSpatialSceneLab
extends MatrixOasisSceneLab

var _spatial_bindings: Dictionary = {}
var _spatial_root_transform := Transform3D.IDENTITY
var _navigation_cells: Array = []
var _navigation_bindings: Dictionary = {}
var _navigation_cell_size := 0.0
var _navigation_agent_half_height := 0.0
var _navigation_maximum_step := 0.0
var _navigation_active := false
var _last_safe_player_transform := Transform3D.IDENTITY


func set_spatial_binding_layout(layout: Array, root_value: Dictionary, euler_order: String) -> bool:
	if is_node_ready() or layout.is_empty() or euler_order != "YXZ":
		return false
	var translation: Variant = root_value.get("translationMm")
	var rotation: Variant = root_value.get("rotationMilliDegrees")
	if typeof(translation) != TYPE_ARRAY or translation.size() != 3 or typeof(rotation) != TYPE_ARRAY or rotation.size() != 3:
		return false
	var root_rotation := Vector3(float(rotation[0]), float(rotation[1]), float(rotation[2])) / 1000.0
	_spatial_root_transform = Transform3D(
		Basis.from_euler(root_rotation * PI / 180.0, EULER_ORDER_YXZ),
		Vector3(float(translation[0]), float(translation[1]), float(translation[2])) / 1000.0,
	)
	var captured: Dictionary = {}
	for item: Variant in layout:
		if typeof(item) != TYPE_DICTIONARY:
			return false
		var node_id: Variant = item.get("nodeId")
		if typeof(node_id) != TYPE_STRING or node_id.is_empty() or captured.has(node_id):
			return false
		var player_spawn: Variant = item.get("playerSpawn")
		var action_anchor: Variant = item.get("actionAnchor")
		if not _valid_spatial_anchor(player_spawn) or not _valid_spatial_anchor(action_anchor):
			return false
		captured[node_id] = {
			"playerSpawn": player_spawn.duplicate(true),
			"actionAnchor": action_anchor.duplicate(true),
		}
	_spatial_bindings = captured
	return true


func set_spatial_navigation(value: Dictionary) -> bool:
	if is_node_ready() or _spatial_bindings.is_empty() or value.get("profile") != "collider-agent-grid-v1":
		return false
	if value.get("cellSizeMm") != 1000 or value.get("agentRadiusMm") != 350 or value.get("agentHeightMm") != 2000 or value.get("maximumStepMm") != 450 or value.get("minimumClearanceMm") != 700:
		return false
	var cells: Variant = value.get("cells")
	var bindings: Variant = value.get("bindings")
	if typeof(cells) != TYPE_ARRAY or cells.is_empty() or typeof(bindings) != TYPE_ARRAY or bindings.size() != _spatial_bindings.size():
		return false
	var captured_cells: Array = []
	for cell: Variant in cells:
		if typeof(cell) != TYPE_ARRAY or cell.size() != 3:
			return false
		captured_cells.append(Vector3(float(cell[0]), float(cell[1]), float(cell[2])) / 1000.0)
	var captured_bindings: Dictionary = {}
	for binding: Variant in bindings:
		if typeof(binding) != TYPE_DICTIONARY:
			return false
		var node_id: Variant = binding.get("nodeId")
		var player_index: Variant = binding.get("playerCellIndex")
		var terminal_index: Variant = binding.get("terminalCellIndex")
		if typeof(node_id) != TYPE_STRING or not _spatial_bindings.has(node_id) or captured_bindings.has(node_id) or typeof(player_index) != TYPE_INT or player_index < 0 or player_index >= captured_cells.size() or typeof(terminal_index) != TYPE_INT or terminal_index < 0 or terminal_index >= captured_cells.size():
			return false
		var anchor: Dictionary = _spatial_bindings[node_id]["playerSpawn"]
		var anchor_position := Vector3(float(anchor["positionMm"][0]), float(anchor["positionMm"][1]), float(anchor["positionMm"][2])) / 1000.0
		var player_cell: Vector3 = captured_cells[player_index]
		if not Vector2(anchor_position.x, anchor_position.z).is_equal_approx(Vector2(player_cell.x, player_cell.z)) or not is_equal_approx(anchor_position.y, player_cell.y + 1.0):
			return false
		captured_bindings[node_id] = {
			"playerCellIndex": player_index,
			"terminalCellIndex": terminal_index,
		}
	_navigation_cells = captured_cells
	_navigation_bindings = captured_bindings
	_navigation_cell_size = float(value["cellSizeMm"]) / 1000.0
	_navigation_agent_half_height = float(value["agentHeightMm"]) / 2000.0
	_navigation_maximum_step = float(value["maximumStepMm"]) / 1000.0
	return true


func activate_spatial_navigation() -> bool:
	if not is_node_ready() or player == null or _navigation_cells.is_empty() or _navigation_bindings.is_empty():
		return false
	if _navigation_cell_for_player(player.global_position) < 0:
		return false
	_last_safe_player_transform = player.global_transform
	_navigation_active = true
	process_physics_priority = 100
	set_physics_process(true)
	return true


func _physics_process(_delta: float) -> void:
	if not _navigation_active or player == null:
		return
	if _navigation_cell_for_player(player.global_position) >= 0:
		_last_safe_player_transform = player.global_transform
		return
	player.global_transform = _last_safe_player_transform
	player.velocity = Vector3.ZERO
	player.reset_physics_interpolation()


func _apply_world_candidate(world: MatrixOasisComposedScene, inspection: Dictionary) -> bool:
	if inspection["status"] == "ended" or _spatial_bindings.is_empty():
		return super._apply_world_candidate(world, inspection)
	var binding: Variant = _spatial_bindings.get(inspection["location"]["id"])
	var navigation_binding: Variant = _navigation_bindings.get(inspection["location"]["id"])
	if typeof(binding) != TYPE_DICTIONARY or (not _navigation_bindings.is_empty() and typeof(navigation_binding) != TYPE_DICTIONARY):
		return false
	var player_transform := _spatial_root_transform * MatrixOasisSceneComposer.anchor_transform(binding["playerSpawn"])
	if not _navigation_bindings.is_empty() and _navigation_cell_for_player(player_transform.origin) != navigation_binding["playerCellIndex"]:
		return false
	if not super._apply_world_candidate(world, inspection):
		return false
	terminal_grid.global_transform = _spatial_root_transform * MatrixOasisSceneComposer.anchor_transform(binding["actionAnchor"])
	player.set_start_transform(player_transform)
	if _navigation_active:
		_last_safe_player_transform = player_transform
	return true


func _navigation_cell_for_player(global_position: Vector3) -> int:
	if _navigation_cells.is_empty() or _navigation_cell_size <= 0.0:
		return -1
	var local_center: Vector3 = _spatial_root_transform.affine_inverse() * global_position
	var local_floor := local_center - Vector3(0.0, _navigation_agent_half_height, 0.0)
	var half_cell := _navigation_cell_size * 0.5
	var selected := -1
	var selected_distance := INF
	for index in _navigation_cells.size():
		var cell: Vector3 = _navigation_cells[index]
		if absf(local_floor.x - cell.x) > half_cell or absf(local_floor.z - cell.z) > half_cell or absf(local_floor.y - cell.y) > _navigation_maximum_step:
			continue
		var distance := Vector2(local_floor.x - cell.x, local_floor.z - cell.z).length_squared()
		if distance < selected_distance:
			selected = index
			selected_distance = distance
	return selected


func _valid_spatial_anchor(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY or value.keys().size() != 2 or not value.has("positionMm") or not value.has("yawMilliDegrees"):
		return false
	var position: Variant = value["positionMm"]
	return typeof(position) == TYPE_ARRAY and position.size() == 3 and position.all(
		func(component: Variant) -> bool: return typeof(component) == TYPE_INT and abs(component) <= 2000000
	) and typeof(value["yawMilliDegrees"]) == TYPE_INT and abs(value["yawMilliDegrees"]) <= 180000
