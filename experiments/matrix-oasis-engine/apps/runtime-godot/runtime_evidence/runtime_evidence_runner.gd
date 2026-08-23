extends Node

const READY_MARKER := "MATRIX_OASIS_R15_RUNTIME_EVIDENCE_READY"
const EVIDENCE_MARKER := "MATRIX_OASIS_R15_RUNTIME_EVIDENCE_JSON:"
const PLAN_PATH := "res://runtime_evidence/replay-plan.json"
const FACTS_PATH := "res://runtime_evidence/environment-facts.json"
const SOLUTION_PATH := "res://spatial_run/spatial-solution.json"
const OUTPUT_PATH := "res://runtime_evidence/runtime-evidence-raw.json"
const TESTED_SCENE := preload("res://solved_spatial_prototype/solved_spatial_lab.tscn")
const MAX_STARTUP_FRAMES := 7200
const MAX_MOVE_FRAMES := 1800
const WAYPOINT_TOLERANCE := 0.35
const APPROACH_TOLERANCE := 0.1
const PLAYER_EYE_HEIGHT := 1.475
const AIM_STEP_RADIANS := 0.25

var _tested_scene: Node
var _scene_lab: MatrixOasisSolvedSpatialSceneLab
var _navigation_region: NavigationRegion3D
var _floor_anchors: Dictionary = {}
var _observations: Array = []
var _frame_micros: Array[int] = []
var _screenshots: Array = []
var _approach_detail := ""
var _movie_frame_start := 0


func _ready() -> void:
	_movie_frame_start = Engine.get_frames_drawn()
	var plan := _read_json(PLAN_PATH)
	var facts := _read_json(FACTS_PATH)
	var solution := _read_json(SOLUTION_PATH)
	if plan.is_empty() or facts.is_empty() or solution.is_empty():
		_fail("R15_RUNTIME_EVIDENCE_INPUT_INVALID")
		return
	_tested_scene = TESTED_SCENE.instantiate()
	if _tested_scene == null:
		_fail("R15_RUNTIME_EVIDENCE_SCENE_INVALID")
		return
	add_child(_tested_scene)
	_run.call_deferred(plan, facts, solution)


func _run(plan: Dictionary, facts: Dictionary, solution: Dictionary) -> void:
	for _frame in MAX_STARTUP_FRAMES:
		await get_tree().process_frame
		var candidate: Variant = _tested_scene.get("_scene_lab")
		if candidate is MatrixOasisSolvedSpatialSceneLab:
			_scene_lab = candidate
			break
	if _scene_lab == null or _scene_lab.player == null or _scene_lab.interaction_ray == null:
		_fail("R15_RUNTIME_EVIDENCE_STARTUP_FAILED")
		return
	if not _install_navigation(facts, solution):
		_fail("R15_RUNTIME_EVIDENCE_INPUT_INVALID")
		return
	if not await _wait_for_navigation_sync():
		_fail("R15_RUNTIME_EVIDENCE_INPUT_INVALID")
		return
	for replay_index in plan["replays"].size():
		var replay: Dictionary = plan["replays"][replay_index]
		var observation := await _run_replay(replay, replay_index)
		_observations.append(observation)
		if observation["outcome"] != "passed":
			_publish(false, observation["diagnosticCode"], observation.get("diagnosticDetail", ""))
			return
	for _index in 300:
		var started := Time.get_ticks_usec()
		await get_tree().process_frame
		_frame_micros.append(maxi(1, Time.get_ticks_usec() - started))
	_publish(true, "")


