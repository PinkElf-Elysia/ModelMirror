class_name MatrixOasisPlayableLab
extends Node3D

const READINESS_MARKER := "MATRIX_OASIS_R6_PLAYABLE_3D_READY"
const SMOKE_ARGUMENT := "--matrix-oasis-3d-smoke"
const READY_COLOR := Color(0.53, 0.88, 0.77, 1.0)
const ERROR_COLOR := Color(1.0, 0.58, 0.58, 1.0)

@onready var player: MatrixOasisFirstPersonController = $FirstPersonPlayer
@onready var interaction_ray: MatrixOasisInteractionRay = $FirstPersonPlayer/Head/Camera3D/InteractionRay
@onready var terminal_grid: MatrixOasisActionTerminalGrid = $ActionTerminalGrid
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

var _prepared_override: MatrixOasisPreparedRuntimePack
var _prepared: MatrixOasisPreparedRuntimePack
var _snapshot: Dictionary
var _inspection: Dictionary
var _latest_cues: Array = []
var _latest_transition: Variant = null
var _created_result: Dictionary
var _latest_runtime_result: Dictionary
var _step_limit_override := 0
var _smoke_requested := false


func set_prepared_for_test(prepared: MatrixOasisPreparedRuntimePack) -> void:
	_prepared_override = prepared


func set_step_limit_for_trace(step_limit: int) -> bool:
	if is_node_ready() or step_limit < 1 or step_limit > MatrixOasisGodotRuntime.MAX_STEP_LIMIT:
		return false
	_step_limit_override = step_limit
	return true


func _ready() -> void:
	interaction_ray.focus_changed.connect(_on_focus_changed)
	interaction_ray.action_requested.connect(_apply_action)
	reset_button.pressed.connect(_reset_session)
	if _prepared_override != null:
		_activate_prepared(_prepared_override)
		return
	var artifact_arguments := PackedStringArray()
	for argument in OS.get_cmdline_user_args():
		if argument == SMOKE_ARGUMENT:
			if _smoke_requested:
				_show_failure("GODOT_PLAYABLE_ARGUMENT_INVALID")
				return
			_smoke_requested = true
		else:
			artifact_arguments.append(argument)
	var loaded := MatrixOasisRuntimeArtifactLoader.load_from_arguments(artifact_arguments)
	if not loaded["ok"]:
		_show_failure(loaded["diagnostics"][0]["code"])
		return
	_activate_prepared(loaded["prepared"])


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed(&"reset_session"):
		_reset_session()
		get_viewport().set_input_as_handled()


func _activate_prepared(prepared: MatrixOasisPreparedRuntimePack) -> void:
	var created := MatrixOasisGodotRuntime.create_game_session(prepared, _session_options())
	if not created["ok"]:
		_show_failure(created["diagnostics"][0]["code"])
		return
	if not _commit_session_candidate(
		prepared,
		created["snapshot"],
		created["inspection"],
		created["emittedCues"],
		null,
	):
		_show_failure("PACK_GODOT_RUNTIME_INTERNAL_ERROR")
		return
	_set_status("Runtime ready")
	print(READINESS_MARKER)
	if _smoke_requested:
		_complete_smoke.call_deferred()


func _apply_action(action_id: String) -> bool:
	if _prepared == null or _snapshot.is_empty():
		_show_failure("PACK_GODOT_RUNTIME_INTERNAL_ERROR")
		return false
	var result := MatrixOasisGodotRuntime.apply_game_session_action(
		_prepared,
		_snapshot,
		action_id,
	)
	if not result["ok"]:
		_latest_runtime_result = result
		_set_status(result["diagnostics"][0]["code"], true)
		return false
	if not _commit_session_candidate(
		_prepared,
		result["snapshot"],
		result["inspection"],
		result["transition"]["emittedCues"],
		result["transition"],
	):
		_latest_runtime_result = _internal_failure()
		_show_failure("PACK_GODOT_RUNTIME_INTERNAL_ERROR")
		return false
	_latest_runtime_result = result
	_set_status("Action applied")
	return true


func _reset_session() -> bool:
	if _prepared == null:
		_show_failure("PACK_GODOT_RUNTIME_INTERNAL_ERROR")
		return false
	var created := MatrixOasisGodotRuntime.create_game_session(_prepared, _session_options())
	if not created["ok"]:
		_show_failure(created["diagnostics"][0]["code"])
		return false
	if not _commit_session_candidate(
		_prepared,
		created["snapshot"],
		created["inspection"],
		created["emittedCues"],
		null,
	):
		_show_failure("PACK_GODOT_RUNTIME_INTERNAL_ERROR")
		return false
	player.reset_to_start()
	_set_status("Session reset")
	return true


func _commit_session_candidate(
	prepared: MatrixOasisPreparedRuntimePack,
	snapshot: Dictionary,
	inspection: Dictionary,
	emitted_cues: Array,
	transition: Variant,
) -> bool:
	if not _valid_candidate(snapshot, inspection, emitted_cues, transition):
		return false
	var actions: Array = inspection["actions"]
	if not terminal_grid.rebuild(actions):
		return false
	_prepared = prepared
	_snapshot = snapshot
	_inspection = inspection
	_latest_cues = emitted_cues
	_latest_transition = transition
	if transition == null:
		_created_result = {
			"ok": true,
			"snapshot": snapshot,
			"inspection": inspection,
			"emittedCues": emitted_cues,
		}
	_render_session()
	return true


func _session_options() -> Dictionary:
	return {} if _step_limit_override == 0 else {"stepLimit": _step_limit_override}


func apply_terminal_action_for_trace(action_id: String) -> Dictionary:
	for index in terminal_grid.get_terminal_count():
		var terminal := terminal_grid.get_terminal(index)
		if terminal.get_action_id() != action_id:
			continue
		if terminal.request_interaction():
			interaction_ray.action_requested.emit(action_id)
		else:
			_apply_action(action_id)
		return _latest_runtime_result
	_apply_action(action_id)
	return _latest_runtime_result


func created_result_for_trace() -> Dictionary:
	return _created_result


func _valid_candidate(
	snapshot: Variant,
	inspection: Variant,
	emitted_cues: Variant,
	transition: Variant,
) -> bool:
	if typeof(snapshot) != TYPE_DICTIONARY or typeof(inspection) != TYPE_DICTIONARY:
		return false
	if typeof(emitted_cues) != TYPE_ARRAY or not inspection.has("actions"):
		return false
	if typeof(inspection["actions"]) != TYPE_ARRAY or inspection["actions"].size() > 64:
		return false
	return transition == null or typeof(transition) == TYPE_DICTIONARY


func _render_session() -> void:
	var pack: Dictionary = _inspection["pack"]
	var location: Dictionary = _inspection["location"]
	pack_title.text = pack["title"]
	location_title.text = location["title"]
	location_text.text = "" if location["text"] == null else location["text"]
	state_label.text = "Step %d / %d · %s" % [
		_inspection["stepCount"],
		_inspection["stepLimit"],
		_inspection["status"],
	]
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
	return "Transition %d · %s → %s" % [
		transition["step"],
		transition["from"]["id"],
		transition["to"]["id"],
	]


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


func _internal_failure() -> Dictionary:
	return {
		"ok": false,
		"diagnostics": [{
			"phase": "runtime",
			"severity": "error",
			"code": "PACK_GODOT_RUNTIME_INTERNAL_ERROR",
			"path": "/runtime",
			"message": "PACK_GODOT_RUNTIME_INTERNAL_ERROR",
		}],
	}


func _complete_smoke() -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	get_tree().quit(0)
