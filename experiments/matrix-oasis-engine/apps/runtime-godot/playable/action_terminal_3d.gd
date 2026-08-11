class_name MatrixOasisActionTerminal3D
extends Area3D

const AVAILABLE_COLOR := Color(0.22, 0.72, 0.58, 1.0)
const UNAVAILABLE_COLOR := Color(0.25, 0.29, 0.36, 1.0)
const AVAILABLE_EMISSION := Color(0.08, 0.32, 0.24, 1.0)

@onready var terminal_mesh: MeshInstance3D = $TerminalMesh
@onready var label_3d: Label3D = $Label3D

var _action_id := ""
var _action_label := ""
var _available := false
var _declaration_index := -1


func _ready() -> void:
	_render_state()


func configure(action: Dictionary, declaration_index: int) -> bool:
	if (
		typeof(action.get("id")) != TYPE_STRING or
		typeof(action.get("label")) != TYPE_STRING or
		typeof(action.get("available")) != TYPE_BOOL or
		declaration_index < 0
	):
		return false
	_action_id = action["id"]
	_action_label = action["label"]
	_available = action["available"]
	_declaration_index = declaration_index
	_render_state()
	return true


func get_action_id() -> String:
	return _action_id


func get_action_label() -> String:
	return _action_label


func is_action_available() -> bool:
	return _available


func get_declaration_index() -> int:
	return _declaration_index


func request_interaction() -> bool:
	return not _action_id.is_empty() and _available


func _render_state() -> void:
	if not is_node_ready():
		return
	label_3d.text = _action_label if _available else "%s\n[Unavailable]" % _action_label
	var material := StandardMaterial3D.new()
	material.albedo_color = AVAILABLE_COLOR if _available else UNAVAILABLE_COLOR
	material.roughness = 0.72
	if _available:
		material.emission_enabled = true
		material.emission = AVAILABLE_EMISSION
		material.emission_energy_multiplier = 0.55
	terminal_mesh.material_override = material