func _run_replay(replay: Dictionary, replay_index: int) -> Dictionary:
	_input_key(KEY_R, true)
	_input_key(KEY_R, false)
	await get_tree().physics_frame
	await get_tree().process_frame
	var checkpoints: Array = []
	var initial := _inspection()
	if initial.is_empty() or initial["location"]["id"] != replay["expectedLocationIds"][0]:
		return _failed_observation(replay, checkpoints, "R15_RUNTIME_RESET_FAILED")
	if not await _append_checkpoint(checkpoints, replay["id"], replay_index, 0, null):
		return _failed_observation(replay, checkpoints, "R15_RUNTIME_SCREENSHOT_FAILED")
	for index in replay["actionIds"].size():
		var action_id: String = replay["actionIds"][index]
		var before := _inspection()
		var before_snapshot := _snapshot()
		if before.get("status") != "active" or not await _approach_action(action_id):
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_ACTION_APPROACH_FAILED")
		_input_key(KEY_E, true)
		_input_key(KEY_E, false)
		var expected: String = replay["expectedLocationIds"][index + 1]
		if not await _wait_for_action(expected, int(before["stepCount"])):
			_approach_detail = _action_failure_detail(int(before["stepCount"]), action_id, before_snapshot)
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_ACTION_INPUT_FAILED")
		if not await _append_checkpoint(checkpoints, replay["id"], replay_index, index + 1, action_id):
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_SCREENSHOT_FAILED")
	if replay["probeActionId"] != null:
		var before_probe := _inspection()
		var before_probe_json := JSON.stringify(before_probe)
		if not await _approach_action(str(replay["probeActionId"])):
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_DISABLED_APPROACH_FAILED")
		_input_key(KEY_E, true)
		_input_key(KEY_E, false)
		await get_tree().physics_frame
		await get_tree().process_frame
		if JSON.stringify(_inspection()) != before_probe_json:
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_DISABLED_ACTION_MUTATED")
	if replay["resetAfter"]:
		_input_key(KEY_R, true)
		_input_key(KEY_R, false)
		await get_tree().physics_frame
		await get_tree().process_frame
		var reset_inspection := _inspection()
		if reset_inspection["location"]["id"] != replay["expectedLocationIds"][0] or \
			int(reset_inspection["stepCount"]) != 0:
			return _failed_observation(replay, checkpoints, "R15_RUNTIME_RESET_FAILED")
	return {"replayId": replay["id"], "kind": replay["kind"], "outcome": "passed", "checkpoints": checkpoints, "diagnosticCode": ""}


func _approach_action(action_id: String) -> bool:
	_approach_detail = ""
	var terminal := _terminal(action_id)
	if terminal == null:
		_approach_detail = "R15_RUNTIME_APPROACH_TARGET_MISSING"
		return false
	await get_tree().physics_frame
	var selected := _select_action_approach(terminal)
	if selected.is_empty():
		return false
	var path: PackedVector3Array = selected["path"]
	for waypoint_index in range(1, path.size()):
		var tolerance := APPROACH_TOLERANCE if waypoint_index == path.size() - 1 else WAYPOINT_TOLERANCE
		if not await _move_to(path[waypoint_index], tolerance):
			_approach_detail = "R15_RUNTIME_APPROACH_MOVE_BLOCKED"
			return false
	terminal = _terminal(action_id)
	if terminal == null:
		_approach_detail = "R15_RUNTIME_APPROACH_TARGET_MISSING"
		return false
	if not await _capture_mouse_with_input():
		_approach_detail = "R15_RUNTIME_APPROACH_AIM_INVALID"
		return false
	if not await _look_toward(terminal.global_position):
		if _approach_detail.is_empty():
			_approach_detail = "R15_RUNTIME_APPROACH_AIM_INVALID"
		return false
	await get_tree().physics_frame
	await get_tree().physics_frame
	_scene_lab.interaction_ray.force_raycast_update()
	await get_tree().physics_frame
	_scene_lab.interaction_ray.force_raycast_update()
	return _terminal_focus_valid(terminal, action_id)


