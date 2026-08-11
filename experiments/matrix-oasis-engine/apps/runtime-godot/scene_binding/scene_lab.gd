class_name MatrixOasisSceneLab
extends Node3D

const READINESS_MARKER := "MATRIX_OASIS_R7_SCENE_BINDING_READY"
const SMOKE_ARGUMENT := "--matrix-oasis-scene-smoke"
const READY_COLOR := Color(0.53, 0.88, 0.77, 1.0)
const ERROR_COLOR := Color(1.0, 0.58, 0.58, 1.0)

@onready var scene_world: Node3D = $SceneWorld
@onready var player: MatrixOasisFirstPersonController = $FirstPersonPlayer
@onready var interaction_ray: MatrixOasisInteractionRay = $FirstPersonPlayer/Head/Camera3D/InteractionRay
@onready var terminal_grid: MatrixOasisActionTerminalGrid = $ActionTerminalGrid
@onready var scene_title: Label = $Hud/SafeArea/Layout/SceneTitle
@onready var pack_title: Label = $Hud/SafeArea/Layout/PackTitle
@onready var location_title: Label = $Hud/SafeArea/Layout/LocationTitle
@onready var location_text: Label = $Hud/SafeArea/Layout/LocationText
@onready var status_label: Label = $Hud/SafeArea/Layout/Status
@onready var state_label: Label = $Hud/SafeArea/Layout/State
@onready var variables_label: Label = $Hud/SafeArea/Layout/Variables
@onready var cues_label: Label = $Hud/SafeArea/Layout/Cues
@onready var transition_label: Label = $Hud/SafeArea/Layout/Transition
@onready var prompt_label: Label = $Hud/Prompt
@onready var reset_button: Button = $Hud/SafeArea/Layout/Reset

var _runtime_override: MatrixOasisPreparedRuntimePack
var _scene_override: MatrixOasisPreparedScene
var _prepared_runtime: MatrixOasisPreparedRuntimePack
var _prepared_scene: MatrixOasisPreparedScene
var _composed_world: MatrixOasisComposedScene
var _snapshot: Dictionary
var _inspection: Dictionary
var _latest_cues: Array = []
var _latest_transition: Variant = null
var _created_result: Dictionary
var _latest_runtime_result: Dictionary
var _step_limit_override := 0
var _last_active_node_id := ""
var _smoke_requested := false


func set_prepared_for_test(runtime: MatrixOasisPreparedRuntimePack, scene: MatrixOasisPreparedScene) -> void:
	_runtime_override = runtime
	_scene_override = scene


func set_step_limit_for_trace(step_limit: int) -> bool:
	if is_node_ready() or step_limit < 1 or step_limit > MatrixOasisGodotRuntime.MAX_STEP_LIMIT:
		return false
	_step_limit_override = step_limit
	return true


func _ready() -> void:
	interaction_ray.focus_changed.connect(_on_focus_changed)
	interaction_ray.action_requested.connect(_apply_action)
	reset_button.pressed.connect(_reset_session)
	if _runtime_override != null and _scene_override != null:
		_activate_pair(_runtime_override, _scene_override)
		return
	var artifact_arguments := PackedStringArray()
	for argument in OS.get_cmdline_user_args():
		if argument == SMOKE_ARGUMENT:
			if _smoke_requested:
				_show_failure("GODOT_SCENE_LAB_ARGUMENT_INVALID")
				return
			_smoke_requested = true
		else:
			artifact_arguments.append(argument)
	var loaded := MatrixOasisSceneArtifactLoader.load_from_arguments(artifact_arguments)
	if not loaded["ok"]:
		_show_failure(loaded["diagnostics"][0]["code"])
		return
	_activate_pair(loaded["prepared_runtime"], loaded["prepared_scene"])


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed(&"reset_session"):
		_reset_session()
		get_viewport().set_input_as_handled()


