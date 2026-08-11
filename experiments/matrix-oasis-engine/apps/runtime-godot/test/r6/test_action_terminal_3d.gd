class_name MatrixOasisActionTerminal3DTest
extends GdUnitTestSuite

const TERMINAL_SCENE := "res://playable/action_terminal_3d.tscn"
const INTERACTION_LAB_SCENE := "res://playable/interaction_lab.tscn"


func test_terminal_preserves_pack_label_order_and_availability() -> void:
	var packed := load(TERMINAL_SCENE) as PackedScene
	assert_object(packed).is_not_null()
	var terminal := auto_free(packed.instantiate()) as MatrixOasisActionTerminal3D
	assert_bool(terminal.configure({
		"id": "action-0",
		"label": "Pack supplied label",
		"available": true,
	}, 0)).is_true()
	assert_str(terminal.get_action_id()).is_equal("action-0")
	assert_str(terminal.get_action_label()).is_equal("Pack supplied label")
	assert_int(terminal.get_declaration_index()).is_equal(0)
	assert_bool(terminal.is_action_available()).is_true()
	assert_bool(terminal.request_interaction()).is_true()
	assert_int(terminal.collision_layer).is_equal(4)
	assert_int(terminal.collision_mask).is_equal(0)
	assert_object(terminal.get_node_or_null("Label3D")).is_not_null()


func test_disabled_and_malformed_terminals_fail_closed() -> void:
	var packed := load(TERMINAL_SCENE) as PackedScene
	var terminal := auto_free(packed.instantiate()) as MatrixOasisActionTerminal3D
	assert_bool(terminal.configure({
		"id": "action-disabled",
		"label": "Visible disabled action",
		"available": false,
	}, 3)).is_true()
	assert_bool(terminal.request_interaction()).is_false()
	assert_bool(terminal.configure({"id": "missing-fields"}, 4)).is_false()


func test_grid_handles_zero_one_and_sixty_four_actions_in_declared_order() -> void:
	var grid: MatrixOasisActionTerminalGrid = auto_free(MatrixOasisActionTerminalGrid.new()) as MatrixOasisActionTerminalGrid
	assert_bool(grid.rebuild([])).is_true()
	assert_int(grid.get_terminal_count()).is_equal(0)
	assert_bool(grid.rebuild(_actions(1))).is_true()
	assert_int(grid.get_terminal_count()).is_equal(1)
	assert_str(grid.get_terminal(0).get_action_id()).is_equal("action-0")
	assert_float(grid.get_terminal(0).position.x).is_equal_approx(0.0, 0.00001)

	assert_bool(grid.rebuild(_actions(64))).is_true()
	assert_int(grid.get_terminal_count()).is_equal(64)
	for index in 64:
		assert_str(grid.get_terminal(index).get_action_id()).is_equal("action-%d" % index)
	assert_float(grid.get_terminal(0).position.x).is_equal_approx(-5.95, 0.00001)
	assert_float(grid.get_terminal(7).position.x).is_equal_approx(5.95, 0.00001)
	assert_float(grid.get_terminal(8).position.z).is_equal_approx(-4.65, 0.00001)
	assert_float(grid.get_terminal(63).position.z).is_equal_approx(-18.15, 0.00001)


func test_grid_rebuild_clears_old_nodes_and_rejects_more_than_sixty_four() -> void:
	var grid: MatrixOasisActionTerminalGrid = auto_free(MatrixOasisActionTerminalGrid.new()) as MatrixOasisActionTerminalGrid
	assert_bool(grid.rebuild(_actions(4))).is_true()
	assert_int(grid.get_child_count()).is_equal(4)
	assert_bool(grid.rebuild(_actions(2))).is_true()
	assert_int(grid.get_child_count()).is_equal(2)
	var first_terminal := grid.get_terminal(0)
	var second_terminal := grid.get_terminal(1)
	assert_bool(grid.rebuild(_actions(65))).is_false()
	assert_int(grid.get_terminal_count()).is_equal(2)
	assert_int(grid.get_child_count()).is_equal(2)
	assert_object(grid.get_terminal(0)).is_same(first_terminal)
	assert_object(grid.get_terminal(1)).is_same(second_terminal)


func test_center_ray_ignores_world_layer_and_requests_only_available_terminal(timeout := 5000) -> void:
	var runner := scene_runner(INTERACTION_LAB_SCENE)
	var ray := runner.find_child("InteractionRay") as MatrixOasisInteractionRay
	var terminal := runner.find_child("ActionTerminal") as MatrixOasisActionTerminal3D
	assert_object(ray).is_not_null()
	assert_object(terminal).is_not_null()
	assert_float(ray.target_position.length()).is_equal_approx(3.0, 0.00001)
	assert_int(ray.collision_mask).is_equal(4)
	assert_bool(ray.collide_with_areas).is_true()
	assert_bool(ray.collide_with_bodies).is_false()
	assert_bool(terminal.configure({
		"id": "action-ray",
		"label": "Ray action",
		"available": true,
	}, 0)).is_true()
	await runner.simulate_frames(3)
	ray.force_raycast_update()
	var requested: Array[String] = []
	ray.action_requested.connect(func(action_id: String) -> void: requested.append(action_id))
	assert_bool(ray.try_interact()).is_true()
	assert_array(requested).contains_exactly(["action-ray"])
	assert_object(ray.get_focused_terminal()).is_same(terminal)

	assert_bool(terminal.configure({
		"id": "action-ray",
		"label": "Ray action",
		"available": false,
	}, 0)).is_true()
	assert_bool(ray.try_interact()).is_false()
	assert_array(requested).contains_exactly(["action-ray"])


func _actions(count: int) -> Array:
	var actions: Array = []
	for index in count:
		actions.append({
			"id": "action-%d" % index,
			"label": "Action %d" % index,
			"available": index % 2 == 0,
		})
	return actions