func _select_action_approach(terminal: MatrixOasisActionTerminal3D) -> Dictionary:
	var candidates: Array[Dictionary] = []
	var anchor_ids: Array = _floor_anchors.keys()
	anchor_ids.sort()
	var saw_unblocked := false
	var saw_in_range := false
	var saw_clear_sight := false
	var saw_path := false
	var saw_complete_path := false
	var navigation_map := _navigation_region.get_navigation_map()
	var space := _scene_lab.player.get_world_3d().direct_space_state
	for anchor_id: Variant in anchor_ids:
		var position: Vector3 = _floor_anchors[anchor_id]
		if _approach_overlaps_terminal_grid(position):
			continue
		saw_unblocked = true
		var eye := position + Vector3.UP * PLAYER_EYE_HEIGHT
		var distance := eye.distance_to(terminal.global_position)
		if distance > MatrixOasisInteractionRay.INTERACTION_DISTANCE + 0.001:
			continue
		saw_in_range = true
		var sight := PhysicsRayQueryParameters3D.create(eye, terminal.global_position, 1)
		sight.exclude = [_scene_lab.player.get_rid()]
		if not space.intersect_ray(sight).is_empty():
			continue
		saw_clear_sight = true
		var path := NavigationServer3D.map_get_path(
			navigation_map, _scene_lab.player.global_position, position, true)
		if path.is_empty():
			continue
		saw_path = true
		if Vector2(path[path.size() - 1].x, path[path.size() - 1].z).distance_to(
			Vector2(position.x, position.z)) > APPROACH_TOLERANCE:
			continue
		saw_complete_path = true
		if not _capsule_path_clear(path, space):
			continue
		candidates.append({"anchorId": String(anchor_id), "distance": distance,
			"path": path})
	if candidates.is_empty():
		_approach_detail = "R15_RUNTIME_APPROACH_MOVE_BLOCKED" if not saw_unblocked else \
			"R15_RUNTIME_APPROACH_DISTANCE_INVALID" if not saw_in_range else \
			"R15_RUNTIME_APPROACH_RAY_WRONG_TARGET" if not saw_clear_sight else \
			"R15_RUNTIME_APPROACH_PATH_EMPTY" if not saw_path else \
			"R15_RUNTIME_APPROACH_ENDPOINT_INVALID" if not saw_complete_path else \
			"R15_RUNTIME_APPROACH_MOVE_BLOCKED"
		return {}
	candidates.sort_custom(func(left: Dictionary, right: Dictionary) -> bool:
		return left["distance"] < right["distance"] or \
			(is_equal_approx(left["distance"], right["distance"]) and \
			left["anchorId"] < right["anchorId"])
	)
	return candidates[0]


func _capsule_path_clear(path: PackedVector3Array,
	space: PhysicsDirectSpaceState3D) -> bool:
	var collision_shape := _scene_lab.player.get_node("CollisionShape3D") as CollisionShape3D
	var capsule := collision_shape.shape as CapsuleShape3D if collision_shape != null else null
	if capsule == null or path.is_empty():
		return false
	var query := PhysicsShapeQueryParameters3D.new()
	query.shape = capsule
	query.collision_mask = 1
	query.collide_with_areas = false
	query.exclude = [_scene_lab.player.get_rid()]
	var center_height := capsule.height * 0.5 + 0.025
	query.transform = Transform3D(Basis.IDENTITY, path[path.size() - 1] + Vector3.UP * center_height)
	if not space.intersect_shape(query, 1).is_empty():
		return false
	for path_index in range(path.size() - 1):
		query.transform = Transform3D(Basis.IDENTITY,
			path[path_index] + Vector3.UP * center_height)
		query.motion = path[path_index + 1] - path[path_index]
		var motion := space.cast_motion(query)
		if motion.is_empty() or motion[0] < 0.999:
			return false
	return true


func _approach_overlaps_terminal_grid(position: Vector3) -> bool:
	var player_shape := _scene_lab.player.get_node("CollisionShape3D").shape as CapsuleShape3D
	if player_shape == null:
		return true
	for index in _scene_lab.terminal_grid.get_terminal_count():
		var terminal := _scene_lab.terminal_grid.get_terminal(index)
		var collision_shape := terminal.get_node_or_null("CollisionShape3D") as CollisionShape3D \
			if terminal != null else null
		var box := collision_shape.shape as BoxShape3D if collision_shape != null else null
		if box == null:
			return true
		var local := terminal.to_local(position)
		if absf(local.x) < box.size.x * 0.5 + player_shape.radius and \
			absf(local.z) < box.size.z * 0.5 + player_shape.radius:
			return true
	return false