func _activate_pair(runtime: MatrixOasisPreparedRuntimePack, scene: MatrixOasisPreparedScene) -> bool:
	var composed := MatrixOasisSceneComposer.compose(scene)
	if not composed["ok"]:
		_show_failure(composed["diagnostics"][0]["code"])
		return false
	var candidate_world: MatrixOasisComposedScene = composed["world"]
	var created := MatrixOasisGodotRuntime.create_game_session(runtime, _session_options())
	if not created["ok"]:
		candidate_world._dispose_for_runtime()
		_show_failure(created["diagnostics"][0]["code"])
		return false
	if not _valid_candidate(created["snapshot"], created["inspection"], created["emittedCues"], null, candidate_world):
		candidate_world._dispose_for_runtime()
		_show_failure("PACK_GODOT_SCENE_INTERNAL_ERROR")
		return false
	var new_root := candidate_world._root_for_runtime()
	if new_root == null:
		candidate_world._dispose_for_runtime()
		_show_failure("PACK_GODOT_SCENE_INTERNAL_ERROR")
		return false
	scene_world.add_child(new_root)
	if not _apply_world_candidate(candidate_world, created["inspection"]):
		candidate_world._dispose_for_runtime()
		_show_failure("PACK_GODOT_SCENE_INTERNAL_ERROR")
		return false
	var previous_world := _composed_world
	_prepared_runtime = runtime
	_prepared_scene = scene
	_composed_world = candidate_world
	_snapshot = created["snapshot"]
	_inspection = created["inspection"]
	_latest_cues = created["emittedCues"]
	_latest_transition = null
	_created_result = created
	_latest_runtime_result = created
	var scene_pack := scene._copy_scene_for_runtime()
	scene_title.text = scene_pack["scene"]["title"]
	if previous_world != null:
		previous_world._dispose_for_runtime()
	_render_session()
	_set_status("Scene and Runtime ready")
	print(READINESS_MARKER)
	if _smoke_requested:
		_complete_smoke.call_deferred()
	return true


func _apply_action(action_id: String) -> bool:
	if _prepared_runtime == null or _composed_world == null or _snapshot.is_empty():
		_show_failure("PACK_GODOT_SCENE_INTERNAL_ERROR")
		return false
	var result := MatrixOasisGodotRuntime.apply_game_session_action(_prepared_runtime, _snapshot, action_id)
	if not result["ok"]:
		_latest_runtime_result = result
		_set_status(result["diagnostics"][0]["code"], true)
		return false
	if not _commit_session_candidate(result["snapshot"], result["inspection"], result["transition"]["emittedCues"], result["transition"]):
		_latest_runtime_result = _internal_failure()
		_show_failure("PACK_GODOT_SCENE_INTERNAL_ERROR")
		return false
	_latest_runtime_result = result
	_set_status("Action applied")
	return true


func _reset_session() -> bool:
	if _prepared_runtime == null or _composed_world == null:
		_show_failure("PACK_GODOT_SCENE_INTERNAL_ERROR")
		return false
	var created := MatrixOasisGodotRuntime.create_game_session(_prepared_runtime, _session_options())
	if not created["ok"]:
		_show_failure(created["diagnostics"][0]["code"])
		return false
	if not _commit_session_candidate(created["snapshot"], created["inspection"], created["emittedCues"], null):
		_show_failure("PACK_GODOT_SCENE_INTERNAL_ERROR")
		return false
	_latest_runtime_result = created
	_set_status("Session reset")
	return true


func _commit_session_candidate(snapshot: Dictionary, inspection: Dictionary, emitted_cues: Array, transition: Variant) -> bool:
	if not _valid_candidate(snapshot, inspection, emitted_cues, transition, _composed_world):
		return false
	if not _apply_world_candidate(_composed_world, inspection):
		return false
	_snapshot = snapshot
	_inspection = inspection
	_latest_cues = emitted_cues
	_latest_transition = transition
	_render_session()
	return true


func _valid_candidate(snapshot: Variant, inspection: Variant, emitted_cues: Variant, transition: Variant, world: MatrixOasisComposedScene) -> bool:
	if world == null or typeof(snapshot) != TYPE_DICTIONARY or typeof(inspection) != TYPE_DICTIONARY or typeof(emitted_cues) != TYPE_ARRAY:
		return false
	if not inspection.has("status") or not inspection.has("location") or not inspection.has("actions") or typeof(inspection["location"]) != TYPE_DICTIONARY or typeof(inspection["actions"]) != TYPE_ARRAY or inspection["actions"].size() > 64:
		return false
	if inspection["status"] == "active":
		return inspection["location"].get("kind") == "node" and world._can_apply_node_for_runtime(inspection["location"].get("id", "")) and (transition == null or typeof(transition) == TYPE_DICTIONARY)
	return inspection["status"] == "ended" and inspection["location"].get("kind") == "ending" and inspection["actions"].is_empty() and (transition == null or typeof(transition) == TYPE_DICTIONARY)


func _apply_world_candidate(world: MatrixOasisComposedScene, inspection: Dictionary) -> bool:
	if inspection["status"] == "ended":
		terminal_grid.clear_terminals()
		return true
	var node_id: String = inspection["location"]["id"]
	var binding := world._binding_for_runtime(node_id)
	if binding.is_empty() or not terminal_grid.rebuild(inspection["actions"]):
		return false
	if not world._apply_node_for_runtime(node_id):
		return false
	terminal_grid.global_transform = MatrixOasisSceneComposer.anchor_transform(binding["actionAnchor"])
	player.set_start_transform(MatrixOasisSceneComposer.anchor_transform(binding["playerSpawn"]))
	_last_active_node_id = node_id
	return true


