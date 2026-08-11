class_name MatrixOasisFirstPersonController
extends CharacterBody3D

const MOVE_SPEED := 3.5
const ACCELERATION := 12.0
const DECELERATION := 16.0
const LOOK_SENSITIVITY := 0.0025
const PITCH_LIMIT_RADIANS := deg_to_rad(85.0)

@export var capture_mouse_on_ready := true
@export var input_enabled := true

@onready var head: Node3D = $Head
@onready var camera: Camera3D = $Head/Camera3D

var _start_transform := Transform3D.IDENTITY
var _gravity := 9.8
var _synthetic_input: Variant = null


func _ready() -> void:
	_start_transform = global_transform
	_gravity = float(ProjectSettings.get_setting("physics/3d/default_gravity", 9.8))
	if capture_mouse_on_ready and input_enabled:
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


func _physics_process(delta: float) -> void:
	var input_vector := _read_move_input() if input_enabled else Vector2.ZERO
	var local_direction := Vector3(input_vector.x, 0.0, input_vector.y)
	var world_direction := global_transform.basis * local_direction
	world_direction.y = 0.0
	if world_direction.length_squared() > 1.0:
		world_direction = world_direction.normalized()
	var horizontal := Vector2(velocity.x, velocity.z)
	horizontal = compute_horizontal_velocity(horizontal, Vector2(world_direction.x, world_direction.z), delta)
	velocity.x = horizontal.x
	velocity.z = horizontal.y
	if is_on_floor():
		velocity.y = minf(velocity.y, 0.0)
	else:
		velocity.y -= _gravity * delta
	move_and_slide()


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.is_action_pressed(&"ui_cancel"):
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
		get_viewport().set_input_as_handled()
		return
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT and event.pressed:
		if Input.mouse_mode != Input.MOUSE_MODE_CAPTURED:
			Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
			get_viewport().set_input_as_handled()
		return
	if event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		apply_look_delta(event.relative)
		get_viewport().set_input_as_handled()


func apply_look_delta(relative: Vector2) -> void:
	rotate_y(-relative.x * LOOK_SENSITIVITY)
	head.rotation.x = clamp_pitch_radians(head.rotation.x - relative.y * LOOK_SENSITIVITY)


func reset_to_start() -> void:
	global_transform = _start_transform
	velocity = Vector3.ZERO
	head.rotation.x = 0.0
	reset_physics_interpolation()


func set_start_transform(value: Transform3D) -> void:
	_start_transform = value
	global_transform = value
	velocity = Vector3.ZERO
	reset_physics_interpolation()


func set_synthetic_move_input(value: Vector2) -> void:
	_synthetic_input = normalize_move_input(value)


func clear_synthetic_move_input() -> void:
	_synthetic_input = null


func _read_move_input() -> Vector2:
	if _synthetic_input is Vector2:
		return _synthetic_input
	return normalize_move_input(Input.get_vector(
		&"move_left", &"move_right", &"move_forward", &"move_backward",
	))


static func normalize_move_input(value: Vector2) -> Vector2:
	return value.limit_length(1.0)


static func compute_horizontal_velocity(current: Vector2, world_direction: Vector2, delta: float) -> Vector2:
	var normalized := normalize_move_input(world_direction)
	var target := normalized * MOVE_SPEED
	var rate := ACCELERATION if not normalized.is_zero_approx() and normalized.dot(current) > 0.0 else DECELERATION
	if current.is_zero_approx() and not normalized.is_zero_approx():
		rate = ACCELERATION
	return current.move_toward(target, rate * maxf(delta, 0.0))


static func clamp_pitch_radians(value: float) -> float:
	return clampf(value, -PITCH_LIMIT_RADIANS, PITCH_LIMIT_RADIANS)