func _terminal_focus_valid(terminal: MatrixOasisActionTerminal3D, action_id: String) -> bool:
	var focused := _scene_lab.interaction_ray.get_focused_terminal()
	if _scene_lab.interaction_ray.global_position.distance_to(terminal.global_position) > \
		MatrixOasisInteractionRay.INTERACTION_DISTANCE:
		_approach_detail = "R15_RUNTIME_APPROACH_DISTANCE_INVALID"
		return false
	if not _scene_lab.interaction_ray.is_colliding():
		_approach_detail = "R15_RUNTIME_APPROACH_RAY_MISSING"
		return false
	if _scene_lab.interaction_ray.get_collider() != terminal:
		_approach_detail = "R15_RUNTIME_APPROACH_RAY_WRONG_TARGET"
		return false
	if focused == null:
		_approach_detail = "R15_RUNTIME_APPROACH_FOCUS_STALE"
		return false
	if focused.get_action_id() != action_id:
		_approach_detail = "R15_RUNTIME_APPROACH_ACTION_MISMATCH"
		return false
	return true


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


func _look_toward(target: Vector3) -> bool:
	var input_observed := false
	for _attempt in 16:
		var previous_basis := _scene_lab.player.global_basis
		var previous_pitch := _scene_lab.player.head.rotation.x
		var horizontal_direction := target - _scene_lab.player.global_position
		horizontal_direction.y = 0.0
		if horizontal_direction.length_squared() > 0.000001:
			horizontal_direction = horizontal_direction.normalized()
			var horizontal_forward := -_scene_lab.player.global_basis.z
			horizontal_forward.y = 0.0
			horizontal_forward = horizontal_forward.normalized()
			var yaw_error := horizontal_forward.signed_angle_to(
				horizontal_direction, Vector3.UP)
			var yaw_step := clampf(yaw_error, -AIM_STEP_RADIANS, AIM_STEP_RADIANS)
			_input_mouse(Vector2(-yaw_step /
				MatrixOasisFirstPersonController.LOOK_SENSITIVITY, 0.0))
		await get_tree().process_frame
		var camera := _scene_lab.player.camera
		var direction := (target - camera.global_position).normalized()
		var desired_pitch := asin(clampf(direction.y, -1.0, 1.0))
		var pitch_delta := desired_pitch - _scene_lab.player.head.rotation.x
		pitch_delta = clampf(pitch_delta, -AIM_STEP_RADIANS, AIM_STEP_RADIANS)
		_input_mouse(Vector2(0.0,
			-pitch_delta / MatrixOasisFirstPersonController.LOOK_SENSITIVITY))
		await get_tree().process_frame
		input_observed = input_observed or not _scene_lab.player.global_basis.is_equal_approx(previous_basis) or \
			not is_equal_approx(_scene_lab.player.head.rotation.x, previous_pitch)
		direction = (target - camera.global_position).normalized()
		if (-camera.global_basis.z).dot(direction) >= 0.999:
			return true
	if not input_observed:
		_approach_detail = "R15_RUNTIME_APPROACH_FOCUS_STALE"
	return false


