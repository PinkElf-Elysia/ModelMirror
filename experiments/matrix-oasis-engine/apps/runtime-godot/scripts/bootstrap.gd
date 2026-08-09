extends Node3D

const READINESS_MARKER := "MATRIX_OASIS_R4_GODOT_FOUNDATION_READY"
const SMOKE_ARGUMENT := "--matrix-oasis-smoke"

@onready var foundation_camera: Camera3D = $FoundationCamera


func _ready() -> void:
	foundation_camera.look_at(Vector3(0.0, 0.55, 0.0), Vector3.UP)
	print(READINESS_MARKER)
	if is_smoke_requested(OS.get_cmdline_user_args()):
		_complete_smoke.call_deferred()


static func is_smoke_requested(arguments: PackedStringArray) -> bool:
	return SMOKE_ARGUMENT in arguments


func _complete_smoke() -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	get_tree().quit(0)