func _session_options() -> Dictionary:
	return {} if _step_limit_override == 0 else {"stepLimit": _step_limit_override}


func apply_terminal_action_for_trace(action_id: String) -> Dictionary:
	for index in terminal_grid.get_terminal_count():
		var terminal := terminal_grid.get_terminal(index)
		if terminal.get_action_id() == action_id:
			if terminal.request_interaction():
				interaction_ray.action_requested.emit(action_id)
			else:
				_apply_action(action_id)
			return _latest_runtime_result
	_apply_action(action_id)
	return _latest_runtime_result


func _render_session() -> void:
	var pack: Dictionary = _inspection["pack"]
	var location: Dictionary = _inspection["location"]
	pack_title.text = pack["title"]
	location_title.text = location["title"]
	location_text.text = "" if location["text"] == null else location["text"]
	state_label.text = "Step %d / %d · %s" % [_inspection["stepCount"], _inspection["stepLimit"], _inspection["status"]]
	variables_label.text = _variables_text(_inspection["variables"])
	cues_label.text = _cues_text(_latest_cues)
	transition_label.text = _transition_text(_latest_transition)
	if _inspection["status"] == "ended":
		prompt_label.text = "Ending reached · R to reset"


func _variables_text(variables: Array) -> String:
	if variables.is_empty():
		return "Variables · none"
	var lines := PackedStringArray(["Variables"])
	for variable in variables:
		lines.append("%s: %s" % [variable["id"], JSON.stringify(variable["value"])])
	return "\n".join(lines)


func _cues_text(cues: Array) -> String:
	if cues.is_empty():
		return "Cues · none this step"
	var lines := PackedStringArray(["Cues this step"])
	for cue in cues:
		lines.append("%s · %s" % [cue["channel"], cue["intent"]])
	return "\n".join(lines)


func _transition_text(transition: Variant) -> String:
	if transition == null:
		return "No transition yet."
	return "Transition %d · %s → %s" % [transition["step"], transition["from"]["id"], transition["to"]["id"]]


func _on_focus_changed(label: String, available: bool) -> void:
	if label.is_empty():
		prompt_label.text = "Aim at an action terminal"
	elif available:
		prompt_label.text = "E / Enter · %s" % label
	else:
		prompt_label.text = "Unavailable · %s" % label


func _show_failure(code: String) -> void:
	_set_status(code, true)
	if _smoke_requested:
		get_tree().quit(1)


func _set_status(text: String, is_error := false) -> void:
	status_label.text = text
	status_label.add_theme_color_override("font_color", ERROR_COLOR if is_error else READY_COLOR)


func snapshot_copy_for_test() -> Dictionary:
	return _snapshot.duplicate(true)


func created_result_for_trace() -> Dictionary:
	return _created_result


func scene_state_for_trace() -> Dictionary:
	if _prepared_scene == null or _composed_world == null or _inspection.is_empty() or _last_active_node_id.is_empty():
		return {}
	var scene_pack := _prepared_scene._copy_scene_for_runtime()
	var ordered_ids: Array = []
	for placement in scene_pack["placements"]:
		ordered_ids.append(placement["id"])
	var binding := _composed_world._binding_for_runtime(_last_active_node_id)
	if binding.is_empty():
		return {}
	var state := {
		"manifestSha256": _prepared_scene.manifest_sha256(),
		"runtimeArtifactSha256": _prepared_scene.runtime_artifact_sha256(),
		"status": _inspection["status"],
		"locationId": _inspection["location"]["id"],
		"visiblePlacementIds": _composed_world._visible_placement_ids_for_runtime(ordered_ids),
		"playerSpawn": binding["playerSpawn"].duplicate(true),
		"actionAnchor": binding["actionAnchor"].duplicate(true),
		"terminalCount": terminal_grid.get_terminal_count(),
	}
	return state


func world_for_test() -> MatrixOasisComposedScene:
	return _composed_world


func _internal_failure() -> Dictionary:
	var result := {"ok": false, "diagnostics": [{"phase": "runtime", "severity": "error", "code": "PACK_GODOT_SCENE_INTERNAL_ERROR", "path": "/scenePack", "message": "PACK_GODOT_SCENE_INTERNAL_ERROR"}]}
	return result


func _complete_smoke() -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	get_tree().quit(0)
