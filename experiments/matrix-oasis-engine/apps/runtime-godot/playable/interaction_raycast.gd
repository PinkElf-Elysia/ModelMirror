class_name MatrixOasisInteractionRay
extends RayCast3D

signal focus_changed(label: String, available: bool)
signal action_requested(action_id: String)

const INTERACTION_DISTANCE := 3.0
const INTERACTION_LAYER_MASK := 4

var _focused_terminal: MatrixOasisActionTerminal3D


func _ready() -> void:
	target_position = Vector3(0.0, 0.0, -INTERACTION_DISTANCE)
	collision_mask = INTERACTION_LAYER_MASK
	collide_with_areas = true
	collide_with_bodies = false
	enabled = true


func _physics_process(_delta: float) -> void:
	_refresh_focus()


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed(&"interact"):
		if try_interact():
			get_viewport().set_input_as_handled()


func try_interact() -> bool:
	_refresh_focus()
	if _focused_terminal == null or not _focused_terminal.request_interaction():
		return false
	action_requested.emit(_focused_terminal.get_action_id())
	return true


func get_focused_terminal() -> MatrixOasisActionTerminal3D:
	return _focused_terminal


func _refresh_focus() -> void:
	var candidate := get_collider() as MatrixOasisActionTerminal3D if is_colliding() else null
	if candidate == _focused_terminal:
		return
	_focused_terminal = candidate
	if _focused_terminal == null:
		focus_changed.emit("", false)
	else:
		focus_changed.emit(
			_focused_terminal.get_action_label(),
			_focused_terminal.is_action_available(),
		)
