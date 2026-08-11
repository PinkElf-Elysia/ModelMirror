class_name MatrixOasisFirstPersonControllerTest
extends GdUnitTestSuite

const PLAYER_SCENE := "res://playable/player.tscn"
const MOVEMENT_LAB_SCENE := "res://playable/movement_lab.tscn"


func test_project_uses_exact_r6_physics_and_input_contract() -> void:
	assert_int(ProjectSettings.get_setting("physics/common/physics_ticks_per_second")).is_equal(60)
	assert_bool(ProjectSettings.get_setting("physics/common/physics_interpolation")).is_true()
	assert_str(ProjectSettings.get_setting("physics/3d/physics_engine")).is_equal("Jolt Physics")
	for action in ["move_forward", "move_backward", "move_left", "move_right", "interact", "reset_session"]:
		assert_bool(InputMap.has_action(action)).is_true()
	assert_int(InputMap.action_get_events("move_forward").size()).is_equal(2)
	assert_int(InputMap.action_get_events("move_backward").size()).is_equal(2)
	assert_int(InputMap.action_get_events("move_left").size()).is_equal(2)
	assert_int(InputMap.action_get_events("move_right").size()).is_equal(2)


func test_player_scene_uses_character_body_and_fixed_collision_layers() -> void:
	var packed := load(PLAYER_SCENE) as PackedScene
	assert_object(packed).is_not_null()
	var player := auto_free(packed.instantiate()) as MatrixOasisFirstPersonController
	assert_object(player).is_not_null()
	assert_int(player.collision_layer).is_equal(2)
	assert_int(player.collision_mask).is_equal(1)
	assert_object(player.get_node_or_null("CollisionShape3D")).is_not_null()
	assert_object(player.get_node_or_null("Head/Camera3D")).is_not_null()
	assert_float(player.MOVE_SPEED).is_equal(3.5)
	assert_float(player.ACCELERATION).is_equal(12.0)
	assert_float(player.DECELERATION).is_equal(16.0)


func test_input_normalization_and_velocity_are_frame_rate_independent() -> void:
	var diagonal := MatrixOasisFirstPersonController.normalize_move_input(Vector2(1.0, 1.0))
	assert_float(diagonal.length()).is_equal_approx(1.0, 0.00001)
	var sixty := _simulate_velocity(1.0 / 60.0, 60)
	var one_twenty := _simulate_velocity(1.0 / 120.0, 120)
	assert_float(sixty.x).is_equal_approx(3.5, 0.00001)
	assert_float(sixty.x).is_equal_approx(one_twenty.x, 0.00001)
	var stopped := sixty
	for index in 60:
		stopped = MatrixOasisFirstPersonController.compute_horizontal_velocity(
			stopped, Vector2.ZERO, 1.0 / 60.0,
		)
	assert_float(stopped.length()).is_equal_approx(0.0, 0.00001)


func test_pitch_is_clamped_to_eighty_five_degrees() -> void:
	var limit := deg_to_rad(85.0)
	assert_float(MatrixOasisFirstPersonController.clamp_pitch_radians(PI)).is_equal_approx(limit, 0.00001)
	assert_float(MatrixOasisFirstPersonController.clamp_pitch_radians(-PI)).is_equal_approx(-limit, 0.00001)
	assert_float(MatrixOasisFirstPersonController.clamp_pitch_radians(0.25)).is_equal_approx(0.25, 0.00001)


func test_movement_lab_has_ground_walls_slope_and_player() -> void:
	var packed := load(MOVEMENT_LAB_SCENE) as PackedScene
	assert_object(packed).is_not_null()
	var lab: Node = auto_free(packed.instantiate())
	for node_path in [
		"Ground/Collision",
		"NorthWall/Collision",
		"WestWall/Collision",
		"EastWall/Collision",
		"Slope/Collision",
		"FirstPersonPlayer",
	]:
		assert_object(lab.get_node_or_null(node_path)).is_not_null()
	for dependency in ResourceLoader.get_dependencies(MOVEMENT_LAB_SCENE):
		assert_bool(dependency.contains("res://")).is_true()


func test_gravity_landing_wall_collision_and_slope_stay_bounded(timeout := 20000) -> void:
	var runner := scene_runner(MOVEMENT_LAB_SCENE)
	var player := runner.find_child("FirstPersonPlayer") as MatrixOasisFirstPersonController
	assert_object(player).is_not_null()
	player.capture_mouse_on_ready = false
	player.clear_synthetic_move_input()
	await runner.simulate_frames(120)
	assert_float(player.global_position.y).is_between(0.85, 1.2)

	player.set_synthetic_move_input(Vector2(0.0, -1.0))
	await runner.simulate_frames(300)
	assert_float(player.global_position.z).is_greater(-8.8)
	assert_bool(player.is_on_floor()).is_true()

	var slope_start := Transform3D(Basis.IDENTITY, Vector3(3.5, 2.8, -3.0))
	player.set_start_transform(slope_start)
	player.clear_synthetic_move_input()
	await runner.simulate_frames(180)
	assert_float(player.global_position.y).is_greater(0.75)
	assert_float(absf(player.global_position.x - 3.5)).is_less(0.2)


func _simulate_velocity(delta: float, count: int) -> Vector2:
	var current := Vector2.ZERO
	for index in count:
		current = MatrixOasisFirstPersonController.compute_horizontal_velocity(
			current, Vector2.RIGHT, delta,
		)
	return current
