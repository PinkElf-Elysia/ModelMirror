extends Node

const READY_MARKER := "MATRIX_OASIS_R15_RUNTIME_EVIDENCE_READY"
const EVIDENCE_MARKER := "MATRIX_OASIS_R15_RUNTIME_EVIDENCE_JSON:"
const PLAN_PATH := "res://runtime_evidence/replay-plan.json"
const FACTS_PATH := "res://runtime_evidence/environment-facts.json"
const OUTPUT_PATH := "res://runtime_evidence/runtime-evidence-raw.json"
const TESTED_SCENE := preload("res://solved_spatial_prototype/solved_spatial_lab.tscn")
const MAX_STARTUP_FRAMES := 7200
const MAX_MOVE_FRAMES := 1800
const WAYPOINT_TOLERANCE := 0.35
const APPROACH_TOLERANCE := 0.55

var _tested_scene: Node
var _scene_lab: MatrixOasisSolvedSpatialSceneLab
var _navigation_region: NavigationRegion3D
var _floor_anchors: Dictionary = {}
var _observations: Array = []
var _frame_micros: Array[int] = []


func _ready() -> void:
	var plan := _read_json(PLAN_PATH)
	var facts := _read_json(FACTS_PATH)
	if plan.is_empty() or facts.is_empty() or not _install_navigation(facts):
		_fail("R15_RUNTIME_EVIDENCE_INPUT_INVALID")
		return
	_tested_scene = TESTED_SCENE.instantiate()
	if _tested_scene == null:
		_fail("R15_RUNTIME_EVIDENCE_SCENE_INVALID")
		return
	add_child(_tested_scene)
	_run.call_deferred(plan)


func _run(plan: Dictionary) -> void:
	for _frame in MAX_STARTUP_FRAMES:
		await get_tree().process_frame
		var candidate: Variant = _tested_scene.get("_scene_lab")
		if candidate is MatrixOasisSolvedSpatialSceneLab:
			_scene_lab = candidate
			break
	if _scene_lab == null or _scene_lab.player == null or _scene_lab.interaction_ray == null:
		_fail("R15_RUNTIME_EVIDENCE_STARTUP_FAILED")
		return
	await get_tree().physics_frame
	await get_tree().physics_frame
	for replay: Dictionary in plan["replays"]:
		var observation := await _run_replay(replay)
		_observations.append(observation)
		if observation["outcome"] != "passed":
			_publish(false, observation["diagnosticCode"])
			return
	for _index in 300:
		var started := Time.get_ticks_usec()
		await get_tree().process_frame
		_frame_micros.append(maxi(1, Time.get_ticks_usec() - started))
	_publish(true, "")


func _run_replay(replay: Dictionary) -> Dictionary:
	_input_key(KEY_R, true)
	_input_key(KEY_R, false)
	await get_tree().physics_frame
	await get_tree().process_frame
	var checkpoints: Array = []
	var initial := _inspection()
	if initial.is_empty() or initial["location"]["id"] != replay["expectedLocationIds"][0]:
		return _failed_observation(replay, checkpoints, "R15_RUNTIME_RESET_FAILED")
	checkpoints.append(_checkpoint(0, null))
	for index in replay["actionIds"].size():
		var action_id: String = replay["actionIds"][index]
		var before := _inspection()
		if before.get("status") != "active" or not await _approach_action(action_id, before["location"]["id"]):
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_ACTION_APPROACH_FAILED")
		_input_key(KEY_E, true)
		_input_key(KEY_E, false)
		var expected: String = replay["expectedLocationIds"][index + 1]
		if not await _wait_for_location(expected):
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_ACTION_INPUT_FAILED")
		checkpoints.append(_checkpoint(index + 1, action_id))
	if replay["probeActionId"] != null:
		var before_probe := _inspection()
		if not await _approach_action(str(replay["probeActionId"]), before_probe["location"]["id"]):
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_DISABLED_APPROACH_FAILED")
		_input_key(KEY_E, true)
		_input_key(KEY_E, false)
		await get_tree().physics_frame
		await get_tree().process_frame
		if _inspection()["location"]["id"] != before_probe["location"]["id"]:
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_DISABLED_ACTION_MUTATED")
	if replay["resetAfter"]:
		_input_key(KEY_R, true)
		_input_key(KEY_R, false)
		await get_tree().physics_frame
		await get_tree().process_frame
		if _inspection()["location"]["id"] != replay["expectedLocationIds"][0]:
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_RESET_FAILED")
	return {"replayId": replay["id"], "kind": replay["kind"], "outcome": "passed", "checkpoints": checkpoints, "diagnosticCode": ""}


