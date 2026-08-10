class_name MatrixOasisRuntimeLab
extends Node3D

const READINESS_MARKER := "MATRIX_OASIS_R5_GODOT_RUNTIME_READY"
const SMOKE_ARGUMENT := "--matrix-oasis-runtime-smoke"
const NARROW_WIDTH := 720.0
const READY_COLOR := Color(0.53, 0.88, 0.77, 1.0)
const ERROR_COLOR := Color(1.0, 0.58, 0.58, 1.0)

@onready var world_spacer: Control = $Hud/SafeArea/Layout/WorldSpacer
@onready var runtime_panel: PanelContainer = $Hud/SafeArea/Layout/RuntimePanel
@onready var pack_title: Label = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/PackTitle
@onready var pack_meta: Label = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/PackMeta
@onready var status_label: Label = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/Status
@onready var location_title: Label = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/Scroll/Content/LocationTitle
@onready var location_text: Label = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/Scroll/Content/LocationText
@onready var step_label: Label = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/Scroll/Content/Step
@onready var variables_list: VBoxContainer = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/Scroll/Content/Variables
@onready var cues_list: VBoxContainer = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/Scroll/Content/Cues
@onready var transition_label: Label = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/Scroll/Content/Transition
@onready var actions_list: VBoxContainer = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/ActionsScroll/Actions
@onready var reset_button: Button = $Hud/SafeArea/Layout/RuntimePanel/PanelMargin/Panel/Reset

var _prepared_override: MatrixOasisPreparedRuntimePack
var _prepared: MatrixOasisPreparedRuntimePack
var _snapshot: Dictionary
var _inspection: Dictionary
var _latest_cues: Array = []
var _latest_transition: Variant = null
var _smoke_requested := false


func set_prepared_for_test(prepared: MatrixOasisPreparedRuntimePack) -> void:
	_prepared_override = prepared


func _ready() -> void:
	reset_button.pressed.connect(_reset_session)
	get_viewport().size_changed.connect(_update_responsive_layout)
	_update_responsive_layout()
	if _prepared_override != null:
		_activate_prepared(_prepared_override)
		return
	var artifact_arguments := PackedStringArray()
	for argument in OS.get_cmdline_user_args():
		if argument == SMOKE_ARGUMENT:
			if _smoke_requested:
				_show_failure("GODOT_RUNTIME_LAB_ARGUMENT_INVALID")
				return
			_smoke_requested = true
		else:
			artifact_arguments.append(argument)
	var loaded := MatrixOasisRuntimeArtifactLoader.load_from_arguments(artifact_arguments)
	if not loaded["ok"]:
		_show_failure(loaded["diagnostics"][0]["code"])
		return
	_activate_prepared(loaded["prepared"])


func _activate_prepared(prepared: MatrixOasisPreparedRuntimePack) -> void:
	var created := MatrixOasisGodotRuntime.create_game_session(prepared)
	if not created["ok"]:
		_show_failure(created["diagnostics"][0]["code"])
		return
	_prepared = prepared
	_snapshot = created["snapshot"]
	_inspection = created["inspection"]
	_latest_cues = created["emittedCues"]
	_latest_transition = null
	_set_status("Runtime ready")
	_render_session()
	print(READINESS_MARKER)
	if _smoke_requested:
		_complete_smoke.call_deferred()


func _apply_action(action_id: String) -> void:
	if _prepared == null or _snapshot.is_empty():
		_show_failure("PACK_GODOT_RUNTIME_INTERNAL_ERROR")
		return
	var result := MatrixOasisGodotRuntime.apply_game_session_action(
		_prepared,
		_snapshot,
		action_id,
	)
	if not result["ok"]:
		_set_status(result["diagnostics"][0]["code"], true)
		return
	_snapshot = result["snapshot"]
	_inspection = result["inspection"]
	_latest_transition = result["transition"]
	_latest_cues = result["transition"]["emittedCues"]
	_set_status("Action applied")
	_render_session()