func _input_key(key: Key, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.physical_keycode = key
	event.pressed = pressed
	Input.parse_input_event(event)


func _input_mouse(relative: Vector2) -> void:
	var event := InputEventMouseMotion.new()
	event.relative = relative
	event.position = get_viewport().get_visible_rect().size * 0.5
	get_viewport().push_input(event, true)


func _capture_mouse_with_input() -> bool:
	if Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		return true
	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_LEFT
	event.position = get_viewport().get_visible_rect().size * 0.5
	event.pressed = true
	get_viewport().push_input(event, true)
	await get_tree().process_frame
	event = InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_LEFT
	event.position = get_viewport().get_visible_rect().size * 0.5
	event.pressed = false
	get_viewport().push_input(event, true)
	await get_tree().process_frame
	return Input.mouse_mode == Input.MOUSE_MODE_CAPTURED


func _wait_for_action(expected: String, previous_step: int) -> bool:
	for _frame in 180:
		await get_tree().physics_frame
		await get_tree().process_frame
		var inspection := _inspection()
		var step_count := int(inspection.get("stepCount", -1))
		if step_count == previous_step + 1:
			return inspection.get("location", {}).get("id", "") == expected
		if step_count > previous_step + 1:
			return false
	return false


func _action_failure_detail(previous_step: int, action_id: String, before_snapshot: Dictionary) -> String:
	var latest: Variant = _scene_lab.get("_latest_runtime_result")
	if typeof(latest) == TYPE_DICTIONARY and latest.get("ok") == false:
		var diagnostics: Variant = latest.get("diagnostics")
		if typeof(diagnostics) == TYPE_ARRAY and diagnostics.size() == 1 and \
			diagnostics[0].get("code") == "PACK_GODOT_SCENE_INTERNAL_ERROR":
			return _world_commit_failure_detail(action_id, before_snapshot)
		return "R15_RUNTIME_ACTION_RUNTIME_FAILED"
	var after := _inspection()
	if int(after.get("stepCount", -1)) == previous_step:
		return "R15_RUNTIME_ACTION_INPUT_NOT_OBSERVED"
	return "R15_RUNTIME_ACTION_TRACE_MISMATCH"


func _world_commit_failure_detail(action_id: String, before_snapshot: Dictionary) -> String:
	# The product scene intentionally collapses every world-commit failure to one
	# public diagnostic. Re-run only the frozen, pure Runtime transition so R15 can
	# classify the failed precondition without bypassing InputMap or mutating the
	# product session/world.
	var prepared := _scene_lab.get("_prepared_runtime") as MatrixOasisPreparedRuntimePack
	var world := _scene_lab.get("_composed_world") as MatrixOasisComposedScene
	if prepared == null or before_snapshot.is_empty():
		return "R15_RUNTIME_COMMIT_DIAGNOSTIC_UNAVAILABLE"
	var candidate: Dictionary = MatrixOasisGodotRuntime.apply_game_session_action(
		prepared, before_snapshot.duplicate(true), action_id)
	if candidate.get("ok") != true:
		return "R15_RUNTIME_ACTION_RUNTIME_FAILED"
	var snapshot: Variant = candidate.get("snapshot")
	var inspection: Variant = candidate.get("inspection")
	var transition: Variant = candidate.get("transition")
	var emitted_cues: Variant = transition.get("emittedCues") if typeof(transition) == TYPE_DICTIONARY else null
	if world == null or typeof(snapshot) != TYPE_DICTIONARY or typeof(inspection) != TYPE_DICTIONARY or \
		typeof(emitted_cues) != TYPE_ARRAY or typeof(transition) != TYPE_DICTIONARY or \
		typeof(inspection.get("location")) != TYPE_DICTIONARY or typeof(inspection.get("actions")) != TYPE_ARRAY:
		return "R15_RUNTIME_COMMIT_CANDIDATE_INVALID"
	if inspection.get("status") == "ended":
		if inspection["location"].get("kind") != "ending" or not inspection["actions"].is_empty():
			return "R15_RUNTIME_COMMIT_CANDIDATE_INVALID"
		return "R15_RUNTIME_COMMIT_ENDED_UNCLASSIFIED"
	if inspection.get("status") != "active" or inspection["location"].get("kind") != "node" or \
		inspection["actions"].size() > MatrixOasisActionTerminalGrid.MAX_TERMINALS:
		return "R15_RUNTIME_COMMIT_CANDIDATE_INVALID"
	var node_id: String = inspection["location"]["id"]
	var contexts: Variant = _scene_lab.get("_solution_contexts")
	var context: Variant = contexts.get(node_id) if typeof(contexts) == TYPE_DICTIONARY else null
	if typeof(context) != TYPE_DICTIONARY:
		return "R15_RUNTIME_COMMIT_CONTEXT_MISSING"
	if not world._can_apply_node_for_runtime(node_id):
		return "R15_RUNTIME_COMMIT_WORLD_BINDING_INVALID"
	if _scene_lab.get("_layout_applied") != true:
		return "R15_RUNTIME_COMMIT_LAYOUT_UNAVAILABLE"
	var actions: Array = inspection["actions"]
	for action: Variant in actions:
		if typeof(action) != TYPE_DICTIONARY or typeof(action.get("id")) != TYPE_STRING or \
			typeof(action.get("label")) != TYPE_STRING or typeof(action.get("available")) != TYPE_BOOL:
			return "R15_RUNTIME_COMMIT_ACTION_SHAPE_INVALID"
	var action_terminal: Variant = context.get("actionTerminal")
	if typeof(action_terminal) != TYPE_DICTIONARY or \
		typeof(action_terminal.get("terminalSupports")) != TYPE_ARRAY or \
		action_terminal["terminalSupports"].size() != actions.size():
		return "R15_RUNTIME_COMMIT_TERMINAL_SUPPORT_MISMATCH"
	var root := world._root_for_runtime()
	var placements: Variant = _scene_lab.get("_solution_placements")
	var placement_paths: Variant = _scene_lab.get("_scene_placement_ids")
	if root == null or typeof(placements) != TYPE_DICTIONARY or typeof(placement_paths) != TYPE_DICTIONARY:
		return "R15_RUNTIME_COMMIT_PLACEMENT_MAPPING_INVALID"
	for placement_id: Variant in placements:
		if typeof(placement_id) != TYPE_STRING:
			return "R15_RUNTIME_COMMIT_PLACEMENT_MAPPING_INVALID"
		var placement_path: Variant = placement_paths.get(placement_id)
		if typeof(placement_path) != TYPE_STRING or root.get_node_or_null(NodePath(String(placement_path))) == null:
			return "R15_RUNTIME_COMMIT_PLACEMENT_MAPPING_INVALID"
	return "R15_RUNTIME_COMMIT_UNCLASSIFIED"


func _inspection() -> Dictionary:
	var value: Variant = _scene_lab.get("_inspection") if _scene_lab != null else null
	return value.duplicate(true) if typeof(value) == TYPE_DICTIONARY else {}


func _snapshot() -> Dictionary:
	var value: Variant = _scene_lab.get("_snapshot") if _scene_lab != null else null
	return value.duplicate(true) if typeof(value) == TYPE_DICTIONARY else {}


func _terminal(action_id: String) -> MatrixOasisActionTerminal3D:
	for index in _scene_lab.terminal_grid.get_terminal_count():
		var terminal := _scene_lab.terminal_grid.get_terminal(index)
		if terminal != null and terminal.get_action_id() == action_id:
			return terminal
	return null


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
	var navigation_complete := Vector2(closest.x, closest.z).distance_to(Vector2(player.global_position.x, player.global_position.z)) <= 0.1
	var focused := _scene_lab.interaction_ray.get_focused_terminal()
	var focused_id: Variant = null if focused == null else focused.get_action_id()
	var interaction_distance: Variant = null if focused == null else roundi(player.global_position.distance_to(focused.global_position) * 1000.0)
	var visible: Array = _scene_lab.get("_current_visible").keys()
	visible.sort()
	return {"sequence": sequence, "locationKind": inspection["location"]["kind"], "locationId": inspection["location"]["id"], "stepCount": inspection["stepCount"], "actionId": action_id, "playerPositionMm": [roundi(player.global_position.x * 1000.0), roundi(player.global_position.y * 1000.0), roundi(player.global_position.z * 1000.0)], "floorDistanceMm": floor_distance_mm, "capsuleClear": capsule_clear, "navigationPathComplete": navigation_complete, "focusedActionId": focused_id, "interactionDistanceMm": interaction_distance, "visiblePlacementIds": visible}


func _append_checkpoint(checkpoints: Array, replay_id: String, replay_index: int,
	sequence: int, action_id: Variant) -> bool:
	if replay_index < 0 or replay_index >= 32 or sequence < 0 or sequence > 256:
		return false
	var checkpoint := _checkpoint(sequence, action_id)
	checkpoints.append(checkpoint)
	await get_tree().process_frame
	var image := get_viewport().get_texture().get_image()
	if image == null or image.is_empty():
		return false
	# The filename is derived only from bounded integer indexes, never from pack IDs.
	var relative := "media/replay-%04d-checkpoint-%04d.png" % [replay_index, sequence]
	if image.save_png("res://runtime_evidence/" + relative) != OK:
		return false
	_screenshots.append({"replayId": replay_id, "locationId": checkpoint["locationId"],
		"relativePath": relative})
	return true


func _install_navigation(facts: Dictionary, solution: Dictionary) -> bool:
	var mesh_value: Variant = facts.get("navigationMesh")
	var navigation_value: Variant = solution.get("navigation")
	if typeof(mesh_value) != TYPE_DICTIONARY or typeof(navigation_value) != TYPE_DICTIONARY:
		return false
	var component_index: int = int(navigation_value.get("componentIndex", -1))
	var domain_anchor_ids: Dictionary = {}
	var zone_domains: Variant = navigation_value.get("zoneDomains")
	if typeof(zone_domains) != TYPE_ARRAY:
		return false
	for zone_domain: Variant in zone_domains:
		if typeof(zone_domain) != TYPE_DICTIONARY or \
			typeof(zone_domain.get("floorAnchorIds")) != TYPE_ARRAY:
			return false
		for anchor_id: Variant in zone_domain["floorAnchorIds"]:
			if typeof(anchor_id) != TYPE_STRING:
				return false
			domain_anchor_ids[anchor_id] = true
	if domain_anchor_ids.is_empty():
		return false
	var faces: Variant = _scene_lab.get("_navigation_domain_faces")
	var support_height_mm: Variant = _scene_lab.get("_runtime_support_height_mm")
	if typeof(faces) != TYPE_PACKED_VECTOR3_ARRAY or faces.is_empty() or faces.size() % 3 != 0 or \
		typeof(support_height_mm) != TYPE_INT:
		return false
	var mesh := NavigationMesh.new()
	var vertices := PackedVector3Array()
	var vertex_indices: Dictionary = {}
	for index in range(0, faces.size(), 3):
		var polygon := PackedInt32Array()
		for offset in 3:
			var point: Vector3 = faces[index + offset]
			if not vertex_indices.has(point):
				vertex_indices[point] = vertices.size()
				vertices.append(point)
			polygon.append(vertex_indices[point])
		if polygon[0] == polygon[1] or polygon[0] == polygon[2] or polygon[1] == polygon[2]:
			return false
		mesh.add_polygon(polygon)
	mesh.vertices = vertices
	_navigation_region = NavigationRegion3D.new()
	_navigation_region.navigation_mesh = mesh
	add_child(_navigation_region)
	for anchor: Dictionary in facts.get("floorAnchors", []):
		if int(anchor.get("componentIndex", -1)) != component_index or \
			not domain_anchor_ids.has(anchor.get("id")):
			continue
		var point: Array = anchor["positionMm"]
		_floor_anchors[anchor["id"]] = Vector3(float(point[0]), float(support_height_mm), float(point[2])) / 1000.0
	return not _floor_anchors.is_empty()


func _wait_for_navigation_sync() -> bool:
	var navigation_map := _navigation_region.get_navigation_map()
	for _sync_frame in 8:
		await get_tree().physics_frame
		if NavigationServer3D.map_get_iteration_id(navigation_map) > 0:
			return true
	return false


func _read_json(path: String) -> Dictionary:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return {}
	var value: Variant = JSON.parse_string(file.get_as_text())
	return value if typeof(value) == TYPE_DICTIONARY else {}


func _failed_observation(replay: Dictionary, checkpoints: Array, code: String) -> Dictionary:
	return {"replayId": replay["id"], "kind": replay["kind"], "outcome": "failed", "checkpoints": checkpoints, "diagnosticCode": code, "diagnosticDetail": _approach_detail}


func _publish(ok: bool, code: String, detail: String = "") -> void:
	var raw := {"ok": ok, "observations": _observations, "frameMicros": _frame_micros,
		"screenshots": _screenshots, "diagnosticCode": code,
		"movieFrameCount": maxi(0, Engine.get_frames_drawn() - _movie_frame_start) \
			if OS.has_feature("movie") else 0}
	if not detail.is_empty():
		raw["diagnosticDetail"] = detail
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