func _approach_action(action_id: String, node_id: String) -> bool:
	var terminal := _terminal(action_id)
	var approach := _approach_position(node_id)
	if terminal == null or not approach.is_finite():
		return false
	var navigation_map := _navigation_region.get_navigation_map()
	var path := NavigationServer3D.map_get_path(navigation_map, _scene_lab.player.global_position, approach, true)
	if path.is_empty() or path[path.size() - 1].distance_to(approach) > 0.1:
		return false
	for waypoint_index in range(1, path.size()):
		var tolerance := APPROACH_TOLERANCE if waypoint_index == path.size() - 1 else WAYPOINT_TOLERANCE
		if not await _move_to(path[waypoint_index], tolerance):
			return false
	terminal = _terminal(action_id)
	if terminal == null:
		return false
	_look_toward(terminal.global_position + Vector3.UP * 0.85)
	await get_tree().physics_frame
	await get_tree().physics_frame
	var focused := _scene_lab.interaction_ray.get_focused_terminal()
	return focused != null and focused.get_action_id() == action_id and _scene_lab.player.global_position.distance_to(terminal.global_position) <= 3.0


func _move_to(target: Vector3, tolerance: float) -> bool:
	var previous_distance := INF
	var stalled := 0
	for _frame in MAX_MOVE_FRAMES:
		var flat_target := Vector3(target.x, _scene_lab.player.global_position.y, target.z)
		var distance := _scene_lab.player.global_position.distance_to(flat_target)
		if distance <= tolerance:
			_input_key(KEY_W, false)
			return true
		_look_horizontal(flat_target)
		_input_key(KEY_W, true)
		await get_tree().physics_frame
		if distance >= previous_distance - 0.001:
			stalled += 1
		else:
			stalled = 0
		previous_distance = distance
		if stalled > 180:
			_input_key(KEY_W, false)
			return false
	_input_key(KEY_W, false)
	return false


func _look_horizontal(target: Vector3) -> void:
	var direction := target - _scene_lab.player.global_position
	direction.y = 0.0
	if direction.length_squared() <= 0.000001:
		return
	direction = direction.normalized()
	var forward := -_scene_lab.player.global_basis.z
	forward.y = 0.0
	forward = forward.normalized()
	var angle := forward.signed_angle_to(direction, Vector3.UP)
	_input_mouse(Vector2(-angle / MatrixOasisFirstPersonController.LOOK_SENSITIVITY, 0.0))


func _look_toward(target: Vector3) -> void:
	_look_horizontal(target)
	var camera := _scene_lab.player.camera
	var direction := (target - camera.global_position).normalized()
	var desired_pitch := -asin(clampf(direction.y, -1.0, 1.0))
	var pitch_delta := desired_pitch - _scene_lab.player.head.rotation.x
	_input_mouse(Vector2(0.0, -pitch_delta / MatrixOasisFirstPersonController.LOOK_SENSITIVITY))


