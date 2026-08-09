extends Node3D

const READINESS_MARKER := "MATRIX_OASIS_R4_GODOT_FOUNDATION_READY"
const SMOKE_ARGUMENT := "--matrix-oasis-smoke"
const CAPTURE_ARGUMENT := "--matrix-oasis-capture"
const CAPTURE_FRAME_COUNT := 12

@onready var foundation_camera: Camera3D = $FoundationCamera


func _ready() -> void:
	foundation_camera.look_at(Vector3(0.0, 0.55, 0.0), Vector3.UP)
	print(READINESS_MARKER)
	var user_arguments := OS.get_cmdline_user_args()
	if is_smoke_requested(user_arguments):
		_complete_smoke.call_deferred()
	elif is_capture_requested(user_arguments):
		_complete_capture.call_deferred()


static func is_smoke_requested(arguments: PackedStringArray) -> bool:
	return SMOKE_ARGUMENT in arguments


static func is_capture_requested(arguments: PackedStringArray) -> bool:
	return CAPTURE_ARGUMENT in arguments


func _complete_smoke() -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	get_tree().quit(0)


func _complete_capture() -> void:
	for frame_index in range(CAPTURE_FRAME_COUNT):
		await get_tree().process_frame
	get_tree().quit(0)
