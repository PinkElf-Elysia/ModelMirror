class_name MatrixOasisSceneComposerTest
extends GdUnitTestSuite


func test_composer_builds_visuals_static_colliders_and_binding_visibility() -> void:
	var pair := MatrixOasisSceneTestFixture.prepared_pair()
	assert_bool(pair.is_empty()).is_false()
	var result := MatrixOasisSceneComposer.compose(pair["scene"])
	assert_bool(result["ok"]).is_true()
	var world: MatrixOasisComposedScene = result["world"]
	var root: Node3D = auto_free(world._root_for_runtime()) as Node3D
	assert_int(root.get_child_count()).is_equal(3)
	assert_bool(world._apply_node_for_runtime("node-start")).is_true()
	assert_bool(world._placement_visible_for_test("placement-floor")).is_true()
	assert_bool(world._placement_visible_for_test("placement-crate")).is_true()
	assert_bool(world._placement_visible_for_test("placement-figurine")).is_false()
	assert_int(world._placement_collision_layer_for_test("placement-crate")).is_equal(1)
	assert_int(world._placement_collision_layer_for_test("placement-figurine")).is_equal(0)
	assert_bool(world._apply_node_for_runtime("node-next")).is_true()
	assert_bool(world._placement_visible_for_test("placement-crate")).is_false()
	assert_bool(world._placement_visible_for_test("placement-figurine")).is_true()
	assert_bool(world._apply_node_for_runtime("missing")).is_false()


func test_anchor_conversion_is_millimetre_and_millidegree_exact() -> void:
	var transform := MatrixOasisSceneComposer.anchor_transform({"positionMm": [2000, 1000, -3000], "yawMilliDegrees": 90000})
	assert_bool(transform.origin.is_equal_approx(Vector3(2.0, 1.0, -3.0))).is_true()
	assert_bool((transform.basis * Vector3.FORWARD).is_equal_approx(Vector3.LEFT)).is_true()


func test_invalid_prepared_input_fails_without_a_world() -> void:
	var result := MatrixOasisSceneComposer.compose(null)
	assert_bool(result["ok"]).is_false()
	assert_str(result["diagnostics"][0]["code"]).is_equal("PACK_GODOT_SCENE_INTERNAL_ERROR")
	assert_bool(result.has("world")).is_false()
