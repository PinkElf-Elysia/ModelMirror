extends Node3D

const MARKER := "R20_NPC_LOAD_JSON:"
const PROBE_OK_MARKER := "R20_NPC_LOAD_PROBE_OK"
const SAMPLE_FRAMES := 300
const ALLOWED_COUNTS := [2, 4, 32, 64]

var _actors: Array[CharacterBody3D] = []
var _frame_durations_us: Array[int] = []
var _previous_frame_us := 0


func _ready() -> void:
	var actor_count := 0
	for argument in OS.get_cmdline_user_args():
		if argument.begins_with("--actors="):
			actor_count = int(argument.trim_prefix("--actors="))
	if actor_count not in ALLOWED_COUNTS:
		_fail("R20_NPC_LOAD_ARGUMENT_INVALID")
		return
	_install_floor()
	for index in actor_count:
		_actors.append(_create_actor(index))
	_previous_frame_us = Time.get_ticks_usec()


func _physics_process(_delta: float) -> void:
	var now := Time.get_ticks_usec()
	_frame_durations_us.append(maxi(1, now - _previous_frame_us))
	_previous_frame_us = now
	var direction := 1.0 if (_frame_durations_us.size() / 60) % 2 == 0 else -1.0
	for actor in _actors:
		actor.velocity = Vector3(direction, 0.0, 0.0)
		actor.move_and_slide()
		if not actor.global_position.is_finite() or actor.global_position.y < -0.01:
			_fail("R20_NPC_LOAD_PHYSICS_INVALID")
			return
	if _frame_durations_us.size() < SAMPLE_FRAMES:
		return
	_frame_durations_us.sort()
	var median_frame_us := _frame_durations_us[_frame_durations_us.size() / 2]
	var median_fps_milli := roundi(1000000000.0 / float(median_frame_us))
	print(MARKER + JSON.stringify({
		"actorCount": _actors.size(),
		"sampleCount": _frame_durations_us.size(),
		"medianFrameMicros": median_frame_us,
		"medianFpsMilli": median_fps_milli,
	}))
	print(PROBE_OK_MARKER)
	get_tree().quit(0)


func _install_floor() -> void:
	var body := StaticBody3D.new()
	body.name = "LoadProbeFloor"
	body.collision_layer = 1
	body.collision_mask = 0
	var collision := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(40.0, 0.1, 40.0)
	collision.shape = shape
	collision.position.y = -0.05
	body.add_child(collision)
	add_child(body)


func _create_actor(index: int) -> CharacterBody3D:
	var actor := CharacterBody3D.new()
	actor.name = "LoadActor%02d" % index
	actor.collision_layer = 2
	actor.collision_mask = 1
	actor.floor_snap_length = 0.2
	actor.up_direction = Vector3.UP
	actor.position = Vector3(float(index % 8) * 1.0 - 3.5, 0.0, float(index / 8) * 1.0 - 3.5)
	var collision := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.radius = 0.35
	capsule.height = 1.8
	collision.shape = capsule
	collision.position.y = 0.9
	actor.add_child(collision)
	add_child(actor)
	return actor


func _fail(code: String) -> void:
	push_error(code)
	get_tree().quit(2)