func _reset_session() -> void:
	if _prepared == null:
		_show_failure("PACK_GODOT_RUNTIME_INTERNAL_ERROR")
		return
	var created := MatrixOasisGodotRuntime.create_game_session(_prepared)
	if not created["ok"]:
		_show_failure(created["diagnostics"][0]["code"])
		return
	_snapshot = created["snapshot"]
	_inspection = created["inspection"]
	_latest_cues = created["emittedCues"]
	_latest_transition = null
	_set_status("Session reset")
	_render_session()


func _render_session() -> void:
	var pack: Dictionary = _inspection["pack"]
	var location: Dictionary = _inspection["location"]
	pack_title.text = pack["title"]
	pack_meta.text = "%s · version %s" % [pack["id"], pack["contentVersion"]]
	location_title.text = location["title"]
	location_text.text = "" if location["text"] == null else location["text"]
	step_label.text = "Step %d / %d · %s" % [
		_inspection["stepCount"],
		_inspection["stepLimit"],
		_inspection["status"],
	]
	_replace_value_labels(variables_list, _inspection["variables"], "variable")
	_replace_value_labels(cues_list, _latest_cues, "cue")
	_render_transition()
	_render_actions()


func _replace_value_labels(container: VBoxContainer, values: Array, kind: String) -> void:
	_clear_children(container)
	if values.is_empty():
		var empty_label := _new_detail_label(
			"No variables declared." if kind == "variable" else "No cues emitted for this step.",
		)
		container.add_child(empty_label)
		return
	for value in values:
		var text := ""
		if kind == "variable":
			text = "%s: %s" % [value["id"], JSON.stringify(value["value"])]
		else:
			text = "%s · %s · %s" % [value["id"], value["channel"], value["intent"]]
		container.add_child(_new_detail_label(text))


func _render_transition() -> void:
	if _latest_transition == null:
		transition_label.text = "No transition yet."
		return
	transition_label.text = "Transition %d: %s → %s via %s" % [
		_latest_transition["step"],
		_latest_transition["from"]["id"],
		_latest_transition["to"]["id"],
		_latest_transition["actionId"],
	]


func _render_actions() -> void:
	_clear_children(actions_list)
	var first_available: Button
	if _inspection["actions"].is_empty():
		actions_list.add_child(_new_detail_label("No actions at this location."))
	else:
		for action in _inspection["actions"]:
			var button := Button.new()
			button.custom_minimum_size.y = 44.0
			button.focus_mode = Control.FOCUS_ALL
			button.text = action["label"]
			button.tooltip_text = action["id"]
			button.disabled = not action["available"]
			button.pressed.connect(_apply_action.bind(action["id"]))
			actions_list.add_child(button)
			if first_available == null and action["available"]:
				first_available = button
	if first_available != null:
		first_available.grab_focus.call_deferred()
	else:
		reset_button.grab_focus.call_deferred()


func _new_detail_label(text: String) -> Label:
	var label := Label.new()
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_color_override("font_color", Color(0.72, 0.79, 0.88, 1.0))
	label.add_theme_font_size_override("font_size", 13)
	return label


func _clear_children(container: Container) -> void:
	for child in container.get_children():
		container.remove_child(child)
		child.queue_free()


func _update_responsive_layout() -> void:
	var narrow := get_viewport().get_visible_rect().size.x < NARROW_WIDTH
	world_spacer.visible = not narrow
	runtime_panel.custom_minimum_size.x = 0.0 if narrow else 420.0
	runtime_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL if narrow else Control.SIZE_FILL


func _show_failure(code: String) -> void:
	_set_status(code, true)
	if _smoke_requested:
		get_tree().quit(1)


func _set_status(text: String, is_error := false) -> void:
	status_label.text = text
	status_label.add_theme_color_override("font_color", ERROR_COLOR if is_error else READY_COLOR)


func _complete_smoke() -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	get_tree().quit(0)