func _input_key(key: Key, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.physical_keycode = key
	event.pressed = pressed
	Input.parse_input_event(event)


func _input_mouse(relative: Vector2) -> void:
	var event := InputEventMouseMotion.new()
	event.relative = relative
	Input.parse_input_event(event)


func _wait_for_location(expected: String) -> bool:
	for _frame in 180:
		await get_tree().physics_frame
		await get_tree().process_frame
		if _inspection().get("location", {}).get("id", "") == expected:
			return true
	return false


func _inspection() -> Dictionary:
	var value: Variant = _scene_lab.get("_inspection") if _scene_lab != null else null
	return value.duplicate(true) if typeof(value) == TYPE_DICTIONARY else {}


func _terminal(action_id: String) -> MatrixOasisActionTerminal3D:
	for index in _scene_lab.terminal_grid.get_terminal_count():
		var terminal := _scene_lab.terminal_grid.get_terminal(index)
		if terminal != null and terminal.get_action_id() == action_id:
			return terminal
	return null


func _approach_position(node_id: String) -> Vector3:
	var context: Variant = _scene_lab.get("_solution_contexts").get(node_id)
	if typeof(context) != TYPE_DICTIONARY:
		return Vector3.INF
	var anchor_id: String = context["actionTerminal"]["approachFloorAnchorId"]
	return _floor_anchors.get(anchor_id, Vector3.INF)


func _checkpoint(sequence: int, action_id: Variant) -> Dictionary:
	var inspection := _inspection()
	var player := _scene_lab.player
	var floor_query := PhysicsRayQueryParameters3D.create(player.global_position + Vector3.UP * 0.2, player.global_position + Vector3.DOWN * 2.0, 1)
	floor_query.exclude = [player.get_rid()]
	var floor_hit := player.get_world_3d().direct_space_state.intersect_ray(floor_query)
	var bottom_y := player.global_position.y - 0.9
	var floor_distance_mm := roundi((bottom_y - float(floor_hit.get("position", player.global_position).y)) * 1000.0)
	var shape_query := PhysicsShapeQueryParameters3D.new()
	shape_query.shape = player.get_node("CollisionShape3D").shape
	shape_query.transform = Transform3D(player.global_basis, player.global_position + Vector3.UP * 0.03)
	shape_query.collision_mask = 1
	shape_query.exclude = [player.get_rid()]
	var capsule_clear := player.get_world_3d().direct_space_state.intersect_shape(shape_query, 1).is_empty()
	var closest := NavigationServer3D.map_get_closest_point(_navigation_region.get_navigation_map(), player.global_position)
	var focused := _scene_lab.interaction_ray.get_focused_terminal()
	var focused_id: Variant = null if focused == null else focused.get_action_id()
	var interaction_distance: Variant = null if focused == null else roundi(player.global_position.distance_to(focused.global_position) * 1000.0)
	var visible: Array = _scene_lab.get("_current_visible").keys()
	visible.sort()
	return {"sequence": sequence, "locationKind": inspection["location"]["kind"], "locationId": inspection["location"]["id"], "stepCount": inspection["stepCount"], "actionId": action_id, "playerPositionMm": [roundi(player.global_position.x * 1000.0), roundi(player.global_position.y * 1000.0), roundi(player.global_position.z * 1000.0)], "floorDistanceMm": floor_distance_mm, "capsuleClear": capsule_clear, "navigationPathComplete": closest.distance_to(player.global_position) <= 0.1, "focusedActionId": focused_id, "interactionDistanceMm": interaction_distance, "visiblePlacementIds": visible}


func _install_navigation(facts: Dictionary) -> bool:
	var mesh_value: Variant = facts.get("navigationMesh")
	if typeof(mesh_value) != TYPE_DICTIONARY:
		return false
	var mesh := NavigationMesh.new()
	var vertices := PackedVector3Array()
	for value: Array in mesh_value["verticesMm"]:
		vertices.append(Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0)
	mesh.vertices = vertices
	for polygon: Dictionary in mesh_value["polygons"]:
		mesh.add_polygon(PackedInt32Array(polygon["vertexIndices"]))
	_navigation_region = NavigationRegion3D.new()
	_navigation_region.navigation_mesh = mesh
	add_child(_navigation_region)
	for anchor: Dictionary in facts.get("floorAnchors", []):
		var point: Array = anchor["positionMm"]
		_floor_anchors[anchor["id"]] = Vector3(float(point[0]), float(point[1]), float(point[2])) / 1000.0
	return not _floor_anchors.is_empty()


func _read_json(path: String) -> Dictionary:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return {}
	var value: Variant = JSON.parse_string(file.get_as_text())
	return value if typeof(value) == TYPE_DICTIONARY else {}


func _failed_observation(replay: Dictionary, checkpoints: Array, code: String) -> Dictionary:
	return {"replayId": replay["id"], "kind": replay["kind"], "outcome": "failed", "checkpoints": checkpoints, "diagnosticCode": code}


func _publish(ok: bool, code: String) -> void:
	var raw := {"ok": ok, "observations": _observations, "frameMicros": _frame_micros, "diagnosticCode": code}
	var file := FileAccess.open(OUTPUT_PATH, FileAccess.WRITE)
	if file == null:
		_fail("R15_RUNTIME_EVIDENCE_WRITE_FAILED")
		return
	file.store_string(JSON.stringify(raw))
	file.close()
	if ok:
		print(READY_MARKER)
		print(EVIDENCE_MARKER + JSON.stringify({"replayCount": _observations.size(), "performanceSampleCount": _frame_micros.size()}))
		get_tree().quit(0)
	else:
		printerr(code)
		get_tree().quit(1)


func _fail(code: String) -> void:
	printerr(code)
	get_tree().quit(1)
