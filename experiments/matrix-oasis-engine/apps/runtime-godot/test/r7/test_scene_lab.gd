class_name MatrixOasisSceneLabTest
extends GdUnitTestSuite

const LAB_SCENE := "res://scene_binding/scene_lab.tscn"


func test_scene_lab_activates_entry_binding_from_data() -> void:
	var lab := await _spawn_lab()
	assert_str(lab.scene_title.text).is_equal("Scene lab fixture")
	assert_str(lab.location_title.text).is_equal("Start")
	assert_int(lab.terminal_grid.get_terminal_count()).is_equal(1)
	assert_str(lab.terminal_grid.get_terminal(0).get_action_id()).is_equal("go-next")
	assert_bool(lab.world_for_test()._placement_visible_for_test("placement-crate")).is_true()
	assert_bool(lab.world_for_test()._placement_visible_for_test("placement-figurine")).is_false()
	_assert_player_near(lab.player, Vector3(0, 1, 4))


func test_transition_refreshes_visibility_spawn_anchor_ending_and_reset() -> void:
	var lab := await _spawn_lab()
	var advanced := lab.apply_terminal_action_for_trace("go-next")
	await await_idle_frame()
	assert_bool(advanced["ok"]).is_true()
	assert_str(lab.location_title.text).is_equal("Next")
	assert_bool(lab.world_for_test()._placement_visible_for_test("placement-crate")).is_false()
	assert_bool(lab.world_for_test()._placement_visible_for_test("placement-figurine")).is_true()
	_assert_player_near(lab.player, Vector3(2, 1, 3))
	assert_bool(lab.terminal_grid.global_position.is_equal_approx(Vector3(-1, 0, 0))).is_true()
	var ended := lab.apply_terminal_action_for_trace("finish")
	await await_idle_frame()
	assert_bool(ended["ok"]).is_true()
	assert_int(lab.terminal_grid.get_terminal_count()).is_equal(0)
	assert_str(lab.location_title.text).is_equal("Done")
	lab.reset_button.pressed.emit()
	await await_idle_frame()
	assert_str(lab.location_title.text).is_equal("Start")
	assert_int(lab.terminal_grid.get_terminal_count()).is_equal(1)
	assert_bool(lab.world_for_test()._placement_visible_for_test("placement-crate")).is_true()
	_assert_player_near(lab.player, Vector3(0, 1, 4))


func test_runtime_failure_preserves_world_session_player_and_terminal() -> void:
	var lab := await _spawn_lab()
	var snapshot := JSON.stringify(lab.snapshot_copy_for_test(), "", true)
	var transform := lab.player.global_transform
	var terminal := lab.terminal_grid.get_terminal(0)
	var crate_visible := lab.world_for_test()._placement_visible_for_test("placement-crate")
	assert_bool(lab._apply_action("missing-action")).is_false()
	assert_str(lab.status_label.text).is_equal("PACK_RUNTIME_ACTION_UNKNOWN")
	assert_str(JSON.stringify(lab.snapshot_copy_for_test(), "", true)).is_equal(snapshot)
	assert_bool(lab.player.global_transform.is_equal_approx(transform)).is_true()
	assert_object(lab.terminal_grid.get_terminal(0)).is_same(terminal)
	assert_bool(lab.world_for_test()._placement_visible_for_test("placement-crate")).is_equal(crate_visible)


func _spawn_lab() -> MatrixOasisSceneLab:
	var pair := MatrixOasisSceneTestFixture.prepared_pair()
	assert_bool(pair.is_empty()).is_false()
	var packed := load(LAB_SCENE) as PackedScene
	assert_object(packed).is_not_null()
	var lab: MatrixOasisSceneLab = auto_free(packed.instantiate()) as MatrixOasisSceneLab
	lab.set_prepared_for_test(pair["runtime"], pair["scene"])
	var controller := lab.get_node("FirstPersonPlayer") as MatrixOasisFirstPersonController
	controller.capture_mouse_on_ready = false
	controller.input_enabled = false
	add_child(lab)
	await await_idle_frame()
	return lab


func _assert_player_near(player: MatrixOasisFirstPersonController, expected: Vector3) -> void:
	assert_float(player.global_position.x).is_equal_approx(expected.x, 0.02)
	assert_float(player.global_position.y).is_between(expected.y - 0.15, expected.y + 0.15)
	assert_float(player.global_position.z).is_equal_approx(expected.z, 0.02)
