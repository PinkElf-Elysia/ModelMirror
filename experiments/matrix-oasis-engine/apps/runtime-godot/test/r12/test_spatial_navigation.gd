class_name MatrixOasisSpatialNavigationTest
extends GdUnitTestSuite

const LAB_SCENE := "res://spatial_prototype/spatial_scene_lab.tscn"


func test_navigation_binding_accepts_valid_nodes_and_rejects_mismatched_player_cell() -> void:
	var packed := load(LAB_SCENE) as PackedScene
	assert_object(packed).is_not_null()
	var lab := auto_free(packed.instantiate()) as MatrixOasisSpatialSceneLab
	assert_bool(lab.set_spatial_binding_layout(_binding_layout(), _identity_root(), "YXZ")).is_true()
	var invalid := _navigation()
	invalid["bindings"][0]["playerCellIndex"] = 1
	assert_bool(lab.set_spatial_navigation(invalid)).is_false()
	assert_bool(lab.set_spatial_navigation(_navigation())).is_true()


func test_navigation_preserves_valid_transition_and_restores_out_of_bounds_player() -> void:
	var pair := MatrixOasisSceneTestFixture.prepared_pair()
	assert_bool(pair.is_empty()).is_false()
	var packed := load(LAB_SCENE) as PackedScene
	assert_object(packed).is_not_null()
	var lab := auto_free(packed.instantiate()) as MatrixOasisSpatialSceneLab
	lab.set_prepared_for_test(pair["runtime"], pair["scene"])
	assert_bool(lab.set_spatial_binding_layout(_binding_layout(), _identity_root(), "YXZ")).is_true()
	assert_bool(lab.set_spatial_navigation(_navigation())).is_true()
	var controller := lab.get_node("FirstPersonPlayer") as MatrixOasisFirstPersonController
	controller.capture_mouse_on_ready = false
	controller.input_enabled = false
	add_child(lab)
	assert_bool(lab.activate_spatial_navigation()).is_true()
	assert_bool(controller.global_position.is_equal_approx(Vector3(0, 1, 4))).is_true()
	controller.global_position = Vector3(100, 1, 100)
	lab._physics_process(1.0 / 60.0)
	assert_bool(controller.global_position.is_equal_approx(Vector3(0, 1, 4))).is_true()
	var advanced := lab.apply_terminal_action_for_trace("go-next")
	assert_bool(advanced["ok"]).is_true()
	assert_bool(controller.global_position.is_equal_approx(Vector3(2, 1, 3))).is_true()


func _identity_root() -> Dictionary:
	return {"translationMm": [0, 0, 0], "rotationMilliDegrees": [0, 0, 0]}


func _binding_layout() -> Array:
	return [
		{"nodeId": "node-start", "playerSpawn": {"positionMm": [0, 1000, 4000], "yawMilliDegrees": 0}, "actionAnchor": {"positionMm": [0, 0, 1000], "yawMilliDegrees": 0}},
		{"nodeId": "node-next", "playerSpawn": {"positionMm": [2000, 1000, 3000], "yawMilliDegrees": -90000}, "actionAnchor": {"positionMm": [-1000, 0, 0], "yawMilliDegrees": 90000}},
	]


func _navigation() -> Dictionary:
	return {
		"profile": "collider-agent-grid-v1",
		"cellSizeMm": 1000,
		"agentRadiusMm": 350,
		"agentHeightMm": 2000,
		"maximumStepMm": 450,
		"minimumClearanceMm": 700,
		"sourceIslandCount": 1,
		"cells": [[0, 0, 4000], [0, 0, 3000], [0, 0, 2000], [0, 0, 1000], [1000, 0, 3000], [2000, 0, 3000], [0, 0, 0], [-1000, 0, 0]],
		"bindings": [
			{"nodeId": "node-start", "playerCellIndex": 0, "terminalCellIndex": 3, "pathCellCount": 4},
			{"nodeId": "node-next", "playerCellIndex": 5, "terminalCellIndex": 7, "pathCellCount": 6},
		],
	}
